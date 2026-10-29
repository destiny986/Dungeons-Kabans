import io
import logging

import pytest
from django.test import Client, override_settings


def test_invitation_logging_redacts_rendered_args_and_traceback_preserving_context():
    from game.logging import InvitationPathFilter, InvitationPathFormatter
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(InvitationPathFilter())
    handler.setFormatter(InvitationPathFormatter("%(levelname)s %(message)s"))
    logger = logging.Logger("privacy-test", logging.INFO)
    logger.addHandler(handler)
    marker = "KNOWN-INVITATION-CREDENTIAL"
    request = type("Request", (), {"path": f"/join/{marker}/"})()
    try:
        raise ValueError(f"upstream failed for /join/{marker}/")
    except ValueError:
        logger.exception("GET %s -> %s; Referer=%s", request.path, 502,
                         f"https://example.test/join/{marker}/", extra={"request": request})
    output = stream.getvalue()
    assert marker not in output
    assert "/join/[redacted]/" in output
    assert "502" in output and "Traceback" in output and "ValueError" in output
    assert request.path == f"/join/{marker}/"
    logger.warning("GET /characters/ordinary-id/ -> 404")
    assert "GET /characters/ordinary-id/ -> 404" in stream.getvalue()


@pytest.mark.django_db
def test_real_invalid_invitation_404_is_redacted_by_configured_django_handler(monkeypatch):
    stream = io.StringIO()
    handlers = logging.getLogger("django").handlers
    assert handlers
    for handler in handlers:
        monkeypatch.setattr(handler, "stream", stream, raising=False)
    marker = "KNOWN-INVALID-INVITATION-CREDENTIAL"
    with override_settings(DEBUG=False):
        response = Client().get(f"/join/{marker}/", secure=True)
    output = stream.getvalue()
    assert response.status_code == 404
    assert marker not in output
    assert "Not Found" in output and "/join/[redacted]/" in output


def test_configured_dev_server_handler_redacts_interpolated_request(monkeypatch):
    stream = io.StringIO()
    handlers = logging.getLogger("django.server").handlers
    assert handlers
    for handler in handlers:
        monkeypatch.setattr(handler, "stream", stream, raising=False)
    logging.getLogger("django.server").info('"%s" %s %s',
        "GET /join/KNOWN-DEV-SERVER-CREDENTIAL/?x=1 HTTP/1.1", 302, 0)
    output = stream.getvalue()
    assert "KNOWN-DEV-SERVER-CREDENTIAL" not in output
    assert "/join/[redacted]/?x=1" in output and "302" in output
