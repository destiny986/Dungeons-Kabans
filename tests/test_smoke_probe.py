"""Isolated HTTP and shell regressions for the Docker logging smoke test."""
import http.client
import os
import ssl
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from deploy import logging_probe
from tests.test_deployment import BASH, ROOT


def http_fixture(monkeypatch, outcomes, elapsed=0):
    clock = [0.0]
    calls = []
    closed = []
    remaining = iter(outcomes)
    last = outcomes[-1]

    class Connection:
        def __init__(self, host, port, **kwargs):
            self.timeout = kwargs['timeout']
            calls.append((host, port, kwargs))

        def request(self, method, path, headers):
            assert method == 'GET'
            assert '/join/' in path and '?credential=' in path
            assert 'CI-INVITATION-CREDENTIAL-LOG-MARKER' in headers['Referer']

        def getresponse(self):
            clock[0] += min(elapsed, self.timeout)
            outcome = next(remaining, last)
            if isinstance(outcome, Exception):
                raise outcome
            return type('Response', (), {'status': outcome})()

        def close(self):
            closed.append(self)

    monkeypatch.setattr(logging_probe.http.client, 'HTTPSConnection', Connection)
    monkeypatch.setattr(logging_probe.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(logging_probe.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    return clock, calls, closed


@pytest.mark.parametrize('statuses', [[502], [404, 502]])
def test_upstream_failure_accepts_only_502_and_reconnects(monkeypatch, statuses):
    clock, calls, closed = http_fixture(monkeypatch, statuses)
    logging_probe.check_upstream_failure()
    assert len(calls) == len(statuses) == len(closed)
    assert clock[0] == len(statuses) - 1


@pytest.mark.parametrize('outcome,diagnostic', [
    (200, 'HTTP 200'), (404, 'HTTP 404'), (504, 'HTTP 504'),
    (ssl.SSLError('SECRET-MARKER'), 'TLS'),
    (TimeoutError('SECRET-MARKER'), 'timeout'),
    (ConnectionRefusedError('SECRET-MARKER'), 'connection'),
    (http.client.BadStatusLine('SECRET-MARKER'), 'HTTP protocol'),
])
def test_upstream_failure_deadline_and_safe_errors(monkeypatch, outcome, diagnostic):
    clock, calls, closed = http_fixture(monkeypatch, [outcome], elapsed=2.5)
    with pytest.raises(SystemExit) as error:
        logging_probe.check_upstream_failure()
    message = str(error.value)
    assert diagnostic in message and 'upstream' in message
    assert 'SECRET-MARKER' not in message and '/join/' not in message
    assert clock[0] == 30
    assert len(calls) == len(closed)
    assert all(0 < call[2]['timeout'] <= 3 for call in calls)
    assert calls[-1][2]['timeout'] <= 2


def test_upstream_mode_requires_isolation_before_http_or_django(monkeypatch):
    monkeypatch.delenv('DELIVERY_LOG_PROBE', raising=False)
    monkeypatch.setattr('sys.argv', ['probe', '--upstream-failure'])
    with pytest.raises(SystemExit, match='restricted'):
        logging_probe.main()


def test_upstream_mode_does_not_initialize_django(monkeypatch):
    monkeypatch.setenv('DELIVERY_LOG_PROBE', 'isolated-ci-only')
    monkeypatch.setattr('sys.argv', ['probe', '--upstream-failure'])
    http_fixture(monkeypatch, [502])
    import django
    monkeypatch.setattr(django, 'setup', lambda: pytest.fail('Failure probe must not initialize Django'))
    logging_probe.main()


@pytest.mark.skipif(os.name == 'nt', reason='Docker probe uses POSIX interval timers')
def test_request_budget_interrupts_a_blocking_operation():
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        with logging_probe.request_budget(.05):
            time.sleep(1)
    assert time.monotonic() - started < .5


def test_request_budget_restores_alarm_after_timeout(monkeypatch):
    clock = [0.0]
    original_handler = object()
    handlers = [original_handler]
    timers = []

    def set_handler(signum, handler):
        previous = handlers[-1]
        handlers.append(handler)
        return previous

    def set_timer(kind, seconds, interval=0):
        timers.append((seconds, interval))
        return (10, 2) if len(timers) == 1 else (0, 0)

    monkeypatch.setattr(logging_probe, 'signal', SimpleNamespace(
        SIGALRM=1, ITIMER_REAL=2, signal=set_handler, setitimer=set_timer))
    monkeypatch.setattr(logging_probe.time, 'monotonic', lambda: clock[0])
    with pytest.raises(TimeoutError):
        with logging_probe.request_budget(3):
            clock[0] = 3
            handlers[-1](1, None)
    assert handlers[-1] is original_handler
    assert timers == [(3, 0), (0, 0), (7, 2)]


def test_upstream_applies_one_budget_to_the_entire_attempt(monkeypatch):
    from contextlib import contextmanager
    clock, calls, closed = http_fixture(monkeypatch, [404, 502])
    budgets = []

    @contextmanager
    def budget(seconds):
        budgets.append(seconds)
        yield

    monkeypatch.setattr(logging_probe, 'request_budget', budget)
    logging_probe.check_upstream_failure()
    assert budgets == [3, 3]
    assert len(calls) == len(closed) == 2


@pytest.mark.skipif(not BASH, reason='Bash unavailable')
@pytest.mark.parametrize('failure', ['', 'syntax', 'reload', 'probe', 'leak'])
def test_smoke_switches_upstream_in_place_and_cleans_up(tmp_path, failure):
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    (bin_dir / 'docker').write_text(r'''#!/bin/bash
printf '%s\n' "$*" >> "$TRACE"
case "$*" in
  *" id -u") echo 10001 ;;
  *"run -d"*"nginx:stable-alpine"*)
    for arg in "$@"; do
      if [[ "$arg" == *:/etc/nginx/conf.d/default.conf:ro ]]; then
        config=${arg%:/etc/nginx/conf.d/default.conf:ro}
        printf '%s\n' "$config" > "$STATE"
        stat -c %i "$config" > "$STATE.inode"
      fi
    done ;;
  *"nginx -t"*)
    config=$(cat "$STATE")
    grep -q 'proxy_pass http://127.0.0.1:1;' "$config" || exit 81
    [[ $(stat -c %i "$config") == $(cat "$STATE.inode") ]] || exit 82
    echo config-switched-in-place >> "$TRACE"
    [[ "$FAILURE" != syntax ]] || { echo SECRET-MARKER >&2; exit 41; } ;;
  *"nginx -s reload"*) [[ "$FAILURE" != reload ]] || { echo SECRET-MARKER >&2; exit 42; } ;;
  *"logging_probe --upstream-failure"*) [[ "$FAILURE" != probe ]] || exit 43 ;;
  *"logging_probe"*) echo '["secret-token", "CI-INVITATION-CREDENTIAL-LOG-MARKER"]' ;;
  "logs "*) [[ "$FAILURE" != leak ]] || echo CI-INVITATION-CREDENTIAL-LOG-MARKER ;;
esac
exit 0
''', encoding='utf-8')
    (bin_dir / 'openssl').write_text('#!/bin/bash\nexit 0\n', encoding='utf-8')
    (bin_dir / 'sleep').write_text('#!/bin/bash\nexit 0\n', encoding='utf-8')
    # Windows' python3 alias is not the project's interpreter. Execute the real
    # inline Python from the smoke script with the test runner's environment.
    (bin_dir / 'python3').write_text('#!/bin/bash\nexec "$TEST_PYTHON" "$@"\n', encoding='utf-8')
    for entry in bin_dir.iterdir():
        entry.chmod(0o755)
    trace = tmp_path / 'trace'
    env = {**os.environ, 'TRACE': trace.as_posix(), 'STATE': (tmp_path / 'state').as_posix(),
           'FAILURE': failure, 'TEST_PYTHON': sys.executable.replace('\\', '/')}
    fake_path = bin_dir.as_posix()
    if os.name == 'nt':
        fake_path = '/' + fake_path[0].lower() + fake_path[2:]
    result = subprocess.run([BASH, '-c', 'export PATH="$1:$PATH"; bash "$2" test-image',
                             '--', fake_path, (ROOT / 'deploy/docker-smoke.sh').as_posix()],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)
    assert (result.returncode != 0) == bool(failure), result.stderr
    assert 'SECRET-MARKER' not in result.stdout + result.stderr
    assert 'CI-INVITATION-CREDENTIAL-LOG-MARKER' not in result.stdout + result.stderr
    operations = trace.read_text().splitlines()
    joined = '\n'.join(operations)
    assert 'stop ' not in joined
    assert 'config-switched-in-place' in operations
    assert joined.index('logging_probe') < joined.index('nginx -t')
    assert 'rm -f ' in joined and 'network rm ' in joined and joined.count('volume rm ') == 3
    if failure == 'syntax':
        assert 'nginx -s reload' not in joined
    else:
        assert joined.index('nginx -t') < joined.index('nginx -s reload')
    if failure in {'syntax', 'reload'}:
        assert '--upstream-failure' not in joined
    else:
        assert joined.index('nginx -s reload') < joined.index('--upstream-failure')
    if failure in {'', 'leak'}:
        assert joined.index('--upstream-failure') < joined.index('\nlogs ') < joined.index('\nrm -f ')
    assert 'Inspect isolated CI resources' not in result.stderr
