from django.db.models.signals import pre_delete, post_delete
from django.dispatch import receiver
from .models import Item, Action, Character, ChatMessage
from .media import release_assets


@receiver(pre_delete, sender=Character, dispatch_uid='game.character.capture_recipients')
def capture_recipient_messages(sender, instance, **kwargs):
    instance._deleted_recipient_messages = list(instance.received_messages.values_list('pk', flat=True))


@receiver(post_delete, sender=Item, dispatch_uid='game.item.media_cleanup')
@receiver(post_delete, sender=Action, dispatch_uid='game.action.media_cleanup')
@receiver(post_delete, sender=Character, dispatch_uid='game.character.media_cleanup')
def release_deleted_media(sender, instance, **kwargs):
    release_assets([instance.portrait_id if sender is Character else instance.icon_id])
    if sender is Character:
        ChatMessage.objects.filter(pk__in=getattr(instance, '_deleted_recipient_messages', []), recipients__isnull=True).delete()
