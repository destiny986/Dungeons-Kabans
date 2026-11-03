"""Real HTTP chat regression in Chrome, using an isolated local database."""
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
ARTIFACTS = ROOT / '.browser-artifacts' / 'chat'
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


try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chrome')
        for width in (1440, 390, 320):
            create_message(recipients=[hero.pk, other.pk], from_master=True, kind='event', event=event)
            create_message(recipients=[hero.pk], sender_character=hero, kind='text', text='Ответ игрока')
            for action in actions:
                create_message(recipients=[hero.pk], from_master=True, kind='action_grant', action=action)
            for metadata in ({'resource': 'spell_slot', 'slot_level': 4},
                             {'resource': 'rage', 'slot_level': 0},
                             {'resource': 'wildshape', 'slot_level': 0}):
                create_message(recipients=[hero.pk], sender_character=hero, kind='action_use',
                               text='Применено действие.', metadata=metadata)
            mc = browser_context(browser, master, viewport={'width': width, 'height': 1000})
            pc = browser_context(browser, player, viewport={'width': width, 'height': 1000})
            mp, pp = mc.new_page(), pc.new_page()
            mp.set_default_timeout(10000); pp.set_default_timeout(10000)
            errors = []
            for page in (mp, pp):
                page.on('pageerror', lambda error: errors.append(str(error)))
            mp.goto(base + reverse('game:master'))
            pp.goto(base + reverse('game:player', args=[hero.pk]))
            pp.get_by_role('tab', name='Чат').click()
            mh = mp.locator('#master-messages header strong').all_text_contents()
            assert any('Мастер ➤ ' in text and hero.name in text and other.name in text for text in mh)
            assert hero.name in mh
            assert 'Мастер' in pp.locator('#player-messages header strong').all_text_contents()
            assert mp.locator('.chat-recipients').count() == 0
            for page, target in [(mp, '#master-messages'), (pp, '#player-messages')]:
                assert page.locator(f'{target} .resource-note').all_text_contents() == [
                    'Потрачена ячейка заклинаний 4 уровня',
                    'Потрачена ячейка ярости', 'Потрачена ячейка дикого облика']
                for action in actions:
                    symbol = ACTIVATION_SYMBOLS[action.activation]
                    assert page.locator(f'{target} .subject-link', has_text=action.name).inner_text() == (symbol + ' ' + action.name).strip()
                compact = page.locator(f'{target} .event-compact')
                assert compact.inner_text().strip() == event.name
                compact.click()
                page.locator('#app-dialog[open] .card-description').wait_for()
                assert 'Полное описание события' in page.locator('#app-dialog').inner_text()
                if page == mp:
                    delete_style = page.locator('#app-dialog .card-tools > form .button').evaluate('''el => {
                      const s = getComputedStyle(el); return [s.backgroundColor, s.color, s.borderColor, s.borderRadius];
                    }''')
                page.locator('#app-dialog > [data-close]').click()
            clear = mp.get_by_role('button', name='Очистить чат', exact=True)
            assert clear.inner_text().strip() == '⦸ Очистить чат'
            assert clear.evaluate('''el => { const s = getComputedStyle(el);
              return [s.backgroundColor, s.color, s.borderColor, s.borderRadius]; }''') == delete_style
            heading = mp.locator('.chat-heading').bounding_box()
            caption = mp.locator('.chat-heading .panel-caption').bounding_box()
            assert caption['x'] + caption['width'] > heading['x'] + heading['width'] - 25
            assert mp.locator('.chat-heading form').count() == 0
            footer = mp.locator('.chat-footer')
            assert footer.locator('form').count() == 1
            assert mp.locator('[data-chat-form] form').count() == 0
            assert pp.get_by_role('button', name='Очистить чат', exact=True).count() == 0
            clear.scroll_into_view_if_needed()
            button_box = clear.bounding_box()
            help_box = footer.locator('.help').bounding_box()
            assert help_box['x'] + help_box['width'] <= button_box['x']
            assert help_box['y'] < button_box['y'] + button_box['height']
            assert button_box['y'] < help_box['y'] + help_box['height']
            assert mp.locator('[data-chat-form] [name=text]').input_value() == ''
            clear.click()
            mp.locator('dialog[open]').filter(has_text='Важное решение').get_by_role('button', name='Отмена').click()
            assert ChatMessage.objects.exists()
            mp.locator('#chat-recipient').select_option(str(other.pk))
            mp.locator('[data-chat-form] [name=text]').fill('Черновик мастера')
            pp.locator('[data-chat-form] [name=text]').fill('Черновик игрока')
            clear.scroll_into_view_if_needed()
            mp.locator('.chat-panel').screenshot(path=str(ARTIFACTS / f'chat-{width}.png'))
            mp.locator('#master-messages').evaluate('el => {el.scrollTop = el.scrollHeight;}')
            mp.locator('.chat-panel').screenshot(path=str(ARTIFACTS / f'resources-{width}.png'))
            clear.click()
            confirmation = mp.locator('dialog[open]').filter(has_text='Важное решение')
            confirmation.get_by_role('button', name='Отмена').click()
            assert ChatMessage.objects.exists()
            clear.click()
            confirmation = mp.locator('dialog[open]').filter(has_text='Важное решение')
            confirmation.screenshot(path=str(ARTIFACTS / f'confirm-{width}.png'))
            confirmation.get_by_role('button', name='Подтвердить').click()
            mp.locator('#master-messages .empty-state').wait_for()
            pp.locator('#player-messages .empty-state').wait_for(timeout=12000)
            assert not ChatMessage.objects.exists()
            assert mp.locator('[data-chat-form] [name=text]').input_value() == 'Черновик мастера'
            assert pp.locator('[data-chat-form] [name=text]').input_value() == 'Черновик игрока'
            assert mp.locator('#chat-recipient').input_value() == str(other.pk)
            assert not errors, errors
            for page in (mp, pp):
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            mc.close(); pc.close()
        # The non-JavaScript flow must require a separate confirmation POST.
        create_message(recipients=[hero.pk], from_master=True, kind='text', text='Без JS')
        context = browser_context(browser, master, java_script_enabled=False)
        page = context.new_page()
        page.goto(base + reverse('game:master'))
        page.get_by_role('button', name='Очистить чат', exact=True).click()
        page.wait_for_url(base + reverse('game:chat_clear'))
        assert 'Важное решение' in page.locator('main').text_content()
        assert ChatMessage.objects.exists()
        page.get_by_role('link', name='Отмена').click()
        page.wait_for_url(base + reverse('game:master'))
        assert ChatMessage.objects.exists()
        page.get_by_role('button', name='Очистить чат', exact=True).click()
        page.wait_for_url(base + reverse('game:chat_clear'))
        page.get_by_role('button', name='Очистить чат', exact=True).click()
        page.wait_for_url(base + reverse('game:master'))
        assert page.url == base + reverse('game:master')
        assert not ChatMessage.objects.exists()
        context.close(); browser.close()
    print('Chat browser checks passed: 1440px, 390px, 320px; footer/style/resources, empty input, confirmation/cancel, polls, drafts, no JavaScript.')
finally:
    server.shutdown()
    server.server_close()
