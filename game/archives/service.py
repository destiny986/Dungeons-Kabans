"""Bounded ZIP validation, staged review and atomic domain replacement."""
import base64
import hashlib
import io
import json
import os
import errno
import shutil
import stat
import uuid
import zipfile
from collections import defaultdict
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import jsonschema
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from game import media
from game.models import (Event, Item, Action, Character, CharacterResource,
                         InventoryEntry, CharacterAction, ImportJob, MediaAsset)
from game.validators import normalize_name
from .schema import SCHEMAS, CHARACTER

MODELS = {'events':Event,'items':Item,'actions':Action,'characters':Character}
MAX_UPLOAD = 50 * 1024**2
MAX_EXPANDED = 250 * 1024**2
RESOURCE_KEYS = [('spell_slot',level) for level in range(1,8)]+[('rage',0),('wildshape',0)]


class StaleImport(ValidationError):
    """The preview has been refreshed; a fresh explicit choice is required."""


def _json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _directory(job):
    root = Path(settings.IMPORT_ROOT).resolve()
    # The only valid directory is this job's UUID, even if DB metadata was tampered.
    if job.directory != str(job.id):
        raise ValidationError('Недопустимый каталог импорта.')
    target = (root / job.directory).resolve()
    if target == root or not target.is_relative_to(root):
        raise ValidationError('Недопустимый каталог импорта.')
    return target


def _remove_directory(job):
    path = _directory(job)
    if path.exists():
        shutil.rmtree(path)


@contextmanager
def _execution_claim(job):
    """Nonblocking OS lease: survives pauses, automatically expires on process exit.

    This is a file lock rather than an age-based heartbeat, so a suspended live
    worker cannot lose ownership to cleanup. No database lock spans PNG decoding.
    """
    directory=_directory(job)
    if not directory.exists():
        yield True
        return
    path=(directory/'.execution.lock').resolve()
    if not path.is_relative_to(directory): raise ValidationError('Недопустимый каталог импорта.')
    with path.open('a+b') as stream:
        if stream.tell()==0:
            stream.write(b'0');stream.flush()
        stream.seek(0)
        locked=False
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            locked=True
        except OSError as exc:
            if exc.errno not in (errno.EACCES,errno.EAGAIN,errno.EDEADLK): raise
        try:
            yield locked
        finally:
            if locked:
                stream.seek(0)
                if os.name=='nt': msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
                else: fcntl.flock(stream.fileno(),fcntl.LOCK_UN)


def _staged(job, digest):
    if len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValidationError('Недопустимое изображение.')
    meta=job.payload['assets'][digest]
    if meta['filename']!=digest+'.png': raise ValidationError('Недопустимое изображение.')
    directory=_directory(job)
    path=(directory/meta['filename']).resolve()
    if not path.is_relative_to(directory): raise ValidationError('Недопустимое изображение.')
    return path


def _snapshot():
    data, assets = {}, {}
    for kind, model in MODELS.items():
        rows = []
        for obj in model.objects.order_by('normalized_name'):
            fields = list(CHARACTER) if kind == 'characters' else list(SCHEMAS[kind]['items']['properties'])
            row = {field: getattr(obj,field) for field in fields if field not in ('icon','portrait','resources','inventory','actions')}
            row['id'] = str(obj.pk)
            for field in ('icon','portrait'):
                if field in fields:
                    asset = getattr(obj,field)
                    row[field] = f'{asset.digest}.png' if asset else None
                    if asset:
                        try: assets[row[field]] = media.asset_path(asset).read_bytes()
                        except OSError as exc: raise ValidationError('Отсутствует файл изображения: ' + asset.digest) from exc
            if kind == 'characters':
                resources={(r['kind'],r['level']):r for r in obj.resources.values('kind','level','current','maximum')}
                # Legacy/raw-created characters may predate the nine-row form invariant.
                # Export fills only absent rows with zeroes, without mutating live data.
                row['resources'] = [resources.get((kind,level),dict(kind=kind,level=level,current=0,maximum=0))
                                    for kind,level in RESOURCE_KEYS]
                row['inventory'] = [{'item':str(e.item_id),'quantity':e.quantity} for e in obj.inventory.order_by('item__normalized_name')]
                row['actions'] = [str(v) for v in obj.action_grants.order_by('action__normalized_name').values_list('action_id',flat=True)]
            rows.append(row)
        data[kind] = rows
    return data, assets


