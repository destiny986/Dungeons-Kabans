"""Exercise delivery boundaries without contacting a server or Docker daemon."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import socket
import time
import http.client
import shlex

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash") or ("C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else None)


def payload():
    return {"DJANGO_SECRET_KEY": "secret '$dollar # hash \\\" $(touch /tmp/nope)",
            "MASTER_PASSWORD": "master '$x # \\\"", "DOCKER_USERNAME": "example",
            "DOCKER_PASSWORD": "registry '$() # \\\""}


def prepare(tmp_path, values):
    source = tmp_path / "secrets.json"
    source.write_text(json.dumps(values), encoding="utf-8")
    output = tmp_path / "config"
    result = subprocess.run([sys.executable, str(ROOT / "deploy/runtime_config.py"),
                             "write", str(source), str(output)], capture_output=True, text=True)
    return result, output


def test_runtime_preserves_shell_characters_without_emitting_secrets(tmp_path):
    values = payload()
    result, output = prepare(tmp_path, values)
    assert result.returncode == 0, result.stderr
    actual = dict(line.split("=", 1) for line in (output / "runtime.env").read_text().splitlines())
    assert actual["DJANGO_SECRET_KEY"] == values["DJANGO_SECRET_KEY"]
    assert actual["MASTER_PASSWORD"] == values["MASTER_PASSWORD"]
    assert actual["DJANGO_DEBUG"] == "False"
    assert actual["DJANGO_ENV"] == "production"
    assert actual["DJANGO_CSRF_TRUSTED_ORIGINS"] == "https://185.232.205.216:9443"
    assert "DOCKER_PASSWORD" not in actual
    assert result.stdout == result.stderr == ""
    if os.name != "nt":
        assert (output / "runtime.env").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("bad", ["", "hello\nINJECTED=true", "hello\rworld", "nul\0value"])
def test_invalid_runtime_secret_leaves_no_partial_configuration(tmp_path, bad):
    values = payload()
    values["MASTER_PASSWORD"] = bad
    result, output = prepare(tmp_path, values)
    assert result.returncode != 0
    assert "Invalid deployment input" in result.stderr
    assert not (output / "runtime.env").exists()
    assert values["DJANGO_SECRET_KEY"] not in result.stderr


@pytest.mark.skipif(not BASH, reason="Bash unavailable")
@pytest.mark.parametrize("failure", ["migrate", "bootstrap_master", "health", ""])
def test_release_orders_single_phase_and_stops_without_destructive_recovery(tmp_path, failure):
    # Only external Docker/curl/sleep are replaced. The real release control flow runs.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(
        '#!/bin/bash\nprintf "%s\\n" "$*" >> "$TRACE"\n'
        'if [[ "$FAILURE" != health && " $* " == *" $FAILURE "* ]]; then exit 41; fi\n',
        encoding="utf-8")
    (bin_dir / "curl").write_text('#!/bin/bash\n[[ "$FAILURE" != health ]]\n', encoding="utf-8")
    (bin_dir / "sleep").write_text('#!/bin/bash\nexit 0\n', encoding="utf-8")
    for entry in bin_dir.iterdir():
        entry.chmod(0o755)
    trace = tmp_path / "trace"
    env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
           "TRACE": trace.as_posix(), "FAILURE": failure}
    fake_path = bin_dir.as_posix()
    if os.name == "nt":
        fake_path = "/" + fake_path[0].lower() + fake_path[2:]
    result = subprocess.run([BASH, "-c", 'export PATH="$1:$PATH"; source "$2" compose.yaml image.env',
                             "--", fake_path, (ROOT / "deploy/release-container.sh").as_posix()],
                            env=env, capture_output=True, text=True, timeout=15)
    assert (result.returncode != 0) == bool(failure)
    operations = trace.read_text().splitlines()
    assert not any(" down" in op or " restart" in op for op in operations)
    starts = [op for op in operations if " up -d" in op]
    assert len(starts) == (1 if failure in {"health", ""} else 0)
    assert sum(" manage.py migrate " in op for op in operations) == 1
    if not failure:
        assert sum(" manage.py bootstrap_master" in op for op in operations) == 1
    if failure == "migrate":
        assert not any("bootstrap_master" in op for op in operations)


@pytest.mark.skipif(os.name == "nt", reason="Gunicorn requires POSIX fcntl")
def test_image_gunicorn_command_never_logs_invitation_request_components(tmp_path):
    """Run the image's real command, replacing only its WSGI target/bind address."""
    command = json.loads(next(line[4:] for line in (ROOT / "Dockerfile").read_text().splitlines()
                              if line.startswith("CMD ")))
    command[0:1] = [sys.executable, "-m", "gunicorn"]
    command[command.index("config.wsgi:application")] = "fixture_wsgi:application"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    command[command.index("--bind") + 1] = f"127.0.0.1:{port}"
    (tmp_path / "fixture_wsgi.py").write_text(
        "def application(environ, start_response):\n"
        " status = '404 Not Found' if 'invalid' in environ['PATH_INFO'] else '302 Found'\n"
        " start_response(status, [('Content-Length', '0')]); return [b'']\n")
    process = subprocess.Popen(command, cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    marker = "KNOWN-INVITATION-CREDENTIAL-DO-NOT-LOG"
    try:
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=.1):
                    break
            except OSError:
                if process.poll() is not None:
                    pytest.fail("Gunicorn exited before accepting test requests")
                time.sleep(.05)
        else:
            pytest.fail("Gunicorn did not start")
        for kind, expected in (("valid", 302), ("invalid", 404)):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            connection.request("GET", f"/join/{marker}-{kind}/?credential={marker}",
                               headers={"Referer": f"https://example.test/join/{marker}/"})
            response = connection.getresponse()
            assert response.status == expected
            response.read()
            connection.close()
    finally:
        process.terminate()
        stdout, stderr = process.communicate(timeout=10)
    assert marker not in stdout + stderr


