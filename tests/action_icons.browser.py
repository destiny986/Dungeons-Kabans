"""Real-browser checks for action/spell icons, without database access.

Run: .venv/Scripts/python tests/action_icons.browser.py (Playwright + Chrome).
"""
import os
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ARTIFACTS = ROOT / '.browser-artifacts' / 'action-icons'
ARTIFACTS.mkdir(parents=True, exist_ok=True)
os.environ['DATA_DIR'] = str(ARTIFACTS / 'data')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()
from django.conf import settings
from django.template import engines
from django.template.loader import render_to_string
from game.forms import get_catalog_form
from game.models import Action, Character, MediaAsset
from playwright.sync_api import sync_playwright

settings.STORAGES['staticfiles'] = {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}
asset = MediaAsset(digest='f' * 64, path='preview.png', width=256, height=256)
action = Action(name='Огненная стрела', description='Описание заклинания', icon=asset)
hero = Character(name='Маг', character_class='Волшебник')
pages = {}
for editing in [False, True]:
    pages['/edit/' if editing else '/new/'] = render_to_string('game/catalog_form.html', {
        'kind': 'action', 'object': action if editing else None, 'is_edit': editing,
        'form': get_catalog_form('action', instance=action if editing else None), 'csrf_token': 'preview',
    })
pages['/icons/'] = engines['django'].from_string("""{% extends 'game/base.html' %}{% block content %}
{% include 'game/catalog_entries.html' with objects=actions kind='action' %}
{% include 'game/player_actions.html' %}
{% include 'game/card.html' with object=action kind='action' readonly=True %}
{% include 'game/card.html' with object=action kind='action' character=hero %}
{% include 'game/import_card.html' with object=action kind='action' %}
{% include 'game/import_card.html' with object=incoming kind='action' %}
{% endblock %}""").render({
    'actions': [action], 'action': action, 'hero': hero, 'character': hero,
    'action_sections': [{'label': 'Действия', 'actions': [action]}],
    'incoming': {'name': action.name, 'preview_icon': '/media/preview.png', 'activation': 'action', 'resource': 'none'}, 'csrf_token': 'preview',
})
pages['/character/'] = render_to_string('game/character_form.html', {
    'form': SimpleNamespace(action_rows=[{'pk': action.pk, 'icon': asset, 'label': action.name}]),
    'csrf_token': 'preview',
})
svg = '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><path d="M12 52L52 12" stroke="#b98032" stroke-width="12"/></svg>'

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
            route.fulfill(body=svg, content_type='image/svg+xml')
        else:
            route.fulfill(status=204)

    page.route('http://preview.local/**', route_request)
    for width, height in [(1440, 1000), (390, 844), (320, 740)]:
        page.set_viewport_size({'width': width, 'height': height})
        for path in ['/icons/', '/edit/', '/new/', '/character/']:
            page.goto('http://preview.local' + path)
            images = page.locator('main img:not(.preview-portrait img)')
            assert images.count() > 0
            for icon in images.all():
                assert icon.evaluate("el => {const s=getComputedStyle(el); return s.borderTopWidth==='0px' && s.paddingTop==='0px' && s.boxShadow==='none'}"), (width, path)
            for icon in page.locator('.catalog-icon').all():
                icon.hover()
                assert icon.evaluate("el => {const s=getComputedStyle(el); return s.borderTopWidth==='0px' && s.paddingTop==='0px' && s.boxShadow==='none'}")
                icon.focus()
                assert icon.evaluate("el => getComputedStyle(el).outlineStyle !== 'none'")
            page.screenshot(path=str(ARTIFACTS / f'{width}-{path.strip("/")}.png'), full_page=True)
    assert not errors, errors
    browser.close()
print('Action/spell icons: borderless in catalogs, player cards, editors and import at 1440/390/320px.')
