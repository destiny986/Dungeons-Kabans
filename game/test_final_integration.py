"""Rendered regressions for the final integration review."""
import uuid

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from game.models import Action, Character, CharacterAction, CharacterResource, Event, InventoryEntry, Item, MediaAsset
from game.test_ui_review import Controls, ui_master
from game.test_web import joined
from game.test_archives import archive

pytestmark = pytest.mark.django_db


def catalog():
    icon = MediaAsset.objects.create(digest='f'*64, path='f'*64+'.png', width=256, height=256)
    item = Item.objects.create(name='Potion', description='Full item description', tags=['healing'], icon=icon)
    action = Action.objects.create(name='<Fire & Ice>', description='Full action description', tags=['magic'], icon=icon, resource='spell_slot', slot_level=2)
    return item, action


def test_activity_history_renders_quantities_zero_health_and_live_links(ui_master):
    item, _ = catalog()
    hero = Character.objects.create(name='Hero', character_class='Mage', health_current=19)
    player = joined(hero)
    InventoryEntry.objects.create(character=hero, item=item, quantity=4)
    assert player.post(reverse('game:remove_item', args=[hero.pk, item.pk]), {'quantity':3, 'request_id':uuid.uuid4()}).status_code == 302
    for value in (0, 12):
        assert player.post(reverse('game:health', args=[hero.pk]), {'health_current':value, 'request_id':uuid.uuid4()}).status_code == 302
    item.name = 'Live potion'; item.save()
    for browser, route, args in ((ui_master, 'master', []), (player, 'player', [hero.pk])):
        html = browser.get(reverse('game:'+route, args=args)).content.decode()
        assert 'в количестве 3' in html
        assert 'Здоровье: 19 → 0' in html
        assert 'Здоровье: 0 → 12' in html
        assert 'Live potion' in html
        assert reverse('game:card', args=['item', item.pk]) in html


def test_editor_icon_and_name_preview_links_preserve_bound_controls(ui_master):
    item, action = catalog()
    hero = Character.objects.create(name='Editor', character_class='Mage')
    for route, args in (('character_new', []), ('character_edit', [hero.pk])):
        response = ui_master.post(reverse('game:'+route, args=args), {'name':'Draft', 'revision':hero.revision, f'action_{action.pk}':'on', f'item_{item.pk}':'7'})
        assert response.status_code == 422
        elements = Controls(response.content.decode()).elements
        for kind, obj in (('item', item), ('action', action)):
            url = reverse('game:card', args=[kind, obj.pk])+'?preview=1'
            links = [attrs for tag, attrs in elements if tag == 'a' and attrs.get('hx-get') == url]
            assert len(links) == 2
            assert all(attrs.get('hx-target') == '#dialog-content' and 'data-dialog' in attrs for attrs in links)
            field = next(attrs for tag, attrs in elements if tag == 'input' and attrs.get('name') == f'{kind}_{obj.pk}')
            assert any(attrs.get('for') == field['id'] for tag, attrs in elements if tag == 'label')
            if kind == 'item':
                assert field.get('value') == '7'
            else:
                assert 'checked' in field
        assert response.context['form']['name'].value() == 'Draft'


def test_readonly_preview_is_master_only_and_regular_cards_keep_controls(ui_master):
    item, action = catalog()
    hero = Character.objects.create(name='Reader', character_class='Mage')
    player = joined(hero)
    CharacterAction.objects.create(character=hero, action=action)
    for kind, obj in (('item', item), ('action', action)):
        url = reverse('game:card', args=[kind, obj.pk])
        preview = ui_master.get(url, {'preview':'1'})
        assert preview.status_code == 200
        html = preview.content.decode()
        assert obj.description in html and obj.tags[0] in html
        assert not any(tag == 'form' for tag, attrs in Controls(html).elements)
        assert 'Редактировать' not in html and 'Отправить' not in html and 'Удалить' not in html
        regular = ui_master.get(url).content.decode()
        assert 'Редактировать' in regular and 'Отправить' in regular and 'Удалить' in regular
        assert Client().get(url, {'preview':'1'}).status_code == 302
        assert player.get(url, {'preview':'1'}).status_code == 302
        assert player.get(url, {'preview':'1', 'character':hero.pk}).status_code == 302


