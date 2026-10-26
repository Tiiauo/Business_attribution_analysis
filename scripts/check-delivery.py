"""Verify persisted demo reports and real low-disk protection on the delivery host."""

import hashlib
import json
import runpy
import shlex
import ssl
import sys
import time
from pathlib import Path

import httpx2

ROOT = Path(__file__).resolve().parents[1]
demo = runpy.run_path(str(ROOT / "scripts/demo-m07.py"))
APP, REMOTE, PACKAGE = (demo[key] for key in ("APP", "REMOTE", "PACKAGE"))
remote = demo["remote"]


def main(evidence):
    ledger = json.loads((evidence / "demos/demos.json").read_text(encoding="utf-8"))
    tls = ssl.create_default_context(cafile=str(PACKAGE / "ca.crt"))
    with httpx2.Client(verify=tls, timeout=30) as client:
        user = demo["login"](client)
        before = {}
        for name in ("behavior", "market"):
            case = ledger[name]
            tid = case["task_ids"][-1]
            value = client.get(APP + "/api/results/" + tid).json()
            assert value["final_report_scope"]["source_task_ids"] == case["task_ids"][:3]
            before[tid] = value["result_markdown"]
        remote(f"cd {REMOTE} && docker compose restart db backend worker idp gateway")
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                if client.get(APP + "/auth/me").status_code == 200:
                    break
            except httpx2.HTTPError:
                pass
            time.sleep(0.5)
        assert client.get(APP + "/auth/me").json()["id"] == user["id"]
        remote(f"cd {REMOTE} && docker compose up -d --wait")
        for name in ("behavior", "market"):
            case = ledger[name]
            tid = case["task_ids"][-1]
            value = client.get(APP + "/api/results/" + tid).json()
            assert value["result_markdown"] == before[tid]
            for kind, digest in case["downloads"].items():
                response = client.get(APP + f"/api/results/{tid}/download", params={"format": kind})
                assert response.status_code == 200 and hashlib.sha256(response.content).hexdigest() == digest
            history = client.get(APP + "/api/chat/ls/" + case["conversation_id"]).json()
            assert history["items"] and any(item["attachments"] for item in history["items"])
    probe = """import errno,json
from pathlib import Path
from app.core.security import require_storage
from app.core.errors import ApiError
old=Path('/probe/report.md');old.write_text('saved evidence')
try:
 require_storage()
 raise AssertionError('Low disk accepted a new write')
except ApiError as e:
 assert e.code=='INSUFFICIENT_STORAGE' and e.status==507
try:
 Path('/probe/oversize.tmp').write_bytes(b'x'*(6*1024*1024))
 raise AssertionError('Expected real ENOSPC')
except OSError as e:
 assert e.errno==errno.ENOSPC
assert old.read_text()=='saved evidence'
Path('/probe/oversize.tmp').unlink();old.unlink()
print(json.dumps({'low_disk_rejected':True,'real_enospc':True,'existing_readable':True,'cleanup_allowed':True}))
"""
    disk = remote(
        "docker run --rm --network none --read-only --user 10001:10001 "
        "--tmpfs /probe:rw,size=4m,mode=1777 -e BA_DATA_ROOT=/probe "
        "-e BA_DATABASE_URL=postgresql+asyncpg://probe@invalid/probe "
        "--entrypoint /app/.venv/bin/python ba-m01-app:0.1.0 -c " + shlex.quote(probe)
    )
    environment = remote(
        "uname -sr; getconf _NPROCESSORS_ONLN; free -m; df -h /; docker version --format '{{.Server.Version}}'; docker compose version"
    )
    state = remote(f"cd {REMOTE} && docker compose ps --format json")
    result = {
        "public_url": APP,
        "environment": environment,
        "network_conditions": {
            "browser_to_application": APP + "; independent OIDC " + demo["IDP"],
            "deployment": "SSH with pinned host key; database has no published host port",
            "model": "Worker used external HTTPS provider in both real demonstrations; no latency SLA",
            "sandbox": "No network; resource-bound containers tested separately from model calls",
        },
        "disk_probe": json.loads(disk),
        "services": [json.loads(line) for line in state.splitlines() if line.strip()],
        "persisted_demo_tasks": before.keys(),
    }
    result["persisted_demo_tasks"] = list(result["persisted_demo_tasks"])
    (evidence / "delivery.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        "Both real-model demo histories, attachments and byte-identical reports survived restart. Real low-disk/ENOSPC checks passed."
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]))
