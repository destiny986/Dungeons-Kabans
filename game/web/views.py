import uuid
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from game.access import master_required, character_required, get_character_grant, has_master_access, access_denied
from game.catalog import CATALOG_MODELS, delete_catalog, delete_character
from game.constants import ABILITY_CHOICES, SKILL_CHOICES, ACTIVATION_CHOICES
from game.forms import CharacterForm, get_catalog_form, StaleEdit
from game.models import Character
from game.validators import normalize_search


def context(**kwargs):
    return {'request_id': uuid.uuid4(), **kwargs}


def catalog_model(kind):
    if kind not in CATALOG_MODELS:
        raise Http404
    return CATALOG_MODELS[kind]


def master_context(request):
    from game.chat import messages_for
    result = context(is_master=True, characters=Character.objects.filter(is_active=True).select_related('portrait').order_by('name'), messages=messages_for(request))
    for kind, plural in [('event','events'),('item','items'),('action','actions')]:
        query = catalog_model(kind).objects.order_by('name')
        if kind != 'event':
            query = query.select_related('icon')
        result[plural] = query.filter(search_text__contains=normalize_search(request.GET.get(plural+'_filter','')))
        result[plural+'_filter'] = request.GET.get(plural+'_filter','')
    return result


def player_context(request, character):
    from game.chat import messages_for, unread_count
    resources = []
    resource_order = {'spell_slot': 0, 'rage': 1, 'wildshape': 2}
    for resource in sorted(character.resources.all(), key=lambda row: (resource_order[row.kind], row.level)):
        enabled = {'spell_slot':character.can_cast,'rage':character.can_rage,'wildshape':character.can_wildshape}[resource.kind]
        if enabled and resource.maximum:
            resources.append({'kind':resource.kind,'level':resource.level,'label':f'Ячейки {resource.level}', 'current':resource.current,'maximum':resource.maximum,'pips':[i < resource.current for i in range(min(resource.maximum,128))]})
    actions = [grant.action for grant in character.action_grants.select_related('action__icon').order_by('action__name')]
    sections = []
    for activation,label in ACTIVATION_CHOICES:
        group = [a for a in actions if a.resource == 'none' and a.activation == activation]
        if group:
            sections.append({'label':label,'actions':group})
    spells = [action for action in actions if action.resource == 'spell_slot']
    if spells:
        sections.append({'label':'Заклинания','actions':spells})
    for kind,label in [('rage','Ярость'),('wildshape','Дикий облик')]:
        group = [a for a in actions if a.resource == kind]
        if group:
            sections.append({'label':label,'actions':group})
    return context(is_master=False, character=character, resources=resources, action_sections=sections,
        ability_rows=[{'label':label,'value':character.abilities[key],'save':character.saves[key]} for key,label in ABILITY_CHOICES],
        skill_rows=[{'label':label,'value':character.skills[key]} for key,label in SKILL_CHOICES],
        inventory=character.inventory.filter(quantity__gt=0).select_related('item__icon').order_by('item__name'),
        weapons=[weapon for weapon in character.weapons if weapon.get('name','').strip()],
        messages=messages_for(request,character.pk), unread=unread_count(request,character.pk))


@require_GET
@master_required
def master(request):
    return render(request,'game/master.html',master_context(request))


@require_GET
@master_required
def master_poll(request):
    return render(request,'game/master_poll.html',master_context(request))


@require_GET
@character_required
def player(request,pk):
    return render(request,'game/player.html',player_context(request,request.character_grant.character))


@require_GET
@character_required
def player_poll(request,pk):
    return render(request,'game/player_poll.html',player_context(request,request.character_grant.character))


@require_GET
@master_required
def characters(request):
    return render(request,'game/character_list.html',context(is_master=True,characters=Character.objects.select_related('portrait').order_by('name')))


def editor(request,kind=None,pk=None):
    model = catalog_model(kind) if kind else Character
    instance = get_object_or_404(model,pk=pk) if pk else None
    form = get_catalog_form(kind,request.POST if request.method == 'POST' else None,request.FILES if request.method == 'POST' else None,instance) if kind else CharacterForm(request.POST if request.method == 'POST' else None,request.FILES if request.method == 'POST' else None,instance)
    status = 200
    if request.method == 'POST':
        if form.is_valid():
            try:
                form.save()
            except StaleEdit as exc:
                form.add_error(None,exc); status = 409
            except OSError:
                form.add_error(None,'Не удалось сохранить изображение. Повторите попытку.'); status = 422
            else:
                return redirect('game:master' if kind else 'game:characters')
        else:
            status = 409 if any('уже изменились' in str(error) for error in form.non_field_errors()) else 422
    return render(request,'game/catalog_form.html' if kind else 'game/character_form.html',context(form=form,kind=kind,object=instance,is_edit=instance is not None,is_master=True),status=status)


@require_http_methods(['GET','POST'])
@master_required
def catalog_edit(request,kind,pk=None):
    return editor(request,kind,pk)


@require_http_methods(['GET','POST'])
@master_required
def character_edit(request,pk=None):
    return editor(request,pk=pk)


def success(request,target='game:master',**kwargs):
    if request.headers.get('HX-Request','').lower() == 'true':
        return HttpResponse(headers={'HX-Trigger':'{"refresh":true,"closeDialog":true}'})
    return redirect(target,**kwargs)


