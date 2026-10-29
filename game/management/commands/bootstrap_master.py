from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = 'Create/update the fixed master account from MASTER_PASSWORD without exposing it.'

    @transaction.atomic
    def handle(self, *args, **options):
        password = settings.MASTER_PASSWORD
        if not isinstance(password, str) or not password:
            raise CommandError('MASTER_PASSWORD must be configured.')
        user, created = get_user_model().objects.get_or_create(username='master')
        # No setter: User.check_password() may upgrade a valid hash and expire sessions.
        if created or not check_password(password, user.password):
            user.set_password(password)
            user.save(update_fields=['password'])
        self.stdout.write(self.style.SUCCESS('Master account configured.'))
