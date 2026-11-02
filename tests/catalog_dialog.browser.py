"""Optional real-browser regression: .venv/Scripts/python tests/catalog_dialog.browser.py.

Requires Playwright and local Chrome; renders real templates without a database/server.
Artifacts and Django's temporary directories stay in .browser-artifacts/.
"""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CATALOG_KIND = 'action' if '--actions' in sys.argv else 'item'
ARTIFACTS = ROOT / '.browser-artifacts' / ('catalog-dialog-actions' if CATALOG_KIND == 'action' else 'catalog-dialog')
ARTIFACTS.mkdir(parents=True, exist_ok=True)
os.environ['DATA_DIR'] = str(ARTIFACTS / 'data')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()
from django.conf import settings
from django.template import engines
from django.template.loader import render_to_string
from django.urls import reverse
from game.models import Action, Character, Event, Item, MediaAsset
from playwright.sync_api import sync_playwright

settings.STORAGES['staticfiles'] = {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}
asset = MediaAsset(digest='d' * 64, path='preview.png', width=64, height=64)
model = Action if CATALOG_KIND == 'action' else Item
item = model(name='Железные шипы <без HTML> ' * 8, description='Описание предмета. ' * 25, icon=asset)
event = Event(name='Появление кабана', description='Описание события. ' * 25, color='blue')
recipients = [Character(name=f'Игрок {n}: Следопыт с длинным именем', character_class='Друид') for n in range(24)]
context = {'kind': CATALOG_KIND, 'objects': [item, item], 'title': 'Каталог', 'csrf_token': 'preview'}
shell = engines['django'].from_string("""{% extends 'game/base.html' %}{% block content %}
{% include 'game/catalog_panel.html' %}
<a href="{{ event_url }}" hx-get="{{ event_url }}" hx-target="#dialog-content" data-dialog id="event-link">Событие</a>
{% endblock %}""").render({**context, 'event_url': reverse('game:card', args=['event', event.pk])})
cards = {reverse('game:card', args=[kind, obj.pk]): render_to_string('game/card.html', {
    'kind': kind, 'object': obj, 'is_master': True, 'active_characters': recipients, 'csrf_token': 'preview',
}) for kind, obj in [(CATALOG_KIND, item), ('event', event)]}


def inside_viewport(page, locator):
    rect = locator.bounding_box()
    viewport = page.viewport_size
    assert rect and rect['x'] >= 13 and rect['y'] >= 13, rect
    assert rect['x'] + rect['width'] <= viewport['width'] - 13, rect
    assert rect['y'] + rect['height'] <= viewport['height'] - 13, rect


