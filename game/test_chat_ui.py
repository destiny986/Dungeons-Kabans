"""Shared chat presentation and master-only history removal."""
import re
import uuid

import pytest
from django.core.exceptions import PermissionDenied
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse
from django.utils.html import escape

from game.chat import create_message
from game.constants import ACTIVATION_SYMBOLS, MESSAGE_KINDS
from game.models import (Action, Character, CharacterAction, CharacterGrant,
                         CharacterResource, ChatMessage, Event, InventoryEntry,
                         Item, MediaAsset, MessageRecipient, Operation)
from game.test_web import joined, master, web_settings

pytestmark = pytest.mark.django_db


def chat_html(messages, character=None):
    return render_to_string('game/chat_messages.html', {'messages': messages, 'character': character})


def test_chat_headers_and_escaped_recipients():
    hero = Character.objects.create(name='Кабас <маг>')
    other = Character.objects.create(name='Друг')
    outgoing = create_message(recipients=[hero.pk, other.pk], from_master=True, kind='text', text='Привет')
    incoming = create_message(recipients=[hero.pk], sender_character=hero, kind='text', text='Ответ')
    names = ', '.join(escape(person.name) for person in outgoing.recipients.all())
    assert f'<strong>Мастер ➤ {names}</strong>' in chat_html([outgoing])
    assert '<strong>Кабас &lt;маг&gt;</strong>' in chat_html([incoming])
    assert 'chat-recipients' not in chat_html([outgoing, incoming])
    player_html = chat_html([outgoing, incoming], hero)
    assert '<strong>Мастер</strong>' in player_html and '➤' not in player_html


@pytest.mark.parametrize('activation,symbol', ACTIVATION_SYMBOLS.items())
def test_chat_activation_uses_constants(activation, symbol):
    action = Action(pk=uuid.uuid4(), name='Магия', activation=activation)
    message = ChatMessage(pk=1, action=action)
    html = chat_html([message])
    text = re.search(r'data-dialog>(.*?)</a>', html, re.S).group(1).strip()
    assert text == (symbol + ' Магия').strip()


@pytest.mark.parametrize('as_player', [False, True])
@pytest.mark.parametrize('metadata,expected', [
    ({'resource': 'spell_slot', 'slot_level': 4, 'used_level': 2}, 'Потрачена ячейка заклинаний 4 уровня'),
    ({'resource': 'spell_slot', 'used_level': 7}, 'Потрачена ячейка заклинаний 7 уровня'),
    ({'resource': 'rage', 'slot_level': 0, 'used_level': 0}, 'Потрачена ячейка ярости'),
    ({'resource': 'wildshape', 'slot_level': 0, 'used_level': 0}, 'Потрачена ячейка дикого облика'),
    ({'slot_level': 3}, 'Потрачена ячейка заклинаний 3 уровня'),
    ({'used_level': 5}, 'Потрачена ячейка заклинаний 5 уровня'),
    ({}, ''), ({'resource': 'none'}, ''), ({'slot_level': 0, 'used_level': 0}, ''),
    ({'resource': 'unknown', 'slot_level': 2}, ''),
    ({'resource': 'spell_slot'}, ''),
])
def test_chat_resource_notes_use_recorded_expense(metadata, expected, as_player):
    hero = Character.objects.create(name='Кабас')
    asset = MediaAsset.objects.create(digest='c' * 64, path='c.png', width=1, height=1)
    action = Action.objects.create(name='Магия', icon=asset, resource='none')
    message = create_message(recipients=[hero.pk], sender_character=hero,
                             kind='action_use', action=action, metadata=metadata)
    # The live action is deliberately different: historical expense belongs
    # to the message and must never be inferred from the current catalog card.
    action.resource = 'wildshape'
    action.save()
    message.refresh_from_db()
    html = chat_html([message], hero if as_player else None)
    notes = re.findall(r'<small class="resource-note">(.*?)</small>', html)
    assert notes == ([expected] if expected else [])


def test_chat_clear_is_in_separate_footer_form(master):
    html = master.get(reverse('game:master')).content.decode()
    heading = re.search(r'<header class="panel-heading chat-heading">(.*?)</header>', html, re.S).group(1)
    assert 'Мастер ↔ игроки' in heading and '<form' not in heading
    composer = html.split('<div class="chat-composer">', 1)[1]
    send_form, after_send = composer.split('</form>', 1)
    assert 'data-chat-form' in send_form and 'chat_clear' not in send_form
    assert reverse('game:chat_clear') not in send_form
    footer = after_send.split('<div class="chat-footer">', 1)[1].split('</div>', 1)[0]
    assert 'Enter — новая строка · Ctrl + Enter — отправить' in footer
    assert reverse('game:chat_clear') in footer
    assert 'class="button secondary small"' in footer and '⦸ Очистить чат' in footer
    hero = Character.objects.create(name='Кабас')
    player_html = joined(hero).get(reverse('game:player', args=[hero.pk])).content.decode()
    assert reverse('game:chat_clear') not in player_html


