"""Short atomic mutations, durable browser-scoped idempotency and explicit slot consent."""
import hashlib
import json
import re
import uuid
from functools import wraps

from django.core import signing
from django.core.exceptions import PermissionDenied
from django.core.validators import MaxValueValidator
from django.db import transaction
from django.db.models import F, Max

from .access import get_character_grant, has_master_access
from .chat import create_message
from .models import (Action, Character, CharacterAction, CharacterGrant, CharacterResource,
                     ChatMessage, Event, InventoryEntry, Item, Operation)

PROPOSAL_SALT = 'game.action-slot-consent.v1'


class DomainError(Exception):
    def __init__(self, message, status='error'):
        self.result = {'status': status, 'message': message}


def _service(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            with transaction.atomic():
                return function(*args, **kwargs)
        except DomainError as exc:
            return exc.result
    return wrapped


def _uuid(value):
    if not isinstance(value, (str, uuid.UUID)):
        raise DomainError('Некорректный идентификатор.')
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise DomainError('Некорректный идентификатор.') from None


def _integer(value, minimum=0, maximum=None):
    if isinstance(value, str):
        if not re.fullmatch(r'[0-9]+', value) or len(value) > 100:
            raise DomainError('Введите целое неотрицательное число.')
        value = int(value)
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise DomainError('Число вне допустимого диапазона.')
    return value


def _maximum(model, field):
    return min(v.limit_value for v in model._meta.get_field(field).validators
               if isinstance(v, MaxValueValidator))


def _player(request, character_id):
    character_id = _uuid(character_id)
    grant = get_character_grant(request, character_id)
    if grant is None:
        raise PermissionDenied
    return grant


def _master(request):
    if not has_master_access(request):
        raise PermissionDenied
    return 'master:' + request.session.session_key


def _begin(scope, request_id, intent):
    key = _uuid(request_id)
    fingerprint = hashlib.sha256(json.dumps(intent, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    old = Operation.objects.filter(scope=scope, key=key).first()
    if old and old.fingerprint != fingerprint:
        raise DomainError('Этот идентификатор запроса уже использован для другого действия.')
    return key, fingerprint, old.result if old else None


def _complete(scope, key, fingerprint, **extra):
    result = {'status': 'ok', 'message': 'Готово.', **extra}
    Operation.objects.create(scope=scope, key=key, fingerprint=fingerprint, result=result)
    return result


def _bump(character):
    if character.revision >= _maximum(Character, 'revision'):
        raise DomainError('Достигнут предел версии персонажа.')
    Character.objects.filter(pk=character.pk).update(revision=F('revision') + 1)
    character.refresh_from_db()


def _offer(grant, action, key):
    resource = CharacterResource.objects.filter(character_id=grant.character_id,
        kind='spell_slot', level__gt=action.slot_level, level__lte=7, current__gte=1).order_by('level').first()
    if resource is None:
        raise DomainError('Нет доступного ресурса для действия.')
    payload = {'grant': str(grant.pk), 'character': str(grant.character_id),
               'character_revision': grant.character.revision, 'action': str(action.pk),
               'action_revision': action.revision, 'required': action.slot_level,
               'offered': resource.level, 'request': str(key)}
    return {'status': 'offer', 'message': 'Использовать ячейку более высокого уровня?',
            'offered_level': resource.level, 'required_level': action.slot_level,
            'proposal': signing.dumps(payload, salt=PROPOSAL_SALT, compress=True)}


@_service
def apply_action(request, character_id, action_id, request_id, confirmed_level=None, proposal=None):
    grant = _player(request, character_id)
    action_id = _uuid(action_id)
    scope = 'player:' + str(grant.pk)
    key, fingerprint, replay = _begin(scope, request_id,
        {'kind': 'apply_action', 'character': str(grant.character_id), 'action': str(action_id)})
    if replay is not None:
        return replay
    action = Action.objects.filter(pk=action_id, character_grants__character_id=grant.character_id).first()
    if action is None:
        raise DomainError('Действие недоступно персонажу.')
    resource = None
    confirming = confirmed_level is not None or proposal is not None
    if action.resource != 'none':
        flag = {'spell_slot': 'can_cast', 'rage': 'can_rage', 'wildshape': 'can_wildshape'}.get(action.resource)
        if not flag or not getattr(grant.character, flag):
            raise DomainError('Эта способность отключена.')
        level = action.slot_level if action.resource == 'spell_slot' else 0
        if action.resource == 'spell_slot' and confirming:
            confirmed_level = _integer(confirmed_level, 1, 7)
            try:
                if not isinstance(proposal, str) or len(proposal) > 4096:
                    raise signing.BadSignature()
                payload = signing.loads(proposal, salt=PROPOSAL_SALT, max_age=600)
            except (signing.BadSignature, ValueError, TypeError):
                raise DomainError('Подтверждение недействительно или истекло.') from None
            expected = {'grant': str(grant.pk), 'character': str(grant.character_id),
                'character_revision': grant.character.revision, 'action': str(action.pk),
                'action_revision': action.revision, 'required': action.slot_level, 'request': str(key)}
            if not isinstance(payload, dict) or any(payload.get(k) != v for k, v in expected.items()):
                raise DomainError('Данные изменились. Повторите действие.', 'stale')
            if payload.get('offered') != confirmed_level or confirmed_level <= action.slot_level:
                raise DomainError('Подтвердите предложенный уровень ячейки.')
            if CharacterResource.objects.filter(character_id=grant.character_id, kind='spell_slot',
                                                level=action.slot_level, current__gte=1).exists():
                raise DomainError('Доступна исходная ячейка. Повторите действие.', 'stale')
            level = confirmed_level
        elif confirming:
            raise DomainError('Подтверждение не требуется для этого действия.')
        resource = CharacterResource.objects.filter(character_id=grant.character_id,
            kind=action.resource, level=level, current__gte=1).first()
        if resource is None:
            if action.resource == 'spell_slot':
                return _offer(grant, action, key)
            raise DomainError('Нет доступного ресурса для действия.')
    elif confirming:
        raise DomainError('Подтверждение не требуется для этого действия.')
    metadata = {}
    if resource:
        changed = CharacterResource.objects.filter(pk=resource.pk, current__gte=1).update(current=F('current') - 1)
        if not changed:
            raise DomainError('Ресурс уже израсходован. Повторите действие.', 'stale')
        metadata = {'used_level': resource.level, 'slot_level': resource.level, 'resource': resource.kind}
    _bump(grant.character)
    message = create_message(recipients=[grant.character_id], kind='action_use',
        sender_character_id=grant.character_id, action=action, from_master=False,
        text='Применено действие.', metadata=metadata)
    return _complete(scope, key, fingerprint, message_id=message.pk, **metadata)


@_service
def set_health(request, character_id, value, request_id):
    grant = _player(request, character_id)
    value = _integer(value, maximum=_maximum(Character, 'health_current'))
    scope = 'player:' + str(grant.pk)
    key, fingerprint, replay = _begin(scope, request_id,
        {'kind': 'health', 'character': str(grant.character_id), 'value': value})
    if replay is not None:
        return replay
    old = grant.character.health_current
    if value != old:
        Character.objects.filter(pk=grant.character_id).update(health_current=value)
        _bump(grant.character)
        create_message(recipients=[grant.character_id], kind='health', from_master=False,
            sender_character_id=grant.character_id, text='Изменено здоровье.', metadata={'old': old, 'new': value})
    return _complete(scope, key, fingerprint)


@_service
def remove_item(request, character_id, item_id, quantity, request_id):
    grant = _player(request, character_id)
    item_id = _uuid(item_id)
    quantity = _integer(quantity, 1, _maximum(InventoryEntry, 'quantity'))
    scope = 'player:' + str(grant.pk)
    key, fingerprint, replay = _begin(scope, request_id,
        {'kind': 'remove_item', 'character': str(grant.character_id), 'item': str(item_id), 'quantity': quantity})
    if replay is not None:
        return replay
    entry = InventoryEntry.objects.filter(character_id=grant.character_id, item_id=item_id).first()
    if entry is None or entry.quantity < quantity:
        raise DomainError('Недостаточно предметов.')
    if entry.quantity == quantity:
        entry.delete()
    else:
        InventoryEntry.objects.filter(pk=entry.pk, quantity__gte=quantity).update(quantity=F('quantity') - quantity)
    _bump(grant.character)
    message = create_message(recipients=[grant.character_id], kind='item_remove',
        sender_character_id=grant.character_id, item_id=item_id, from_master=False,
        text='Удалён предмет из инвентаря.', metadata={'quantity': quantity})
    return _complete(scope, key, fingerprint, message_id=message.pk)


@_service
def grant_catalog(request, kind, object_id, character_ids, request_id):
    scope = _master(request)
    if kind not in ('event', 'item', 'action'):
        raise DomainError('Неизвестный вид записи.')
    object_id = _uuid(object_id)
    if not isinstance(character_ids, (list, tuple, set)):
        raise DomainError('Выберите получателей.')
    ids = sorted(set(str(_uuid(pk)) for pk in character_ids))
    if not ids:
        raise DomainError('Выберите получателей.')
    key, fingerprint, replay = _begin(scope, request_id,
        {'kind': 'grant_catalog', 'catalog': kind, 'object': str(object_id), 'characters': ids})
    if replay is not None:
        return replay
    obj = {'event': Event, 'item': Item, 'action': Action}[kind].objects.filter(pk=object_id).first()
    characters = list(Character.objects.filter(pk__in=ids, is_active=True))
    if obj is None or len(characters) != len(ids):
        raise DomainError('Запись или получатель недоступны.')
    recipients = []
    for character in characters:
        if kind == 'item':
            entry, created = InventoryEntry.objects.get_or_create(character=character, item=obj)
            if not created:
                if entry.quantity >= _maximum(InventoryEntry, 'quantity'):
                    raise DomainError('Достигнут предел количества предметов.')
                InventoryEntry.objects.filter(pk=entry.pk).update(quantity=F('quantity') + 1)
        elif kind == 'action':
            _, created = CharacterAction.objects.get_or_create(character=character, action=obj)
            if not created:
                continue
        if kind != 'event':
            _bump(character)
        recipients.append(character.pk)
    extra = {}
    if recipients:
        message = create_message(recipients=recipients, kind={'event': 'event', 'item': 'item_grant',
            'action': 'action_grant'}[kind], from_master=True,
            text={'event': 'Отправлено событие.', 'item': 'Выдан предмет.', 'action': 'Выдано действие.'}[kind],
            **{kind: obj})
        extra['message_id'] = message.pk
    return _complete(scope, key, fingerprint, **extra)


@_service
def rest_characters(request, request_id):
    scope = _master(request)
    key, fingerprint, replay = _begin(scope, request_id, {'kind': 'rest'})
    if replay is not None:
        return replay
    changed_count = 0
    for character in Character.objects.filter(is_active=True):
        changes = {}
        if character.health_current != character.health_max:
            changes['health'] = {'old': character.health_current, 'new': character.health_max}
            Character.objects.filter(pk=character.pk).update(health_current=character.health_max)
        resource_changes = []
        flags = {'spell_slot': character.can_cast, 'rage': character.can_rage, 'wildshape': character.can_wildshape}
        for resource in character.resources.all():
            if flags.get(resource.kind) and resource.current != resource.maximum:
                resource_changes.append({'kind': resource.kind, 'level': resource.level,
                                         'old': resource.current, 'new': resource.maximum})
                CharacterResource.objects.filter(pk=resource.pk).update(current=F('maximum'))
        if resource_changes:
            changes['resources'] = resource_changes
        if changes:
            _bump(character)
            changed_count += 1
            create_message(recipients=[character.pk], kind='rest', from_master=True,
                           text='Завершён отдых.', metadata=changes)
    return _complete(scope, key, fingerprint, changed_count=changed_count)


@_service
def send_chat(request, *, text, character_id, as_master, request_id):
    if type(as_master) is not bool:
        raise DomainError('Некорректная роль отправителя.')
    if as_master:
        scope = _master(request)
        character_id = _uuid(character_id)
        if not Character.objects.filter(pk=character_id, is_active=True).exists():
            raise DomainError('Получатель недоступен.')
    else:
        grant = _player(request, character_id)
        character_id = grant.character_id
        scope = 'player:' + str(grant.pk)
    if not isinstance(text, str) or not text.strip() or len(text.strip()) > 10000:
        raise DomainError('Введите сообщение длиной от 1 до 10000 символов.')
    text = text.strip()
    key, fingerprint, replay = _begin(scope, request_id,
        {'kind': 'text', 'character': str(character_id), 'text': text, 'as_master': as_master})
    if replay is not None:
        return replay
    message = create_message(recipients=[character_id], kind='text', text=text, from_master=as_master,
                            sender_character_id=None if as_master else character_id)
    return _complete(scope, key, fingerprint, message_id=message.pk)


@_service
def mark_chat_read(request, character_id, through_id=None):
    grant = _player(request, character_id)
    latest = ChatMessage.objects.filter(recipient_links__character_id=grant.character_id).aggregate(
        latest=Max('id'))['latest'] or 0
    cursor = latest if through_id is None else min(_integer(through_id), latest)
    CharacterGrant.objects.filter(pk=grant.pk, last_read_message_id__lt=cursor).update(last_read_message_id=cursor)
    grant.refresh_from_db()
    return {'status': 'ok', 'message': 'Прочитано.', 'through_id': grant.last_read_message_id}
