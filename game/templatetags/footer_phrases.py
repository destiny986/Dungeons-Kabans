import random

from django import template
from django.conf import settings

register = template.Library()

FALLBACK_PHRASE = 'Броски решают многое. Историю создаёте вы.'


@register.simple_tag
def footer_phrase():
    try:
        phrases = [line.strip() for line in
                   (settings.BASE_DIR / 'phrases.txt').read_text(encoding='utf-8-sig').splitlines()
                   if line.strip()]
    except (OSError, UnicodeError):
        return FALLBACK_PHRASE
    return random.choice(phrases) if phrases else FALLBACK_PHRASE