with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome')
    page = browser.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))

    def route_request(route):
        path = route.request.url.split('preview.local', 1)[1].split('?', 1)[0]
        if path.startswith('/static/'):
            route.fulfill(path=str(ROOT / 'game' / path.lstrip('/')))
        elif path in cards:
            route.fulfill(body=cards[path], content_type='text/html')
        elif path == '/':
            route.fulfill(body=shell, content_type='text/html')
        elif '/media/' in path:
            route.fulfill(body='<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect width="64" height="64" fill="#e5dcc9"/><path d="M20 52L38 20M30 46L44 18" stroke="#6c6047" stroke-width="6"/></svg>', content_type='image/svg+xml')
        else:
            route.fulfill(status=204)

    page.route('http://preview.local/**', route_request)
    for width, height in [(1440, 900), (390, 740), (320, 640)]:
        page.set_viewport_size({'width': width, 'height': height})
        page.goto('http://preview.local/')
        icons = page.locator('.catalog-icon')
        for icon in [icons.first, icons.last]:
            icon.hover()
            tip = page.locator('#catalog-tooltip')
            tip.wait_for(state='visible')
            assert tip.text_content() == item.name
            assert icon.get_attribute('title') is None
            inside_viewport(page, tip)
            assert tip.evaluate('(el) => el.scrollHeight <= el.clientHeight + 1')
            page.mouse.move(width - 1, 1)
            tip.wait_for(state='hidden')
        icons.first.focus()
        page.locator('#catalog-tooltip').wait_for(state='visible')
        page.locator('.panel-body').dispatch_event('scroll')
        page.locator('#catalog-tooltip').wait_for(state='hidden')
        icons.first.hover()
        page.evaluate("document.body.dispatchEvent(new CustomEvent('htmx:beforeSwap', {bubbles:true, detail:{xhr:{status:200}, target:document.querySelector('.panel-body')}}))")
        page.locator('#catalog-tooltip').wait_for(state='hidden')
        icons.last.hover()
        page.screenshot(path=str(ARTIFACTS / f'{width}-tooltip.png'))

        # Open each kind near the lower/right edge, reproducing the original overflow.
        for selector in ['.catalog-icon', '#event-link']:
            source = page.locator(selector).first
            source.evaluate("el => {el.style.position='fixed'; el.style.right='18px'; el.style.bottom='20px'; el.style.zIndex='2'}")
            source.click()
            dialog = page.locator('#app-dialog')
            dialog.wait_for(state='visible')
            inside_viewport(page, dialog)
            summary = dialog.locator('summary')
            edit = dialog.locator('.card-tools > a').bounding_box()
            send = summary.bounding_box()
            delete = dialog.locator('.card-tools > form').bounding_box()
            assert edit['x'] < send['x'] < delete['x']
            assert abs(edit['y'] - send['y']) < 1 and abs(send['y'] - delete['y']) < 1
            summary.scroll_into_view_if_needed()
            page.screenshot(path=str(ARTIFACTS / f'{width}-{selector.strip(".#")}-closed.png'))
            initial_top = dialog.bounding_box()['y']
            summary.click()
            page.wait_for_timeout(100)
            inside_viewport(page, dialog)
            if initial_top > 15:
                assert dialog.bounding_box()['y'] < initial_top
            page.locator('#catalog-tooltip').wait_for(state='hidden')
            first = dialog.locator('[name=recipients]').first
            first.check()
            summary.click()
            summary.click()
            assert first.is_checked()
            submit = dialog.locator('[data-send-form] button.button')
            submit.scroll_into_view_if_needed()
            inside_viewport(page, dialog)
            assert submit.is_visible()
            previous_scroll = dialog.evaluate('el => el.scrollTop')
            page.evaluate('window.focusBeforeResize = document.activeElement')
            page.evaluate("window.dispatchEvent(new Event('resize'))")
            page.wait_for_timeout(100)
            assert dialog.evaluate('el => el.scrollTop') == previous_scroll
            assert page.evaluate('document.activeElement === window.focusBeforeResize')
            # Content changes without a details toggle must also trigger clamping.
            dialog.locator('fieldset').evaluate("el => { const extra = document.createElement('p'); extra.textContent = 'Длинный список '.repeat(100); el.append(extra); }")
            page.wait_for_timeout(100)
            inside_viewport(page, dialog)
            page.set_viewport_size({'width': width, 'height': height - 120})
            page.wait_for_timeout(100)
            inside_viewport(page, dialog)
            page.set_viewport_size({'width': width, 'height': height})
            page.screenshot(path=str(ARTIFACTS / f'{width}-{selector.strip(".#")}.png'))
            page.keyboard.press('Escape')
            dialog.wait_for(state='hidden')
            assert source.evaluate('el => document.activeElement === el')
            source.evaluate("el => el.removeAttribute('style')")
        # Destructive-action confirmations keep the original centered layout.
        page.locator('#event-link').click()
        page.locator('#app-dialog .card-tools > form button').click()
        confirmation = page.locator('dialog').filter(has=page.locator('.confirmation'))
        confirmation.wait_for(state='visible')
        for resized_height in [height, height - 120]:
            page.set_viewport_size({'width': width, 'height': resized_height})
            rect = confirmation.bounding_box()
            assert abs(rect['x'] + rect['width'] / 2 - width / 2) < 1
            assert abs(rect['y'] + rect['height'] / 2 - resized_height / 2) < 1
        page.keyboard.press('Escape')
        page.locator('#app-dialog [data-close]').click()
    assert not errors, errors
    browser.close()
print(f'Catalog tooltips and growing {CATALOG_KIND}/event dialogs: passed at 1440, 390, 320px.')
