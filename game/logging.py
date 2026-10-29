"""Remove invitation credentials from Django logs without changing requests."""
import logging
import re


_INVITATION_PATH = re.compile(r"(/join/)[^/\s?#<>\"']+")


def _redact(text):
    return _INVITATION_PATH.sub(r"\1[redacted]", text)


class InvitationPathFilter(logging.Filter):
    def filter(self, record):
        # Django often stores the request path in %-format arguments. Rendering
        # first sanitizes both styles while leaving the real request untouched.
        record.msg = _redact(record.getMessage())
        record.args = ()
        return True


class InvitationPathFormatter(logging.Formatter):
    def format(self, record):
        # Keep traceback/status context, sanitizing paths in exception text too.
        return _redact(super().format(record))
