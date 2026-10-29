"""Receipt-scoped private chat queries; master access never implies a player grant."""
from django.core.exceptions import PermissionDenied

from .access import get_character_grant, has_master_access
from .models import ChatMessage, MessageRecipient


def messages_for(request, character_id=None):
    if character_id is None:
        if not has_master_access(request):
            raise PermissionDenied
        queryset = ChatMessage.objects.all()
    else:
        grant = get_character_grant(request, character_id)
        if grant is None:
            raise PermissionDenied
        queryset = ChatMessage.objects.filter(recipient_links__character_id=grant.character_id)
    return queryset.select_related('sender_character', 'event', 'item', 'action').prefetch_related(
        'recipients').order_by('id')


def unread_count(request, character_id):
    grant = get_character_grant(request, character_id)
    if grant is None:
        raise PermissionDenied
    return ChatMessage.objects.filter(recipient_links__character_id=grant.character_id,
                                     from_master=True, id__gt=grant.last_read_message_id).count()


def create_message(*, recipients, **fields):
    """Internal write helper. Caller owns the atomic transaction and authorization."""
    message = ChatMessage.objects.create(**fields)
    MessageRecipient.objects.bulk_create([
        MessageRecipient(message=message, character_id=pk) for pk in dict.fromkeys(recipients)])
    return message
