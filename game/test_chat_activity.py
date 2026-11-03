"""Master background activity is private, filtered and read-only."""
import pytest
from django.test import Client
from django.urls import reverse
from game.constants import MESSAGE_KINDS
from game.models import Character, ChatMessage
from game.test_web import joined, master, web_settings

pytestmark = pytest.mark.django_db


def test_activity_filters_and_cursor(master):
    hero = Character.objects.create(name='Игрок')
    rows = []
    for kind, _ in MESSAGE_KINDS:
        rows.append(ChatMessage.objects.create(kind=kind, sender_character=hero))
        ChatMessage.objects.create(kind=kind, from_master=True)
    url = reverse('game:chat_activity')
    expected = [row for row in rows if row.kind in ('text', 'action_use', 'item_remove')]
    for after in (0, expected[0].pk, expected[-1].pk, 9223372036854775807):
        remaining = [row.pk for row in expected if row.pk > after]
        response = master.get(url, {'after': str(after)})
        assert response.status_code == 200
        assert response.json() == {'count': len(remaining), 'max_id': str(max(remaining, default=0))}
        assert 'no-store' in response['Cache-Control']
    ChatMessage.objects.all().delete()
    assert master.get(url, {'after': '0'}).json() == {'count': 0, 'max_id': '0'}


@pytest.mark.parametrize('after', [None, '', '-1', '1.5', 'word', '+1', ' 1', '١', '9' * 100])
def test_invalid_cursor(master, after):
    response = master.get(reverse('game:chat_activity'), {} if after is None else {'after': after})
    assert response.status_code == 400
    assert response.json()['error'] == 'Некорректный идентификатор сообщения.'
    assert 'no-store' in response['Cache-Control']


def test_activity_permissions_and_method(master):
    hero = Character.objects.create(name='Игрок')
    url = reverse('game:chat_activity')
    for client in (Client(), joined(hero)):
        assert client.get(url, {'after': '0'}).status_code == 302
        response = client.get(url, {'after': '0'}, HTTP_HX_REQUEST='true')
        assert response['HX-Redirect'] == reverse('game:index')
        assert not response.content
    assert master.post(url, {'after': '0'}).status_code == 405


def test_activity_markup(master):
    row = ChatMessage.objects.create(kind='action_use')
    html = master.get(reverse('game:master')).content.decode()
    assert f'data-message-id="{row.pk}" data-message-kind="action_use"' in html
    assert f'data-activity-url="{reverse("game:chat_activity")}"' in html
    assert 'game/master_activity.js' in html
    assert "refresh[document.visibilityState === 'visible']" in html
