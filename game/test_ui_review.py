"""Regression checks for reviewed journal controls and browser/server boundaries."""
import uuid
from html.parser import HTMLParser

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from game.models import Action, Character, CharacterGrant, ChatMessage, Event, MediaAsset
from game.test_web import joined

pytestmark = pytest.mark.django_db


@pytest.fixture
def ui_master(client, settings):
    settings.STORAGES = {'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}
    get_user_model().objects.create_user(username='master', password='secret')
    assert client.post(reverse('game:master_login'), {'password': 'secret'}).status_code == 302
    return client


class Controls(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


@pytest.mark.parametrize('editing', [False, True])
def test_event_preview_keeps_full_description_out_of_compact_card(ui_master, editing):
    event = Event.objects.create(name='Туман', description='Скрытое полное описание', color='red')
    url = reverse('game:catalog_edit', args=['event', event.pk]) if editing else reverse('game:catalog_new', args=['event'])
    response = ui_master.get(url)
    assert response.status_code == 200
    html = response.content.decode()
    compact = html.split('data-preview-card>', 1)[1].split('<h3 class="preview-label">', 1)[0]
    full = html.split('<article class="paper preview-full">', 1)[1].split('</article>', 1)[0]
    assert not any(attrs.get('data-preview') == 'description' for _, attrs in Controls(compact).elements)
    assert any(attrs.get('data-preview') == 'name' for _, attrs in Controls(compact).elements)
    assert any(attrs.get('data-preview') == 'description' for _, attrs in Controls(full).elements)
    if editing:
        assert event.description not in compact
        assert event.description in full


def test_master_event_cards_show_only_names_and_keep_search_metadata(ui_master):
    event = Event.objects.create(name='Появление кабана', description='Секретное описание', color='blue', tags=['Лес'])
    for url in ('game:master', 'game:master_poll'):
        response = ui_master.get(reverse(url))
        html = response.content.decode()
        card = html.split('class="event-card ', 1)[1].split('</a>', 1)[0]
        attrs, body = card.split('>', 1)
        assert event.name in body
        assert event.description not in body
        assert 'Лес' not in body
        assert event.description in attrs  # Search by full description still works.
        assert 'data-dialog' in attrs


def test_master_full_event_card_is_neutral_and_keeps_all_three_controls(ui_master):
    event = Event.objects.create(name='Кабан', description='Описание события', color='blue', tags=['Лес'])
    html = ui_master.get(reverse('game:card', args=['event', event.pk])).content.decode()
    elements = Controls(html).elements
    article = next(attrs for tag, attrs in elements if tag == 'article')
    assert 'event-blue' not in article.get('class', '').split()
    assert event.description in html and 'Лес' in html
    edit = next(attrs for tag, attrs in elements if tag == 'a' and attrs.get('href') == reverse('game:catalog_edit', args=['event', event.pk]))
    send = next(attrs for tag, attrs in elements if tag == 'summary')
    delete = next(attrs for tag, attrs in elements if tag == 'button' and attrs.get('type') == 'submit')
    assert set(edit['class'].split()) == set(send['class'].split()) == set(delete['class'].split())
    for operation in ('catalog_send', 'catalog_delete'):
        assert any(tag == 'form' and attrs.get('method') == 'post' and attrs.get('action') == reverse('game:' + operation, args=['event', event.pk]) for tag, attrs in elements)


def test_character_editor_exposes_combined_filters_and_slot_level(ui_master):
    asset = MediaAsset.objects.create(digest='a'*64, path='a'*64+'.png', width=64, height=64)
    spell = Action.objects.create(name='Седьмой круг', description='Описание', icon=asset, activation='reaction', resource='spell_slot', slot_level=7)
    html = ui_master.get(reverse('game:character_new')).content.decode()
    elements = Controls(html).elements
    filters = {attrs['data-filter-key']: attrs for tag, attrs in elements if tag == 'select' and attrs.get('data-filter') == 'action-choices'}
    assert set(filters) == {'activation', 'resource'}
    options = {attrs.get('value') for tag, attrs in elements if tag == 'option'}
    assert {'action', 'bonus', 'reaction', 'none', 'rage', 'wildshape', *{f'spell_slot_{n}' for n in range(1, 8)}} <= options
    rows = [attrs for tag, attrs in elements if attrs.get('data-activation') == 'reaction']
    assert rows[0]['data-resource'] == 'spell_slot_7'
    assert '7 уровень' in html
    assert any(attrs.get('name') == f'action_{spell.pk}' for tag, attrs in elements if tag == 'input')


@pytest.mark.parametrize('invalid', ['-1', ''])
def test_invalid_health_uses_server_reason_and_keeps_confirmed_health(ui_master, invalid):
    hero = Character.objects.create(name='Следопыт', character_class='Друид', health_current=12, health_max=20)
    player = joined(hero)
    html = player.get(reverse('game:player', args=[hero.pk])).content.decode()
    health_form = next(attrs for tag, attrs in Controls(html).elements if tag == 'form' and 'data-health-form' in attrs)
    assert 'novalidate' in health_form  # Invalid blur must reach the reason-producing endpoint.
    response = player.post(reverse('game:health', args=[hero.pk]), {'health_current': invalid, 'request_id': uuid.uuid4()}, HTTP_HX_REQUEST='true')
    assert response.status_code == 422
    assert b'class="confirmation"' in response.content
    hero.refresh_from_db()
    assert hero.health_current == 12


def test_read_acknowledgement_does_not_advance_to_unrendered_message(ui_master):
    hero = Character.objects.create(name='Летописец', character_class='Бард')
    player = joined(hero)
    def send(text):
        assert ui_master.post(reverse('game:chat_send'), {'recipient_id': hero.pk, 'text': text, 'request_id': uuid.uuid4()}).status_code == 302
        return ChatMessage.objects.latest('id')
    rendered = send('Уже показано')
    assert 'Уже показано' in player.get(reverse('game:player', args=[hero.pk])).content.decode()
    unseen = send('Ещё не показано')
    assert player.post(reverse('game:chat_read', args=[hero.pk]), {'through_id': rendered.pk}).status_code == 302
    grant = CharacterGrant.objects.get(character=hero, session_key=player.session.session_key)
    assert grant.last_read_message_id == rendered.pk < unseen.pk
    assert player.get(reverse('game:player', args=[hero.pk])).context['unread'] == 1