def _semantic(kind, row, data, image_digests):
    result = {k:v for k,v in row.items() if k != 'id'}
    result['name'] = normalize_name(result['name'])
    for field in ('icon','portrait'):
        if result.get(field): result[field] = image_digests[result[field]]
    if kind == 'characters':
        names = {r['id']:normalize_name(r['name']) for r in data['items']}
        actions = {r['id']:normalize_name(r['name']) for r in data['actions']}
        result['inventory'] = sorted([{'item':names[e['item']],'quantity':e['quantity']} for e in row['inventory']],key=_json)
        result['actions'] = sorted(actions[a] for a in row['actions'])
        result['resources'] = sorted(row['resources'],key=_json)
    return result


def _fingerprint(data, images):
    digests = {name:hashlib.sha256(value).hexdigest() for name,value in images.items()}
    return _digest({kind: sorted([_semantic(kind,r,data,digests) for r in rows],key=_json) for kind,rows in data.items()})


def export_archive():
    with transaction.atomic():
        data, assets = _snapshot()
        stream = io.BytesIO()
        with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as out:
            out.writestr('manifest.json',_json({'schema_version':1}))
            for kind, rows in data.items(): out.writestr(kind+'.json',_json(rows))
            for name, content in assets.items(): out.writestr(name,content)
        return stream.getvalue()


def _validate_data(data):
    try:
        def strict_numbers(value):
            if isinstance(value,float): raise ValueError('Дробные числовые значения недопустимы')
            if isinstance(value,dict):
                for child in value.values(): strict_numbers(child)
            elif isinstance(value,list):
                for child in value: strict_numbers(child)
        strict_numbers(data)
        for kind, rows in data.items():
            jsonschema.Draft202012Validator(SCHEMAS[kind]).validate(rows)
            ids = [r['id'] for r in rows]
            if len(ids) != len(set(ids)): raise ValueError('Повтор идентификатора')
            for row in rows:
                fields = {k:v for k,v in row.items() if k not in ('id','icon','portrait','resources','inventory','actions')}
                obj = MODELS[kind](**fields)
                obj.full_clean(exclude=['icon','portrait'],validate_unique=False,validate_constraints=False)
                if kind == 'characters':
                    resources = [(r['kind'],r['level']) for r in row['resources']]
                    if len(resources) != len(set(resources)): raise ValueError('Повтор ресурса')
                    if set(resources)!=set(RESOURCE_KEYS): raise ValueError('Требуются все девять ресурсов персонажа')
                    for resource in row['resources']:
                        CharacterResource(character=obj,**resource).full_clean(exclude=['character'],validate_unique=False,validate_constraints=False)
                    members = [e['item'] for e in row['inventory']]
                    if len(members) != len(set(members)): raise ValueError('Повтор предмета')
                    for ref, kindref in [(x,'actions') for x in row['actions']] + [(e['item'],'items') for e in row['inventory']]:
                        if ref not in {r['id'] for r in data[kindref]}: raise ValueError('Отсутствует ссылка')
                    for kindref,refs in [('items',members),('actions',row['actions'])]:
                        names={r['id']:normalize_name(r['name']) for r in data[kindref]}
                        logical=[names[ref] for ref in refs]
                        if len(logical)!=len(set(logical)): raise ValueError('Повтор логического членства')
    except ValidationError as exc:
        raise ValidationError('Данные архива не прошли проверку полей.') from exc
    except (jsonschema.ValidationError,ValueError,TypeError) as exc:
        raise ValidationError('Некорректные данные архива: ' + str(exc).splitlines()[0]) from exc


