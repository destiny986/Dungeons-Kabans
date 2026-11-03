"""Real HTTP duplicate-grant checks in Chrome with an isolated local database."""
import io
import os
from pathlib import Path
import sys
import tempfile
import threading
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / '.browser-artifacts' / 'duplicate-action'
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
from game.models import Action, Character, CharacterAction, ChatMessage, MediaAsset
from PIL import Image
from playwright.sync_api import expect, sync_playwright

settings.ALLOWED_HOSTS = ['127.0.0.1', 'testserver']
settings.STORAGES['staticfiles'] = {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}
call_command('migrate', verbosity=0, stdout=io.StringIO())
get_user_model().objects.create_user(username='master', password='browser-test-password')
master = Client()
assert master.post(reverse('game:master_login'), {'password': 'browser-test-password'}).status_code == 302
owners = [Character.objects.create(name=name, is_active=True)
          for name in ('Анна <маг>', 'Борис с длинным именем для проверки переноса')]
new = Character.objects.create(name='Новый получатель', is_active=True)
asset = MediaAsset.objects.create(digest='a' * 64, path='a' * 64 + '.png', width=1, height=1)
settings.MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
Image.new('RGBA', (1, 1), '#456b51').save(settings.MEDIA_ROOT / asset.path)


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
        for width in (1440, 390, 320):
            action = Action.objects.create(name=f'Огненный шар <заклинание> {width}', icon=asset,
                                           resource='spell_slot', slot_level=2)
            for owner in owners:
                CharacterAction.objects.create(character=owner, action=action)
            context = browser.new_context(viewport={'width': width, 'height': 900})
            context.add_cookies([{'name': name, 'value': morsel.value, 'url': base}
                                for name, morsel in master.cookies.items()])
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(base + reverse('game:master'))
            page.locator(f'a.catalog-action-icon[aria-label="{action.name}"]').click()
            dialog = page.locator('#app-dialog')
            expect(dialog).to_be_visible()
            dialog.locator('summary').click()
            dialog.get_by_role('button', name='Выбрать всех', exact=True).click()
            dialog.get_by_role('button', name='Отправить выбранным').click()
            expect(dialog.get_by_role('heading', name='Уже есть у персонажей')).to_be_visible()
            expect(dialog.locator('.confirmation > p').last).to_have_text(
                f'«{action.name}» уже есть у персонажей: {owners[0].name}, {owners[1].name}.')
            assert new.name not in dialog.inner_text()
            assert CharacterAction.objects.filter(action=action).count() == 3
            assert ChatMessage.objects.filter(action=action).count() == 1
            assert list(ChatMessage.objects.get(action=action).recipients.values_list('pk', flat=True)) == [new.pk]
            rect = dialog.bounding_box()
            assert rect and rect['x'] >= 0 and rect['x'] + rect['width'] <= width
            assert dialog.evaluate('el => el.scrollWidth <= el.clientWidth + 1')
            page.screenshot(path=str(ARTIFACTS / f'notice-{width}.png'))
            dialog.get_by_role('button', name='Понятно').click()
            expect(dialog).not_to_be_visible()
            assert not errors, errors
            context.close()
        context = browser.new_context(java_script_enabled=False)
        context.add_cookies([{'name': name, 'value': morsel.value, 'url': base}
                            for name, morsel in master.cookies.items()])
        page = context.new_page()
        page.goto(base + reverse('game:card', args=['action', action.pk]))
        page.locator('summary').click()
        page.locator(f'input[name="recipients"][value="{owners[0].pk}"]').check()
        page.get_by_role('button', name='Отправить выбранным').click()
        expect(page.get_by_role('heading', name='Уже есть у персонажей')).to_be_visible()
        page.get_by_role('link', name='Понятно').click()
        expect(page).to_have_url(base + reverse('game:master'))
        context.close()
        browser.close()
    print('Duplicate action browser checks passed: 1440/390/320 px, partial grant, escaped names, close, no JavaScript.')
finally:
    server.shutdown()
    server.server_close()
