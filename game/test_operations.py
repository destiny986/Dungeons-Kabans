import importlib.util
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.core.exceptions import PermissionDenied
from django.core.validators import MaxValueValidator
from django.test import RequestFactory

from game.access import issue_invitation, redeem_invitation, MASTER_SESSION_KEY
from game.models import (Character, MediaAsset, Action, Item, Event, CharacterAction,
                         CharacterResource, InventoryEntry, ChatMessage, Operation)

pytestmark = pytest.mark.django_db


def browser(character=None, master=False):
    request = RequestFactory().post('/')
    request.session = SessionStore()
    request.session.create()
    if character:
        token, _ = issue_invitation(character)
        redeem_invitation(request, token)
    if master:
        user, _ = get_user_model().objects.get_or_create(username='master')
        user.set_password('secret')
        user.save()
        request.session[MASTER_SESSION_KEY] = user.get_session_auth_hash()
        request.session.save()
    return request


@pytest.fixture
def setup():
    assert importlib.util.find_spec('game.operations'), 'Atomic operation service is required'
    from game import operations as op
    char = Character.objects.create(name='Hero', character_class='Wizard', can_cast=True,
                                    spellcasting_ability='int', health_current=80, health_max=100)
    request = browser(char)
    char.refresh_from_db()
    icon = MediaAsset.objects.create(digest='a' * 64, path='a.png', width=1, height=1)
    action = Action.objects.create(name='Spell', description='Live description', icon=icon,
                                   resource='spell_slot', slot_level=2)
    CharacterAction.objects.create(character=char, action=action)
    return op, char, request, action, icon


def slot(char, level, current=1):
    return CharacterResource.objects.create(character=char, kind='spell_slot', level=level,
                                            current=current, maximum=max(1, current))


def apply(data, key=None, **kwargs):
    op, char, req, action, _ = data
    return op.apply_action(req, char.pk, action.pk, key or uuid.uuid4(), **kwargs)


def test_exact_slot_priority_and_idempotency(setup):
    _, char, _, _, _ = setup
    low, high = slot(char, 2), slot(char, 4)
    key = uuid.uuid4()
    result = apply(setup, key)
    assert result['status'] == 'ok' and result['used_level'] == 2
    assert apply(setup, key) == result
    low.refresh_from_db(); high.refresh_from_db()
    assert (low.current, high.current) == (0, 1)
    assert ChatMessage.objects.count() == Operation.objects.count() == 1
    assert ChatMessage.objects.get().metadata['slot_level'] == 2


def test_offer_is_minimum_and_confirmation_once(setup):
    _, char, _, action, _ = setup
    fourth, sixth = slot(char, 4), slot(char, 6)
    key = uuid.uuid4()
    offer = apply(setup, key)
    assert offer['status'] == 'offer' and offer['offered_level'] == 4
    assert not ChatMessage.objects.exists() and not Operation.objects.exists()
    fourth.refresh_from_db(); assert fourth.current == 1
    result = apply(setup, key, confirmed_level=4, proposal=offer['proposal'])
    assert result['status'] == 'ok' and result['used_level'] == 4
    assert apply(setup, key, confirmed_level=4, proposal=offer['proposal']) == result
    assert ChatMessage.objects.get().action == action
    sixth.refresh_from_db(); assert sixth.current == 1


def test_offer_rechecks_stock_and_requires_fresh_consent(setup):
    _, char, _, _, _ = setup
    fourth = slot(char, 4); slot(char, 6)
    key = uuid.uuid4(); offer = apply(setup, key)
    fourth.current = 0; fourth.save()
    fresh = apply(setup, key, confirmed_level=4, proposal=offer['proposal'])
    assert fresh['status'] == 'offer' and fresh['offered_level'] == 6
    assert not ChatMessage.objects.exists()
    assert apply(setup, key, confirmed_level=6, proposal=fresh['proposal'])['used_level'] == 6


def test_forged_revision_other_browser_and_manual_upcast_rejected(setup):
    op, char, req, action, _ = setup
    slot(char, 4)
    key = uuid.uuid4(); offer = apply(setup, key)
    assert apply(setup, key, confirmed_level=4, proposal='bad')['status'] == 'error'
    assert apply(setup, key, confirmed_level=6, proposal=offer['proposal'])['status'] == 'error'
    other = browser(char)
    assert op.apply_action(other, char.pk, action.pk, key, 4, offer['proposal'])['status'] == 'stale'
    action.revision += 1; action.save()
    assert apply(setup, key, confirmed_level=4, proposal=offer['proposal'])['status'] == 'stale'
    assert not ChatMessage.objects.exists()


