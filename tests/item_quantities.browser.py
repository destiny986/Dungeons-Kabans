"""Real Chrome checks using an isolated database under .browser-artifacts."""
import io
import os
from pathlib import Path
import sys
import tempfile
import threading
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / '.browser-artifacts' / 'item-quantities'
ARTIFACTS.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))
os.environ['DATA_DIR'] = tempfile.mkdtemp(prefix='data-', dir=ARTIFACTS)
os.environ['MEDIA_ROOT'] = str(Path(os.environ['DATA_DIR']) / 'media')
os.environ['IMPORT_ROOT'] = str(Path(os.environ['DATA_DIR']) / 'imports')
os.environ['DJANGO_ENV'] = 'local'
os.environ['DJANGO_ALLOW_ASYNC_UNSAFE'] = 'true'
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'

import django
django.setup()
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.handlers import StaticFilesHandler
from django.core.management import call_command
from django.core.wsgi import get_wsgi_application
from django.test import Client
from django.urls import reverse
from game.models import Character, ChatMessage, InventoryEntry, Item, MediaAsset
from game.test_web import joined
from PIL import Image
from playwright.sync_api import sync_playwright, expect

settings.ALLOWED_HOSTS = ['127.0.0.1', 'testserver']
settings.STORAGES['staticfiles'] = {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}
call_command('migrate', verbosity=0, stdout=io.StringIO())
get_user_model().objects.create_user(username='master', password='browser-test-password')
master = Client()
assert master.post(reverse('game:master_login'), {'password': 'browser-test-password'}).status_code == 302
hero = Character.objects.create(name='Первый игрок с очень длинным именем для проверки переноса')
joined(hero)
other = Character.objects.create(name='Второй игрок', is_active=True)
asset = MediaAsset.objects.create(digest='a' * 64, path='a' * 64 + '.png', width=1, height=1)
settings.MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
Image.new('RGBA', (1, 1), '#456b51').save(settings.MEDIA_ROOT / asset.path)
item = Item.objects.create(name='Зелье', icon=asset)


class Server(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass


server = make_server('127.0.0.1', 0, StaticFilesHandler(get_wsgi_application()),
                     server_class=Server, handler_class=QuietHandler)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f'http://127.0.0.1:{server.server_port}'

try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chrome')
        for width in (1440, 390):
            context = browser.new_context(viewport={'width': width, 'height': 1000})
            context.add_cookies([{'name': name, 'value': morsel.value, 'url': base}
                                for name, morsel in master.cookies.items()])
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(base + reverse('game:master'))
            page.locator(f'a[href="{reverse("game:card", args=["item", item.pk])}"]').first.click()
            dialog = page.locator('#app-dialog')
            expect(dialog).to_be_visible()
            dialog.locator('.catalog-send > summary').click()
            form = dialog.locator('[data-send-form]')
            checkbox = form.locator(f'[name=recipients][value="{hero.pk}"]')
            quantity = form.locator(f'[name="quantity_{hero.pk}"]')
            second = form.locator(f'[name="quantity_{other.pk}"]')
            expect(quantity).to_be_disabled()
            expect(quantity).to_have_value('1')
            checkbox.check()
            expect(quantity).to_be_enabled()
            quantity.fill('3')
            checkbox.uncheck()
            expect(quantity).to_be_disabled()
            checkbox.check()
            expect(quantity).to_have_value('3')
            dialog.locator('.catalog-send > summary').click()
            dialog.locator('.catalog-send > summary').click()
            expect(quantity).to_have_value('3')
            form.locator('[data-select-all]').click()
            expect(second).to_be_enabled()
            second.fill('7')
            form.locator('[data-select-none]').click()
            expect(quantity).to_be_disabled()
            expect(second).to_be_disabled()
            form.locator('[data-select-all]').click()
            expect(second).to_have_value('7')
            with page.expect_response(lambda r: '/master/poll/' in r.url):
                page.evaluate("htmx.trigger(document.querySelector('#poller'), 'refresh')")
            expect(quantity).to_have_value('3')
            expect(checkbox).to_be_checked()
            label_box = checkbox.locator('..').bounding_box()
            quantity_box = quantity.bounding_box()
            assert label_box['x'] + label_box['width'] <= quantity_box['x']
            assert quantity_box['x'] + quantity_box['width'] <= width - 14
            page.screenshot(path=str(ARTIFACTS / f'form-{width}.png'))
            # Native number constraints are deliberately bypassed to exercise
            # server rejection, the bound response, and UUID retry end to end.
            form.evaluate('form => form.noValidate = true')
            second.fill('0')
            with page.expect_response(lambda r: '/send/' in r.url) as response:
                form.locator('button[type=submit], button:not([type])').last.click()
            assert response.value.status == 422
            submitted = response.value.request.post_data
            from urllib.parse import parse_qs
            key = parse_qs(submitted)['request_id'][0]
            expect(form.locator('[name=request_id]')).to_have_value(key)
            expect(quantity).to_have_value('3')
            expect(second).to_have_value('0')
            expect(form.locator('[role=alert]')).to_be_visible()
            second.fill('7')
            with page.expect_response(lambda r: '/send/' in r.url) as response:
                form.locator('button[type=submit], button:not([type])').last.click()
            assert response.value.status == 200
            assert parse_qs(response.value.request.post_data)['request_id'] == [key]
            expect(dialog).not_to_be_visible()
            assert InventoryEntry.objects.get(character=hero, item=item).quantity == (3 if width == 1440 else 6)
            assert InventoryEntry.objects.get(character=other, item=item).quantity == (7 if width == 1440 else 14)
            assert ChatMessage.objects.filter(kind='item_grant').count() == (2 if width == 1440 else 4)
            assert not errors, errors
            context.close()
            print(f'Chrome {width}px: selection, quantities, polling, layout, validation, UUID retry, grant OK')
        # No-JS fallback must ignore even an empty field of an unselected player.
        context = browser.new_context(java_script_enabled=False)
        context.add_cookies([{'name': name, 'value': morsel.value, 'url': base}
                            for name, morsel in master.cookies.items()])
        page = context.new_page()
        page.goto(base + reverse('game:card', args=['item', item.pk]))
        page.locator('.catalog-send > summary').click()
        form = page.locator('[data-send-form]')
        form.locator(f'[name=recipients][value="{hero.pk}"]').check()
        form.locator(f'[name="quantity_{other.pk}"]').fill('')
        with page.expect_response(lambda r: '/send/' in r.url, timeout=3000) as response:
            form.locator('button:not([type])').click()
        assert response.value.status == 302
        assert InventoryEntry.objects.get(character=hero, item=item).quantity == 7
        assert InventoryEntry.objects.get(character=other, item=item).quantity == 14
        context.close()
        print('Chrome without JavaScript: unselected empty quantity ignored OK')
        browser.close()
finally:
    server.shutdown()
    server.server_close()
