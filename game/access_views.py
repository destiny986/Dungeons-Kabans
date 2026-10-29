import base64
import io
from pathlib import Path

import qrcode
from django import forms
from django.conf import settings
from django.contrib.auth import authenticate
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from .access import (MASTER_SESSION_KEY, InvalidInvitation, _valid_grants, has_master_access,
                     issue_invitation, master_required, redeem_invitation, revoke_character, roles_for_request)
from .models import Character, MediaAsset


class MasterLoginForm(forms.Form):
    password = forms.CharField(label='Пароль мастера', widget=forms.PasswordInput)


@require_GET
def index(request):
    return render(request, 'game/index.html', {'roles': roles_for_request(request), 'form': MasterLoginForm()})


@require_POST
def master_login(request):
    form = MasterLoginForm(request.POST)
    if form.is_valid():
        user = authenticate(request, username='master', password=form.cleaned_data['password'])
        if user is not None and user.is_active:
            # Rotate against fixation and migrate every independent browser capability.
            from .models import CharacterGrant
            old_key = request.session.session_key
            request.session.cycle_key()
            if old_key:
                CharacterGrant.objects.filter(session_key=old_key).update(session_key=request.session.session_key)
            request.session[MASTER_SESSION_KEY] = user.get_session_auth_hash()
            return redirect('game:master')
        form.add_error('password', 'Неверный пароль.')
    return render(request, 'game/index.html', {'roles': roles_for_request(request), 'form': form}, status=400)


def _sensitive(response):
    response['Cache-Control'] = 'no-store'
    response['Referrer-Policy'] = 'no-referrer'
    return response


@require_GET
def join(request, token):
    try:
        grant = redeem_invitation(request, token)
    except InvalidInvitation:
        return _sensitive(HttpResponse('Приглашение недействительно.', status=404))
    return _sensitive(redirect('game:player', pk=grant.character_id))


@require_POST
@master_required
def invite(request, pk):
    character = get_object_or_404(Character, pk=pk)
    token, _ = issue_invitation(character)
    relative = reverse('game:join', args=[token])
    base = settings.PUBLIC_BASE_URL.rstrip('/')
    link = base + relative if base else request.build_absolute_uri(relative)
    png = io.BytesIO()
    qrcode.make(link).save(png, format='PNG')
    return _sensitive(render(request, 'game/invitation.html', {'character': character, 'link': link,
        'qr_data_uri': 'data:image/png;base64,' + base64.b64encode(png.getvalue()).decode('ascii')}))


@require_POST
@master_required
def revoke(request, pk):
    revoke_character(get_object_or_404(Character, pk=pk))
    if request.headers.get('HX-Request', '').lower() == 'true':
        return HttpResponse(headers={'HX-Trigger': '{"refresh":true,"closeDialog":true}'})
    return redirect('game:index')


@require_GET
def protected_media(request, digest):
    asset = get_object_or_404(MediaAsset, digest=digest)
    if not has_master_access(request):
        characters = _valid_grants(request).values_list('character_id', flat=True)
        allowed = MediaAsset.objects.filter(pk=asset.pk).filter(
            Q(portraits__id__in=characters) | Q(items__inventory_entries__character_id__in=characters) |
            Q(actions__character_grants__character_id__in=characters) |
            Q(items__chat_messages__recipients__id__in=characters) |
            Q(actions__chat_messages__recipients__id__in=characters) |
            Q(items__chat_messages__sender_character_id__in=characters) |
            Q(actions__chat_messages__sender_character_id__in=characters)).exists()
        if not allowed:
            raise Http404
    try:
        root = Path(settings.MEDIA_ROOT).resolve()
        path = (root / asset.path).resolve()
        if not path.is_relative_to(root) or not path.is_file() or asset.path != f'{asset.digest}.png':
            raise Http404
        if settings.DEBUG:
            response = FileResponse(path.open('rb'), content_type='image/png')
        else:
            response = HttpResponse(content_type='image/png', headers={'X-Accel-Redirect': f'/_protected_media/{asset.digest}.png'})
    except (OSError, ValueError, RuntimeError):
        raise Http404 from None
    response['Cache-Control'] = 'private, no-store'
    return response
