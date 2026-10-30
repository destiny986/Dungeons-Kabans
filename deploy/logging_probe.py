"""CI-only live request probe. Never run against a production database."""
import http.client
from contextlib import contextmanager
import json
import os
import signal
import ssl
import sys
import time


MARKER = "CI-INVITATION-CREDENTIAL-LOG-MARKER"


@contextmanager
def request_budget(seconds):
    """Bound DNS, TLS and all HTTP operations together in the Linux CI image."""
    if not hasattr(signal, "setitimer"):
        # Windows runs HTTP doubles in unit tests; Docker always uses Linux.
        yield
        return

    def expired(signum, frame):
        raise TimeoutError

    previous_handler = signal.signal(signal.SIGALRM, expired)
    started = time.monotonic()
    previous_timer = signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0]:
            signal.setitimer(signal.ITIMER_REAL,
                            max(.000001, previous_timer[0] - (time.monotonic() - started)),
                            previous_timer[1])


def check_upstream_failure():
    """Wait for reloaded nginx workers without exposing request diagnostics."""
    context = ssl._create_unverified_context()
    deadline = time.monotonic() + 30
    diagnostic = "no HTTP response"
    while (remaining := deadline - time.monotonic()) > 0:
        connection = None
        try:
            with request_budget(min(3, remaining)):
                connection = http.client.HTTPSConnection(
                    "logging-proxy", 9443, context=context, timeout=min(3, remaining))
                connection.request("GET", f"/join/{MARKER}-invalid/?credential={MARKER}", headers={
                    "Host": "185.232.205.216:9443",
                    "Referer": f"https://example.test/join/{MARKER}/"})
                response = connection.getresponse()
                diagnostic = f"HTTP {int(response.status)}"
                if response.status == 502 and time.monotonic() <= deadline:
                    return
        except ssl.SSLError:
            diagnostic = "TLS"
        except TimeoutError:
            diagnostic = "timeout"
        except OSError:
            diagnostic = "connection"
        except http.client.HTTPException:
            diagnostic = "HTTP protocol"
        finally:
            if connection is not None:
                connection.close()
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(1, remaining))
    raise SystemExit(f"Isolated nginx upstream failure check failed: {diagnostic}; expected HTTP 502")


def main():
    if os.environ.get("DELIVERY_LOG_PROBE") != "isolated-ci-only":
        raise SystemExit("Logging probe is restricted to the isolated CI container")
    if sys.argv[1:] == ["--upstream-failure"]:
        check_upstream_failure()
        return
    if sys.argv[1:]:
        raise SystemExit("Unsupported logging probe mode")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    django.setup()
    from game.access import issue_invitation
    from game.models import Character

    character = Character.objects.create(name="CI invitation logging", character_class="Wizard")
    token, _ = issue_invitation(character)
    marker = MARKER
    # Deliberately self-signed, disposable localhost TLS in an isolated Docker network.
    context = ssl._create_unverified_context()
    for path, expected in ((f"/join/{token}/", 302), (f"/join/{marker}-invalid/", 404),
                           ("/healthz/", 200)):
        connection = http.client.HTTPSConnection("logging-proxy", 9443, context=context, timeout=10)
        connection.request("GET", path + f"?credential={marker}", headers={
            "Host": "185.232.205.216:9443", "Referer": f"https://example.test/join/{token}/{marker}/"})
        response = connection.getresponse()
        if response.status != expected:
            raise SystemExit("Invitation logging probe received an unexpected response status")
        response.read()
        connection.close()
    # Captured privately by the runner, not printed in CI logs.
    json.dump([token, marker], sys.stdout)


if __name__ == "__main__":
    main()
