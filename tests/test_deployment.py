"""Exercise delivery boundaries without contacting a server or Docker daemon."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

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
