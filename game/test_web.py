import uuid
import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from game.models import Character, Event, ChatMessage

pytestmark = pytest.mark.django_db

@pytest.fixture(autouse=True)
def web_settings(settings):
    settings.STORAGES = {'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}

@pytest.fixture
def master(client):
    get_user_model().objects.create_user(username='master', password='secret')
    assert client.post(reverse('game:master_login'), {'password':'secret'}).status_code == 302
    return client

def joined(character):
    from game.access import issue_invitation
    player = Client()
    token, _ = issue_invitation(character)
    assert player.get(reverse('game:join', args=[token])).status_code == 302
    character.refresh_from_db()
    return player

def test_master_and_player_boundaries(master):
    hero = Character.objects.create(name='Hero', character_class='Mage')
    player = joined(hero)
    assert master.get(reverse('game:master')).status_code == 200
    assert master.get(reverse('game:player', args=[hero.pk])).status_code == 302
    response = player.get(reverse('game:player', args=[hero.pk]))
    assert response.status_code == 200
    assert len(response.context['ability_rows']) == 6
    assert len(response.context['skill_rows']) == 18
    poll = player.get(reverse('game:player_poll', args=[hero.pk]))
    assert b'hx-swap-oob' in poll.content and b'<!doctype' not in poll.content.lower()
    assert player.get(reverse('game:data')).status_code == 302

def test_catalog_crud_filters_stale_and_csrf(master):
    new = reverse('game:catalog_new', args=['event'])
    assert master.post(new, {'name':'Storm','description':'Rain','color':'blue','tags':'weather'}).status_code == 302
    event = Event.objects.get()
    edit = reverse('game:catalog_edit', args=['event',event.pk])
    assert master.post(edit, {'name':'Storm','description':'Wind','color':'blue','revision':0}).status_code in (409,422)
    poll = master.get(reverse('game:master_poll'), {'events_filter':'RAIN'})
    assert b'Storm' in poll.content
    assert b'grid-stack-item' not in poll.content
    strict = Client(enforce_csrf_checks=True)
    assert strict.post(new, {'name':'bad'}).status_code == 403
    assert master.get(reverse('game:catalog_delete',args=['event',event.pk])).status_code == 405

def test_private_chat_health_cards_and_wipe(master):
    a = Character.objects.create(name='A',character_class='Mage',health_current=10)
    b = Character.objects.create(name='B',character_class='Mage')
    player = joined(a); other = joined(b)
    assert master.post(reverse('game:chat_send'), {'recipient_id':a.pk,'text':'Private hello','request_id':uuid.uuid4()}).status_code == 302
    assert b'Private hello' in player.get(reverse('game:player',args=[a.pk])).content
    assert b'Private hello' not in other.get(reverse('game:player',args=[b.pk])).content
    url = reverse('game:health',args=[a.pk])
    before = ChatMessage.objects.count()
    assert player.post(url, {'health_current':'-1','request_id':uuid.uuid4()}).status_code == 422
    a.refresh_from_db(); assert a.health_current == 10
    assert player.post(url, {'health_current':'10','request_id':uuid.uuid4()}).status_code == 302
    assert ChatMessage.objects.count() == before
    assert master.post(reverse('game:wipe'), {}).status_code == 422
    assert Character.objects.count() == 2
    assert master.get(reverse('game:card',args=['nonsense',a.pk])).status_code == 404
    assert player.get(reverse('game:card',args=['character',a.pk])).status_code == 302

def png_upload():
    import io
    from PIL import Image
    from django.core.files.uploadedfile import SimpleUploadedFile
    stream = io.BytesIO(); Image.new('RGBA',(4,4),'red').save(stream,format='PNG')
    return SimpleUploadedFile('icon.png',stream.getvalue(),content_type='image/png')

def test_full_http_catalog_character_operations_archive(master,settings,tmp_path):
    from game.constants import ABILITY_CHOICES,SKILL_CHOICES
    from game.models import Item,Action,CharacterResource,InventoryEntry
    settings.MEDIA_ROOT = tmp_path/'media'; settings.IMPORT_ROOT = tmp_path/'imports'
    for kind in ('item','action'):
        data = {'name':kind,'description':'Live description','icon':png_upload()}
        if kind == 'action': data.update(activation='action',resource='spell_slot',slot_level=2)
        assert master.post(reverse('game:catalog_new',args=[kind]),data).status_code == 302
    item = Item.objects.get(); action = Action.objects.get()
    data = {'name':'Full Hero','character_class':'Mage','portrait':png_upload(), 'health_current':10,'health_max':20,'speed':30,'armor_class':15,'proficiency_bonus':2,'can_cast':'on','spellcasting_ability':'int','spell_save_dc':14,'spell_attack_bonus':6}
    for key,_ in ABILITY_CHOICES:
        data['ability_'+key]=-1; data['save_'+key]=2
    for key,_ in SKILL_CHOICES: data['skill_'+key]=3
    for n in range(1,8): data.update({f'slot_{n}_current':1 if n in (2,4) else 0,f'slot_{n}_maximum':1 if n in (2,4) else 0})
    data.update(weapon_1_name='Staff',weapon_1_attack='+2',weapon_1_damage='1d6')
    assert master.post(reverse('game:character_new'),data).status_code == 302
    hero = Character.objects.get(); player = joined(hero)
    for kind,obj in [('item',item),('action',action),('event',Event.objects.create(name='News',description='Private event',color='red'))]:
        assert master.post(reverse('game:catalog_send',args=[kind,obj.pk]),{'recipients':[hero.pk],'request_id':uuid.uuid4()}).status_code == 302
        response = player.get(reverse('game:card',args=[kind,obj.pk]),{'character':hero.pk})
        assert response.status_code == 200 and response.context['is_master'] is False
    apply = reverse('game:apply_action',args=[hero.pk,action.pk])
    assert player.post(apply,{'request_id':uuid.uuid4()}).status_code == 302
    key=uuid.uuid4()
    offer=player.post(apply,{'request_id':key})
    assert offer.status_code == 409
    assert offer.context['result']['offered_level'] == 4
    assert str(offer.context['request_id']) == str(key)
    confirm=player.post(apply,{'request_id':key,'confirmed_level':4,'proposal':offer.context['result']['proposal']})
    assert confirm.status_code == 302
    assert CharacterResource.objects.get(character=hero,kind='spell_slot',level=4).current == 0
    assert player.post(reverse('game:remove_item',args=[hero.pk,item.pk]),{'request_id':uuid.uuid4(),'quantity':1}).status_code == 302
    assert not InventoryEntry.objects.filter(character=hero).exists()
    assert player.post(reverse('game:chat_read',args=[hero.pk])).status_code == 302
    assert player.get(reverse('game:player',args=[hero.pk])).context['unread'] == 0
    archive=master.get(reverse('game:export'))
    assert archive.status_code == 200 and archive['Content-Type']=='application/zip'
    from django.core.files.uploadedfile import SimpleUploadedFile
    response=master.post(reverse('game:import_upload'),{'archive':SimpleUploadedFile('data.zip',archive.content)})
    assert response.status_code==302
    review=master.get(response.url); assert review.status_code == 200
    job=review.context['job']
    stranger=Client(); stranger.post(reverse('game:master_login'),{'password':'secret'})
    assert stranger.get(response.url).status_code == 404
    assert stranger.post(reverse('game:import_cancel',args=[job.pk])).status_code == 404
    assert master.post(reverse('game:import_commit',args=[job.pk])).status_code == 302
    assert master.get(reverse('game:card',args=['character',hero.pk])).status_code == 200
    assert master.post(reverse('game:revoke',args=[hero.pk])).status_code == 302
    revoked=player.get(reverse('game:player_poll',args=[hero.pk]),HTTP_HX_REQUEST='true')
    assert revoked.status_code == 200 and revoked['HX-Redirect']==reverse('game:index')

def test_live_chat_cards_authorization_stale_form_and_request_ids(master):
    a=Character.objects.create(name='Owner',character_class='Mage')
    b=Character.objects.create(name='Other',character_class='Mage')
    player=joined(a); joined(b)
    event=Event.objects.create(name='Old name',description='old text',color='red')
    card=reverse('game:card',args=['event',event.pk])
    assert player.get(card,{'character':a.pk}).status_code==404
    assert player.get(card,{'character':b.pk}).status_code==302
    assert master.get(card,{'character':a.pk}).status_code==302
    assert master.get(card,{'character':'not-uuid'}).status_code==404
    assert master.post(reverse('game:catalog_send',args=['event',event.pk]),{'recipients':a.pk,'request_id':uuid.uuid4()}).status_code==302
    revision=event.revision
    event.name='New name'; event.description='updated text'; event.revision+=1; event.save()
    poll=player.get(reverse('game:player_poll',args=[a.pk]))
    assert b'New name' in poll.content and b'Old name' not in poll.content
    edit=master.post(reverse('game:catalog_edit',args=['event',event.pk]),{'name':'Attempt','description':'text','color':'red','revision':revision})
    assert edit.status_code==409 and edit.context['form'].is_bound
    before=ChatMessage.objects.count()
    bad=player.post(reverse('game:health',args=[a.pk]),{'health_current':1,'request_id':'malformed'})
    assert bad.status_code==422 and ChatMessage.objects.count()==before
    assert master.post(reverse('game:catalog_delete',args=['event',event.pk])).status_code==302
    assert b'New name' not in player.get(reverse('game:player_poll',args=[a.pk])).content

def test_template_reverse_names_are_registered(master):
    import re
    from pathlib import Path
    from django.urls import get_resolver
    registered={pattern.name for pattern in get_resolver().namespace_dict['game'][1].url_patterns}
    referenced=set()
    for path in Path('game/templates/game').glob('*.html'):
        referenced.update(re.findall(r"{% url 'game:([^']+)'",path.read_text(encoding='utf-8')))
    assert referenced <= registered
    for name,args in [('catalog_new',['item']),('catalog_new',['action']),('character_new',[]),('characters',[]),('data',[])]:
        assert master.get(reverse('game:'+name,args=args)).status_code==200

def test_import_stale_review_and_cancel(master,settings,tmp_path):
    from django.core.files.uploadedfile import SimpleUploadedFile
    from game.models import ImportJob
    settings.MEDIA_ROOT=tmp_path/'media'; settings.IMPORT_ROOT=tmp_path/'imports'
    event=Event.objects.create(name='Archive event',description='Before',color='red')
    archive=master.get(reverse('game:export')).content
    upload=master.post(reverse('game:import_upload'),{'archive':SimpleUploadedFile('data.zip',archive)})
    job=ImportJob.objects.get()
    event.description='Changed during review'; event.save()
    stale=master.post(reverse('game:import_commit',args=[job.pk]))
    assert stale.status_code==409
    event.refresh_from_db(); assert event.description=='Changed during review'
    assert master.get(upload.url).status_code==200
    assert master.post(reverse('game:import_cancel',args=[job.pk])).status_code==302
    job.refresh_from_db(); assert job.status=='cancelled'

def test_large_resources_are_bounded_and_inventory_cards_are_private(master):
    from game.models import CharacterResource
    hero=Character.objects.create(name='Pips',character_class='Mage',can_cast=True)
    player=joined(hero)
    CharacterResource.objects.create(character=hero,kind='spell_slot',level=1,current=1000000,maximum=1000000)
    response=player.get(reverse('game:player',args=[hero.pk]))
    resource=response.context['resources'][0]
    assert len(resource['pips'])==128 and resource['maximum']==1000000