def test_configured_logging_probe_imports_app_from_container_workdir(tmp_path):
    """Execute the configured invocation with isolated SQLite and only HTTP doubled.

    PYTHONPATH contains the HTTP fixture alone, so it cannot accidentally provide
    the project-root import path that the image's invocation must supply itself.
    """
    fixture = tmp_path / "http_fixture"
    fixture.mkdir()
    (fixture / "sitecustomize.py").write_text(
        "import http.client, json, os\n"
        "class Connection:\n"
        " def __init__(self, *args, **kwargs): pass\n"
        " def request(self, method, path, headers): self.path=path\n"
        " def getresponse(self):\n"
        "  status = 200 if self.path.startswith('/healthz/') else (404 if '-invalid/' in self.path else 302)\n"
        "  with open(os.environ['PROBE_TRACE'], 'a') as stream: stream.write(json.dumps(status)+'\\n')\n"
        "  return type('Response', (), {'status': status, 'read': lambda self: b''})()\n"
        " def close(self): pass\n"
        "http.client.HTTPSConnection=Connection\n", encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(fixture), "DELIVERY_LOG_PROBE": "isolated-ci-only",
           "DJANGO_ENV": "production", "DJANGO_DEBUG": "False",
           "DJANGO_SECRET_KEY": "isolated-probe-test-secret-0123456789-abcdefghijklmnopqrstuvwxyz",
           "DJANGO_ALLOWED_HOSTS": "localhost", "DATA_DIR": str(tmp_path / "data"),
           "MEDIA_ROOT": str(tmp_path / "media"), "IMPORT_ROOT": str(tmp_path / "imports"),
           "PROBE_TRACE": str(tmp_path / "trace.jsonl")}
    migration = subprocess.run([sys.executable, "manage.py", "migrate", "--noinput"],
                               cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert migration.returncode == 0, migration.stderr
    invocation = next(line.strip().split(" > ", 1)[0] for line in
                      (ROOT / "deploy/docker-smoke.sh").read_text().splitlines()
                      if line.strip().startswith("python ") and "logging_probe" in line)
    command = shlex.split(invocation)
    command[0] = sys.executable
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    credentials = json.loads(result.stdout)
    assert len(credentials) == 2 and all(credentials)
    assert (tmp_path / "trace.jsonl").read_text().splitlines() == ["302", "404", "200"]
