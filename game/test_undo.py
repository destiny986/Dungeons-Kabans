"""Undo must reverse only its operation, without defeating durable request replay."""
import uuid

import pytest
from django.core.exceptions import PermissionDenied
from django.core.validators import MaxValueValidator

from game import operations as op
from game import models
from game.models import (Action, Character, CharacterAction, CharacterResource,
                         ChatMessage, Event, InventoryEntry, Item, MediaAsset, Operation)
from game.test_operations import browser

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    hero = Character.objects.create(name='Кабас', character_class='Рыцарь',
        health_current=9, health_max=10, can_cast=True, spellcasting_ability='int')
    player = browser(hero)
    master = browser(master=True)
    icon = MediaAsset.objects.create(digest='c' * 64, path='c.png', width=1, height=1)
    item = Item.objects.create(name='Зелье', description='Лечение', icon=icon)
    action = Action.objects.create(name='Искра', description='Свет', icon=icon,
                                   resource='spell_slot', slot_level=2)
    event = Event.objects.create(name='Сумерки', description='Темнеет', color='blue')
    hero.refresh_from_db()
    return hero, player, master, item, action, event


def history():
    assert hasattr(models, 'UndoEntry'), 'Нужен независимый от чата журнал отмены'
    return models.UndoEntry.objects.order_by('-pk')


def undo(master, *, entry=None, key=None):
    target = entry or history().first()
    assert target is not None
    return op.undo_last(master, target.pk, key or uuid.uuid4())


def grant(world, kind='item', ids=None, key=None, quantities=None):
    hero, _, master, item, action, event = world
    obj = {'item': item, 'action': action, 'event': event}[kind]
    result = op.grant_catalog(master, kind, obj.pk, ids or [hero.pk], key or uuid.uuid4(),
                              quantities=quantities)
    assert result['status'] == 'ok', result
    return result


def test_grant_undo_and_both_request_replays_are_durable(world):
    hero, _, master, item, _, _ = world
    key = uuid.uuid4()
    issued = grant(world, key=key, quantities={hero.pk: 3})
    assert history().count() == 1
    entry = history().first()
    undo_key = uuid.uuid4()
    result = undo(master, entry=entry, key=undo_key)
    assert result['status'] == 'ok'
    assert not InventoryEntry.objects.exists()
    assert not ChatMessage.objects.exists()
    assert history().count() == 0
    assert undo(master, entry=entry, key=undo_key) == result
    assert grant(world, key=key, quantities={hero.pk: 3}) == issued
    assert not InventoryEntry.objects.exists() and not ChatMessage.objects.exists()
    assert history().count() == 0 and Operation.objects.count() == 2
    assert op.undo_last(master, entry.pk + 1, undo_key)['status'] == 'error'


def test_ten_entries_discard_oldest_permanently(world):
    hero, _, master, item, _, _ = world
    for _ in range(11):
        grant(world)
    assert history().count() == 10 and Operation.objects.count() == 11
    for _ in range(10):
        assert undo(master)['status'] == 'ok'
    assert history().count() == 0
    assert InventoryEntry.objects.get(character=hero, item=item).quantity == 1
    assert ChatMessage.objects.count() == 1
    assert Operation.objects.count() == 21


def test_group_grant_undo_removes_all_messages_and_preserves_unrelated_chat(world):
    hero, player, master, item, _, _ = world
    other = Character.objects.create(name='Другой', character_class='Маг', is_active=True)
    InventoryEntry.objects.create(character=hero, item=item, quantity=5)
    chat = op.send_chat(player, text='Останется', character_id=hero.pk,
                        as_master=False, request_id=uuid.uuid4())
    grant(world, ids=[hero.pk, other.pk], quantities={hero.pk: 3, other.pk: 2})
    assert history().count() == 1
    assert undo(master)['status'] == 'ok'
    assert InventoryEntry.objects.get(character=hero).quantity == 5
    assert not InventoryEntry.objects.filter(character=other).exists()
    assert list(ChatMessage.objects.values_list('pk', flat=True)) == [chat['message_id']]


def test_group_conflict_rolls_back_every_recipient_and_message(world):
    hero, _, master, item, _, _ = world
    other = Character.objects.create(name='Другой', character_class='Маг', is_active=True)
    grant(world, ids=[hero.pk, other.pk])
    InventoryEntry.objects.filter(character=other).update(quantity=2)
    messages = list(ChatMessage.objects.values_list('pk', flat=True))
    assert undo(master)['status'] == 'stale'
    assert InventoryEntry.objects.get(character=hero).quantity == 1
    assert InventoryEntry.objects.get(character=other).quantity == 2
    assert list(ChatMessage.objects.values_list('pk', flat=True)) == messages
    assert history().count() == 1


