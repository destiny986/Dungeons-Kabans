from django.db import transaction
from .models import Event, Item, Action, Character, ChatMessage

CATALOG_MODELS = {'event': Event, 'item': Item, 'action': Action}


def delete_catalog(kind, pk):
    with transaction.atomic():
        CATALOG_MODELS[kind].objects.get(pk=pk).delete()


def delete_character(pk):
    with transaction.atomic():
        character = Character.objects.get(pk=pk)
        received = list(character.received_messages.values_list('pk', flat=True))
        character.delete()
        ChatMessage.objects.filter(pk__in=received, recipients__isnull=True).delete()
