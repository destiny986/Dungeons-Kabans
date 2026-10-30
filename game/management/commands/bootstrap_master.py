from django.conf import settings
from django.core.management.base import BaseCommand

from game.management.master import configure_master


class Command(BaseCommand):
    help = 'Create/update the fixed master account from MASTER_PASSWORD without exposing it.'

    def handle(self, *args, **options):
        configure_master(settings.MASTER_PASSWORD)
        self.stdout.write(self.style.SUCCESS('Master account configured.'))
