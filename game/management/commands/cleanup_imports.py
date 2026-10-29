from django.core.management.base import BaseCommand
from game.archives import cleanup_imports


class Command(BaseCommand):
    help = 'Удалить временные данные импорта старше 24 часов.'

    def handle(self, *args, **options):
        self.stdout.write(f'Удалено заданий: {cleanup_imports()}')