def test_event_chat_uses_shared_compact_template(master):
    hero = Character.objects.create(name='Кабас')
    player = joined(hero)
    event = Event.objects.create(name='Дождь', description='Полное описание', color='blue')
    create_message(recipients=[hero.pk], from_master=True, kind='event', event=event)
    for client, url, character in [(master, reverse('game:master'), None),
                                    (player, reverse('game:player', args=[hero.pk]), hero)]:
        response = client.get(url)
        assert 'game/_event_compact.html' in [t.name for t in response.templates]
        html = chat_html(ChatMessage.objects.all(), character)
        assert 'event-card event-compact event-blue' in html
        assert '<p>Полное описание' not in html
        card = reverse('game:card', args=['event', event.pk])
        if character:
            card += f'?character={hero.pk}'
        assert f'href="{card}"' in html and f'hx-get="{card}"' in html
        assert client.get(card).status_code == 200


def test_clear_chat_confirmation_security_and_idempotency(master):
    hero = Character.objects.create(name='Кабас', health_current=7, health_max=10)
    player = joined(hero)
    asset = MediaAsset.objects.create(digest='b' * 64, path='b.png', width=1, height=1)
    item = Item.objects.create(name='Меч', icon=asset)
    action = Action.objects.create(name='Заклинание', icon=asset)
    event = Event.objects.create(name='Событие', color='red')
    InventoryEntry.objects.create(character=hero, item=item, quantity=3)
    CharacterAction.objects.create(character=hero, action=action)
    resource = CharacterResource.objects.create(character=hero, kind='spell_slot', level=1, current=2, maximum=3)
    grant_url = reverse('game:catalog_send', args=['item', item.pk])
    grant_key = uuid.uuid4()
    assert master.post(grant_url, {'recipients': hero.pk, 'request_id': grant_key}).status_code == 302
    for kind, _ in MESSAGE_KINDS:
        create_message(recipients=[hero.pk], kind=kind, from_master=True, event=event)
    CharacterGrant.objects.update(last_read_message_id=ChatMessage.objects.latest('id').pk)
    url = reverse('game:chat_clear')
    key = uuid.uuid4()
    assert master.get(url).status_code == 405
    assert player.post(url, {'confirmed': '1', 'request_id': key}).status_code == 302
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.cookies = master.cookies
    assert csrf_client.post(url, {'confirmed': '1', 'request_id': key}).status_code == 403
    assert ChatMessage.objects.count() == len(MESSAGE_KINDS) + 1
    confirmation = master.post(url, {'request_id': key})
    assert confirmation.status_code == 200
    assert 'Важное решение' in confirmation.content.decode()
    assert str(key) in confirmation.content.decode()
    assert ChatMessage.objects.count() == len(MESSAGE_KINDS) + 1
    result = master.post(url, {'confirmed': '1', 'request_id': key}, HTTP_HX_REQUEST='true')
    assert result.status_code == 200 and 'refresh' in result['HX-Trigger']
    assert not ChatMessage.objects.exists() and not MessageRecipient.objects.exists()
    assert not CharacterGrant.objects.exclude(last_read_message_id=0).exists()
    assert InventoryEntry.objects.get().quantity == 4
    assert CharacterAction.objects.get().action_id == action.pk
    resource.refresh_from_db(); hero.refresh_from_db()
    assert resource.current == 2 and hero.health_current == 7
    assert Event.objects.filter(pk=event.pk).exists()
    assert Operation.objects.count() == 2
    assert master.post(grant_url, {'recipients': hero.pk, 'request_id': grant_key}).status_code == 302
    assert InventoryEntry.objects.get().quantity == 4
    assert not ChatMessage.objects.exists()
    for client, poll in [(master, reverse('game:master_poll')),
                         (player, reverse('game:player_poll', args=[hero.pk]))]:
        html = client.get(poll).content.decode()
        assert 'data-message-id=' not in html and 'empty-state' in html
    assert player.get(reverse('game:card', args=['event', event.pk]), {'character': hero.pk}).status_code == 404
    assert player.get(reverse('game:card', args=['action', action.pk]), {'character': hero.pk}).status_code == 200
    new = create_message(recipients=[hero.pk], kind='text', from_master=True, text='Новое')
    assert master.post(url, {'confirmed': '1', 'request_id': key}).status_code == 302
    assert ChatMessage.objects.filter(pk=new.pk).exists()
    assert player.get(reverse('game:player', args=[hero.pk])).context['unread'] == 1
    request = player.get(reverse('game:player', args=[hero.pk])).wsgi_request
    from game.operations import clear_chat
    with pytest.raises(PermissionDenied):
        clear_chat(request, uuid.uuid4())


def test_clear_empty_chat_and_invalid_request(master):
    url = reverse('game:chat_clear')
    assert master.post(url, {'confirmed': '1', 'request_id': 'broken'}).status_code == 422
    assert not Operation.objects.exists()
    assert master.post(url, {'confirmed': '1', 'request_id': uuid.uuid4()}).status_code == 302
    assert not ChatMessage.objects.exists()
