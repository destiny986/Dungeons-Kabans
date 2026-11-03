import json

from django import template

from game.constants import ACTIVATION_CHOICES, ACTIVATION_SYMBOLS, ACTION_RESOURCE_CHOICES

register = template.Library()


@register.filter
def activation_symbol(activation):
    return ACTIVATION_SYMBOLS.get(activation, '')


@register.inclusion_tag('game/_action_meta.html')
def action_metadata(activation, resource, slot_level=None, preview=False):
    return {
        'activation': activation, 'resource': resource, 'slot_level': slot_level,
        'activation_label': dict(ACTIVATION_CHOICES).get(activation, ''),
        'resource_label': dict(ACTION_RESOURCE_CHOICES).get(resource, ''),
        'activation_symbol': activation_symbol(activation),
        'activation_symbols': json.dumps(ACTIVATION_SYMBOLS, ensure_ascii=False),
        'preview': preview,
    }
