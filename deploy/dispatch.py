"""GitHub runner: verify the supplied trust anchor, stage files, invoke release.

Secrets are environment inputs and JSON on SSH stdin, never executable text.
"""
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from runtime_config import load_payload


def main():
    env = os.environ
    host = str(ipaddress.ip_address(env["HOST"]))
    if host != "185.232.205.216":
        raise ValueError("HOST does not match the reviewed deployment destination")
    user, sha, run = env["SSH_USER"], env["GITHUB_SHA"], env["DEPLOY_RUN"]
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*", user):
        raise ValueError("invalid SSH user")
    if not re.fullmatch(r"[a-f0-9]{40}", sha) or not re.fullmatch(r"[0-9]+-[0-9]+", run):
        raise ValueError("invalid release identifier")
    fingerprint = env["SSH_HOST_FINGERPRINT"]
    if not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", fingerprint):
        raise ValueError("supply the trusted SHA256 ED25519 host fingerprint")
    payload = {name: env[name] for name in (
        "DJANGO_SECRET_KEY", "MASTER_PASSWORD", "DOCKER_USERNAME", "DOCKER_PASSWORD")}
    child_env = {**env, "SSHPASS": env["SSH_PASSWORD"]}
    with tempfile.TemporaryDirectory(prefix="dungeons-release-") as temp:
        temporary = Path(temp)
        temporary.chmod(0o700)
        secret_file = temporary / "secrets.json"
        secret_file.write_text(json.dumps(payload), encoding="utf-8")
        secret_file.chmod(0o600)
        load_payload(secret_file)  # Reject invalid env values before any network operation.
        scanned = subprocess.run(["ssh-keyscan", "-T", "10", "-t", "ed25519", host],
                                 check=True, capture_output=True, text=True).stdout
        known_hosts = temporary / "known_hosts"
        known_hosts.write_text(scanned, encoding="utf-8")
        known_hosts.chmod(0o600)
        actual = subprocess.run(["ssh-keygen", "-lf", str(known_hosts), "-E", "sha256"],
                                check=True, capture_output=True, text=True).stdout.splitlines()
        if len(actual) != 1 or actual[0].split()[1] != fingerprint:
            raise ValueError("SSH host fingerprint mismatch; refusing connection")
        options = ["-o", f"UserKnownHostsFile={known_hosts}", "-o", "StrictHostKeyChecking=yes",
                   "-o", "PreferredAuthentications=password", "-o", "PubkeyAuthentication=no",
                   "-o", "NumberOfPasswordPrompts=1", "-o", "ConnectTimeout=15",
                   "-o", "LogLevel=ERROR"]
        destination = f"{user}@{host}"
        stage = f"/srv/dungeons-kabans/incoming/{run}"
        ssh = ["sshpass", "-e", "ssh", *options, destination]
        subprocess.run([*ssh, f"umask 077; mkdir -m 700 {stage}"], check=True, env=child_env)
        try:
            sources = ["compose.production.yaml", "deploy/release.sh", "deploy/release-container.sh",
                       "deploy/runtime_config.py", "deploy/nginx.conf"]
            subprocess.run(["sshpass", "-e", "scp", *options, *sources, f"{destination}:{stage}/"],
                           check=True, env=child_env)
            subprocess.run([*ssh, f"umask 077; cat > {stage}/secrets.json"],
                           input=json.dumps(payload), text=True, check=True, env=child_env)
            subprocess.run([*ssh, f"sudo -n bash {stage}/release.sh {stage} {sha} {payload['DOCKER_USERNAME']}"],
                           check=True, env=child_env)
            # This verifies TLS and reachability from outside the deployment host.
            subprocess.run(["curl", "--fail", "--silent", "--show-error", "--max-time", "15",
                            "https://185.232.205.216:9443/healthz/"], check=True)
        finally:
            # Only staging credentials; persistent app data and code are never removed here.
            subprocess.run([*ssh, f"rm -f -- {stage}/secrets.json"], env=child_env, check=False)


if __name__ == "__main__":
    main()
