"""Real HTTP integration: confirmation targets, capabilities, polling and data boundaries."""
import io
import json
import uuid
import zipfile

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from game.archives import create_import, commit_import, cancel_import
from game.models import Character, ChatMessage, InventoryEntry, Operation
from game.test_archives import archive
from game.test_undo import world, history, grant

pytestmark = pytest.mark.django_db


@pytest.fixture
def clients(world, settings):
    settings.STORAGES = {'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}
    master, player = Client(), Client()
    master.cookies[settings.SESSION_COOKIE_NAME] = world[2].session.session_key
    player.cookies[settings.SESSION_COOKIE_NAME] = world[1].session.session_key
    return master, player


def posted(entry=None, key=None, confirmed=False):
    result = {'entry_id': entry or history().first().pk, 'request_id': key or uuid.uuid4()}
    if confirmed:
        result['confirmed'] = '1'
    return result


@pytest.mark.parametrize('htmx', [False, True])
def test_preview_is_escaped_read_only_and_same_request_can_be_confirmed(world, clients, htmx):
    master, _ = clients
    hero = world[0]
    hero.name = '<b>Кабас</b>'; hero.save()
    grant(world, quantities={hero.pk: 3})
    data = posted()
    headers = {'HTTP_HX_REQUEST': 'true'} if htmx else {}
    response = master.post(reverse('game:undo_last'), data, **headers)
    assert response.status_code == 200
    body = response.content.decode()
    assert 'Важное решение' in body and 'Зелье' in body and '3 шт.' in body
    assert '&lt;b&gt;Кабас&lt;/b&gt;' in body and '<b>Кабас</b>' not in body
    assert ('<!doctype' not in body.lower()) == htmx
    assert str(data['request_id']) in body and 'name="confirmed" value="1"' in body
    assert InventoryEntry.objects.get().quantity == 3 and Operation.objects.count() == 1
    data['confirmed'] = '1'
    response = master.post(reverse('game:undo_last'), data, **headers)
    assert response.status_code == (200 if htmx else 302)
    if htmx:
        assert json.loads(response['HX-Trigger']) == {'refresh': True, 'closeDialog': True}
    assert not InventoryEntry.objects.exists() and not ChatMessage.objects.exists()
    assert master.post(reverse('game:undo_last'), data, **headers).status_code == response.status_code
    assert Operation.objects.count() == 2


def test_confirmation_does_not_cancel_a_newer_operation_from_another_tab(world, clients):
    master, _ = clients
    grant(world)
    old = posted()
    assert master.post(reverse('game:undo_last'), old, HTTP_HX_REQUEST='true').status_code == 200
    grant(world)
    old['confirmed'] = '1'
    response = master.post(reverse('game:undo_last'), old, HTTP_HX_REQUEST='true')
    assert response.status_code == 409
    assert response.context['request_id'] == old['request_id']
    assert InventoryEntry.objects.get().quantity == 2 and history().count() == 2


def test_undo_access_methods_csrf_and_invalid_input(world, clients):
    master, player = clients
    grant(world)
    url = reverse('game:undo_last')
    assert master.get(url).status_code == 405
    assert player.post(url, posted(confirmed=True)).status_code == 302
    assert Client().post(url, posted(confirmed=True)).status_code == 302
    assert Client(enforce_csrf_checks=True).post(url, posted(confirmed=True)).status_code == 403
    assert master.post(url, {'entry_id': 'bad'}).status_code == 422
    assert master.post(url, {'entry_id': history().first().pk, 'request_id': 'bad'}).status_code == 422
    assert InventoryEntry.objects.get().quantity == 1


def test_panel_navigation_card_stats_and_polled_undo_state(world, clients):
    master, _ = clients
    hero = world[0]
    hero.abilities['str'], hero.saves['str'] = 8, -3
    hero.save()
    response = master.get(reverse('game:master'))
    body = response.content.decode()
    assert 'aria-label="Управление персонажами"' in body
    assert 'Отменить последнее действие' in body
    assert '⋮' not in body and 'revoke-' not in body
    assert response.context['undo_entry'] is None
    card = master.get(reverse('game:card', args=['character', hero.pk]))
    assert len(card.context['ability_rows']) == 6
    body = card.content.decode()
    assert 'Характеристики и спасброски' in body and '+8' in body and '-3' in body
    assert 'Отозвать доступ' in body and 'Редактировать' in body
    grant(world)
    poll = master.get(reverse('game:master_poll'))
    assert poll.context['undo_entry'].pk == history().first().pk
    assert 'hx-swap-oob="innerHTML"' in poll.content.decode()
    assert master.post(reverse('game:undo_last'), posted(confirmed=True)).status_code == 302
    assert master.get(reverse('game:master_poll')).context['undo_entry'] is None


def test_chat_clear_keeps_undo_and_refreshed_player_state(world, clients):
    master, player = clients
    grant(world)
    assert master.post(reverse('game:chat_clear'), {'confirmed': '1', 'request_id': uuid.uuid4()}).status_code == 302
    assert history().count() == 1
    assert master.post(reverse('game:undo_last'), posted(confirmed=True)).status_code == 302
    response = player.get(reverse('game:player_poll', args=[world[0].pk]))
    assert response.status_code == 200 and not response.context['inventory']


def test_wipe_clears_history_only_after_valid_confirmation_request(world, clients):
    master, _ = clients
    grant(world)
    assert master.post(reverse('game:wipe'), {}).status_code == 422
    assert history().count() == 1
    assert master.post(reverse('game:wipe'), {'categories': ['events']}).status_code == 302
    assert history().count() == 0 and Operation.objects.count() == 1
    assert InventoryEntry.objects.get().quantity == 1


def test_import_resets_history_but_cancel_error_and_completed_replay_do_not(world, settings, tmp_path):
    from PIL import Image
    settings.IMPORT_ROOT, settings.MEDIA_ROOT = tmp_path / 'imports', tmp_path / 'media'
    settings.MEDIA_ROOT.mkdir()
    Image.new('RGBA', (1, 1), 'red').save(settings.MEDIA_ROOT / 'c.png')
    grant(world)
    session = 'a' * 32
    job = create_import(archive(events=[{'id': 'a', 'name': 'Туман', 'description': 'Мгла',
                                        'tags': [], 'color': 'blue'}]), session)
    assert history().count() == 1
    with pytest.raises(ValidationError):
        commit_import(job.pk, session, {'bad': 'bad'})
    assert history().count() == 1
    cancelled = create_import(archive(), session)
    cancel_import(cancelled.pk, session)
    assert history().count() == 1
    job = create_import(archive(), session)
    commit_import(job.pk, session, {})
    assert history().count() == 0 and Operation.objects.count() == 1
    grant(world)
    commit_import(job.pk, session, {})
    assert history().count() == 1


def test_export_does_not_include_undo_data(world, settings, tmp_path):
    from game.archives import export_archive
    from PIL import Image
    settings.MEDIA_ROOT = tmp_path
    Image.new('RGBA', (1, 1), 'red').save(tmp_path / 'c.png')
    grant(world)
    with zipfile.ZipFile(io.BytesIO(export_archive())) as exported:
        assert set(exported.namelist()) == {'manifest.json', 'events.json', 'items.json',
                                            'actions.json', 'characters.json', 'c' * 64 + '.png'}
    assert history().count() == 1