def test_action_grant_skips_known_owners_and_noop_does_not_fill_history(world):
    hero, _, master, _, action, _ = world
    other = Character.objects.create(name='Другой', character_class='Маг', is_active=True)
    CharacterAction.objects.create(character=hero, action=action)
    grant(world, kind='action')
    assert history().count() == 0
    grant(world, kind='action', ids=[hero.pk, other.pk])
    assert undo(master)['status'] == 'ok'
    assert list(CharacterAction.objects.values_list('character_id', flat=True)) == [hero.pk]


def test_item_removal_restores_deleted_row_and_can_undo_earlier_grant(world):
    hero, player, master, item, _, _ = world
    grant(world, quantities={hero.pk: 3})
    assert op.remove_item(player, hero.pk, item.pk, 3, uuid.uuid4())['status'] == 'ok'
    assert not InventoryEntry.objects.exists()
    assert undo(master)['status'] == 'ok'
    assert InventoryEntry.objects.get().quantity == 3
    assert undo(master)['status'] == 'ok'
    assert not InventoryEntry.objects.exists()


@pytest.mark.parametrize('resource', ['none', 'spell_slot', 'rage', 'wildshape'])
def test_action_use_refunds_recorded_resource_after_catalog_edit(world, resource):
    hero, player, master, _, action, _ = world
    hero.can_rage = hero.can_wildshape = True
    hero.save()
    action.resource = resource
    action.slot_level = 2 if resource == 'spell_slot' else None
    action.save()
    CharacterAction.objects.create(character=hero, action=action)
    stock = None
    if resource != 'none':
        stock = CharacterResource.objects.create(character=hero, kind=resource,
            level=2 if resource == 'spell_slot' else 0, current=1, maximum=2)
    key = uuid.uuid4()
    used = op.apply_action(player, hero.pk, action.pk, key)
    assert used['status'] == 'ok'
    action.resource, action.slot_level = 'none', None
    action.save()
    assert history().count() == 1
    assert undo(master)['status'] == 'ok'
    if stock:
        stock.refresh_from_db()
        assert stock.current == 1
    assert CharacterAction.objects.filter(character=hero, action=action).exists()
    assert not ChatMessage.objects.exists()
    assert op.apply_action(player, hero.pk, action.pk, key) == used
    assert history().count() == 0


def test_higher_slot_offer_is_not_recorded_and_refunds_actual_level(world):
    hero, player, master, _, action, _ = world
    CharacterAction.objects.create(character=hero, action=action)
    low = CharacterResource.objects.create(character=hero, kind='spell_slot', level=2, current=0, maximum=1)
    high = CharacterResource.objects.create(character=hero, kind='spell_slot', level=4, current=1, maximum=1)
    key = uuid.uuid4()
    offer = op.apply_action(player, hero.pk, action.pk, key)
    assert offer['status'] == 'offer' and history().count() == 0
    assert op.apply_action(player, hero.pk, action.pk, key,
        confirmed_level=4, proposal=offer['proposal'])['status'] == 'ok'
    assert undo(master)['status'] == 'ok'
    low.refresh_from_db(); high.refresh_from_db()
    assert (low.current, high.current) == (0, 1)


def test_events_remove_receipts_and_survive_chat_clear(world):
    _, _, master, _, _, event = world
    grant(world, kind='event')
    assert history().count() == 1
    assert op.clear_chat(master, uuid.uuid4())['status'] == 'ok'
    assert history().count() == 1
    assert undo(master)['status'] == 'ok'
    assert Event.objects.filter(pk=event.pk).exists() and not ChatMessage.objects.exists()


def test_inventory_undo_survives_chat_clear(world):
    _, _, master, _, _, _ = world
    grant(world)
    op.clear_chat(master, uuid.uuid4())
    assert undo(master)['status'] == 'ok'
    assert not InventoryEntry.objects.exists()


def test_unrelated_character_does_not_break_chain(world):
    hero, _, master, _, _, _ = world
    other = Character.objects.create(name='Другой', character_class='Маг', is_active=True)
    grant(world)
    grant(world, ids=[other.pk])
    grant(world)
    for _ in range(3):
        assert undo(master)['status'] == 'ok'
    hero.refresh_from_db()
    assert hero.revision > 3 and not InventoryEntry.objects.exists()


