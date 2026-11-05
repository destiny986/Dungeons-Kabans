import uuid
import re
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.urls import reverse

from game import operations
from game.access import issue_invitation, redeem_invitation
from game.catalog import delete_character
from game.models import (Action, Character, CharacterAction, CharacterResource,
                         ChatMessage, InventoryEntry, Item, MediaAsset, Operation, UndoEntry)
from game.test_operations import browser
from game.test_web import master, web_settings  # noqa: F401 -- shared pytest fixtures

pytestmark = pytest.mark.django_db


def copy(request, hero, key=None):
    return operations.copy_character(request, hero.pk, key or uuid.uuid4())


def test_copy_full_sheet_and_independent_relations():
    request = browser(master=True)
    asset = MediaAsset.objects.create(digest='c' * 64, path='c.png', width=512, height=512)
    hero = Character.objects.create(name='Кабас', character_class='Воин', portrait=asset,
        backstory='История', traits='Черты', health_current=4, health_max=20,
        speed=30, armor_class=15, proficiency_bonus=2, can_cast=True,
        spellcasting_ability='int', spell_save_dc=14, spell_attack_bonus=6,
        can_rage=True, can_wildshape=True, revision=7)
    hero.abilities['str'] = 3
    hero.saves['dex'] = -2
    hero.skills['stealth'] = 4
    hero.weapons[0]['name'] = 'Меч'
    hero.save()
    for level in range(1, 8):
        CharacterResource.objects.create(character=hero, kind='spell_slot', level=level,
                                         current=1, maximum=3)
    for kind in ['rage', 'wildshape']:
        CharacterResource.objects.create(character=hero, kind=kind, current=0, maximum=2)
    item = Item.objects.create(name='Меч', description='Предмет', icon=asset)
    action = Action.objects.create(name='Удар', description='Действие', icon=asset)
    InventoryEntry.objects.create(character=hero, item=item, quantity=5)
    CharacterAction.objects.create(character=hero, action=action)
    player = browser()
    token, _ = issue_invitation(hero)
    redeem_invitation(player, token)
    message = ChatMessage.objects.create(kind='text', text='Секрет')
    message.recipients.add(hero)
    hero.refresh_from_db()
    result = copy(request, hero)
    assert result['status'] == 'ok'
    clone = Character.objects.get(pk=result['character_id'])
    assert clone.name == 'Кабас копия (1)' and clone.pk != hero.pk
    assert not clone.is_active and clone.revision == clone.invitation_generation == 1
    excluded = {'id', 'name', 'normalized_name', 'revision', 'updated_at',
                'is_active', 'invitation_generation'}
    for field in Character._meta.concrete_fields:
        if field.name not in excluded:
            assert getattr(clone, field.attname) == getattr(hero, field.attname), field.name
    fields = ('kind', 'level', 'current', 'maximum')
    assert sorted(clone.resources.values_list(*fields)) == sorted(hero.resources.values_list(*fields))
    assert clone.inventory.get().quantity == 5
    assert clone.action_grants.get().action_id == action.pk
    assert not clone.invitations.exists() and not clone.browser_grants.exists()
    assert not clone.received_messages.exists() and not clone.sent_messages.exists()
    assert ChatMessage.objects.count() == 1 and not UndoEntry.objects.exists()
    clone.abilities['str'] = 9
    clone.save()
    clone.resources.update(current=0)
    clone.inventory.update(quantity=1)
    clone.action_grants.all().delete()
    hero.refresh_from_db()
    assert hero.abilities['str'] == 3 and hero.inventory.get().quantity == 5
    assert hero.resources.filter(current=1).count() == 7 and hero.action_grants.count() == 1
    delete_character(hero.pk)
    clone.refresh_from_db()
    assert clone.portrait_id == asset.pk and MediaAsset.objects.filter(pk=asset.pk).exists()


@pytest.mark.parametrize('name,occupied,expected', [
    ('Кабас', ['КАБАС КОПИЯ (1)', 'Кабас копия (3)'], 'Кабас копия (2)'),
    ('Кабас копия (1)', [], 'Кабас копия (2)'),
    ('Кабас копия (3) копия (2)', [], 'Кабас копия (1)'),
    ('Я' * 30, ['Я' * 20 + ' копия (1)'], 'Я' * 20 + ' копия (2)'),
    ('Е\u0308ж', ['ёж копия (1)'], 'Ёж копия (2)'),
])
def test_copy_smallest_normalized_name(name, occupied, expected):
    hero = Character.objects.create(name=name)
    for value in occupied:
        Character.objects.create(name=value)
    result = copy(browser(master=True), hero)
    clone = Character.objects.get(pk=result['character_id'])
    assert clone.name == expected and len(clone.name) <= 30


