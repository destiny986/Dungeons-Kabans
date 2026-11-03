"""Master activity regression in Chrome, using an isolated local database."""
import io
import os
from pathlib import Path
import sys
import tempfile
import threading
from wsgiref.simple_server import WSGIRequestHandler, make_server
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / '.browser-artifacts' / 'chat-activity'
ARTIFACTS.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))
os.environ['DATA_DIR'] = tempfile.mkdtemp(prefix='data-', dir=ARTIFACTS)
os.environ['MEDIA_ROOT'] = str(Path(os.environ['DATA_DIR']) / 'media')
os.environ['IMPORT_ROOT'] = str(Path(os.environ['DATA_DIR']) / 'imports')
os.environ['DJANGO_ENV'] = 'local'
# Playwright's synchronous API runs an event loop; this standalone script uses
# synchronous ORM calls only between browser operations, on its isolated DB.
os.environ['DJANGO_ALLOW_ASYNC_UNSAFE'] = 'true'
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.handlers import StaticFilesHandler
from django.core.management import call_command
from django.core.wsgi import get_wsgi_application
from django.test import Client
from django.urls import reverse
from game.chat import create_message
from game.constants import ACTIVATION_SYMBOLS
from game.models import Action, Character, ChatMessage, Event, MediaAsset
from game.test_web import joined
from playwright.sync_api import sync_playwright
from PIL import Image

settings.ALLOWED_HOSTS = ['127.0.0.1', 'testserver']
settings.STORAGES['staticfiles'] = {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}
call_command('migrate', verbosity=0, stdout=io.StringIO())
get_user_model().objects.create_user(username='master', password='browser-test-password')
master = Client()
assert master.post(reverse('game:master_login'), {'password': 'browser-test-password'}).status_code == 302
hero = Character.objects.create(name='Кабас <маг>')
player = joined(hero)
other = Character.objects.create(name='Второй игрок', is_active=True)
event = Event.objects.create(name='Событие с длинным названием для проверки переноса', description='Полное описание события', color='blue')
asset = MediaAsset.objects.create(digest='a' * 64, path='a' * 64 + '.png', width=1, height=1)
settings.MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
Image.new('RGBA', (1, 1), '#456b51').save(settings.MEDIA_ROOT / asset.path)
actions = [Action.objects.create(name=f'Магия {activation}', activation=activation, icon=asset)
           for activation in ACTIVATION_SYMBOLS]