def test_required_slot_appearing_returns_stale(setup):
    _, char, _, _, _ = setup
    slot(char, 4)
    key = uuid.uuid4(); offer = apply(setup, key); slot(char, 2)
    assert apply(setup, key, confirmed_level=4, proposal=offer['proposal'])['status'] == 'stale'
    assert not ChatMessage.objects.exists()


def test_empty_level_seven_disabled_and_free_actions(setup):
    _, char, _, action, _ = setup
    assert apply(setup)['status'] == 'error'
    action.slot_level = 7; action.save(); slot(char, 7)
    assert apply(setup)['used_level'] == 7
    char.can_cast = False; char.save(); slot(char, 4)
    assert apply(setup)['status'] == 'error'
    action.resource = 'none'; action.slot_level = None; action.save()
    assert apply(setup)['status'] == 'ok'
    for resource, flag in [('rage', 'can_rage'), ('wildshape', 'can_wildshape')]:
        action.resource = resource; action.save()
        CharacterResource.objects.create(character=char, kind=resource, current=1, maximum=1)
        assert apply(setup)['status'] == 'error'
        setattr(char, flag, True); char.save()
        assert apply(setup)['status'] == 'ok'
        assert apply(setup)['status'] == 'error'


def test_health_validation_nochange_and_key_binding(setup):
    op, char, req, _, _ = setup
    key = uuid.uuid4()
    result = op.set_health(req, char.pk, '150', key)
    assert result['status'] == 'ok'
    assert op.set_health(req, char.pk, '150', key) == result
    assert op.set_health(req, char.pk, 151, key)['status'] == 'error'
    for bad in [-1, True, 1.5, '1.5', '1e2', 10**100, 'x']:
        assert op.set_health(req, char.pk, bad, uuid.uuid4())['status'] == 'error'
    assert op.set_health(req, char.pk, 150, uuid.uuid4())['status'] == 'ok'
    char.refresh_from_db(); assert char.health_current == 150
    assert ChatMessage.objects.count() == 1
    assert ChatMessage.objects.get().metadata == {'old': 80, 'new': 150}
    assert op.set_health(req, char.pk, 1, 'not-uuid')['status'] == 'error'


def test_inventory_removal_and_master_grants_atomic(setup):
    op, char, req, action, icon = setup
    item = Item.objects.create(name='Item', description='desc', icon=icon)
    InventoryEntry.objects.create(character=char, item=item, quantity=2)
    assert op.remove_item(req, char.pk, item.pk, 0, uuid.uuid4())['status'] == 'error'
    assert op.remove_item(req, char.pk, item.pk, 3, uuid.uuid4())['status'] == 'error'
    assert op.remove_item(req, char.pk, item.pk, '2', uuid.uuid4())['status'] == 'ok'
    assert not InventoryEntry.objects.exists()
    master = browser(master=True)
    bad = op.grant_catalog(master, 'item', item.pk, [char.pk, uuid.uuid4()], uuid.uuid4())
    assert bad['status'] == 'error' and not InventoryEntry.objects.exists()
    before = ChatMessage.objects.count()
    assert op.grant_catalog(master, 'action', action.pk, [char.pk], uuid.uuid4())['status'] == 'ok'
    assert ChatMessage.objects.count() == before
    result = op.grant_catalog(master, 'item', item.pk, [char.pk, char.pk], uuid.uuid4())
    assert result['status'] == 'ok' and InventoryEntry.objects.get().quantity == 1
    assert ChatMessage.objects.last().recipients.count() == 1


