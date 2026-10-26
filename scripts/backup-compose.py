"""Back up an owned Compose stack and restore it into new, unexposed volumes."""

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
BASE = "/home/secops/business-attribution"
TARGET = "secops@192.168.10.22"
PROJECTS = {"ba-m01-compose": "compose", "ba-m07-acceptance": "ba-m07-acceptance"}
VOLUMES = {"data": ("backend", "/app/data"), "caddy": ("gateway", "/data"), "config": ("gateway", "/config")}


def remote(command, data=None):
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", TARGET, command],
        input=data,
        capture_output=True,
        timeout=300,
    )
    if result.returncode:
        diagnostic = re.search(rb"BACKUP_PROBE_ERROR [A-Za-z]+ \[[0-9, ]+\]", result.stderr)
        if diagnostic:
            raise RuntimeError(diagnostic.group().decode())
        raise RuntimeError("Backup/restore command failed; private output was not printed")
    return result.stdout.decode("utf8")


def probe(manifest, folder, network, volumes):
    mounts = " ".join("-v " + shlex.quote(value + ":/snapshot/" + key + ":ro") for key, value in volumes.items())
    command = f"docker run --rm --read-only --user 0 --network {network} {mounts} -v {folder}/settings/.secrets:/snapshot/secrets:ro -v {folder}:/backup:ro --entrypoint /app/.venv/bin/python {manifest['app_image']} /backup/probe.py"
    return json.loads(remote(command))


