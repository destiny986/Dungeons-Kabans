"""Chrome integration against an isolated DB: panel geometry and confirmed undo."""
import io
import os
from pathlib import Path
import sys
import tempfile
import threading
import uuid
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / '.browser-artifacts' / 'master-undo'
ARTIFACTS.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))
os.environ['DATA_DIR'] = tempfile.mkdtemp(prefix='data-', dir=ARTIFACTS)
os.environ['MEDIA_ROOT'] = str(Path(os.environ['DATA_DIR']) / 'media')
os.environ['IMPORT_ROOT'] = str(Path(os.environ['DATA_DIR']) / 'imports')
os.environ['DJANGO_ENV'] = 'local'
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
from game.access import issue_invitation
from game.media import prepare_png, store_prepared
from game.models import Character, ChatMessage, Event, InventoryEntry, Item, UndoEntry
from PIL import Image
from playwright.sync_api import expect, sync_playwright

settings.ALLOWED_HOSTS = ['127.0.0.1', 'testserver']
settings.STORAGES['staticfiles'] = {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}
call_command('migrate', verbosity=0, stdout=io.StringIO())
get_user_model().objects.create_user(username='master', password='browser-test-password')
master = Client()
assert master.post(reverse('game:master_login'), {'password': 'browser-test-password'}).status_code == 302
image = io.BytesIO()
Image.new('RGBA', (150, 150), '#a6baa1').save(image, 'PNG'); image.seek(0)
asset = store_prepared(prepare_png(image, size=256))
heroes = [Character.objects.create(name=name, character_class='Рыцарь туманной долины',
    portrait=asset, is_active=True, health_current=99, health_max=100)
    for name in ['Кабас', 'БорисОченьДлинноеИмяБезПробелов'] + [f'Спутник {n}' for n in range(8)]]
hero, other = heroes[:2]
player = Client()
token, _ = issue_invitation(hero)
assert player.get(reverse('game:join', args=[token])).status_code == 302
item = Item.objects.create(name='Зелье', description='Тестовая выдача', icon=asset)
event = Event.objects.create(name='Туман', description='Новая операция', color='blue')


