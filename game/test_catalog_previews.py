"""Catalog previews must use the same content as the cards they represent."""
import re

import pytest
from django.urls import reverse
from django.utils.html import escape

from game.models import Action, Event, Item, MediaAsset
from game.test_ui_review import Controls, ui_master

pytestmark = pytest.mark.django_db


def preview_content(response):
    return response.content.decode().split('<aside class="preview-aside"', 1)[1]


@pytest.mark.parametrize('kind,model', [('event', Event), ('item', Item), ('action', Action)])
@pytest.mark.parametrize('editing', [False, True])
def test_catalog_preview_uses_real_card_content(ui_master, kind, model, editing):
    asset = MediaAsset.objects.create(digest='d' * 64, path='d.png', width=64, height=64)
    kwargs = {} if kind == 'event' else {'icon': asset}
    obj = model.objects.create(name='Огонь', description='Первая строка\nВторая строка', tags=['Магия'], **kwargs)
    url = reverse('game:catalog_edit', args=[kind, obj.pk]) if editing else reverse('game:catalog_new', args=[kind])
    response = ui_master.get(url)
    html = preview_content(response)
    assert 'game/_card_content.html' in [template.name for template in response.templates]
    assert 'detail-card' in html
    assert ('Свернутая карточка' in html) == (kind == 'event')
    assert 'card-tools' not in html and '<form' not in html
    if editing:
        assert obj.name in html and 'Первая строка<br>Вторая строка' in html
        assert '<span>Магия</span>' in html
        if kind != 'event':
            image = next(attrs for tag, attrs in Controls(html).elements if tag == 'img')
            assert image['src'] == reverse('game:media', args=[asset.digest])
            assert 'detail-icon' in image['class'] and 'hidden' not in image
    elif kind != 'event':
        image = next(attrs for tag, attrs in Controls(html).elements if tag == 'img')
        assert 'hidden' in image


@pytest.mark.parametrize('resource,level', [('spell_slot', 7), ('none', None), ('rage', None), ('wildshape', None)])
def test_action_preview_metadata_matches_full_card(ui_master, resource, level):
    asset = MediaAsset.objects.create(digest='e' * 64, path='e.png', width=64, height=64)
    obj = Action.objects.create(name='Заклинание', description='Описание', icon=asset, activation='reaction', resource=resource, slot_level=level)
    html = preview_content(ui_master.get(reverse('game:catalog_edit', args=['action', obj.pk])))
    assert '✧</span>' in html
    assert '>Реакция</span>' in html
    assert re.search(r'▣\s*<span[^>]*data-preview-select="resource"[^>]*>' + obj.get_resource_display(), html)
    assert 'data-preview-slot' in html
    if level:
        assert '· <span data-preview="slot_level">7</span> уровень' in html
    else:
        assert re.search(r'<span[^>]*data-preview-slot[^>]*hidden', html)


@pytest.mark.parametrize('kind,model', [('event', Event), ('item', Item), ('action', Action)])
@pytest.mark.parametrize('stale', [False, True])
def test_invalid_post_preview_uses_bound_values(ui_master, kind, model, stale):
    asset = MediaAsset.objects.create(digest='e' * 64, path='e.png', width=64, height=64)
    kwargs = {} if kind == 'event' else {'icon': asset}
    obj = model.objects.create(name='Старое', description='Старое описание', tags=['Старый'], **kwargs)
    data = {'name': '<Новое>', 'description': 'Новая строка\n<текст>', 'tags': 'Первый, Второй',
            'revision': obj.revision, 'color': 'blue', 'activation': 'bonus', 'resource': 'spell_slot', 'slot_level': 3}
    if stale:
        data['revision'] += 1
    else:
        data['name'] = '<' + 'Новое' * 7 + '>'  # Invalid length keeps submitted values too.
    response = ui_master.post(reverse('game:catalog_edit', args=[kind, obj.pk]), data)
    assert response.status_code == (409 if stale else 422)
    html = preview_content(response)
    assert escape(data['name']) in html and 'Новая строка<br>&lt;текст&gt;' in html
    assert '<span>Первый</span><span>Второй</span>' in html
    assert 'Старое описание' not in html
    if kind == 'action':
        assert '>Бонусное действие</span>' in html
        assert '· <span data-preview="slot_level">3</span> уровень' in html
    elif kind == 'event':
        assert 'event-blue' in html


@pytest.mark.parametrize('resource', ['none', 'rage'])
def test_invalid_post_keeps_free_metadata_hidden(ui_master, resource):
    asset = MediaAsset.objects.create(digest='f' * 64, path='f.png', width=64, height=64)
    obj = Action.objects.create(name='Старое', description='Описание', icon=asset)
    response = ui_master.post(reverse('game:catalog_edit', args=['action', obj.pk]), {
        'name': '', 'description': 'Описание', 'activation': 'free', 'resource': resource, 'revision': obj.revision,
    })
    assert response.status_code == 422
    elements = Controls(preview_content(response)).elements
    badges = {attrs['data-action-badge']: attrs for _, attrs in elements if 'data-action-badge' in attrs}
    assert 'hidden' in badges['activation']
    assert ('hidden' in badges['resource']) == (resource == 'none')