def backup(project):
    directory = BASE + "/" + PROJECTS[project]
    compose = "cd " + directory + " && docker compose "
    services = remote(compose + "ps --services --status running").split()
    assert set(services) <= {"db", "backend", "worker", "idp", "gateway", "migrate"} and "db" in services
    active = [s for s in services if s in {"backend", "worker", "idp", "gateway"}]
    assert {"backend", "idp", "gateway"} <= set(active)
    identifier = (
        project + "-" + datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
    )
    folder = BASE + "/backups/" + identifier
    remote(
        "umask 077; mkdir -p "
        + folder
        + "/settings; cp -a "
        + directory
        + "/.secrets "
        + folder
        + "/settings/; cp "
        + directory
        + "/.env "
        + directory
        + "/compose.yml "
        + directory
        + "/Caddyfile "
        + folder
        + "/settings/; test ! -f "
        + directory
        + "/source.tar || cp "
        + directory
        + "/source.tar "
        + folder
        + "/settings/"
    )
    remote("cat > " + folder + "/probe.py", (ROOT / "scripts/backup-probe.py").read_bytes())
    containers = {}
    for service in ("backend", "gateway", "db"):
        cid = remote(compose + "ps -q " + service).strip()
        containers[service] = json.loads(remote("docker inspect " + shlex.quote(cid)))[0]
    manifest = {
        "project": project,
        "directory": directory,
        "backup": folder,
        "app_image": containers["backend"]["Image"],
        "db_image": containers["db"]["Image"],
        "volumes": {},
        "created_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
    }
    for key, (service, destination) in VOLUMES.items():
        manifest["volumes"][key] = next(
            m["Name"] for m in containers[service]["Mounts"] if m["Destination"] == destination
        )
    network = next(iter(containers["backend"]["NetworkSettings"]["Networks"]))
    remote(compose + "stop " + " ".join(active))
    try:
        remote(compose + "exec -T db pg_dumpall -U ba_app --clean --if-exists > " + folder + "/cluster.sql")
        for key, volume in manifest["volumes"].items():
            remote(
                f"docker run --rm --network none --read-only --user 0 -v {volume}:/source:ro -v {folder}:/backup --entrypoint tar {manifest['app_image']} -cpf /backup/{key}.tar -C /source ."
            )
        manifest["inventory"] = probe(manifest, folder, network, manifest["volumes"])
        remote("cat > " + folder + "/manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode())
        remote(
            "cd "
            + folder
            + " && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS; chmod 700 "
            + folder
        )
    finally:
        remote(compose + "start " + " ".join(active))
    local = ROOT / ".run/backups" / identifier
    local.mkdir(parents=True)
    owner = os.environ["USERDOMAIN"] + "\\" + os.environ["USERNAME"]
    subprocess.run(
        ["icacls", str(local), "/inheritance:r", "/grant:r", owner + ":(OI)(CI)F", "SYSTEM:(OI)(CI)F"],
        check=True,
        capture_output=True,
    )
    remote("tar -cf " + folder + ".tar -C " + BASE + "/backups " + identifier + "; chmod 600 " + folder + ".tar")
    subprocess.run(
        ["scp", "-q", TARGET + ":" + folder + ".tar", str(local / "backup.tar")], check=True, capture_output=True
    )
    assert (
        hashlib.sha256((local / "backup.tar").read_bytes()).hexdigest()
        == remote("sha256sum " + folder + ".tar").split()[0]
    )
    (local / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf8")
    print("Backup retained privately: " + str(local), flush=True)
    return folder, local


def restore_check(folder, local):
    assert re.fullmatch(
        re.escape(BASE) + r"/backups/ba-(?:m01-compose|m07-acceptance)-[0-9]{8}-[0-9]{6}-[a-f0-9]{6}", folder
    )
    remote("cd " + folder + " && sha256sum -c SHA256SUMS > /dev/null")
    manifest = json.loads(remote("cat " + folder + "/manifest.json"))
    name = "ba-restore-" + uuid4().hex[:12]
    volumes = {key: name + "-" + key for key in VOLUMES}
    dbvolume = name + "-db"
    remote("docker network create " + name)
    for volume in [dbvolume, *volumes.values()]:
        remote("docker volume create " + volume)
    try:
        for key, volume in volumes.items():
            remote(
                f"docker run --rm --network none --read-only --user 0 -v {volume}:/target -v {folder}:/backup:ro --entrypoint tar {manifest['app_image']} -xpf /backup/{key}.tar -C /target"
            )
        remote(
            f"docker run -d --name {name} --network {name} --network-alias db -v {dbvolume}:/var/lib/postgresql/data -v {folder}/settings/.secrets/postgres.password:/run/secrets/postgres_password:ro -e TZ=Asia/Shanghai -e POSTGRES_USER=ba_restore -e POSTGRES_DB=restore_control -e POSTGRES_PASSWORD_FILE=/run/secrets/postgres_password {manifest['db_image']}"
        )
        for _ in range(60):
            ready = remote("docker inspect --format={{.State.Running}} " + name).strip()
            assert ready == "true"
            try:
                remote(f"docker exec {name} pg_isready -U ba_restore -d restore_control")
                break
            except RuntimeError:
                time.sleep(0.5)
        else:
            raise RuntimeError("Restore database did not become ready")
        remote(f"docker exec -i {name} psql -U ba_restore -d restore_control -v ON_ERROR_STOP=1 < {folder}/cluster.sql")
        restored = probe(manifest, folder, name, volumes)
        (local / "restore-observed.json").write_text(
            json.dumps(restored, ensure_ascii=False, indent=2), encoding="utf8"
        )
        assert restored == manifest["inventory"], "Restored tables, roles, files or credentials differ"
        result = {
            "status": "passed",
            "source_project": manifest["project"],
            "backup": folder,
            "restore_instance": name,
            "published_ports": [],
            "databases": list(restored["databases"]),
            "tables": sum(len(t) for t in restored["databases"].values()),
            "file_count": sum(len(t) for t in restored["files"].values()),
            "credentials_decrypted": restored["credentials_decrypted"],
            "source_logins": restored["source_logins"],
            "verified": [
                "all_table_rows_sha256",
                "database_roles",
                "application_files",
                "caddy_files",
                "key_files",
                "encrypted_credentials",
                "readonly_source_logins",
            ],
            "finished_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        }
        (local / "restore-check.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
        remote("cat > " + folder + "/restore-check.json", json.dumps(result, ensure_ascii=False, indent=2).encode())
        print(json.dumps(result, ensure_ascii=True), flush=True)
    finally:
        # Only UUID resources created above are removed; source projects and backup files are retained.
        assert re.fullmatch(r"ba-restore-[a-f0-9]{12}", name)
        remote("docker rm -f " + name + " >/dev/null 2>&1 || true")
        for volume in [dbvolume, *volumes.values()]:
            remote("docker volume rm " + volume)
        remote("docker network rm " + name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", choices=PROJECTS)
    parser.add_argument("--restore-check", action="store_true")
    args = parser.parse_args()
    folder, local = backup(args.project)
    if args.restore_check:
        restore_check(folder, local)