def test_private_chat_unread_browser_cursors_and_rest(setup):
    from game.chat import messages_for, unread_count
    op, char, req, _, _ = setup
    other = Character.objects.create(name='Other', character_class='Fighter', is_active=True)
    other_req = browser(other); second_browser = browser(char); master = browser(master=True)
    result = op.send_chat(master, text=' hello ', character_id=char.pk, as_master=True, request_id=uuid.uuid4())
    assert result['status'] == 'ok'
    assert messages_for(req, char.pk).get().text == 'hello'
    assert not messages_for(other_req, other.pk).exists()
    assert messages_for(master).count() == 1
    assert unread_count(req, char.pk) == unread_count(second_browser, char.pk) == 1
    assert op.send_chat(req, text='response', character_id=char.pk, as_master=False, request_id=uuid.uuid4())['status'] == 'ok'
    assert unread_count(req, char.pk) == 1
    assert op.mark_chat_read(req, char.pk, 10**100)['status'] == 'ok'
    assert unread_count(req, char.pk) == 0 and unread_count(second_browser, char.pk) == 1
    op.mark_chat_read(req, char.pk, 0)
    assert unread_count(req, char.pk) == 0
    with pytest.raises(PermissionDenied): messages_for(master, char.pk)
    with pytest.raises(PermissionDenied): op.set_health(other_req, char.pk, 1, uuid.uuid4())
    for text in ['', '  ', 'x' * 10001]:
        assert op.send_chat(req, text=text, character_id=char.pk, as_master=False, request_id=uuid.uuid4())['status'] == 'error'
    char.health_current = 150; char.can_rage = False; char.save()
    slot(char, 2, 0)
    disabled = CharacterResource.objects.create(character=char, kind='rage', current=0, maximum=2)
    assert op.rest_characters(master, uuid.uuid4())['status'] == 'ok'
    char.refresh_from_db(); disabled.refresh_from_db()
    assert char.health_current == 100 and disabled.current == 0
    assert char.resources.get(kind='spell_slot').current == 1
    assert ChatMessage.objects.filter(kind='rest', recipients=char).exists()


def maximum(model, field):
    return min(v.limit_value for v in model._meta.get_field(field).validators if isinstance(v, MaxValueValidator))


def test_model_counter_limits_and_multi_recipient_rollback(setup):
    op, char, req, _, icon = setup
    cap = maximum(Character, 'health_current')
    assert op.set_health(req, char.pk, cap, uuid.uuid4())['status'] == 'ok'
    assert op.set_health(req, char.pk, cap + 1, uuid.uuid4())['status'] == 'error'
    item = Item.objects.create(name='Full inventory', description='desc', icon=icon)
    full = InventoryEntry.objects.create(character=char, item=item, quantity=maximum(InventoryEntry, 'quantity'))
    other = Character.objects.create(name='Offline eligible', character_class='Fighter', is_active=True)
    before = (ChatMessage.objects.count(), Operation.objects.count(), other.revision)
    result = op.grant_catalog(browser(master=True), 'item', item.pk, [other.pk, char.pk], uuid.uuid4())
    assert result['status'] == 'error'
    assert not InventoryEntry.objects.filter(character=other).exists()
    full.refresh_from_db(); other.refresh_from_db()
    assert full.quantity == maximum(InventoryEntry, 'quantity')
    assert before == (ChatMessage.objects.count(), Operation.objects.count(), other.revision)
    Character.objects.filter(pk=char.pk).update(revision=maximum(Character, 'revision'))
    assert op.set_health(req, char.pk, 1, uuid.uuid4())['status'] == 'error'
    char.refresh_from_db(); assert char.health_current == cap


def test_grant_event_action_membership_and_replays(setup):
    op, char, _, action, icon = setup
    master = browser(master=True)
    other = Character.objects.create(name='Second', character_class='Fighter', is_active=True)
    item = Item.objects.create(name='Potion', description='desc', icon=icon)
    event = Event.objects.create(name='Event', description='private', color='red')
    for kind, obj in [('event', event), ('item', item), ('action', action)]:
        key = uuid.uuid4()
        result = op.grant_catalog(master, kind, obj.pk, [char.pk, other.pk], key)
        assert result['status'] == 'ok'
        count = ChatMessage.objects.count()
        assert op.grant_catalog(master, kind, obj.pk, [other.pk, char.pk, other.pk], key) == result
        assert ChatMessage.objects.count() == count
        assert op.grant_catalog(master, kind, obj.pk, [other.pk], key)['status'] == 'error'
    assert InventoryEntry.objects.filter(quantity=1).count() == 2
    message = ChatMessage.objects.get(kind='action_grant')
    assert list(message.recipients.all()) == [other]
    assert CharacterAction.objects.count() == 2
    assert ChatMessage.objects.get(kind='event').recipients.count() == 2