def _load(upload, directory):
    content = upload.read(MAX_UPLOAD+1)
    if len(content)>MAX_UPLOAD: raise ValidationError('Архив превышает 50 МиБ.')
    (directory/'upload.zip').write_bytes(content)
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as source:
            members = source.infolist()
            names = [m.filename for m in members]
            required = {'manifest.json',*(k+'.json' for k in MODELS)}
            if len(members)>5000 or len(set(names))!=len(names) or not required.issubset(names): raise ValueError('Состав архива')
            files, total = {}, 0
            for member in members:
                name = member.filename
                if (not name or '/' in name or '\\' in name or ':' in name or name in ('.','..') or
                    (name not in required and not name.lower().endswith('.png')) or
                    member.flag_bits & 1 or stat.S_ISLNK(member.external_attr >> 16)):
                    raise ValueError('Недопустимый файл')
                if member.file_size > MAX_EXPANDED-total: raise ValueError('Объём архива')
                parts = []
                with source.open(member) as entry:
                    while chunk := entry.read(65536):
                        total += len(chunk)
                        if total>MAX_EXPANDED: raise ValueError('Объём архива')
                        parts.append(chunk)
                files[name] = b''.join(parts)
        if json.loads(files['manifest.json']) != {'schema_version':1}: raise ValueError('Версия архива')
        if type(json.loads(files['manifest.json']).get('schema_version')) is not int: raise ValueError('Версия архива')
        for name,content in files.items():
            if name.lower().endswith('.png'):
                media.prepare_png(io.BytesIO(content),size=256)
        data = {kind:json.loads(files[kind+'.json']) for kind in MODELS}
        _validate_data(data)
        assets = {}
        for kind, rows in data.items():
            for row in rows:
                field = 'portrait' if kind=='characters' else 'icon'
                if not row.get(field): continue
                name = row[field]
                if name not in files or not name.lower().endswith('.png'): raise ValueError('Отсутствует PNG')
                prepared = media.prepare_png(io.BytesIO(files[name]),size=512 if kind=='characters' else 256)
                filename = prepared.digest+'.png'
                (directory/filename).write_bytes(prepared.data)
                assets[prepared.digest] = {'filename':filename,'width':prepared.width,'height':prepared.height}
                row[field] = filename
        return {'data':data,'assets':assets}
    except (zipfile.BadZipFile,RuntimeError,ValueError,KeyError,UnicodeError,TypeError) as exc:
        raise ValidationError('Некорректный ZIP-архив.') from exc


def _plan(job):
    existing, images = _snapshot()
    incoming = job.payload['data']
    digests = {meta['filename']:digest for digest,meta in job.payload['assets'].items()}
    olddigests = {name:hashlib.sha256(content).hexdigest() for name,content in images.items()}
    groups, conflicts = [], []
    for kind, rows in incoming.items():
        grouped = defaultdict(list)
        for row in rows: grouped[normalize_name(row['name'])].append(row)
        old = {normalize_name(r['name']):r for r in existing[kind]}
        for name, versions in grouped.items():
            candidates, seen = [], set()
            if name in old:
                row = old[name]
                seen.add(_digest(_semantic(kind,row,existing,olddigests)))
                candidates.append({'value':'existing','row':row})
            for index,row in enumerate(versions):
                semantic = _digest(_semantic(kind,row,incoming,digests))
                if semantic not in seen:
                    seen.add(semantic)
                    candidates.append({'value':f'incoming_{index}','row':row})
            key = kind+'_'+hashlib.sha256(name.encode()).hexdigest()[:24]
            group = {'key':key,'kind':kind,'aliases':[r['id'] for r in versions], 'candidates':candidates}
            groups.append(group)
            if len(candidates)>1:
                conflicts.append({'key':key,'kind':kind[:-1] if kind!='characters' else 'character','label':versions[0]['name'],
                                  'candidates':[{'value':c['value'],'label':'Существующая версия' if c['value']=='existing' else 'Версия из архива',
                                                 'row':c['row'],'selected':i==0} for i,c in enumerate(candidates)]})
    return groups, {'conflicts':conflicts,'summary':f'Групп записей: {len(groups)}. Конфликтов: {len(conflicts)}.'}, _fingerprint(existing,images)


def _owned(job_id, session_key, lock=False):
    query = ImportJob.objects.select_for_update() if lock else ImportJob.objects
    job = query.filter(pk=getattr(job_id,'pk',job_id)).first()
    if not job or job.session_key!=session_key: raise ValidationError('Импорт недоступен в этой сессии.')
    return job