@require_POST
@master_required
def catalog_delete(request,kind,pk):
    get_object_or_404(catalog_model(kind),pk=pk)
    delete_catalog(kind,pk)
    return success(request)


@require_POST
@master_required
def character_delete(request,pk):
    get_object_or_404(Character,pk=pk)
    delete_character(pk)
    return success(request,'game:characters')


@require_GET
def card(request,kind,pk):
    readonly = request.GET.get('preview') == '1'
    if readonly and not has_master_access(request):
        return access_denied(request)
    if kind == 'character':
        if not has_master_access(request):
            return access_denied(request)
        return render(request,'game/active_character.html',context(character=get_object_or_404(Character,pk=pk),is_master=True))
    model = catalog_model(kind)
    obj = get_object_or_404(model,pk=pk)
    character = None
    quantity = 1
    if 'character' in request.GET:
        try:
            char_id = uuid.UUID(request.GET['character'])
        except (ValueError,TypeError,AttributeError):
            raise Http404 from None
        grant = get_character_grant(request,char_id)
        if grant is None:
            return access_denied(request)
        character = grant.character
        owned = kind == 'item' and character.inventory.filter(item=obj).exists() or kind == 'action' and character.action_grants.filter(action=obj).exists()
        received = character.received_messages.filter(**{kind:obj}).exists()
        if not (owned or received):
            raise Http404
        if kind == 'item':
            entry = character.inventory.filter(item=obj).first()
            quantity = entry.quantity if entry else 0
    elif not has_master_access(request):
        return access_denied(request)
    return render(request,'game/card.html',context(kind=kind,object=obj,is_master=character is None,readonly=readonly,character=character,quantity=quantity,active_characters=Character.objects.filter(is_active=True).order_by('name')))


def request_key(request):
    value = request.POST.get('request_id')
    if value is None:
        return uuid.uuid4()
    try:
        return uuid.UUID(value)
    except (ValueError,TypeError,AttributeError):
        raise ValidationError('Некорректный идентификатор запроса.') from None


def operation(request,callback,*,character=None,action=None):
    key = request.POST.get('request_id') or uuid.uuid4()
    try:
        key = request_key(request)
        result = callback(key)
    except PermissionDenied:
        return access_denied(request)
    except ValidationError as exc:
        result = {'status':'error','message':' '.join(exc.messages)}
    status = {'error':422,'stale':409,'offer':409}.get(result.get('status'),200)
    if result.get('status') == 'ok':
        if action is not None and request.headers.get('HX-Request','').lower() == 'true':
            response = render(request,'game/operation_result.html',context(result=result,character=character,action=action))
            response['HX-Trigger'] = '{"refresh":true}'
            return response
        return success(request,'game:player',pk=character.pk) if character else success(request)
    return render(request,'game/operation_result.html',context(result=result,character=character,action=action,request_id=key),status=status)


@require_POST
@character_required
def apply_action(request,pk,action_id):
    from game.operations import apply_action as apply
    action = get_object_or_404(CATALOG_MODELS['action'],pk=action_id)
    return operation(request,lambda key:apply(request,pk,action_id,key,confirmed_level=request.POST.get('confirmed_level'),proposal=request.POST.get('proposal')),character=request.character_grant.character,action=action)


@require_POST
@character_required
def health(request,pk):
    from game.operations import set_health
    return operation(request,lambda key:set_health(request,pk,request.POST.get('health_current'),key),character=request.character_grant.character)


@require_POST
@character_required
def remove_item(request,pk,item_id):
    from game.operations import remove_item as remove
    return operation(request,lambda key:remove(request,pk,item_id,request.POST.get('quantity'),key),character=request.character_grant.character)


@require_POST
@master_required
def catalog_send(request,kind,pk):
    from game.operations import grant_catalog
    get_object_or_404(catalog_model(kind),pk=pk)
    return operation(request,lambda key:grant_catalog(request,kind,pk,request.POST.getlist('recipients'),key))


@require_POST
@master_required
def rest(request):
    from game.operations import rest_characters
    return operation(request,lambda key:rest_characters(request,key))


@require_POST
def chat_send(request):
    from game.operations import send_chat
    # The selected identity determines the required capability, even for mixed-role browsers.
    player_id = request.POST.get('character_id')
    character = None
    if player_id:
        try:
            grant = get_character_grant(request,uuid.UUID(player_id))
        except (ValueError,TypeError,AttributeError):
            raise Http404 from None
        if grant is None:
            return access_denied(request)
        character = grant.character
    elif not has_master_access(request):
        return access_denied(request)
    return operation(request,lambda key:send_chat(request,text=request.POST.get('text',''),character_id=player_id or request.POST.get('recipient_id'),as_master=character is None,request_id=key),character=character)


@require_POST
@character_required
def chat_read(request,pk):
    from game.operations import mark_chat_read
    try:
        result = mark_chat_read(request,pk,through_id=request.POST.get('through_id'))
    except PermissionDenied:
        return access_denied(request)
    if result.get('status') in ('error','stale'):
        return render(request,'game/operation_result.html',context(result=result),status=422)
    return success(request,'game:player',pk=pk)