def test_copy_replay_conflict_deleted_source_and_permissions():
    request = browser(master=True)
    hero = Character.objects.create(name='Кабас')
    other = Character.objects.create(name='Другой')
    key = uuid.uuid4()
    result = copy(request, hero, key)
    assert copy(request, hero, key) == result
    assert copy(request, other, key)['status'] == 'error'
    with pytest.raises(PermissionDenied):
        copy(browser(), hero)
    Character.objects.filter(pk=hero.pk).delete()
    assert copy(request, hero, key) == result
    assert copy(request, hero)['status'] == 'stale'
    assert Operation.objects.count() == 1


def test_copy_rolls_back_partial_creation():
    request = browser(master=True)
    hero = Character.objects.create(name='Кабас')
    CharacterResource.objects.create(character=hero, kind='rage', current=1, maximum=2)
    # Failure after character creation must roll back the sheet and operation together.
    with patch.object(operations, '_complete', side_effect=RuntimeError('failure')):
        with pytest.raises(RuntimeError):
            copy(request, hero)
    assert Character.objects.count() == 1 and CharacterResource.objects.count() == 1
    assert not Operation.objects.exists()


def test_copy_portrait_file_survives_source_deletion(settings, tmp_path, django_capture_on_commit_callbacks):
    from io import BytesIO
    from PIL import Image
    from game.media import asset_path, store_png

    settings.MEDIA_ROOT = tmp_path / 'media'
    image = BytesIO()
    Image.new('RGBA', (8, 8), '#abcdef').save(image, format='PNG')
    image.seek(0)
    asset = store_png(image, size=512)
    path = asset_path(asset)
    hero = Character.objects.create(name='Портрет', portrait=asset)
    result = copy(browser(master=True), hero)
    with django_capture_on_commit_callbacks(execute=True):
        delete_character(hero.pk)
    assert path.is_file() and MediaAsset.objects.filter(pk=asset.pk).exists()
    with django_capture_on_commit_callbacks(execute=True):
        delete_character(result['character_id'])
    assert not path.exists() and not MediaAsset.objects.filter(pk=asset.pk).exists()


def test_copy_http_and_list_keys(master):
    hero = Character.objects.create(name='Кабас')
    other = Character.objects.create(name='Другой')
    url = reverse('game:character_copy', args=[hero.pk])
    page = master.get(reverse('game:characters'))
    forms = re.findall(r'<form[^>]*data-copy-character[^>]*>(.*?)</form>', page.content.decode(), re.S)
    assert len(forms) == 2
    keys = [re.search(r'name="request_id" value="([^"]+)"', form)[1] for form in forms]
    assert len(set(keys)) == 2
    key = uuid.uuid4()
    assert master.get(url).status_code == 405
    assert Client().post(url, {'request_id': key}).status_code == 302
    strict = Client(enforce_csrf_checks=True)
    strict.cookies = master.cookies
    assert strict.post(url, {'request_id': key}).status_code == 403
    for _ in range(2):
        response = master.post(url, {'request_id': key})
        assert response.status_code == 302 and response.url == reverse('game:characters')
    response = master.post(url, {'request_id': key}, HTTP_HX_REQUEST='true')
    assert response.status_code == 302 and response.url == reverse('game:characters')
    assert Character.objects.count() == 3
    response = master.post(reverse('game:character_copy', args=[other.pk]), {'request_id': key})
    assert response.status_code == 422
    retry_url = reverse('game:character_copy', args=[other.pk])
    retry = re.search(r'<form[^>]*action="' + re.escape(retry_url) + r'"[^>]*>(.*?)</form>',
                      response.content.decode(), re.S)[1]
    assert f'name="request_id" value="{key}" data-operation-key="true"' in retry
    assert master.post(url, {'request_id': 'invalid'}).status_code == 422
    assert master.post(reverse('game:character_copy', args=[uuid.uuid4()]),
                       {'request_id': uuid.uuid4()}).status_code == 409


def test_revoke_from_list_returns_to_list_and_revokes_all_access(master):
    from game.test_web import joined
    hero = Character.objects.create(name='Кабас')
    player = joined(hero)
    url = reverse('game:revoke', args=[hero.pk])
    response = master.post(url, {'return_to': 'characters'})
    assert response.status_code == 302 and response.url == reverse('game:characters')
    hero.refresh_from_db()
    assert not hero.is_active
    assert not hero.invitations.filter(revoked=False).exists()
    assert not hero.browser_grants.filter(revoked=False).exists()
    assert player.get(reverse('game:player', args=[hero.pk])).status_code == 302
    assert master.post(url).url == reverse('game:index')
    assert 'closeDialog' in master.post(url, HTTP_HX_REQUEST='true')['HX-Trigger']
