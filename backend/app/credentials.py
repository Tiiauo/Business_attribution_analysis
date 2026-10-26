"""Operator-only local secret replacement; reads the value without echoing it."""

import getpass

from app.core.config import get_settings
from app.core.security import cipher

if __name__ == "__main__":
    settings = get_settings()
    value = getpass.getpass("New OIDC client secret (hidden): ")
    if not value:
        raise SystemExit("Client secret must not be empty.")
    encrypted = cipher(settings.master_key_file).encrypt(value.encode())
    temporary = settings.client_secret_file.with_suffix(".new")
    try:
        with temporary.open("xb") as output:
            output.write(encrypted)
        temporary.chmod(0o600)
        temporary.replace(settings.client_secret_file)
    finally:
        temporary.unlink(missing_ok=True)
    print("OIDC client secret encrypted and replaced. Restart affected services to use the new registration.")
