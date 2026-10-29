import io
import json
import tempfile
import zipfile
import threading
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from PIL import Image

from django.core.exceptions import ValidationError
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from django.db import close_old_connections

from game.models import (Event, ImportJob, Item, Action, Character, CharacterResource,
                         InventoryEntry, CharacterAction, MediaAsset, CharacterInvitation, CharacterGrant, ChatMessage)
from game.archives import create_import, get_preview, commit_import, cancel_import, export_archive, StaleImport, cleanup_imports
from game import media
from game.validators import default_abilities, default_skills, default_weapons


def archive(events=None, extra=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as out:
        out.writestr('manifest.json', json.dumps({'schema_version': 1}))
        for kind in ('events', 'items', 'actions', 'characters'):
            out.writestr(kind + '.json', json.dumps(events or [] if kind == 'events' else []))
        for name, data in (extra or {}).items():
            out.writestr(name, data)
    stream.seek(0)
    return stream


def png(color='red'):
    stream=io.BytesIO()
    Image.new('RGBA',(10,10),color).save(stream,format='PNG')
    stream.seek(0)
    return stream


def rewrite(content, transform):
    with zipfile.ZipFile(io.BytesIO(content)) as source:
        entries={name:source.read(name) for name in source.namelist()}
    transform(entries)
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as out:
        for name,value in entries.items(): out.writestr(name,value)
    stream.seek(0)
    return stream


class ArchiveTests(TransactionTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = override_settings(IMPORT_ROOT=Path(self.tmp.name)/'imports', MEDIA_ROOT=Path(self.tmp.name)/'media')
        self.settings.enable()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.settings.disable)

    def event(self, id='one', description='First'):
        return dict(id=id, name='Fire', description=description, tags=[], color='red')

    def test_staging_and_idempotent_commit(self):
        job = create_import(archive([self.event()]), 'a'*32)
        self.assertEqual(Event.objects.count(), 0)
        self.assertEqual(get_preview(job, 'a'*32)['conflicts'], [])
        result = commit_import(job.id, 'a'*32, {})
        self.assertEqual(Event.objects.count(), 1)
        self.assertEqual(commit_import(job.id, 'a'*32, {}), result)
        exported = zipfile.ZipFile(io.BytesIO(export_archive()))
        self.assertEqual(set(exported.namelist()), {'manifest.json','events.json','items.json','actions.json','characters.json'})

    def test_full_duplicate_group_and_existing_identity(self):
        old = Event.objects.create(name='fire', description='Old', tags=[], color='red')
        job = create_import(archive([self.event('a'),self.event('b','Second')]), 'a'*32)
        conflicts = get_preview(job, 'a'*32)['conflicts']
        self.assertEqual(len(conflicts[0]['candidates']), 3)
        commit_import(job.id,'a'*32,{conflicts[0]['key']:'incoming_1'})
        old.refresh_from_db()
        self.assertEqual(old.description,'Second')
        self.assertEqual(Event.objects.count(),1)

    def test_invalid_paths_and_references_leave_no_domain_writes(self):
        for name in ('../evil.png', 'C:/evil.png', 'folder/x.png', '\\evil.png'):
            with self.assertRaises(ValidationError):
                create_import(archive([self.event()],{name:b'bad'}),'a'*32)
        self.assertFalse(Event.objects.exists())
        self.assertFalse(any((Path(self.tmp.name)/'imports').glob('*')))

    def test_session_cancel_and_stale(self):
        job = create_import(archive([self.event()]),'a'*32)
        with self.assertRaises(ValidationError): cancel_import(job.id,'b'*32)
        Event.objects.create(name='Other',description='Changed',color='blue')
        with self.assertRaises(StaleImport): commit_import(job.id,'a'*32,{})
        self.assertFalse(Event.objects.filter(name='Fire').exists())
        cancel_import(job.id,'a'*32)
        self.assertFalse((Path(self.tmp.name)/'imports'/str(job.id)).exists())

    def domain(self):
        icon=media.store_png(png(),size=256)
        portrait=media.store_png(png('blue'),size=512)
        item=Item.objects.create(name='Sword',description='Sharp',tags=['metal'],icon=icon)
        action=Action.objects.create(name='Hit',description='Strike',tags=[],icon=icon,resource='spell_slot',slot_level=3)
        char=Character.objects.create(name='Hero',character_class='Mage',portrait=portrait,
            abilities={k:-3 for k in default_abilities()},saves=default_abilities(),skills={k:-2 for k in default_skills()},
            weapons=[{'name':'Sword','attack':'+3','damage':'1d6'}]+default_weapons()[1:],
            health_current=15,health_max=10,can_cast=True,spellcasting_ability='int',can_rage=True,can_wildshape=True)
        for level in range(1,8): CharacterResource.objects.create(character=char,kind='spell_slot',level=level,current=level,maximum=level)
        for kind in ('rage','wildshape'): CharacterResource.objects.create(character=char,kind=kind,current=2,maximum=3)
        InventoryEntry.objects.create(character=char,item=item,quantity=4)
        CharacterAction.objects.create(character=char,action=action)
        return char,item,action

    def test_complete_png_roundtrip_excludes_auth_and_chat(self):
        char,item,action=self.domain()
        invitation=CharacterInvitation.objects.create(character=char,token_digest='a'*64)
        CharacterGrant.objects.create(character=char,invitation=invitation,session_key='b'*32)
        ChatMessage.objects.create(kind='text',text='Secret')
        content=export_archive()
        with zipfile.ZipFile(io.BytesIO(content)) as source:
            self.assertEqual(len(source.namelist()),7)
            self.assertNotIn(b'Secret',source.read('characters.json'))
        Character.objects.all().delete();Item.objects.all().delete();Action.objects.all().delete()
        job=create_import(io.BytesIO(content),'a'*32)
        commit_import(job.id,'a'*32,{})
        char=Character.objects.get()
        self.assertEqual(char.resources.count(),9)
        self.assertEqual(char.inventory.get().quantity,4)
        self.assertEqual(char.action_grants.count(),1)
        self.assertEqual(char.abilities['str'],-3)
        self.assertEqual(char.skills['arcana'],-2)
        self.assertEqual(len(char.weapons),5)
        self.assertEqual(char.health_current,15)
        self.assertFalse(char.is_active)
        self.assertTrue(media.asset_path(char.portrait).exists())

    def test_identical_opaque_ids_and_image_filenames_collapse(self):
        self.domain()
        def transform(entries):
            aliases={}
            for kind in ('items','actions','characters'):
                rows=json.loads(entries[kind+'.json'])
                for row in rows:
                    aliases[row['id']]='new_'+row['id'];row['id']=aliases[row['id']]
                    field='portrait' if kind=='characters' else 'icon'
                    old=row[field];new='renamed_'+old
                    entries[new]=entries[old];row[field]=new
                    if kind=='characters':
                        for entry in row['inventory']:entry['item']=aliases[entry['item']]
                        row['actions']=[aliases[a] for a in row['actions']]
                entries[kind+'.json']=json.dumps(rows)
        job=create_import(rewrite(export_archive(),transform),'a'*32)
        self.assertEqual(get_preview(job,'a'*32)['conflicts'],[])
        result=commit_import(job.id,'a'*32,{})
        self.assertEqual(result['changed'],0)

    def test_replacement_preserves_pk_chat_and_grants(self):
        char,item,action=self.domain()
        char.is_active=True;char.save()
        invitation=CharacterInvitation.objects.create(character=char,token_digest='a'*64)
        grant=CharacterGrant.objects.create(character=char,invitation=invitation,session_key='b'*32)
        message=ChatMessage.objects.create(kind='item_grant',item=item)
        def transform(entries):
            for kind in ('items','characters'):
                rows=json.loads(entries[kind+'.json'])
                rows[0]['description' if kind=='items' else 'backstory']='Replacement'
                entries[kind+'.json']=json.dumps(rows)
        job=create_import(rewrite(export_archive(),transform),'a'*32)
        choices={c['key']:'incoming_0' for c in get_preview(job,'a'*32)['conflicts']}
        commit_import(job.id,'a'*32,choices)
        char.refresh_from_db();item.refresh_from_db();message.refresh_from_db();grant.refresh_from_db()
        self.assertTrue(char.is_active)
        self.assertEqual(char.revision,2)
        self.assertEqual(char.backstory,'Replacement')
        self.assertEqual(item.description,'Replacement')
        self.assertEqual(message.item_id,item.pk)
        self.assertEqual(grant.character_id,char.pk)

    def test_failed_commit_compensates_png_and_rolls_back(self):
        self.domain();content=export_archive()
        Character.objects.all().delete();Item.objects.all().delete();Action.objects.all().delete()
        MediaAsset.objects.all().delete()
        for path in (Path(self.tmp.name)/'media').glob('*'):path.unlink()
        job=create_import(io.BytesIO(content),'a'*32)
        with patch.object(Character,'save',side_effect=RuntimeError('disk failure')):
            with self.assertRaises(RuntimeError):commit_import(job.id,'a'*32,{})
        self.assertEqual(Item.objects.count()+Action.objects.count()+Character.objects.count()+MediaAsset.objects.count(),0)
        self.assertEqual(list((Path(self.tmp.name)/'media').glob('*')),[])
        self.assertFalse((Path(self.tmp.name)/'imports'/str(job.pk)).exists())
        job.refresh_from_db();self.assertEqual(job.payload,{})

    def test_last_invalid_record_and_missing_png_reference_rejected(self):
        self.domain();content=export_archive()
        for mode in ('invalid','reference','png'):
            def transform(entries):
                rows=json.loads(entries['characters.json'])
                if mode=='invalid': rows[0]['health_max']=True
                elif mode=='reference': rows[0]['actions']=['missing']
                else: entries.pop(rows[0]['portrait'])
                entries['characters.json']=json.dumps(rows)
            with self.assertRaises(ValidationError):create_import(rewrite(content,transform),'a'*32)
        self.assertEqual(Character.objects.count(),1)
        self.assertFalse(any((Path(self.tmp.name)/'imports').glob('*')))

    def test_cleanup_age_and_asset_protection(self):
        char,item,action=self.domain()
        job=create_import(io.BytesIO(export_archive()),'a'*32)
        char.delete();item.delete();action.delete()
        ids=list(MediaAsset.objects.values_list('pk',flat=True))
        media.release_assets(ids)
        self.assertTrue(MediaAsset.objects.exists())
        self.assertEqual(cleanup_imports(),0)
        ImportJob.objects.filter(pk=job.pk).update(updated_at=timezone.now()-timedelta(hours=25))
        self.assertEqual(cleanup_imports(),1)
        self.assertFalse(MediaAsset.objects.exists())
        self.assertFalse((Path(self.tmp.name)/'imports'/str(job.pk)).exists())

    def test_duplicate_zip_symlink_and_expanded_limit(self):
        content=archive([self.event()]).getvalue()
        stream=io.BytesIO(content)
        with zipfile.ZipFile(stream,'a') as out: out.writestr('events.json','[]')
        stream.seek(0)
        with self.assertRaises(ValidationError):create_import(stream,'a'*32)
        stream=io.BytesIO(content)
        with zipfile.ZipFile(stream,'a') as out:
            info=zipfile.ZipInfo('link.png');info.create_system=3;info.external_attr=(0o120777<<16)
            out.writestr(info,b'outside')
        stream.seek(0)
        with self.assertRaises(ValidationError):create_import(stream,'a'*32)
        with patch('game.archives.service.MAX_EXPANDED',10):
            with self.assertRaises(ValidationError):create_import(io.BytesIO(content),'a'*32)

    def test_new_duplicate_group_and_bad_choices_cleanup(self):
        job=create_import(archive([self.event('a'),self.event('b','Second'),self.event('c','Third')]),'a'*32)
        conflict=get_preview(job,'a'*32)['conflicts'][0]
        self.assertEqual(len(conflict['candidates']),3)
        self.assertTrue(conflict['candidates'][0]['selected'])
        with self.assertRaises(ValidationError):commit_import(job.pk,'a'*32,{conflict['key']:'forged'})
        self.assertEqual(Event.objects.count(),0)
        self.assertFalse((Path(self.tmp.name)/'imports'/str(job.pk)).exists())

    def test_differing_image_content_conflicts_and_cancel_keeps_shared(self):
        char,item,action=self.domain()
        def transform(entries):
            rows=json.loads(entries['items.json'])
            entries[rows[0]['icon']]=png('green').getvalue()
        job=create_import(rewrite(export_archive(),transform),'a'*32)
        kinds={c['kind'] for c in get_preview(job,'a'*32)['conflicts']}
        self.assertIn('item',kinds)
        shared=media.asset_path(item.icon)
        cancel_import(job.pk,'a'*32)
        self.assertTrue(shared.exists())
        self.assertFalse((Path(self.tmp.name)/'imports'/str(job.pk)).exists())

    def test_float_numeric_and_tampered_directory(self):
        self.domain()
        def transform(entries):
            rows=json.loads(entries['characters.json']);rows[0]['health_max']=1.0
            entries['characters.json']=json.dumps(rows)
        with self.assertRaises(ValidationError):create_import(rewrite(export_archive(),transform),'a'*32)
        job=create_import(archive([self.event()]),'a'*32)
        ImportJob.objects.filter(pk=job.pk).update(directory='../outside')
        with self.assertRaises(ValidationError):cancel_import(job.pk,'a'*32)
        self.assertTrue((Path(self.tmp.name)/'imports'/str(job.pk)).exists())

    def test_missing_export_image_and_staged_corruption_fail_safely(self):
        char,item,action=self.domain()
        job=create_import(io.BytesIO(export_archive()),'a'*32)
        digest=next(iter(job.payload['assets']))
        (Path(self.tmp.name)/'imports'/str(job.pk)/(digest+'.png')).write_bytes(b'corrupt')
        with self.assertRaises(ValidationError):commit_import(job.pk,'a'*32,{})
        self.assertEqual(Character.objects.count(),1)
        self.assertFalse((Path(self.tmp.name)/'imports'/str(job.pk)).exists())
        media.asset_path(item.icon).unlink()
        with self.assertRaises(ValidationError):export_archive()

    def conflicting_images(self):
        asset=media.store_png(png('red'),size=256)
        item=Item.objects.create(name='Conflict',description='Old',tags=[],icon=asset)
        def transform(entries):
            entries[asset.digest+'.png']=png('green').getvalue()
        return item,asset,rewrite(export_archive(),transform).getvalue()

    def test_cancel_releases_preview_only_asset_and_keeps_other_job_protection(self):
        item,asset,content=self.conflicting_images()
        first=create_import(io.BytesIO(content),'a'*32)
        second=create_import(io.BytesIO(content),'a'*32)
        path=media.asset_path(asset)
        item.delete();media.release_assets([asset.pk])
        self.assertTrue(path.exists())
        cancel_import(first.pk,'a'*32)
        self.assertTrue(path.exists())
        cancel_import(second.pk,'a'*32)
        self.assertFalse(MediaAsset.objects.filter(pk=asset.pk).exists())
        self.assertFalse(path.exists())

    def test_stale_preview_releases_dropped_existing_image(self):
        item,asset,content=self.conflicting_images()
        job=create_import(io.BytesIO(content),'a'*32)
        item.delete();media.release_assets([asset.pk])
        with self.assertRaises(StaleImport):commit_import(job.pk,'a'*32,{})
        self.assertFalse(MediaAsset.objects.filter(pk=asset.pk).exists())
        self.assertFalse(media.asset_path(asset).exists())
        job.refresh_from_db();self.assertEqual(job.status,'review')

    def test_empty_and_partial_resource_sets_rejected_and_legacy_export_roundtrips(self):
        char,item,action=self.domain()
        content=export_archive()
        for count in (0,1,8):
            def transform(entries):
                rows=json.loads(entries['characters.json']);rows[0]['resources']=rows[0]['resources'][:count]
                entries['characters.json']=json.dumps(rows)
            with self.assertRaises(ValidationError):create_import(rewrite(content,transform),'a'*32)
        char.resources.filter(kind='rage').delete()
        job=create_import(io.BytesIO(export_archive()),'a'*32)
        self.assertEqual(len(job.payload['data']['characters'][0]['resources']),9)
        self.assertEqual(char.resources.count(),8)  # exporting is read-only

    def test_cleanup_cannot_cancel_aged_running_staging(self):
        from game.archives import service
        loaded=threading.Event();resume=threading.Event();outcome=[]
        original=service._load
        def paused(upload,directory):
            result=original(upload,directory)
            loaded.set()
            if not resume.wait(10):raise RuntimeError('test timeout')
            return result
        def worker():
            close_old_connections()
            try:outcome.append(create_import(archive([self.event()]),'a'*32))
            except Exception as exc:outcome.append(exc)
            finally:close_old_connections()
        with patch.object(service,'_load',side_effect=paused):
            thread=threading.Thread(target=worker)
            thread.start()
            try:
                self.assertTrue(loaded.wait(10))
                job=ImportJob.objects.get()
                ImportJob.objects.filter(pk=job.pk).update(updated_at=timezone.now()-timedelta(hours=25))
                self.assertEqual(cleanup_imports(),0)
                job.refresh_from_db();self.assertEqual(job.status,'staged')
            finally:
                resume.set();thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(outcome),1)
        self.assertIsInstance(outcome[0],ImportJob)
        job.refresh_from_db();self.assertEqual(job.status,'review')
        self.assertTrue((Path(self.tmp.name)/'imports'/str(job.pk)/'upload.zip').exists())

    def test_staging_rechecks_status_before_publish(self):
        from game.archives import service
        original=service._load
        def cancelled(upload,directory):
            result=original(upload,directory)
            ImportJob.objects.filter(pk=directory.name).update(status='cancelled',payload={})
            return result
        with patch.object(service,'_load',side_effect=cancelled):
            with self.assertRaises(ValidationError):create_import(archive([self.event()]),'a'*32)
        self.assertFalse(ImportJob.objects.filter(status='review').exists())
        self.assertFalse(any((Path(self.tmp.name)/'imports').glob('*')))

    def test_abandoned_claim_cleanup_releases_preview_assets_after_worker_exit(self):
        from game.archives import service
        item,asset,content=self.conflicting_images()
        job=create_import(io.BytesIO(content),'a'*32)
        item.delete();media.release_assets([asset.pk])
        ImportJob.objects.filter(pk=job.pk).update(status='staged',updated_at=timezone.now()-timedelta(hours=25))
        with service._execution_claim(job) as claimed:
            self.assertTrue(claimed)
            self.assertEqual(cleanup_imports(),0)
        # A dead worker's OS handle is closed, but the lock file remains.
        self.assertEqual(cleanup_imports(),1)
        self.assertFalse(MediaAsset.objects.filter(pk=asset.pk).exists())
        self.assertFalse(media.asset_path(asset).exists())
        self.assertFalse((Path(self.tmp.name)/'imports'/str(job.pk)).exists())
