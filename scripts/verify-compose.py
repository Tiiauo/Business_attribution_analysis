"""Exercise the deployed stack without deleting any volumes."""

import json
import os
import re
import ssl
import subprocess
import time
from pathlib import Path

import httpx2

ROOT = Path(__file__).resolve().parents[1]
TARGET = "secops@192.168.10.22"
REMOTE = os.environ.get("BA_COMPOSE_REMOTE", "/home/secops/business-attribution/compose")
if not re.fullmatch(r"/home/secops/business-attribution/[a-z][a-z0-9-]*", REMOTE):
    raise ValueError("Invalid project directory")


def remote(command):
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "UpdateHostKeys=no", TARGET, command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
    )
    if result.returncode:
        raise RuntimeError("Project Compose operation failed; inspect the remote project status")
    return result.stdout


def run():
    package = Path(os.environ.get("BA_COMPOSE_PACKAGE", ROOT / ".run/compose-package"))
    tls = ssl.create_default_context(cafile=str(package / "ca.crt"))
    accounts = json.loads((package / ".secrets/demo-accounts.json").read_text(encoding="utf-8"))
    app = os.environ.get("BA_UI_URL", "https://192.168.10.22")
    issuer = os.environ.get("BA_UI_ISSUER", "https://192.168.10.22:9443")
    record = {"mode": "independent Compose", "public_url": app, "issuer": issuer, "checks": []}
    with httpx2.Client(verify=tls, timeout=15, follow_redirects=False) as client:
        assert client.get(app + "/health/ready").status_code == 200
        page = client.get(app + "/login")
        assert page.status_code == 200 and 'src="/assets/' in page.text
        for account in (accounts[0], accounts[2]):
            client.cookies.clear()
            response = client.get(app + "/auth/login")
            form = client.get(response.headers["location"])
            csrf = re.search(r'name="csrf" value="([^"]+)"', form.text).group(1)
            response = client.post(
                issuer + "/authorize",
                data={"csrf": csrf, **{key: account[key] for key in ("username", "password", "role")}},
                headers={"Origin": issuer},
            )
            callback = client.get(response.headers["location"])
            assert callback.status_code == 303 and "error=" not in callback.headers["location"]
            user = client.get(app + "/auth/me").json()
            assert user["username"] == account["username"] and user["role"] == account["role"]
            record["checks"].append(f"real_oidc_{account['role']}")
        old_user = user
        remote(f"cd {REMOTE} && docker compose restart db backend worker idp gateway")
        for _ in range(50):
            try:
                response = client.get(app + "/auth/me")
                if response.status_code == 200:
                    break
            except httpx2.HTTPError:
                pass
            time.sleep(0.3)
        else:
            raise AssertionError("Compose login did not survive database/service restart")
        assert response.json()["id"] == old_user["id"]
        remote(f"cd {REMOTE} && docker compose run --rm migrate")
        assert client.get(app + "/auth/me").status_code == 200
        record["checks"].extend(["database_and_services_restart_session_persisted", "repeat_migration_preserved_data"])
        assert (
            client.post(
                app + "/auth/logout", headers={"Origin": app, "X-CSRF-Token": old_user["csrf_token"]}
            ).status_code
            == 303
        )
        assert client.get(app + "/auth/me").status_code == 401
        record["checks"].append("logout_revocation")
    remote(f"cd {REMOTE} && docker compose up -d --wait")
    # Return only Compose service metadata; never inspect or publish container environment variables.
    status = remote(f"cd {REMOTE} && docker compose ps --format json")
    record["services"] = [json.loads(line) for line in status.splitlines() if line.strip()]
    assert {service["Service"] for service in record["services"]} == {"db", "backend", "worker", "idp", "gateway"}
    assert all(service.get("Health") == "healthy" for service in record["services"])
    record["linux_python"] = remote(
        f"cd {REMOTE} && docker compose exec -T backend /app/.venv/bin/python --version"
    ).strip()
    record["image_digests"] = remote(
        "docker image inspect python:3.12-slim postgres:16 --format={{.RepoDigests}}"
    ).splitlines()
    print(json.dumps(record, ensure_ascii=False, indent=2))
    print("Independent Compose: real analyst/admin OIDC, restart persistence, repeated migration and logout passed.")


if __name__ == "__main__":
    run()