class Server(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass


server = make_server('127.0.0.1', 0, StaticFilesHandler(get_wsgi_application()),
                     server_class=Server, handler_class=QuietHandler)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f'http://127.0.0.1:{server.server_port}'


def context_for(browser, client, width=1440, **kwargs):
    context = browser.new_context(viewport={'width': width, 'height': 1000}, **kwargs)
    context.add_cookies([{'name': name, 'value': value.value, 'url': base}
                        for name, value in client.cookies.items()])
    return context


def refresh(page):
    with page.expect_response(lambda response: '/poll/' in response.url):
        page.evaluate("htmx.trigger('#poller', 'refresh')")
    page.wait_for_timeout(150)


def give(kind='item'):
    data = {'recipients': [str(hero.pk), str(other.pk)], 'request_id': str(uuid.uuid4())}
    if kind == 'item':
        data.update({f'quantity_{hero.pk}': '3', f'quantity_{other.pk}': '2'})
    response = master.post(reverse('game:catalog_send', args=[kind, item.pk if kind == 'item' else event.pk]), data)
    assert response.status_code == 302


try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chrome', headless=True)
        for width in (1440, 390, 320):
            mc, pc = context_for(browser, master, width), context_for(browser, player, width)
            mp, pp = mc.new_page(), pc.new_page()
            errors = []
            for page in (mp, pp):
                page.on('pageerror', lambda error: errors.append(str(error)))
            mp.goto(base + reverse('game:master'))
            pp.goto(base + reverse('game:player', args=[hero.pk]))
            pp.locator('#tab-chat').click()
            undo = mp.get_by_role('button', name='Отменить последнее действие', exact=True)
            expect(undo).to_be_disabled()
            assert mp.locator('[data-open^="revoke-"]').count() == 0
            mp.get_by_role('link', name='Управление персонажами', exact=True).click()
            expect(mp).to_have_url(base + reverse('game:characters'))
            mp.goto(base + reverse('game:master'))
            if width == 1440:
                mp.evaluate("document.querySelector('#master-grid').gridstack.update(document.querySelector('[gs-id=players]'), {w:6,h:6})")
                mp.wait_for_timeout(250)
                rows = mp.locator('.player-mini').evaluate_all('nodes => nodes.map(n => Math.round(n.getBoundingClientRect().top))')
                assert rows[0] == rows[1], rows
                mp.screenshot(path=str(ARTIFACTS / 'players-wide.png'))
                mp.evaluate("document.querySelector('#master-grid').gridstack.update(document.querySelector('[gs-id=players]'), {w:3,h:5})")
                mp.wait_for_timeout(250)
                mp.locator('.players-list').evaluate('el => el.scrollTop = 70')
                refresh(mp)
                assert mp.locator('.players-list').evaluate('el => el.scrollTop') == 70, 'Player scroll lost on OOB update'
                mp.locator('.players-list').evaluate('el => el.scrollTop = 0')
            else:
                rows = mp.locator('.player-mini').evaluate_all('nodes => nodes.map(n => Math.round(n.getBoundingClientRect().top))')
                assert rows[0] < rows[1], rows
            mp.locator('.player-mini h3 a').filter(has_text='Кабас').click()
            dialog = mp.locator('#app-dialog')
            expect(dialog.locator('.active-character')).to_be_visible()
            assert dialog.locator('.ability-tile').count() == 6
            portrait, details = dialog.locator('.active-character-heading>.portrait').bounding_box(), dialog.locator('.active-character-heading>div').bounding_box()
            assert portrait['x'] + portrait['width'] <= details['x']
            assert abs(portrait['y'] - details['y']) < 2
            assert dialog.get_by_role('link', name='Редактировать').get_attribute('href') == reverse('game:character_edit', args=[hero.pk])
            neutral = dialog.get_by_role('link', name='Редактировать').evaluate('el => [getComputedStyle(el).backgroundColor,getComputedStyle(el).borderColor]')
            assert dialog.get_by_role('button', name='Отозвать доступ').evaluate('el => [getComputedStyle(el).backgroundColor,getComputedStyle(el).borderColor]') == neutral
            assert dialog.evaluate('el => el.scrollWidth <= el.clientWidth + 1')
            mp.screenshot(path=str(ARTIFACTS / f'character-{width}.png'))
            mp.keyboard.press('Escape')
            expect(dialog).not_to_be_visible()
            mp.locator('#chat-text').fill('Черновик мастера')
            mp.locator('#chat-recipient').select_option(str(other.pk))
            pp.locator('#chat-text').fill('Черновик игрока')
            give()
            refresh(mp); refresh(pp)
            expect(undo).to_be_enabled()
            undo.click()
            expect(dialog.get_by_role('heading', name='Отменить последнее действие?')).to_be_visible()
            original_id = dialog.locator('[name=entry_id]').input_value()
            assert '3 шт.' in dialog.inner_text() and '2 шт.' in dialog.inner_text()
            dialog.get_by_role('button', name='Назад').click()
            expect(dialog).not_to_be_visible()
            assert InventoryEntry.objects.get(character=hero, item=item).quantity == 3
            undo.click()
            expect(dialog).to_be_visible()
            expect(dialog.locator('[name=entry_id]')).to_have_value(original_id)
            confirmed_key = dialog.locator('[name=request_id]').input_value()
            give('event')
            refresh(mp)
            assert dialog.locator('[name=entry_id]').input_value() == original_id
            assert dialog.locator('[name=request_id]').input_value() == confirmed_key
            dialog.get_by_role('button', name='Отменить действие', exact=True).click()
            expect(dialog.locator('[data-result=stale]')).to_be_visible()
            assert InventoryEntry.objects.get(character=hero, item=item).quantity == 3
            dialog.get_by_role('button', name='Понятно').click()
            refresh(mp)
            for expected_count in (1, 0):
                refresh(mp)
                mp.wait_for_function("!document.querySelector('.players-footer').classList.contains('htmx-added')")
                undo.click()
                expect(dialog.get_by_role('heading', name='Отменить последнее действие?')).to_be_visible()
                if expected_count == 0:
                    mp.screenshot(path=str(ARTIFACTS / f'undo-confirm-{width}.png'))
                dialog.get_by_role('button', name='Отменить действие', exact=True).click()
                expect(dialog).not_to_be_visible()
                assert UndoEntry.objects.count() == expected_count
            refresh(mp); refresh(pp)
            expect(undo).to_be_disabled()
            assert not InventoryEntry.objects.exists() and not ChatMessage.objects.exists()
            assert mp.locator('#chat-text').input_value() == 'Черновик мастера'
            assert pp.locator('#chat-text').input_value() == 'Черновик игрока'
            assert mp.locator('#chat-recipient').input_value() == str(other.pk)
            for page in (mp, pp):
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert not errors, errors
            mc.close(); pc.close()
        give()
        nc = context_for(browser, master, java_script_enabled=False)
        page = nc.new_page()
        page.goto(base + reverse('game:master'))
        page.get_by_role('button', name='Отменить последнее действие').click()
        expect(page).to_have_url(base + reverse('game:undo_last'))
        assert 'Важное решение' in page.locator('main').text_content()
        assert InventoryEntry.objects.get(character=hero).quantity == 3
        page.get_by_role('link', name='Назад').click()
        page.get_by_role('button', name='Отменить последнее действие').click()
        page.get_by_role('button', name='Отменить действие', exact=True).click()
        expect(page).to_have_url(base + reverse('game:master'))
        assert not InventoryEntry.objects.exists()
        nc.close(); browser.close()
    print('PASS: master players 1440/390/320px, resize, scroll, card, confirmation/cancel/stale, two chats/drafts, no-JS')
finally:
    server.shutdown(); server.server_close()
