"""Copy names and request replay serialize on independent file-backed connections."""
import os
from pathlib import Path
import subprocess
import sys


def test_copy_races_use_free_names_and_replay_one_copy(tmp_path):
    script = r'''
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
import django
django.setup()
from django.core.management import call_command
from django.db import connections
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory
from game.models import Character, CharacterResource, Operation
from game.operations import copy_character
from game.test_operations import browser

call_command('migrate', verbosity=0)
master = browser(master=True)
session_key = master.session.session_key
hero = Character.objects.create(name='Кабас')
CharacterResource.objects.create(character=hero, kind='rage', current=1, maximum=2)

def race(keys):
    barrier = threading.Barrier(2, timeout=10)
    def worker(key):
        try:
            request = RequestFactory().post('/')
            request.session = SessionStore(session_key=session_key)
            connection = connections['default']
            connection.ensure_connection()
            assert ':memory:' not in str(connection.settings_dict['NAME'])
            assert connection.settings_dict['OPTIONS']['transaction_mode'] == 'IMMEDIATE'
            with connection.cursor() as cursor:
                cursor.execute('PRAGMA journal_mode')
                assert cursor.fetchone()[0] == 'wal'
            identity = id(connection.connection)
            barrier.wait()
            return identity, copy_character(request, hero.pk, key)
        finally:
            connections['default'].close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, key) for key in keys]
        results = [future.result(timeout=25) for future in futures]
    assert results[0][0] != results[1][0]
    return [result for _, result in results]

results = race([uuid.uuid4(), uuid.uuid4()])
assert all(result['status'] == 'ok' for result in results), results
assert results[0]['character_id'] != results[1]['character_id']
assert set(Character.objects.exclude(pk=hero.pk).values_list('name', flat=True)) == {'Кабас копия (1)', 'Кабас копия (2)'}
key = uuid.uuid4()
results = race([key, key])
assert results[0] == results[1] and results[0]['status'] == 'ok', results
assert Character.objects.count() == CharacterResource.objects.count() == 4
assert Operation.objects.count() == 3
print('COPY_SQLITE_RACES_OK')
'''
    env = {**os.environ, 'DATA_DIR': str(tmp_path), 'DJANGO_SETTINGS_MODULE': 'config.settings',
           'DJANGO_ENV': 'local', 'PYTHONIOENCODING': 'utf-8'}
    result = subprocess.run([sys.executable, '-c', script], env=env, text=True,
        encoding='utf-8', capture_output=True, timeout=60, cwd=Path(__file__).resolve().parents[1])
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'COPY_SQLITE_RACES_OK' in result.stdout
