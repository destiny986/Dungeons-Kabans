"""Private, transaction-owned reversal data, independent of chat retention."""
from django.core.validators import MaxValueValidator
from django.db.models import F

from .models import (Action, Character, CharacterAction, CharacterResource,
                     ChatMessage, InventoryEntry, Item, UndoEntry)

HISTORY_LIMIT = 10


class UndoConflict(Exception):
    pass


def change(character, kind, **values):
    """Capture an effect immediately after the operation's single revision bump."""
    return {'character': str(character.pk), 'kind': kind,
            'before_revision': character.revision - 1, 'after_revision': character.revision,
            'generation': character.invitation_generation, **values}


def record(operation, kind, description, effects, message_ids):
    UndoEntry.objects.create(operation=operation, kind=kind, description=description,
                             payload={'effects': effects, 'message_ids': message_ids})
    expired = list(UndoEntry.objects.order_by('-pk').values_list('pk', flat=True)[HISTORY_LIMIT:])
    if expired:
        UndoEntry.objects.filter(pk__in=expired).delete()


def _maximum(model, field):
    return min(v.limit_value for v in model._meta.get_field(field).validators
               if isinstance(v, MaxValueValidator))


def _validate(effect):
    character = Character.objects.filter(pk=effect['character']).first()
    matches = (character is not None and character.is_active
               and character.revision == effect['after_revision']
               and character.invitation_generation == effect['generation'])
    # A resource-free use has only a log to erase. Never bridge a later edit.
    if effect['kind'] == 'log' and not matches:
        return None
    if not matches:
        raise UndoConflict('Персонаж изменён, удалён или его доступ отозван. Отмена недоступна.')
    if character.revision >= _maximum(Character, 'revision'):
        raise UndoConflict('Достигнут предел версии персонажа. Отмена недоступна.')
    kind = effect['kind']
    if kind == 'inventory':
        if not Item.objects.filter(pk=effect['object']).exists():
            raise UndoConflict('Предмет удалён из каталога. Отмена недоступна.')
        row = InventoryEntry.objects.filter(character=character, item_id=effect['object']).first()
        if (row.quantity if row else 0) != effect['after']:
            raise UndoConflict('Количество предметов изменилось. Отмена недоступна.')
        if not 0 <= effect['before'] <= _maximum(InventoryEntry, 'quantity'):
            raise UndoConflict('Количество предметов вне допустимого диапазона.')
    elif kind == 'membership':
        if (not Action.objects.filter(pk=effect['object']).exists()
                or not CharacterAction.objects.filter(character=character, action_id=effect['object']).exists()):
            raise UndoConflict('Действие удалено или владение изменилось. Отмена недоступна.')
    elif kind == 'resource':
        if not Action.objects.filter(pk=effect['object']).exists():
            raise UndoConflict('Действие удалено из каталога. Отмена недоступна.')
        row = CharacterResource.objects.filter(pk=effect['resource_id'], character=character,
            kind=effect['resource'], level=effect['level']).first()
        if (row is None or row.current != effect['after'] or row.maximum != effect['maximum']
                or not 0 <= effect['before'] <= row.maximum):
            raise UndoConflict('Расходуемый ресурс изменён или удалён. Отмена недоступна.')
    return character


def _advance_previous(entry, effect, revision):
    """Rebase only the nearest preceding effect with proven revision continuity."""
    for previous in UndoEntry.objects.filter(pk__lt=entry.pk).order_by('-pk'):
        for older in previous.payload['effects']:
            if older['character'] != effect['character']:
                continue
            if (older['after_revision'] == effect['before_revision']
                    and older['generation'] == effect['generation']):
                older['after_revision'] = revision
                previous.save(update_fields=['payload'])
            return


def restore(entry):
    # Validate the complete multi-recipient reversal before changing any row.
    prepared = [(effect, _validate(effect)) for effect in entry.payload['effects']]
    for effect, character in prepared:
        if character is None:
            continue
        kind = effect['kind']
        if kind == 'inventory':
            query = InventoryEntry.objects.filter(character=character, item_id=effect['object'])
            if effect['before'] == 0:
                query.delete()
            else:
                InventoryEntry.objects.update_or_create(character=character, item_id=effect['object'],
                                                        defaults={'quantity': effect['before']})
        elif kind == 'membership':
            CharacterAction.objects.filter(character=character, action_id=effect['object']).delete()
        elif kind == 'resource':
            CharacterResource.objects.filter(pk=effect['resource_id']).update(current=effect['before'])
        Character.objects.filter(pk=character.pk).update(revision=F('revision') + 1)
        _advance_previous(entry, effect, character.revision + 1)
    ChatMessage.objects.filter(pk__in=entry.payload['message_ids']).delete()
    entry.delete()
