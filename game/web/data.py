"""Master data pages; imports retain the archive service's session ownership."""
from datetime import date
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from game.access import master_required
from game.models import Character, Event, Item, Action, ImportJob
from .views import context

MODELS = {'events':Event,'items':Item,'actions':Action,'characters':Character}


def data_page(request,error=None,status=200):
    return render(request,'game/data.html',context(is_master=True,error=error,counts=[{'label':{'events':'События','items':'Предметы','actions':'Действия','characters':'Персонажи'}[label],'count':model.objects.count()} for label,model in MODELS.items()]),status=status)


def navigate(request,name,**kwargs):
    if request.headers.get('HX-Request','').lower() == 'true':
        return HttpResponse(headers={'HX-Redirect':reverse(name,kwargs=kwargs)})
    return redirect(name,**kwargs)


def owned_job(request,pk):
    return get_object_or_404(ImportJob,pk=pk,session_key=request.session.session_key)


@require_GET
@master_required
def index(request):
    return data_page(request)


@require_GET
@master_required
def export(request):
    from game.archives import export_archive
    try:
        content = export_archive()
    except (ValidationError,OSError) as exc:
        return data_page(request,' '.join(exc.messages) if isinstance(exc,ValidationError) else 'Не удалось прочитать изображения.',422)
    response = HttpResponse(content,content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="dungeons-kabans-{date.today().isoformat()}.zip"'
    return response


@require_POST
@master_required
def upload(request):
    from game.archives import create_import
    if 'archive' not in request.FILES:
        return data_page(request,'Выберите ZIP-архив.',422)
    try:
        job = create_import(request.FILES['archive'],request.session.session_key)
    except ValidationError as exc:
        return data_page(request,' '.join(exc.messages),422)
    except OSError:
        return data_page(request,'Не удалось сохранить архив.',422)
    return navigate(request,'game:import_review',pk=job.pk)


def preview_page(request,job,status=200,error=None):
    from game.archives import get_preview
    preview = get_preview(job,request.session.session_key)
    for conflict in preview.get('conflicts',[]):
        candidates = conflict['candidates']
        chosen = next((candidate['value'] for candidate in candidates if candidate['value']=='existing'),candidates[0]['value'] if candidates else None)
        for candidate in candidates:
            candidate['selected'] = candidate['value'] == chosen
    if error:
        preview['summary'] = error + ' ' + preview.get('summary','')
    return render(request,'game/import_review.html',context(**preview,job=job,is_master=True),status=status)


@require_GET
@master_required
def review(request,pk):
    job = owned_job(request,pk)
    try:
        return preview_page(request,job)
    except ValidationError as exc:
        return data_page(request,' '.join(exc.messages),422)


@require_POST
@master_required
def commit(request,pk):
    from game.archives import commit_import, StaleImport
    job = owned_job(request,pk)
    choices = {key[7:]:request.POST[key] for key in request.POST if key.startswith('choice_')}
    try:
        commit_import(job.pk,request.session.session_key,choices)
    except StaleImport as exc:
        job.refresh_from_db()
        return preview_page(request,job,409,' '.join(exc.messages))
    except ValidationError as exc:
        return data_page(request,' '.join(exc.messages),422)
    except OSError:
        return data_page(request,'Не удалось сохранить изображения архива.',422)
    return navigate(request,'game:data')


@require_POST
@master_required
def cancel(request,pk):
    from game.archives import cancel_import
    job = owned_job(request,pk)
    try:
        cancel_import(job.pk,request.session.session_key)
    except ValidationError as exc:
        return data_page(request,' '.join(exc.messages),422)
    return navigate(request,'game:data')


@require_POST
@master_required
def wipe(request):
    categories = request.POST.getlist('categories')
    if not categories or any(category not in MODELS for category in categories):
        return data_page(request,'Выберите категории для удаления.',422)
    with transaction.atomic():
        for category in set(categories):
            MODELS[category].objects.all().delete()
    return navigate(request,'game:data')
