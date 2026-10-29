"""Regression checks for reviewed journal controls and browser/server boundaries."""
import uuid
from html.parser import HTMLParser

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from game.models import Action, Character, CharacterGrant, ChatMessage, MediaAsset
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
