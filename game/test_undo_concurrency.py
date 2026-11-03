"""Undo races must serialize on independent connections to a real SQLite file."""
import os
import subprocess
import sys


def test_undo_races_and_new_grant_are_atomic(tmp_path):
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
from game.test_operations import browser
from game.models import Character, MediaAsset, Item, InventoryEntry, ChatMessage, UndoEntry
from game.operations import grant_catalog, undo_last

call_command('migrate', verbosity=0)
master = browser(master=True)
session_key = master.session.session_key
hero = Character.objects.create(name='Race', character_class='Mage', is_active=True)
asset = MediaAsset.objects.create(digest='d'*64, path='d.png', width=1, height=1)
item = Item.objects.create(name='Potion', description='Test', icon=asset)

def grant(request, key):
    return grant_catalog(request, 'item', item.pk, [hero.pk], key)

def race(functions):
    barrier = threading.Barrier(2, timeout=10)
    def worker(function):
        try:
            request = RequestFactory().post('/')
            request.session = SessionStore(session_key=session_key)
            connection = connections['default']
            connection.ensure_connection()
            identity = id(connection.connection)
            barrier.wait()
            return identity, function(request)
        finally:
            connections['default'].close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, function) for function in functions]
        results = [future.result(timeout=25) for future in futures]
    assert results[0][0] != results[1][0]
    return [value for _, value in results]

grant(master, uuid.uuid4())
entry = UndoEntry.objects.latest('pk').pk
results = race([lambda request: undo_last(request, entry, uuid.uuid4())] * 2)
assert sorted(r['status'] for r in results) == ['ok', 'stale'], results
assert not InventoryEntry.objects.exists() and not ChatMessage.objects.exists()
assert not UndoEntry.objects.exists()

grant(master, uuid.uuid4())
entry = UndoEntry.objects.latest('pk').pk
key = uuid.uuid4()
results = race([lambda request: undo_last(request, entry, key)] * 2)
assert results[0] == results[1] and results[0]['status'] == 'ok', results
assert not InventoryEntry.objects.exists() and not UndoEntry.objects.exists()

grant(master, uuid.uuid4())
entry = UndoEntry.objects.latest('pk').pk
results = race([lambda request: undo_last(request, entry, uuid.uuid4()),
                lambda request: grant(request, uuid.uuid4())])
expected = 1 if results[0]['status'] == 'ok' else 2
assert results[0]['status'] in ('ok', 'stale') and results[1]['status'] == 'ok', results
assert InventoryEntry.objects.get().quantity == expected
assert UndoEntry.objects.count() == ChatMessage.objects.count() == expected
print('FILE_SQLITE_UNDO: different UUID, duplicate UUID, concurrent grant passed')
'''
    env = {**os.environ, 'DATA_DIR': str(tmp_path), 'DJANGO_SETTINGS_MODULE': 'config.settings',
           'DJANGO_ENV': 'local'}
    result = subprocess.run([sys.executable, '-c', script], env=env, text=True,
                            capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'FILE_SQLITE_UNDO' in result.stdout
