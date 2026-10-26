import asyncio
import os
import socket
import ssl
import subprocess
import sys
import time
import uuid
from pathlib import Path

import asyncpg
import httpx2
import pytest
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def environment():
    from app.core.config import get_settings

    initial = get_settings()
    database_name = "ba_test_" + uuid.uuid4().hex[:12]
    original_url = make_url(initial.database_url.get_secret_value())
    url = original_url.set(database=database_name)

    async def create_database():
        connection = await asyncpg.connect(
            original_url.set(drivername="postgresql").render_as_string(hide_password=False)
        )
        try:
            await connection.execute(f'CREATE DATABASE "{database_name}"')
        finally:
            await connection.close()

    for port in (8444, 9444, 18010, 18011):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                pytest.fail(f"Test port {port} is occupied; no existing process will be stopped")
    asyncio.run(create_database())
    saved = dict(os.environ)
    os.environ.update(
        BA_DATABASE_URL=url.render_as_string(hide_password=False),
        BA_PUBLIC_URL="https://localhost:8444",
        BA_ISSUER_URL="https://localhost:9444",
        BA_ISSUER_INTERNAL_URL="https://localhost:9444",
        BA_DATA_ROOT=str(ROOT / "data" / "test" / database_name),
        PYTHONPATH=str(ROOT / "backend"),
        PYTHONUTF8="1",
    )
    get_settings.cache_clear()
    work = ROOT / ".run" / database_name
    work.mkdir(parents=True)
    processes, handles = {}, []

    def start(name, args):
        output = (work / f"{name}.log").open("w", encoding="utf-8")
        handles.append(output)
        processes[name] = subprocess.Popen(
            args,
            cwd=ROOT,
            env=os.environ.copy(),
            stdout=output,
            stderr=output,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

    gateway = work / "Caddyfile"
    gateway.write_text(
        """{
 admin off
 auto_https disable_redirects
 skip_install_trust
 storage file_system {
  root .secrets/caddy
 }
}
https://localhost:8444 {
 bind 127.0.0.1
 tls internal
 @backend path /auth/login /auth/callback /auth/me /auth/logout /api/* /health/*
 handle @backend {
  reverse_proxy 127.0.0.1:18010
 }
 handle {
  reverse_proxy 127.0.0.1:5174
 }
}
https://localhost:9444 {
 bind 127.0.0.1
 tls internal
 reverse_proxy 127.0.0.1:18011
}
""",
        encoding="utf-8",
    )
    tls = ssl.create_default_context(cafile=str(initial.ca_file))
    try:
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=ROOT,
            env=os.environ,
            check=True,
            capture_output=True,
        )
        start(
            "backend",
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                "18010",
                "--no-access-log",
            ],
        )
        start(
            "idp",
            [
                sys.executable,
                "-m",
                "uvicorn",
                "demo_auth.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                "18011",
                "--no-access-log",
            ],
        )
        start(
            "gateway",
            [str(ROOT / ".tools" / "caddy" / "caddy.exe"), "run", "--config", str(gateway), "--adapter", "caddyfile"],
        )
        with httpx2.Client(verify=tls, timeout=2) as client:
            for _ in range(40):
                try:
                    if all(
                        client.get(base + "/health/ready").status_code == 200
                        for base in ("https://localhost:8444", "https://localhost:9444")
                    ):
                        break
                except httpx2.HTTPError:
                    pass
                time.sleep(0.25)
            else:
                pytest.fail("Isolated authentication services failed readiness; inspect .run/ba_test_*/")
        yield {
            "url": url,
            "tls": tls,
            "work": work,
            "processes": processes,
            "start": start,
            "app": "https://localhost:8444",
            "idp": "https://localhost:9444",
        }
    finally:
        for process in processes.values():
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
        for handle in handles:
            handle.close()

        async def drop_test_database():
            connection = await asyncpg.connect(
                original_url.set(drivername="postgresql").render_as_string(hide_password=False)
            )
            try:
                assert database_name.startswith("ba_test_") and len(database_name) == 20
                await connection.execute(f'DROP DATABASE "{database_name}" WITH (FORCE)')
            finally:
                await connection.close()

        asyncio.run(drop_test_database())
        os.environ.clear()
        os.environ.update(saved)
        get_settings.cache_clear()


@pytest.fixture
def client(environment):
    with httpx2.Client(verify=environment["tls"], timeout=30, follow_redirects=False) as client:
        yield client