def test_higher_slot_offer_names_escaped_action_and_retains_consent(ui_master):
    _, action = catalog()
    hero = Character.objects.create(name='Caster', character_class='Mage', can_cast=True)
    player = joined(hero)
    CharacterAction.objects.create(character=hero, action=action)
    stock = CharacterResource.objects.create(character=hero, kind='spell_slot', level=4, current=1, maximum=1)
    key = uuid.uuid4()
    response = player.post(reverse('game:apply_action', args=[hero.pk, action.pk]), {'request_id':key}, HTTP_HX_REQUEST='true')
    assert response.status_code == 409
    html = response.content.decode()
    assert '&lt;Fire &amp; Ice&gt;' in html and action.name not in html
    fields = {attrs['name']:attrs.get('value') for tag, attrs in Controls(html).elements if tag == 'input' and 'name' in attrs}
    assert fields['proposal'] == response.context['result']['proposal']
    assert fields['request_id'] == str(key) and fields['confirmed_level'] == '4'
    assert '<strong>2</strong>' in html and '<strong>4</strong>' in html
    stock.refresh_from_db(); assert stock.current == 1


def upload_review(master, content):
    response = master.post(reverse('game:import_upload'), {'archive':SimpleUploadedFile('data.zip', content)})
    assert response.status_code == 302
    return master.get(response.url)


def test_archive_summary_classifies_groups_and_bulk_controls(ui_master, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path/'media'; settings.IMPORT_ROOT = tmp_path/'imports'
    Event.objects.create(name='Exact', description='Same', color='red')
    Event.objects.create(name='Conflict', description='Old', color='red')
    def row(id, name, description):
        return dict(id=id, name=name, description=description, tags=[], color='red')
    content = archive([row('1','Exact','Same'), row('2','Conflict','First'), row('3','Conflict','Second'), row('4','New','New'), row('5','New conflict','First'), row('6','New conflict','Second')]).getvalue()
    response = upload_review(ui_master, content)
    assert response.status_code == 200
    assert response.context['counts'] == {'new':1, 'exact':1, 'conflict':2}
    html = response.content.decode()
    assert 'Новых: 1. Совпадающих: 1. Конфликтов: 2.' in html
    controls = Controls(html).elements
    buttons = {attrs.get('data-import-bulk') for tag, attrs in controls if tag == 'button'}
    assert {'keep', 'incoming'} <= buttons
    # Default existing/first selection and all candidates remain individually available.
    conflicts = response.context['conflicts']
    for conflict in conflicts:
        assert conflict['candidates'][0]['selected']
    choices = {c['key']:next(v['value'] for v in c['candidates'] if v['value'].startswith('incoming_')) for c in conflicts}
    # A per-group override after bulk selection must survive server validation.
    old = next(c for c in conflicts if c['label'] == 'Conflict')
    choices[old['key']] = 'existing'
    committed = ui_master.post(reverse('game:import_commit', args=[response.context['job'].pk]), {'choice_'+k:v for k,v in choices.items()})
    assert committed.status_code == 302
    assert Event.objects.get(name='Conflict').description == 'Old'
    assert Event.objects.get(name='New conflict').description == 'First'
    same = upload_review(ui_master, ui_master.get(reverse('game:export')).content)
    assert same.context['counts'] == {'new':0, 'exact':4, 'conflict':0}
    assert 'Все новые записи готовы к добавлению' not in same.content.decode()


def test_signed_modifiers_outside_int32_roundtrip_through_http(ui_master, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path/'media'; settings.IMPORT_ROOT = tmp_path/'imports'
    hero = Character.objects.create(name='Signed', character_class='Mage')
    for attr, value in (('abilities',2**40), ('saves',-2**40), ('skills',2**40)):
        setattr(hero, attr, {key:value for key in getattr(hero, attr)})
    hero.full_clean(); hero.save()
    exported = ui_master.get(reverse('game:export')).content
    hero.abilities = {key:0 for key in hero.abilities}; hero.save()
    response = upload_review(ui_master, exported)
    assert response.status_code == 200
    conflict, = response.context['conflicts']
    assert ui_master.post(reverse('game:import_commit', args=[response.context['job'].pk]), {'choice_'+conflict['key']:'incoming_0'}).status_code == 302
    hero.refresh_from_db()
    assert set(hero.abilities.values()) == {2**40}
    assert set(hero.saves.values()) == {-2**40}
    assert set(hero.skills.values()) == {2**40}
