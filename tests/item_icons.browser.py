"""Browser checks for item icon styling and live catalog previews.

Run: .venv/Scripts/python tests/item_icons.browser.py
Requires Playwright and Chrome. Uses rendered templates without a database.
"""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ARTIFACTS = ROOT / '.browser-artifacts' / 'item-icons'
ARTIFACTS.mkdir(parents=True, exist_ok=True)
os.environ['DATA_DIR'] = str(ARTIFACTS / 'data')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()
from django.conf import settings
from django.template import engines
from django.template.loader import render_to_string
from game.forms import get_catalog_form
from game.models import Item, MediaAsset
from playwright.sync_api import sync_playwright

settings.STORAGES['staticfiles'] = {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}
asset = MediaAsset(digest='e' * 64, path='preview.png', width=256, height=256)
item = Item(name='Походный факел', description='Светит в темноте.', icon=asset, tags=['Снаряжение'])
pages = {}
for editing in [False, True]:
    pages['/edit/' if editing else '/new/'] = render_to_string('game/catalog_form.html', {
        'kind': 'item', 'object': item if editing else None, 'is_edit': editing, 'csrf_token': 'preview',
        'form': get_catalog_form('item', instance=item if editing else None),
    })
pages['/icons/'] = engines['django'].from_string("""{% extends 'game/base.html' %}{% block content %}
<div class="icon-grid">{% include 'game/icon.html' with object=item kind='item' master_catalog_tooltip=True %}</div>
<div class="icon-grid">{% include 'game/icon.html' with object=item kind='item' quantity=3 %}</div>
{% include 'game/card.html' with object=item kind='item' readonly=True %}
{% include 'game/import_card.html' with object=item kind='item' %}
{% endblock %}""").render({'item': item, 'csrf_token': 'preview'})
# A transparent PNG also exercises file upload and object URL image loading.
from io import BytesIO
from PIL import Image, ImageDraw
buffer = BytesIO()
image = Image.new('RGBA', (256, 256))
draw = ImageDraw.Draw(image)
draw.line((100, 220, 140, 85), fill='#765533', width=25)
draw.ellipse((100, 20, 170, 100), fill='#e5a12b')
image.save(buffer, format='PNG')
png = buffer.getvalue()

with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome')
    page = browser.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    def route_request(route):
        path = route.request.url.split('preview.local', 1)[1].split('?', 1)[0]
        if path.startswith('/static/'):
            route.fulfill(path=str(ROOT / 'game' / path.lstrip('/')))
        elif path in pages:
            route.fulfill(body=pages[path], content_type='text/html')
        elif '/media/' in path:
            route.fulfill(body=png, content_type='image/png')
        else:
            route.fulfill(status=204)
    page.route('http://preview.local/**', route_request)
    for width, height in [(1440, 1000), (390, 844), (320, 740)]:
        page.set_viewport_size({'width': width, 'height': height})
        for path in ['/new/', '/edit/']:
            page.goto('http://preview.local' + path)
            assert page.get_by_text('Свернутая карточка', exact=True).count() == 0
            preview = page.locator('.preview-full [data-preview-image]')
            if path == '/edit/':
                assert preview.is_visible()
                assert preview.evaluate('(img) => img.complete && img.naturalWidth > 0')
            else:
                assert preview.is_hidden()
            page.locator('[name=icon]').set_input_files({'name': 'torch.png', 'mimeType': 'image/png', 'buffer': png})
            preview.wait_for(state='visible')
            page.wait_for_function("document.querySelector('.preview-full img').naturalWidth > 0")
            assert preview.get_attribute('src').startswith('blob:')
            page.locator('[name=name]').fill('Новый факел')
            assert page.locator('.preview-full h2').text_content() == 'Новый факел'
            assert preview.evaluate("img => getComputedStyle(img).borderTopWidth === '0px' && getComputedStyle(img).paddingTop === '0px'")
            preview.scroll_into_view_if_needed()
            page.screenshot(path=str(ARTIFACTS / f'{width}-{path.strip("/")}.png'), full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.goto('http://preview.local/icons/')
        for icon in page.locator('.item-icon, .catalog-item-icon').all():
            assert icon.evaluate("el => {const s=getComputedStyle(el); return s.borderTopWidth==='0px' && s.paddingTop==='0px' && s.boxShadow==='none'}")
        link = page.locator('.catalog-item-icon').first
        link.hover()
        assert link.evaluate("el => getComputedStyle(el).boxShadow === 'none'")
        link.focus()
        assert link.evaluate("el => getComputedStyle(el).outlineStyle !== 'none'")
        page.screenshot(path=str(ARTIFACTS / f'{width}-icons.png'), full_page=True)
    assert not errors, errors
    browser.close()
print('Item icons and live new/edit previews passed at 1440, 390 and 320px.')
