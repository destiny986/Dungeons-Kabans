"""Shared fixed-master setup; preserve a valid hash and its browser sessions."""
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.core.management.base import CommandError
from django.db import transaction


@transaction.atomic
def configure_master(password):
    if not isinstance(password, str) or not password:
        raise CommandError('MASTER_PASSWORD must be configured.')
    user, created = get_user_model().objects.get_or_create(username='master')
    # No setter: User.check_password() may upgrade a valid hash and expire sessions.
    if created or not check_password(password, user.password):
        user.set_password(password)
        user.save(update_fields=['password'])
