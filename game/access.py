"""Independent browser capabilities; Django's single-user login is not used."""
import hashlib
import uuid
from functools import wraps

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.crypto import constant_time_compare

from .models import Character, CharacterGrant, CharacterInvitation

MASTER_SESSION_KEY = 'master_auth_hash'
INVITATION_SALT = 'game.character-invitation.v1'


def has_master_access(request):
    saved = request.session.get(MASTER_SESSION_KEY)
    if not isinstance(saved, str) or not saved:
        return False
    user = get_user_model().objects.filter(username='master', is_active=True).first()
    valid = user is not None and constant_time_compare(saved, user.get_session_auth_hash())
    if not valid:
        request.session.pop(MASTER_SESSION_KEY, None)
    return valid


def _valid_grants(request):
    if not request.session.session_key:
        return CharacterGrant.objects.none()
    return CharacterGrant.objects.select_related('character', 'invitation').filter(
        session_key=request.session.session_key, revoked=False, character__is_active=True,
        invitation__revoked=False, generation=F('character__invitation_generation'),
        invitation__generation=F('generation'), invitation__character_id=F('character_id'))


def get_character_grant(request, character_id):
    try:
        return _valid_grants(request).filter(character_id=character_id).first()
    except (ValueError, TypeError, ValidationError):
        return None


def roles_for_request(request):
    roles = []
    if has_master_access(request):
        roles.append({'label': 'Мастер', 'url': reverse('game:master')})
    roles.extend({'label': grant.character.name, 'url': reverse('game:player', args=[grant.character_id])}
                 for grant in _valid_grants(request).order_by('character__name', 'character_id'))
    return roles


def access_denied(request):
    if request.headers.get('HX-Request', '').lower() == 'true':
        return HttpResponse(headers={'HX-Redirect': reverse('game:index')})
    return redirect('game:index')


def master_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not has_master_access(request):
            return access_denied(request)
        return view(request, *args, **kwargs)
    return wrapped


def character_required(view=None, *, parameter='pk'):
    def decorate(func):
        @wraps(func)
        def wrapped(request, *args, **kwargs):
            character_id = kwargs.get(parameter, args[0] if args else None)
            grant = get_character_grant(request, character_id)
            if grant is None:
                return access_denied(request)
            request.character_grant = grant
            return func(request, *args, **kwargs)
        return wrapped
    return decorate(view) if view else decorate


def _token(character):
    return signing.Signer(salt=INVITATION_SALT).sign(f'{character.pk}:{character.invitation_generation}')


@transaction.atomic
def issue_invitation(character):
    character = Character.objects.select_for_update().get(pk=character.pk)
    token = _token(character)
    invitation, _ = CharacterInvitation.objects.get_or_create(
        character=character, generation=character.invitation_generation,
        token_digest=hashlib.sha256(token.encode()).hexdigest(), defaults={'revoked': False})
    if invitation.revoked:
        raise ValueError('Invitation is unavailable')
    return token, invitation


class InvalidInvitation(ValueError):
    pass


@transaction.atomic
def redeem_invitation(request, token):
    try:
        if not isinstance(token, str) or len(token) > 256:
            raise ValueError
        value = signing.Signer(salt=INVITATION_SALT).unsign(token)
        character_id, generation = value.split(':')
        character_id, generation = uuid.UUID(character_id), int(generation)
    except (ValueError, TypeError, signing.BadSignature):
        raise InvalidInvitation('Invitation is unavailable') from None
    character = Character.objects.select_for_update().filter(pk=character_id, invitation_generation=generation).first()
    if character is None:
        raise InvalidInvitation('Invitation is unavailable')
    invitation = CharacterInvitation.objects.filter(character=character, generation=generation, revoked=False,
        token_digest=hashlib.sha256(token.encode()).hexdigest()).first()
    if invitation is None:
        raise InvalidInvitation('Invitation is unavailable')
    if not request.session.session_key:
        request.session.create()
    grant, _ = CharacterGrant.objects.update_or_create(session_key=request.session.session_key, character=character,
        defaults={'invitation': invitation, 'generation': generation, 'revoked': False})
    if not character.is_active:
        character.is_active = True
        character.save(update_fields=['is_active'])
    return grant


@transaction.atomic
def revoke_character(character):
    character = Character.objects.select_for_update().get(pk=character.pk)
    character.invitations.update(revoked=True)
    character.browser_grants.update(revoked=True)
    character.is_active = False
    character.invitation_generation += 1
    character.save(update_fields=['is_active', 'invitation_generation'])
