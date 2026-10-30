"""Local startup contracts; only the blocking HTTP server is replaced."""
import io
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.contrib.staticfiles.management.commands.runserver import Command as Runserver
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.migrations.recorder import MigrationRecorder

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def startup(settings, tmp_path, monkeypatch):
    settings.IS_LOCAL = True
    settings.MASTER_PASSWORD = ''
    settings.STATIC_ROOT = tmp_path / 'static'
    monkeypatch.delenv('RUN_MAIN', raising=False)
    server = Mock()
    monkeypatch.setattr(Runserver, 'run', lambda command, **options: server(command, **options))
    return server


def launch(*args, **kwargs):
    output = io.StringIO()
    call_command('serve_game', *args, stdout=output, stderr=output, verbosity=0, **kwargs)
    return output.getvalue()


def test_first_start_prepares_database_static_and_master(startup, settings):
    settings.MASTER_PASSWORD = 'first-start-secret'
    output = launch('--noreload', '--noinput')
    assert get_user_model().objects.get(username='master').check_password('first-start-secret')
    assert MigrationRecorder.Migration.objects.filter(app='game').exists()
    assert (settings.STATIC_ROOT / 'staticfiles.json').exists()
    assert 'first-start-secret' not in output
    startup.assert_called_once()
    assert startup.call_args.kwargs['use_reloader'] is False
    command = startup.call_args.args[0]
    assert (command.addr, command.port) == ('127.0.0.1', '8000')


def test_existing_master_hash_and_session_survive_without_password(startup):
    user = get_user_model().objects.create_user(username='master', password='existing-secret')
    encoded = user.password
    session = Session.objects.create(session_key='existing-session', session_data='unchanged', expire_date='2099-01-01T00:00Z')
    launch('--noinput')
    user.refresh_from_db()
    assert user.password == encoded
    assert Session.objects.get(pk=session.pk).session_data == 'unchanged'
    startup.assert_called_once()


def test_explicit_password_reuses_bootstrap_hash_semantics(startup, settings):
    settings.MASTER_PASSWORD = 'supplied-secret'
    launch('--noinput')
    user = get_user_model().objects.get(username='master')
    encoded = user.password
    launch('--noinput')
    user.refresh_from_db()
    assert user.password == encoded
    settings.MASTER_PASSWORD = 'replacement-secret'
    launch('--noinput')
    user.refresh_from_db()
    assert user.password != encoded and user.check_password('replacement-secret')


@pytest.mark.parametrize('noinput,tty', [(True, True), (False, False)])
def test_missing_password_never_prompts_noninteractively(startup, monkeypatch, noinput, tty):
    monkeypatch.setattr('sys.stdin.isatty', lambda: tty)
    prompt = Mock(side_effect=AssertionError('must not prompt'))
    monkeypatch.setattr('getpass.getpass', prompt)
    with pytest.raises(CommandError, match='MASTER_PASSWORD'):
        launch(interactive=not noinput)
    assert not get_user_model().objects.filter(username='master').exists()
    startup.assert_not_called()
    prompt.assert_not_called()


def test_first_start_prompts_privately_and_confirms(startup, monkeypatch):
    monkeypatch.setattr('sys.stdin.isatty', lambda: True)
    prompt = Mock(side_effect=['private-secret', 'private-secret'])
    monkeypatch.setattr('getpass.getpass', prompt)
    output = launch()
    assert prompt.call_count == 2
    assert get_user_model().objects.get(username='master').check_password('private-secret')
    assert 'private-secret' not in output


@pytest.mark.parametrize('answers', [('', ''), ('one-secret', 'different-secret')])
def test_blank_or_mismatched_confirmation_fails_without_master(startup, monkeypatch, answers):
    monkeypatch.setattr('sys.stdin.isatty', lambda: True)
    monkeypatch.setattr('getpass.getpass', Mock(side_effect=answers))
    with pytest.raises(CommandError, match='[Pp]assword'):
        launch()
    assert not get_user_model().objects.filter(username='master').exists()
    startup.assert_not_called()


def test_production_is_refused_before_any_preparation(startup, settings, monkeypatch):
    prepare = Mock(side_effect=AssertionError('must not prepare production'))
    monkeypatch.setattr('game.management.commands.serve_game.call_command', prepare)
    settings.IS_LOCAL = False
    settings.MASTER_PASSWORD = 'production-secret'
    with pytest.raises(CommandError, match='local'):
        launch('--noinput')
    assert not settings.STATIC_ROOT.exists()
    assert not get_user_model().objects.exists()
    startup.assert_not_called()
    prepare.assert_not_called()


def test_reloader_child_skips_preparation_and_password_prompt(startup, settings, monkeypatch, django_assert_num_queries):
    monkeypatch.setenv('RUN_MAIN', 'true')
    # No database operations or static writes are needed in a reload child.
    prompt = Mock(side_effect=AssertionError('must not prompt in reload child'))
    monkeypatch.setattr('getpass.getpass', prompt)
    with django_assert_num_queries(0):
        launch('127.0.0.1:8765')
    assert not settings.STATIC_ROOT.exists()
    assert not get_user_model().objects.exists()
    startup.assert_called_once()
    assert startup.call_args.kwargs['addrport'] == '127.0.0.1:8765'
    prompt.assert_not_called()

@pytest.mark.parametrize('error', [EOFError(), KeyboardInterrupt()])
def test_interrupted_private_input_fails_clearly_without_master(startup, monkeypatch, error):
    monkeypatch.setattr('sys.stdin.isatty', lambda: True)
    monkeypatch.setattr('getpass.getpass', Mock(side_effect=error))
    with pytest.raises(CommandError, match='Password input'):
        launch()
    assert not get_user_model().objects.filter(username='master').exists()
    startup.assert_not_called()


def test_private_input_never_falls_back_to_echo(startup, monkeypatch):
    import getpass
    import warnings

    monkeypatch.setattr('sys.stdin.isatty', lambda: True)

    def unsafe_prompt(prompt):
        warnings.warn('Cannot disable terminal echo', getpass.GetPassWarning)
        pytest.fail('password input must stop before an echoed fallback')

    monkeypatch.setattr('getpass.getpass', unsafe_prompt)
    with pytest.raises(CommandError, match='Password input'):
        launch()
    assert not get_user_model().objects.filter(username='master').exists()
    startup.assert_not_called()
