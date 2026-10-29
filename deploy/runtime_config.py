"""Serialize raw Compose env files; never source these files in a shell."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def load_payload(source):
    values = json.loads(Path(source).read_text(encoding="utf-8"))
    for name in ("DJANGO_SECRET_KEY", "MASTER_PASSWORD", "DOCKER_USERNAME", "DOCKER_PASSWORD"):
        value = values.get(name)
        if not isinstance(value, str) or not value.strip() or any(c in value for c in "\r\n\0"):
            raise ValueError("invalid value")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,38}", values["DOCKER_USERNAME"]):
        raise ValueError("invalid registry user")
    return values


def write_runtime(values, directory):
    runtime = {
        "DJANGO_SECRET_KEY": values["DJANGO_SECRET_KEY"],
        "MASTER_PASSWORD": values["MASTER_PASSWORD"],
        "DJANGO_ENV": "production",
        "DJANGO_DEBUG": "False",
        "DJANGO_ALLOWED_HOSTS": "185.232.205.216,localhost,127.0.0.1",
        "DJANGO_CSRF_TRUSTED_ORIGINS": "https://185.232.205.216:9443",
        "PUBLIC_BASE_URL": "https://185.232.205.216:9443",
        "DATA_DIR": "/data", "MEDIA_ROOT": "/media", "IMPORT_ROOT": "/imports",
    }
    target = Path(directory)
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = target / "runtime.env.tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.chmod(temporary, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        stream.write("".join(f"{key}={value}\n" for key, value in runtime.items()))
    temporary.replace(target / "runtime.env")


def main():
    try:
        operation, source, *rest = sys.argv[1:]
        values = load_payload(source)
        if operation == "write" and len(rest) == 1:
            write_runtime(values, rest[0])
        elif operation == "login" and not rest:
            subprocess.run(["docker", "login", "--username", values["DOCKER_USERNAME"],
                            "--password-stdin"], input=values["DOCKER_PASSWORD"] + "\n",
                           text=True, check=True, stdout=subprocess.DEVNULL)
        else:
            raise ValueError("invalid invocation")
    except (ValueError, OSError, subprocess.CalledProcessError):
        print("Invalid deployment input or failed configuration operation.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
