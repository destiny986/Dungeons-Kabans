"""Render real character-list templates in Chrome without touching the working database."""
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ARTIFACTS = ROOT / '.browser-artifacts' / 'character-management'
ARTIFACTS.mkdir(parents=True, exist_ok=True)
os.environ['DATA_DIR'] = str(ARTIFACTS / 'data')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()
from django.conf import settings
from django.template.loader import render_to_string
from game.models import Character, MediaAsset
from playwright.sync_api import sync_playwright

settings.STORAGES['staticfiles'] = {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}
asset = MediaAsset(digest='d' * 64, path='preview.png', width=512, height=512)
characters = [
    Character(name='Кабас', character_class='Воин', portrait=asset, health_current=12, health_max=20, is_active=True),
    Character(name='Оченьдлинноеимяперсонажабезпроб', character_class='Друид-путешественник', health_current=0, health_max=99),
    Character(name='Кабас копия (1)', character_class='Воин', health_current=12, health_max=20),
]
for character in characters:
    character.copy_request_id = uuid.uuid4()
shell = render_to_string('game/character_list.html', {'characters': characters,
    'csrf_token': 'preview', 'request_id': uuid.uuid4()})

with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome')
    page = browser.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))

    def route_request(route):
        path = route.request.url.split('preview.local', 1)[1].split('?', 1)[0]
        if path.startswith('/static/'):
            route.fulfill(path=str(ROOT / 'game' / path.lstrip('/')))
        elif '/media/' in path:
            route.fulfill(body='<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96"><rect width="96" height="96" fill="#748765"/><circle cx="48" cy="36" r="20" fill="#e6d4ac"/><path d="M18 96V75Q48 40 78 75V96" fill="#544e3e"/></svg>', content_type='image/svg+xml')
        elif path == '/':
            route.fulfill(body=shell, content_type='text/html')
        else:
            route.fulfill(status=204)

    page.route('http://preview.local/**', route_request)
    for width, height in [(1440, 1000), (768, 900), (390, 844), (320, 740)]:
        page.set_viewport_size({'width': width, 'height': height})
        page.goto('http://preview.local/')
        page.locator('.character-management-tools').first.wait_for()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        for card in page.locator('.character-management .character-card').all():
            assert card.locator('.character-management-tools .button').all_text_contents() == [
                'Изменить', 'Копировать', 'Отозвать приглашение', 'Удалить']
            controls = card.locator('.character-management-tools .button')
            rectangles = [control.bounding_box() for control in controls.all()]
            assert max(rect['y'] for rect in rectangles) - min(rect['y'] for rect in rectangles) < 1
            for control in controls.all():
                assert control.evaluate('(el) => el.scrollWidth <= el.clientWidth + 1 && el.scrollHeight <= el.clientHeight + 1')
            for index in [0, 1, 3]:
                assert controls.nth(index).evaluate('''el => {
                    const range = document.createRange(); range.selectNodeContents(el);
                    return range.getClientRects().length === 1;
                }'''), (width, index, controls.nth(index).evaluate('''el => {
                    const range = document.createRange(); range.selectNodeContents(el);
                    return {label:el.textContent, rects:Array.from(range.getClientRects(), r => ({x:r.x,y:r.y,width:r.width,height:r.height}))};
                }'''))
            portrait = card.locator('.portrait').bounding_box()
            text = card.locator('.character-management-heading>div').bounding_box()
            assert text['x'] >= portrait['x'] + portrait['width']
            assert card.locator('.character-management-heading>div').evaluate('''el => {
                const s = getComputedStyle(el);
                return s.paddingTop === s.paddingBottom && s.paddingLeft === s.paddingRight && s.paddingTop === s.paddingLeft;
            }''')
            status = card.locator('.status-chip').bounding_box()
            assert status['y'] < portrait['y']
        page.screenshot(path=str(ARTIFACTS / f'characters-{width}.png'), full_page=True)
        # Cancellation is read-only and the confirmation uses the established wording.
        page.locator('.character-management-tools form').nth(1).locator('button').click()
        dialog = page.locator('dialog[open]')
        assert 'завершить доступ к персонажу Кабас во всех браузерах' in dialog.inner_text()
        page.keyboard.press('Escape')
        assert page.locator('dialog[open]').count() == 0
    assert not errors, errors
    browser.close()
print('CHARACTER_MANAGEMENT_BROWSER_OK: desktop/tablet/390/320, buttons, portraits, confirmation')
