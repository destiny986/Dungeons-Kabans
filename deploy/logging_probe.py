"""CI-only live request probe. Never run against a production database."""
import http.client
import json
import os
import ssl
import sys


def main():
    if os.environ.get("DELIVERY_LOG_PROBE") != "isolated-ci-only":
        raise SystemExit("Logging probe is restricted to the isolated CI container")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    django.setup()
    from game.access import issue_invitation
    from game.models import Character

    character = Character.objects.create(name="CI invitation logging", character_class="Wizard")
    token, _ = issue_invitation(character)
    marker = "CI-INVITATION-CREDENTIAL-LOG-MARKER"
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