class Server(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass


server = make_server('127.0.0.1', 0, StaticFilesHandler(get_wsgi_application()),
                     server_class=Server, handler_class=QuietHandler)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f'http://127.0.0.1:{server.server_port}'


def browser_context(browser, client, **kwargs):
    context = browser.new_context(**kwargs)
    context.add_cookies([{'name': name, 'value': morsel.value, 'url': base}
                        for name, morsel in client.cookies.items()])
    return context


def hidden(page, value):
    # Headless Chromium keeps pages visible; drive the same DOM API event
    # deterministically, with real network requests and unmodified app timers.
    page.evaluate('''value => {
      Object.defineProperty(document, 'hidden', {configurable:true, value});
      Object.defineProperty(document, 'visibilityState', {configurable:true, value:value ? 'hidden' : 'visible'});
      document.dispatchEvent(new Event('visibilitychange'));
    }''', value)


try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chrome')
        for width in (1440, 390):
            create_message(recipients=[hero.pk], sender_character=hero, kind='text', text='Старая история')
            mc = browser_context(browser, master, viewport={'width':width, 'height':1000})
            pc = browser_context(browser, player, viewport={'width':width, 'height':1000})
            mp, pp = mc.new_page(), pc.new_page()
            errors, paths = [], []
            mp.on('pageerror', lambda error: errors.append(str(error)))
            mp.on('request', lambda request: paths.append(request.url))
            mp.goto(base + reverse('game:master'))
            pp.goto(base + reverse('game:player', args=[hero.pk]))
            original = mp.title()
            assert pp.locator('[data-activity-url]').count() == 0
            mp.locator('#chat-recipient').select_option(str(other.pk))
            mp.locator('#chat-text').fill('Черновик мастера')
            mp.locator('#master-messages').evaluate('el => { el.scrollTop = 0; }')
            position = mp.locator('#master-messages').evaluate('el => el.scrollTop')
            # A full refresh queued while visible must not start after hide.
            held = []
            mp.route('**/master/poll/**', lambda route: held.append(route))
            mp.evaluate("htmx.trigger('#poller', 'refresh')")
            mp.wait_for_timeout(200)
            assert len(held) == 1
            mp.evaluate("htmx.trigger('#poller', 'refresh')")
            hidden(mp, True)
            held[0].fulfill(status=200, content_type='text/html', body='')
            mp.wait_for_timeout(250)
            assert len(held) == 1
            mp.unroute('**/master/poll/**')
            hidden(mp, True)
            mp.wait_for_timeout(400)
            assert mp.title() == original
            pp.get_by_role('tab', name='Чат').click()
            pp.locator('#chat-text').fill('Новое сообщение игрока')
            pp.get_by_role('button', name='Отправить сообщение').click()
            pp.wait_for_function("document.querySelector('#chat-text').value === ''")
            mp.wait_for_function('document.title.includes("Новых сообщений: 1")', timeout=12000)
            mp.wait_for_function('document.title.startsWith("(1)")', timeout=4000)
            # Actual player operations over HTTP with that browser's CSRF/session.
            from game.models import CharacterAction, InventoryEntry, Item
            import uuid
            CharacterAction.objects.get_or_create(character=hero, action=actions[0])
            item = Item.objects.create(name=f'Предмет {width}', icon=asset)
            InventoryEntry.objects.create(character=hero, item=item, quantity=1)
            csrf = pp.locator('[data-chat-form] [name=csrfmiddlewaretoken]').input_value()
            headers = {'X-CSRFToken':csrf, 'HX-Request':'true'}
            action_url = reverse('game:apply_action', args=[hero.pk, actions[0].pk])
            response = pp.request.post(base + action_url, form={'request_id':str(uuid.uuid4())}, headers=headers)
            assert response.status == 200, response.text()
            response = pp.request.post(base + reverse('game:remove_item', args=[hero.pk, item.pk]),
                                       form={'request_id':str(uuid.uuid4()), 'quantity':'1'}, headers=headers)
            assert response.status == 200, response.text()
            mp.wait_for_function('document.title.includes("3")', timeout=12000)
            create_message(recipients=[hero.pk], sender_character=hero, kind='health')
            create_message(recipients=[hero.pk], from_master=True, kind='text')
            paths.clear()
            mp.evaluate("htmx.trigger(document.body, 'refresh')")
            mp.wait_for_timeout(5500)
            assert '3' in mp.title()
            assert not any('/master/poll/' in path for path in paths)
            assert any('/master/chat/activity/' in path for path in paths)
            assert mp.locator('#chat-text').input_value() == 'Черновик мастера'
            assert mp.locator('#chat-recipient').input_value() == str(other.pk)
            assert mp.locator('#master-messages').evaluate('el => el.scrollTop') == position
            hidden(mp, False)
            mp.wait_for_function('document.title === ' + __import__('json').dumps(original))
            mp.locator('#master-messages').get_by_text('Новое сообщение игрока', exact=True).wait_for()
            assert mp.locator('#chat-text').input_value() == 'Черновик мастера'
            assert mp.locator('#chat-recipient').input_value() == str(other.pk)
            mp.screenshot(path=str(ARTIFACTS / f'activity-{width}.png'), full_page=True)
            # Next hidden cycle acknowledges successfully rendered activity.
            hidden(mp, True)
            mp.wait_for_timeout(600)
            assert mp.title() == original
            create_message(recipients=[hero.pk], sender_character=hero, kind='text', text='Удаляемая запись')
            mp.wait_for_function('document.title.includes("1")', timeout=12000)
            ChatMessage.objects.all().delete()
            mp.wait_for_function('document.title === ' + __import__('json').dumps(original), timeout=12000)
            hidden(mp, False)
            assert not errors, errors
            mc.close(); pc.close()
        browser.close()
    print('Activity browser checks passed: 1440/390px, separate sessions, real player POSTs, hidden polling, blink/count, clear, drafts and recipient.')
finally:
    server.shutdown()
    server.server_close()


