"""Create local credentials once; never reset an existing environment."""

import json
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.config import ROOT


def create_file(path: Path, content: bytes) -> None:
    try:
        with path.open("xb") as output:
            output.write(content)
        path.chmod(0o600)
    except FileExistsError:
        pass


def prepare(root: Path = ROOT) -> None:
    secret_dir = root / ".secrets"
    secret_dir.mkdir(mode=0o700, exist_ok=True)
    create_file(secret_dir / "master.key", Fernet.generate_key())
    create_file(
        secret_dir / "oidc-client.secret",
        Fernet((secret_dir / "master.key").read_bytes()).encrypt(secrets.token_urlsafe(48).encode()),
    )
    create_file(secret_dir / "postgres.password", secrets.token_urlsafe(32).encode())
    if not (secret_dir / "idp.pem").exists():
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        create_file(
            secret_dir / "idp.pem",
            key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            ),
        )
    accounts = [
        {"username": username, "display_name": display, "role": role, "password": secrets.token_urlsafe(20)}
        for username, display, role in (
            ("analyst_demo", "分析用户", "analyst"),
            ("analyst_b", "第二分析用户", "analyst"),
            ("admin_demo", "管理员", "admin"),
        )
    ]
    create_file(secret_dir / "demo-accounts.json", json.dumps(accounts, ensure_ascii=False, indent=2).encode())
    password = (secret_dir / "postgres.password").read_text().strip()
    # Paths use forward slashes so dotenv does not interpret Windows backslashes.
    config = (
        f"BA_DATABASE_URL=postgresql+asyncpg://ba_app:{password}@127.0.0.1:15432/ba_dev\n"
        "BA_PUBLIC_URL=https://localhost:8443\nBA_ISSUER_URL=https://localhost:9443\n"
        "BA_ISSUER_INTERNAL_URL=https://localhost:9443\n"
        f"BA_CA_FILE={secret_dir.as_posix()}/ca.crt\n"
    )
    create_file(root / ".env", config.encode())
    print("Local configuration prepared; credentials stay in .secrets (no values printed).")


if __name__ == "__main__":
    prepare(Path(os.environ.get("BA_PREPARE_ROOT", str(ROOT))))