def create_import(upload, session_key):
    if not isinstance(session_key,str) or len(session_key)!=32: raise ValidationError('Недопустимая сессия.')
    job = ImportJob(id=uuid.uuid4(),session_key=session_key)
    job.directory = str(job.id)
    directory = _directory(job)
    job.save()
    try:
        directory.mkdir(parents=True,exist_ok=False)
        with _execution_claim(job) as claimed:
            if not claimed: raise ValidationError('Импорт уже обрабатывается.')
            job=_owned(job.pk,session_key)
            if job.status!='staged': raise ValidationError('Импорт уже завершён.')
            payload = _load(upload,directory)
            with transaction.atomic():
                # Cleanup/cancel must never be undone by a stale staging object.
                job=_owned(job.pk,session_key,True)
                if job.status!='staged': raise ValidationError('Импорт уже завершён.')
                job.payload=payload
                _, job.preview, job.base_digest = _plan(job)
                job.status='review'
                job.save()
        return job
    except Exception:
        with transaction.atomic():
            current=_owned(job.pk,session_key,True)
            if current.status=='staged': _finish(current,'failed')
        _remove_directory(job)
        raise


def get_preview(job, session_key):
    job = _owned(job,session_key)
    if job.status!='review': raise ValidationError('Импорт уже завершён.')
    # Preview is a detached DTO; only digest-based staging paths become data URIs.
    result = json.loads(_json(job.preview))
    for conflict in result['conflicts']:
        for candidate in conflict['candidates']:
            row = candidate.pop('row')
            candidate['json'] = json.dumps(row,ensure_ascii=False,indent=2)
            obj = SimpleNamespace(**row)
            filename = row.get('icon') or row.get('portrait')
            if filename:
                digest = filename.removesuffix('.png')
                if digest in job.payload['assets']:
                    content = _staged(job,digest).read_bytes()
                else:
                    asset = MediaAsset.objects.get(digest=digest)
                    content = media.asset_path(asset).read_bytes()
                obj.preview_icon = 'data:image/png;base64,'+base64.b64encode(content).decode()
            candidate['object'] = obj
    return result


def _protected_asset_ids(job):
    """Capture every asset reference before a payload/preview loses protection."""
    tokens=set()
    def collect(value):
        if isinstance(value,dict):
            for key,child in value.items():
                tokens.add(str(key));collect(child)
        elif isinstance(value,list):
            for child in value: collect(child)
        else: tokens.add(str(value))
    collect(job.payload);collect(job.preview)
    # Avoid an unbounded SQL IN clause for large but valid archives.
    return [asset.pk for asset in MediaAsset.objects.only('pk','digest','path').iterator()
            if str(asset.pk) in tokens or asset.digest in tokens or asset.path in tokens]


def _finish(job,status):
    ids = _protected_asset_ids(job)
    job.status=status
    job.payload={}
    job.preview={} if status!='complete' else job.preview
    job.save()
    media.release_assets(ids)


