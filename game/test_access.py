import hashlib
import io
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.http import HttpResponse
from django.test import Client
from django.urls import include, path, reverse

from game.models import Character, CharacterGrant, CharacterInvitation, MediaAsset, Item, InventoryEntry, Action, CharacterAction, ChatMessage

pytestmark = pytest.mark.django_db


def master_page(request):
    from game.access import master_required
    return master_required(lambda request: HttpResponse('master'))(request)


def player_page(request, pk):
    from game.access import character_required
    return character_required(lambda request, pk: HttpResponse('player'))(request, pk=pk)


def patterns():
    from game.access_urls import urlpatterns as access_patterns
    return access_patterns + [path('master/', master_page, name='master'), path('player/<uuid:pk>/', player_page, name='player')]


# Lazy import gives the first test an explicit feature assertion before URL loading.
urlpatterns = []


@pytest.fixture(autouse=True)
def access_setup(settings):
    import importlib.util
    assert importlib.util.find_spec('game.access') is not None, 'Browser access service is required'
    global urlpatterns
    urlpatterns = [path('', include((patterns(), 'game')))]
    settings.ROOT_URLCONF = __name__
    settings.STORAGES = {'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}


@pytest.fixture
def master():
    return get_user_model().objects.create_user(username='master', password='test-master-secret')


def hero(name='Hero', **kwargs):
    return Character.objects.create(name=name, character_class='Wizard', **kwargs)


def join(client, character):
    from game.access import issue_invitation
    token, invitation = issue_invitation(character)
    response = client.get(reverse('game:join', args=[token]))
    assert response.status_code == 302
    assert response.url == reverse('game:player', args=[character.pk])
    return token, invitation


def test_login_and_multiple_characters_are_independent(client, master):
    a, b = hero(), hero('Second')
    token, invitation = join(client, a)
    grant_id = CharacterGrant.objects.get(character=a).pk
    assert client.post(reverse('game:master_login'), {'password': 'test-master-secret'}).status_code == 302
    join(client, b)
    client.get(reverse('game:join', args=[token]))
    assert CharacterGrant.objects.filter(session_key=client.session.session_key).count() == 2
    assert CharacterGrant.objects.get(character=a).pk == grant_id
    assert client.get(reverse('game:master')).status_code == 200
    assert client.get(reverse('game:player', args=[a.pk])).status_code == 200
    response = client.get(reverse('game:index'))
    assert len(response.context['roles']) == 3
    other = Client()
    assert other.get(reverse('game:join', args=[token])).status_code == 302
    assert CharacterGrant.objects.filter(character=a).count() == 2
    assert other.get(reverse('game:master')).status_code == 302


def test_revoke_invalidates_all_browsers_and_old_links(client, master):
    from game.access import revoke_character
    a, b = hero(), hero('Second')
    token, _ = join(client, a)
    join(client, b)
    other = Client()
    other.get(reverse('game:join', args=[token]))
    revoke_character(a)
    a.refresh_from_db()
    assert not a.is_active and a.invitation_generation == 2
    assert not CharacterGrant.objects.filter(character=a, revoked=False).exists()
    for browser in [client, other]:
        assert browser.get(reverse('game:player', args=[a.pk])).status_code == 302
        assert browser.get(reverse('game:join', args=[token])).status_code == 404
    assert client.get(reverse('game:player', args=[b.pk])).status_code == 200
    join(client, a)
    assert client.get(reverse('game:join', args=[token])).status_code == 404


def test_password_change_invalidates_only_master_and_denials_redirect_htmx(client, master):
    a = hero()
    join(client, a)
    client.post(reverse('game:master_login'), {'password': 'test-master-secret'})
    master.set_password('replacement-secret')
    master.save()
    assert client.get(reverse('game:master')).status_code == 302
    assert client.get(reverse('game:player', args=[a.pk])).status_code == 200
    response = client.get(reverse('game:player', args=[uuid.uuid4()]), HTTP_HX_REQUEST='true')
    assert response.status_code == 200 and response['HX-Redirect'] == reverse('game:index')
    client.post(reverse('game:master_login'), {'password': 'replacement-secret'})
    assert client.get(reverse('game:player', args=[hero('ungranted').pk])).status_code == 302


def test_bootstrap_is_stable_and_rejects_empty(settings):
    settings.MASTER_PASSWORD = 'bootstrap-secret'
    call_command('bootstrap_master', stdout=io.StringIO())
    user = get_user_model().objects.get(username='master')
    encoded = user.password
    call_command('bootstrap_master', stdout=io.StringIO())
    user.refresh_from_db()
    assert user.password == encoded and user.check_password('bootstrap-secret')
    settings.MASTER_PASSWORD = 'changed-secret'
    call_command('bootstrap_master', stdout=io.StringIO())
    user.refresh_from_db()
    assert user.password != encoded and user.check_password('changed-secret')
    settings.MASTER_PASSWORD = ''
    with pytest.raises(CommandError):
        call_command('bootstrap_master', stdout=io.StringIO())


def test_invitation_is_reusable_digest_only_and_public_port(client, master, settings):
    from game.access import issue_invitation
    a = hero()
    token, invitation = issue_invitation(a)
    assert invitation.token_digest == hashlib.sha256(token.encode()).hexdigest()
    assert issue_invitation(a)[0] == token
    assert client.post(reverse('game:invite', args=[a.pk])).status_code == 302
    assert client.post(reverse('game:master_login'), {'password': 'wrong'}).status_code == 400
    client.post(reverse('game:master_login'), {'password': 'test-master-secret'})
    settings.PUBLIC_BASE_URL = 'https://example.org:9443'
    assert client.get(reverse('game:invite', args=[a.pk])).status_code == 405
    response = client.post(reverse('game:invite', args=[a.pk]))
    assert response.status_code == 200
    assert response.context['link'] == 'https://example.org:9443' + reverse('game:join', args=[token])
    assert response.context['qr_data_uri'].startswith('data:image/png;base64,')
    assert 'no-store' in response['Cache-Control'] and response['Referrer-Policy'] == 'no-referrer'
    for bad in ['malformed', token + 'x']:
        assert client.get(reverse('game:join', args=[bad])).status_code == 404
    assert client.get(reverse('game:revoke', args=[a.pk])).status_code == 405


def asset(root, value):
    digest = hashlib.sha256(value.encode()).hexdigest()
    (root / (digest + '.png')).write_bytes(b'png')
    return MediaAsset.objects.create(digest=digest, path=digest + '.png', width=1, height=1)


def test_protected_media_permission_and_delivery(client, master, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.DEBUG = True
    own, foreign, unused = [asset(tmp_path, v) for v in ['own', 'foreign', 'unused']]
    a = hero(portrait=own)
    hero('Other', portrait=foreign)
    url = lambda media: reverse('game:media', args=[media.digest])
    assert client.get(url(own)).status_code == 404
    join(client, a)
    response = client.get(url(own))
    assert response.status_code == 200 and response['Content-Type'] == 'image/png'
    response.close()
    assert client.get(url(foreign)).status_code == 404
    assert client.get(url(unused)).status_code == 404
    client.post(reverse('game:master_login'), {'password': 'test-master-secret'})
    settings.DEBUG = False
    response = client.get(url(unused))
    assert response.status_code == 200 and response['X-Accel-Redirect'] == '/_protected_media/' + unused.digest + '.png'
    unused.path = '../outside.png'
    unused.save()
    assert client.get(url(unused)).status_code == 404


def test_player_media_includes_inventory_actions_and_private_chat(client, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    icons = [asset(tmp_path, str(i)) for i in range(4)]
    a, b = hero(), hero('Other')
    item = Item.objects.create(name='Item', description='item', icon=icons[0])
    InventoryEntry.objects.create(character=a, item=item)
    action = Action.objects.create(name='Action', description='action', icon=icons[1])
    CharacterAction.objects.create(character=a, action=action)
    chat_item = Item.objects.create(name='Chat item', description='chat', icon=icons[2])
    message = ChatMessage.objects.create(kind='item_grant', item=chat_item)
    message.recipients.add(a)
    secret = Item.objects.create(name='Secret item', description='secret', icon=icons[3])
    ChatMessage.objects.create(kind='item_grant', item=secret).recipients.add(b)
    join(client, a)
    for icon in icons[:3]:
        response = client.get(reverse('game:media', args=[icon.digest]))
        assert response.status_code == 200
        response.close()
    assert client.get(reverse('game:media', args=[icons[3].digest])).status_code == 404


def test_invalid_grant_generation_invitation_and_inactive_character(client):
    a = hero()
    join(client, a)
    grant = CharacterGrant.objects.get(character=a)
    invitation = grant.invitation
    invitation.revoked = True
    invitation.save()
    assert client.get(reverse('game:player', args=[a.pk])).status_code == 302
    invitation.revoked = False
    invitation.save()
    grant.generation += 1
    grant.save()
    assert client.get(reverse('game:player', args=[a.pk])).status_code == 302
    grant.generation -= 1
    grant.save()
    a.is_active = False
    a.save()
    assert client.get(reverse('game:player', args=[a.pk])).status_code == 302


def test_bootstrap_retains_existing_master_session(client, settings):
    settings.MASTER_PASSWORD = 'same-bootstrap-secret'
    call_command('bootstrap_master', stdout=io.StringIO())
    client.post(reverse('game:master_login'), {'password': settings.MASTER_PASSWORD})
    call_command('bootstrap_master', stdout=io.StringIO())
    assert client.get(reverse('game:master')).status_code == 200


def test_bootstrap_does_not_upgrade_hash_and_invalidate_sessions(settings):
    from django.contrib.auth.hashers import make_password
    settings.PASSWORD_HASHERS = ['django.contrib.auth.hashers.PBKDF2PasswordHasher',
                                'django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher']
    settings.MASTER_PASSWORD = 'existing-secret'
    encoded = make_password(settings.MASTER_PASSWORD, hasher='pbkdf2_sha1')
    user = get_user_model().objects.create(username='master', password=encoded)
    call_command('bootstrap_master', stdout=io.StringIO())
    user.refresh_from_db()
    assert user.password == encoded


def test_csrf_required_for_login_and_revocation(master):
    browser = Client(enforce_csrf_checks=True)
    assert browser.post(reverse('game:master_login'), {'password': 'test-master-secret'}).status_code == 403
    assert browser.post(reverse('game:revoke', args=[hero().pk])).status_code == 403


def test_public_url_fallback_and_method_restrictions(client, master, settings):
    settings.PUBLIC_BASE_URL = ''
    a = hero()
    client.post(reverse('game:master_login'), {'password': 'test-master-secret'})
    response = client.post(reverse('game:invite', args=[a.pk]), HTTP_HOST='localhost:9443', secure=True)
    assert response.context['link'].startswith('https://localhost:9443/join/')
    assert client.get(reverse('game:master_login')).status_code == 405
    assert client.post(reverse('game:join', args=['invalid'])).status_code == 405


def test_media_rejects_missing_and_outside_symlink(client, master, settings, tmp_path):
    root = tmp_path / 'media'
    root.mkdir()
    settings.MEDIA_ROOT = root
    media = asset(root, 'outside')
    client.post(reverse('game:master_login'), {'password': 'test-master-secret'})
    local = root / media.path
    local.unlink()
    assert client.get(reverse('game:media', args=[media.digest])).status_code == 404
    outside = tmp_path / 'outside.png'
    outside.write_bytes(b'png')
    try:
        local.symlink_to(outside)
    except OSError:
        pytest.skip('Windows symlink privilege unavailable; missing/path traversal tests still run')
    assert client.get(reverse('game:media', args=[media.digest])).status_code == 404


@pytest.mark.parametrize('invalidated', ['expired', 'deleted'])
@pytest.mark.parametrize('htmx', [False, True])
def test_direct_player_rejects_replayed_invalid_session_cookie(client, settings, invalidated, htmx):
    from datetime import timedelta
    from django.contrib.sessions.models import Session
    from django.utils import timezone

    a = hero()
    join(client, a)
    cookie = client.cookies[settings.SESSION_COOKIE_NAME].value
    sessions = Session.objects.filter(session_key=cookie)
    assert sessions.exists()
    if invalidated == 'expired':
        sessions.update(expire_date=timezone.now() - timedelta(seconds=1))
    else:
        sessions.delete()
    assert CharacterGrant.objects.filter(session_key=cookie, character=a, revoked=False).exists()

    replay = Client()
    replay.cookies[settings.SESSION_COOKIE_NAME] = cookie
    headers = {'HTTP_HX_REQUEST': 'true'} if htmx else {}
    response = replay.get(reverse('game:player', args=[a.pk]), **headers)
    if htmx:
        assert response.status_code == 200
        assert response['HX-Redirect'] == reverse('game:index')
        assert response.content == b''
    else:
        assert response.status_code == 302
        assert response.url == reverse('game:index')


def test_direct_player_accepts_unexpired_empty_session(client, settings):
    from django.contrib.sessions.models import Session

    a = hero()
    join(client, a)
    cookie = client.cookies[settings.SESSION_COOKIE_NAME].value
    assert Session.objects.get(session_key=cookie).get_decoded() == {}
    replay = Client()
    replay.cookies[settings.SESSION_COOKIE_NAME] = cookie
    response = replay.get(reverse('game:player', args=[a.pk]))
    assert response.status_code == 200
    assert response.content == b'player'