def test_character_revision_other_character_and_expired_proposal(setup):
    from django.core import signing
    op, char, req, action, _ = setup
    slot(char, 4)
    key = uuid.uuid4(); offer = apply(setup, key)
    other = Character.objects.create(name='Other offer', character_class='Wizard', can_cast=True,
                                    spellcasting_ability='int')
    other_req = browser(other)
    CharacterAction.objects.create(character=other, action=action); slot(other, 4)
    assert op.apply_action(other_req, other.pk, action.pk, key, 4, offer['proposal'])['status'] == 'stale'
    assert op.set_health(req, char.pk, 90, uuid.uuid4())['status'] == 'ok'
    assert apply(setup, key, confirmed_level=4, proposal=offer['proposal'])['status'] == 'stale'
    fresh = apply(setup, key)
    # A genuine timestamped signature from more than ten minutes ago, without mocking validation.
    from unittest.mock import patch
    import time
    payload = signing.loads(fresh['proposal'], salt=op.PROPOSAL_SALT)
    with patch('django.core.signing.time.time', return_value=time.time() - 601):
        expired = signing.dumps(payload, salt=op.PROPOSAL_SALT, compress=True)
    assert apply(setup, key, confirmed_level=4, proposal=expired)['status'] == 'error'
    assert not ChatMessage.objects.filter(kind='action_use').exists()


def test_revoke_denies_replay_and_master_cannot_act_as_player(setup):
    from game.access import revoke_character
    from game.chat import unread_count
    op, char, req, action, _ = setup
    slot(char, 2)
    key = uuid.uuid4(); assert apply(setup, key)['status'] == 'ok'
    with pytest.raises(PermissionDenied):
        op.apply_action(browser(master=True), char.pk, action.pk, uuid.uuid4())
    revoke_character(char)
    with pytest.raises(PermissionDenied): apply(setup, key)
    with pytest.raises(PermissionDenied): unread_count(req, char.pk)
    with pytest.raises(PermissionDenied): op.mark_chat_read(req, char.pk)


def test_rest_all_enabled_resources_and_idempotency(setup):
    op, char, _, _, _ = setup
    Character.objects.filter(pk=char.pk).update(can_rage=True, can_wildshape=True, health_current=150)
    for kind, level in [('spell_slot', 7), ('rage', 0), ('wildshape', 0)]:
        CharacterResource.objects.create(character=char, kind=kind, level=level, current=0, maximum=3)
    inactive = Character.objects.create(name='Inactive', character_class='Fighter', health_current=2, health_max=10)
    offline = Character.objects.create(name='Offline', character_class='Fighter', is_active=True, health_max=20)
    master = browser(master=True); key = uuid.uuid4()
    result = op.rest_characters(master, key)
    assert result['status'] == 'ok' and result['changed_count'] == 2
    assert op.rest_characters(master, key) == result
    assert ChatMessage.objects.filter(kind='rest').count() == 2
    char.refresh_from_db(); offline.refresh_from_db(); inactive.refresh_from_db()
    assert (char.health_current, offline.health_current, inactive.health_current) == (100, 20, 2)
    assert char.resources.filter(current=3).count() == 3
    message = ChatMessage.objects.get(kind='rest', recipients=char)
    assert len(message.metadata['resources']) == 3 and message.metadata['health']['old'] == 150
    assert message.from_master and message.recipients.count() == 1
    count = ChatMessage.objects.count()
    assert op.rest_characters(master, uuid.uuid4())['changed_count'] == 0
    assert ChatMessage.objects.count() == count


def test_chat_and_item_removal_same_uuid_exactly_once(setup):
    op, char, req, _, icon = setup
    key = uuid.uuid4()
    kwargs = dict(text=' hi ', character_id=char.pk, as_master=False, request_id=key)
    result = op.send_chat(req, **kwargs)
    assert op.send_chat(req, **kwargs) == result
    assert op.send_chat(req, **{**kwargs, 'text': 'different'})['status'] == 'error'
    item = Item.objects.create(name='Consume', description='desc', icon=icon)
    InventoryEntry.objects.create(character=char, item=item)
    key = uuid.uuid4()
    result = op.remove_item(req, char.pk, item.pk, 1, key)
    assert op.remove_item(req, char.pk, item.pk, 1, key) == result
    assert ChatMessage.objects.count() == 2 and not InventoryEntry.objects.exists()
