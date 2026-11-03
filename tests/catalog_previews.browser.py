"""Compare live catalog previews to real cards in Chrome without database access.

Run: .venv/Scripts/python tests/catalog_previews.browser.py (Playwright + Chrome).
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / '.browser-artifacts' / 'catalog-previews'
ARTIFACTS.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))
os.environ['DATA_DIR'] = str(ARTIFACTS / 'data')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from django.conf import settings
from django.template.loader import render_to_string
from game.forms import get_catalog_form
from game.models import Action, Event, Item, MediaAsset
from PIL import Image
from playwright.sync_api import sync_playwright

settings.STORAGES['staticfiles'] = {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}
asset = MediaAsset(digest='a' * 64, path='a.png', width=64, height=64)
icon = ARTIFACTS / 'icon.png'
Image.new('RGBA', (64, 64), (27, 115, 72, 255)).save(icon)
replacement = ARTIFACTS / 'replacement.png'
Image.new('RGBA', (64, 64), (160, 54, 24, 255)).save(replacement)
name = 'Заклинание с длинным названием'
description = 'Первая строка описания.\nВторая строка с <текстом>.\n\nПоследний абзац.'
styles = '''el => {const s=getComputedStyle(el); return Object.fromEntries(
 ['fontFamily','fontSize','fontWeight','lineHeight','color','border','padding','objectFit'].map(k=>[k,s[k]]));}'''

with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome')
    for width in (1440, 390):
        for kind, model in [('action', Action), ('event', Event), ('item', Item)]:
            kwargs = {'color': 'blue'} if kind == 'event' else {'icon': asset}
            if kind == 'action':
                kwargs.update(activation='reaction', resource='spell_slot', slot_level=7)
            obj = model(name=name, description=description, tags=['Первый', 'Второй'], **kwargs)
            form = get_catalog_form(kind, instance=obj)
            html = render_to_string('game/catalog_form.html', {'form':form, 'object':obj, 'kind':kind, 'is_edit':True, 'csrf_token':'preview'})
            page = browser.new_page(viewport={'width':width, 'height':1100})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            def route(request):
                url = request.request.url
                if '/static/' in url:
                    request.fulfill(path=str(ROOT / 'game' / url.split('preview.local/')[1]))
                elif '/media/' in url:
                    request.fulfill(path=str(icon))
                else:
                    request.fulfill(body=html, content_type='text/html')
            page.route('http://preview.local/**', route)
            page.goto('http://preview.local/')
            preview = page.locator('.preview-full')
            assert preview.locator('h2').inner_text() == name
            assert preview.locator('.card-description').inner_text() == description
            assert preview.locator('.tags span').all_text_contents() == ['Первый','Второй']
            assert page.locator('[data-preview-card]').count() == (1 if kind == 'event' else 0)
            if kind != 'event':
                assert preview.locator('img').evaluate('img => img.complete && img.naturalWidth === 64')
            if kind == 'action':
                assert preview.locator('.action-meta').inner_text() == '✧ Реакция\n▣ Ячейка заклинания · 7 уровень'
                for resource in ('none', 'rage', 'wildshape'):
                    page.locator('[name=resource]').select_option(resource)
                    assert not preview.locator('[data-preview-slot]').is_visible()
                    assert 'уровень' not in preview.locator('.action-meta').inner_text()
                page.locator('[name=resource]').select_option('spell_slot')
                page.locator('[name=slot_level]').fill('3')
                page.locator('[name=activation]').select_option('bonus')
                assert '△ Бонусное действие' in preview.locator('.action-meta').inner_text()
                assert '· 3 уровень' in preview.locator('.action-meta').inner_text()
                page.locator('[name=activation]').select_option('free')
                assert not preview.locator('[data-action-badge="activation"]').is_visible()
                assert preview.locator('[data-action-badge="resource"]').is_visible()
                page.locator('[name=resource]').select_option('none')
                assert not preview.locator('.action-meta').is_visible()
                page.locator('[name=activation]').select_option('action')
                assert preview.locator('.action-meta').inner_text() == '◯ Действие'
                page.locator('[name=resource]').select_option('spell_slot')
                page.locator('[name=slot_level]').fill('7')
                page.locator('[name=activation]').select_option('reaction')
            if kind == 'event':
                for color in ('red','blue','green','orange','yellow','cyan'):
                    page.locator('[name=color]').select_option(color)
                    assert page.locator('[data-preview-card]').get_attribute('class') == f'event-card event-compact event-{color}'
                    assert page.locator('[data-preview-card]').inner_text() == name
                page.locator('[name=color]').select_option('blue')
            for field, value in [('name',name[:-1]+'!'), ('description',description+'\nДополнение'), ('tags','Один, Два')]:
                page.locator(f'[name={field}]').fill(value)
            assert preview.locator('h2').inner_text() == name[:-1]+'!'
            assert preview.locator('.card-description').inner_text() == description+'\nДополнение'
            assert preview.locator('.tags span').all_text_contents() == ['Один','Два']
            for field, value in [('name',name), ('description',description), ('tags','Первый, Второй')]:
                page.locator(f'[name={field}]').fill(value)
            if kind != 'event':
                page.locator('[name=icon]').set_input_files(str(replacement))
                page.wait_for_function("document.querySelector('.preview-full img').complete")
                assert preview.locator('img').get_attribute('src').startswith('blob:')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(ARTIFACTS / f'{kind}-preview-{width}.png'), full_page=True)
            if kind == 'event':
                compact = render_to_string('game/catalog_entries.html', {'kind':kind, 'objects':[obj]})
                page.locator('#dialog-content').evaluate('(el, html) => el.innerHTML = html', compact)
                real_compact = page.locator('#dialog-content .event-compact')
                assert page.locator('[data-preview-card]').evaluate(styles) == real_compact.evaluate(styles)
                assert page.locator('[data-preview-card]').inner_text().strip() == real_compact.inner_text().strip()
            # Mount the actual master card into the app's existing dialog.
            card = render_to_string('game/card.html', {'kind':kind, 'object':obj, 'is_master':True, 'active_characters':[], 'csrf_token':'preview'})
            page.locator('#dialog-content').evaluate('(el, html) => el.innerHTML = html', card)
            page.locator('#app-dialog').evaluate('el => el.showModal()')
            real = page.locator('#dialog-content .detail-card')
            for selector in ('.eyebrow', 'h2', '.card-description', '.tags'):
                assert preview.locator(selector).evaluate(styles) == real.locator(selector).evaluate(styles), (kind,width,selector,preview.locator(selector).evaluate(styles),real.locator(selector).evaluate(styles))
                assert preview.locator(selector).inner_text() == real.locator(selector).inner_text(), (kind,width,selector)
            if kind != 'event':
                assert preview.locator('img').evaluate(styles) == real.locator('img').evaluate(styles)
            if kind == 'action':
                assert preview.locator('.action-meta').inner_text() == real.locator('.action-meta').inner_text()
            page.screenshot(path=str(ARTIFACTS / f'{kind}-card-{width}.png'), full_page=True)
            assert not errors, errors
            if kind == 'action':
                # Bound invalid POST values must also initialize the hidden nodes correctly.
                invalid = get_catalog_form(kind, data={'name':'', 'description':'Описание',
                    'activation':'free', 'resource':'none'})
                html = render_to_string('game/catalog_form.html', {'form':invalid, 'object':obj,
                    'kind':kind, 'is_edit':True, 'csrf_token':'preview'})
                page.goto('http://preview.local/')
                assert not page.locator('.preview-full .action-meta').is_visible()
                page.locator('[name=activation]').select_option('bonus')
                assert page.locator('.preview-full .action-meta').inner_text() == '△ Бонусное действие'
                page.locator('[name=resource]').select_option('rage')
                assert page.locator('.preview-full .action-meta').inner_text() == '△ Бонусное действие\n▣ Ярость'
                assert not errors, errors
            page.close()
            print(f'PASS {kind} at {width}px', flush=True)
    browser.close()