def commit_import(job_id, session_key, choices):
    prepared_outputs = []
    job = _owned(job_id,session_key)
    stale = False
    try:
        with transaction.atomic():
            job = _owned(job_id,session_key,True)
            if job.status=='complete': return job.preview
            if job.status!='review': raise ValidationError('Импорт уже завершён.')
            _validate_data(job.payload['data'])
            groups, preview, baseline = _plan(job)
            if baseline!=job.base_digest:
                previous_assets=_protected_asset_ids(job)
                job.preview,job.base_digest = preview,baseline
                job.save()
                media.release_assets(previous_assets)
                stale=True
            else:
                required = {c['key'] for c in preview['conflicts']}
                if not isinstance(choices,dict) or set(choices)!=required: raise ValidationError('Выберите каждую конфликтующую версию.')
                for group in groups:
                    selected = choices.get(group['key'],group['candidates'][0]['value'])
                    if selected not in {c['value'] for c in group['candidates']}: raise ValidationError('Недопустимый выбор версии.')
                    group['selected']=next(c for c in group['candidates'] if c['value']==selected)
                # Recheck every staged image before the first domain write.
                for digest,meta in job.payload['assets'].items():
                    content=_staged(job,digest).read_bytes()
                    if hashlib.sha256(content).hexdigest()!=digest or meta['width'] not in (256,512) or meta['height']!=meta['width']:
                        raise ValidationError('Изображение изменено.')
                job.status='committing';job.save()
                mappings, old_assets = {}, _protected_asset_ids(job)
                count = 0
                for group in groups:
                    kind = group['kind']; selected=group['selected']; row=selected['row']
                    existing = MODELS[kind].objects.filter(normalized_name=normalize_name(row['name'])).first()
                    if selected['value']=='existing': obj=existing
                    else:
                        obj = existing or MODELS[kind]()
                        if existing: obj.revision+=1
                        for key,value in row.items():
                            if key in ('id','resources','inventory','actions'): continue
                            if key in ('icon','portrait'):
                                previous=getattr(obj,key,None)
                                if previous: old_assets.append(previous.pk)
                                if value:
                                    digest=value.removesuffix('.png')
                                    meta=job.payload['assets'][digest]
                                    if meta['filename']!=digest+'.png': raise ValidationError('Недопустимое изображение.')
                                    content=_staged(job,digest).read_bytes()
                                    if hashlib.sha256(content).hexdigest()!=digest: raise ValidationError('Изображение изменено.')
                                    prepared=media.PreparedPNG(content,digest,meta['width'],meta['height'])
                                    prepared_outputs.append(prepared)
                                    value=media.store_prepared(prepared)
                            setattr(obj,key,value)
                        obj.full_clean()
                        obj.save()
                        count+=1
                        if kind=='characters':
                            obj.resources.all().delete();obj.inventory.all().delete();obj.action_grants.all().delete()
                            for r in row['resources']: CharacterResource.objects.create(character=obj,**r)
                            # Name aliases can coalesce archive IDs; duplicate logical memberships are rejected.
                            inventory = {}
                            for entry in row['inventory']:
                                pk=mappings[('items',entry['item'])]
                                if pk in inventory: raise ValidationError('Повтор логического предмета.')
                                inventory[pk]=entry['quantity']
                            for pk,quantity in inventory.items(): InventoryEntry.objects.create(character=obj,item_id=pk,quantity=quantity)
                            action_ids=[mappings[('actions',ref)] for ref in row['actions']]
                            if len(set(action_ids))!=len(action_ids): raise ValidationError('Повтор логического действия.')
                            for pk in action_ids: CharacterAction.objects.create(character=obj,action_id=pk)
                    for alias in group['aliases']: mappings[(kind,alias)]=obj.pk
                job.preview={'summary':f'Импорт завершён. Изменено записей: {count}.','changed':count}
                job.choices=choices
                _finish(job,'complete')
                media.release_assets(old_assets)
        if stale: raise StaleImport('Данные изменились. Проверьте обновлённые версии и подтвердите импорт снова.')
        _remove_directory(job)
        return job.preview
    except StaleImport:
        raise
    except Exception:
        with transaction.atomic():
            failed=_owned(job_id,session_key,True)
            if failed.status not in ('complete','cancelled'): _finish(failed,'failed')
        for prepared in prepared_outputs: media.cleanup_unreferenced_file(prepared)
        _remove_directory(job)
        raise


def cancel_import(job_id,session_key):
    with transaction.atomic():
        job=_owned(job_id,session_key,True)
        _directory(job)
        if job.status=='complete': raise ValidationError('Импорт уже завершён.')
        _finish(job,'cancelled')
    _remove_directory(job)


def cleanup_imports():
    """Write-lock claim prevents a concurrent commit; no transaction spans review."""
    count=0
    cutoff=timezone.now()-timedelta(hours=24)
    for pk in ImportJob.objects.filter(updated_at__lt=cutoff).values_list('pk',flat=True):
        job=ImportJob.objects.get(pk=pk)
        with _execution_claim(job) as claimed:
            if not claimed: continue
            with transaction.atomic():
                job=ImportJob.objects.select_for_update().get(pk=pk)
                if job.updated_at>=cutoff: continue
                if job.status=='committing': continue
                _directory(job)  # refuse tampered metadata before changing any status
                if job.status not in ('complete','cancelled','failed'): _finish(job,'cancelled')
        # Close the lease handle before deleting it, including on Windows.
        with transaction.atomic():
            job=ImportJob.objects.select_for_update().get(pk=pk)
            if job.status not in ('complete','cancelled','failed'): continue
            _remove_directory(job)
            count+=1
    return count
