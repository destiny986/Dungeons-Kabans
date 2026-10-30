"""Prepare persistent local data once, then run Django's foreground server."""
import getpass
import os
import sys
import warnings

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.management.commands.runserver import Command as Runserver
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils.autoreload import DJANGO_AUTORELOAD_ENV

from game.management.master import configure_master


class Command(Runserver):
    help = 'Prepare and start the local game (default: 127.0.0.1:8000).'

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            '--noinput', '--no-input', action='store_false', dest='interactive',
            help='Never prompt for the initial master password; use MASTER_PASSWORD.',
        )

    def handle(self, *args, **options):
        if not settings.IS_LOCAL:
            raise CommandError('serve_game is only available with local configuration (DJANGO_ENV=local).')
        super().handle(*args, **options)

    def run(self, **options):
        # Django reexecutes this command on every reload. Preparation belongs
        # to the original process, before the reloader starts its first child.
        if os.environ.get(DJANGO_AUTORELOAD_ENV) != 'true':
            common = {'stdout': self.stdout, 'stderr': self.stderr, 'verbosity': options['verbosity']}
            call_command('migrate', interactive=False, **common)
            password = settings.MASTER_PASSWORD
            if password:
                configure_master(password)
            elif not get_user_model().objects.filter(username='master').exists():
                if not options['interactive'] or not sys.stdin.isatty():
                    raise CommandError('First start requires MASTER_PASSWORD or an interactive terminal without --noinput.')
                configure_master(self.prompt_password())
            call_command('collectstatic', interactive=False, **common)
        super().run(**options)

    @staticmethod
    def prompt_password():
        try:
            # Never allow getpass to fall back to an echoed password.
            with warnings.catch_warnings():
                warnings.simplefilter('error', getpass.GetPassWarning)
                password = getpass.getpass('Master password: ')
                confirmation = getpass.getpass('Confirm master password: ')
        except (EOFError, KeyboardInterrupt, getpass.GetPassWarning):
            raise CommandError('Password input unavailable or cancelled; configure MASTER_PASSWORD.') from None
        if not password:
            raise CommandError('Password must not be blank.')
        if password != confirmation:
            raise CommandError('Password confirmation does not match.')
        return password
