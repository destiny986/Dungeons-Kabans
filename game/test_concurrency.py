"""Real file-backed SQLite contention, isolated from pytest's shared-memory DB."""
import os
import subprocess
import sys


def test_independent_connections_serialize_last_slot_and_duplicate_request(tmp_path):
    script = r'''
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
import django
django.setup()
from django.core.management import call_command
from django.db import connection, connections
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory
from game.access import issue_invitation, redeem_invitation
from game.models import Character, MediaAsset, Action, CharacterAction, CharacterResource, ChatMessage, Operation
from game.operations import apply_action

call_command('migrate', verbosity=0)
assert str(connection.settings_dict['NAME']).endswith('db.sqlite3')
assert ':memory:' not in str(connection.settings_dict['NAME'])
assert connection.settings_dict['OPTIONS']['transaction_mode'] == 'IMMEDIATE'
with connection.cursor() as cursor:
    cursor.execute('PRAGMA journal_mode')
    assert cursor.fetchone()[0] == 'wal'
hero = Character.objects.create(name='Concurrent', character_class='Wizard', can_cast=True, spellcasting_ability='int')
request = RequestFactory().post('/')
request.session = SessionStore()
token, _ = issue_invitation(hero)
redeem_invitation(request, token)
session_key = request.session.session_key
icon = MediaAsset.objects.create(digest='b'*64, path='b.png', width=1, height=1)
action = Action.objects.create(name='Last spell', description='spell', icon=icon, resource='spell_slot', slot_level=7)
CharacterAction.objects.create(character=hero, action=action)
resource = CharacterResource.objects.create(character=hero, kind='spell_slot', level=7, current=1, maximum=1)

def race(keys):
    barrier = threading.Barrier(2, timeout=10)
    def consume(key):
        try:
            local = RequestFactory().post('/')
            local.session = SessionStore(session_key=session_key)
            connections['default'].ensure_connection()
            raw_connection_id = id(connections['default'].connection)
            barrier.wait()
            return raw_connection_id, apply_action(local, hero.pk, action.pk, key)
        finally:
            connections['default'].close()
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(consume, key) for key in keys]
        results = [future.result(timeout=25) for future in futures]
    assert results[0][0] != results[1][0], results
    return [result for _, result in results]

results = race([uuid.uuid4(), uuid.uuid4()])
assert sorted(r['status'] for r in results) == ['error', 'ok'], results
resource.refresh_from_db()
assert resource.current == 0 and ChatMessage.objects.count() == 1 and Operation.objects.count() == 1
CharacterResource.objects.filter(pk=resource.pk).update(current=1)
key = uuid.uuid4()
results = race([key, key])
assert results[0] == results[1] and results[0]['status'] == 'ok', results
resource.refresh_from_db()
assert resource.current == 0 and ChatMessage.objects.count() == 2 and Operation.objects.count() == 2
print('FILE_SQLITE_WAL_IMMEDIATE: last-slot and duplicate UUID races passed')
'''
    env = {**os.environ, 'DATA_DIR': str(tmp_path), 'DJANGO_SETTINGS_MODULE': 'config.settings',
           'DJANGO_ENV': 'local'}
    result = subprocess.run([sys.executable, '-c', script], env=env, text=True,
                            capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'FILE_SQLITE_WAL_IMMEDIATE' in result.stdout