@pytest.mark.parametrize('barrier', ['health', 'rest', 'revoke', 'edit', 'delete_item', 'delete_character'])
def test_nonundoable_change_blocks_old_operation(world, barrier):
    hero, player, master, item, _, _ = world
    grant(world)
    if barrier == 'health':
        op.set_health(player, hero.pk, 8, uuid.uuid4())
    elif barrier == 'rest':
        op.rest_characters(master, uuid.uuid4())
    elif barrier == 'revoke':
        from game.access import revoke_character
        revoke_character(hero)
    elif barrier == 'edit':
        hero.refresh_from_db(); hero.revision += 1; hero.save()
    elif barrier == 'delete_item':
        item.delete()
    else:
        hero.delete()
    before = ChatMessage.objects.count()
    assert undo(master)['status'] == 'stale'
    assert history().count() == 1 and ChatMessage.objects.count() == before


def test_undo_new_step_does_not_bridge_intermediate_health_change(world):
    hero, player, master, _, _, _ = world
    grant(world)
    op.set_health(player, hero.pk, 8, uuid.uuid4())
    grant(world)
    assert undo(master)['status'] == 'ok'
    assert undo(master)['status'] == 'stale'
    assert InventoryEntry.objects.get().quantity == 1


def test_resource_deleted_or_altered_blocks_refund(world):
    hero, player, master, _, action, _ = world
    CharacterAction.objects.create(character=hero, action=action)
    stock = CharacterResource.objects.create(character=hero, kind='spell_slot', level=2, current=1, maximum=1)
    op.apply_action(player, hero.pk, action.pk, uuid.uuid4())
    stock.delete()
    assert undo(master)['status'] == 'stale' and ChatMessage.objects.count() == 1


def test_removal_refund_overflow_does_not_write(world):
    hero, player, master, item, _, _ = world
    maximum = min(v.limit_value for v in InventoryEntry._meta.get_field('quantity').validators if isinstance(v, MaxValueValidator))
    InventoryEntry.objects.create(character=hero, item=item, quantity=maximum)
    op.remove_item(player, hero.pk, item.pk, 1, uuid.uuid4())
    InventoryEntry.objects.filter(character=hero).update(quantity=maximum)
    assert undo(master)['status'] == 'stale'
    assert InventoryEntry.objects.get().quantity == maximum


def test_exact_top_required_and_preview_is_read_only(world):
    _, player, master, _, _, _ = world
    grant(world)
    old = history().first()
    preview = op.undo_preview(master, old.pk)
    assert preview['status'] == 'confirm' and 'Зелье' in preview['message'] and 'Кабас' in preview['message']
    assert Operation.objects.count() == 1 and ChatMessage.objects.count() == 1
    grant(world)
    assert op.undo_preview(master, old.pk)['status'] == 'stale'
    assert undo(master, entry=old)['status'] == 'stale'
    with pytest.raises(PermissionDenied):
        undo(player)
    assert op.undo_last(master, 'bad', uuid.uuid4())['status'] == 'error'
    assert op.undo_last(master, history().first().pk, 'bad')['status'] == 'error'


def test_deleted_action_with_expense_blocks_refund(world):
    hero, player, master, _, action, _ = world
    CharacterAction.objects.create(character=hero, action=action)
    stock = CharacterResource.objects.create(character=hero, kind='spell_slot', level=2, current=1, maximum=1)
    op.apply_action(player, hero.pk, action.pk, uuid.uuid4())
    action.delete()
    assert undo(master)['status'] == 'stale'
    stock.refresh_from_db()
    assert stock.current == 0 and history().count() == 1


def test_failed_grant_creates_no_history(world):
    hero, _, master, item, _, _ = world
    assert op.grant_catalog(master, 'item', item.pk, [hero.pk], uuid.uuid4(),
                            quantities={hero.pk: 0})['status'] == 'error'
    assert history().count() == 0 and Operation.objects.count() == 0


def test_refund_refuses_revision_overflow_and_preserves_history(world):
    hero, _, master, _, _, _ = world
    maximum = min(v.limit_value for v in Character._meta.get_field('revision').validators if isinstance(v, MaxValueValidator))
    Character.objects.filter(pk=hero.pk).update(revision=maximum - 1)
    grant(world)
    assert undo(master)['status'] == 'stale'
    assert history().count() == 1 and InventoryEntry.objects.get().quantity == 1
