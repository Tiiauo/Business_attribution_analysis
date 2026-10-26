from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BA_", env_file=ROOT / ".env", extra="ignore", hide_input_in_errors=True
    )

    database_url: SecretStr
    public_url: str = "https://localhost:8443"
    issuer_url: str = "https://localhost:9443"
    issuer_internal_url: str = "https://localhost:9443"
    client_id: str = "business-attribution"
    client_secret_file: Path = ROOT / ".secrets" / "oidc-client.secret"
    master_key_file: Path = ROOT / ".secrets" / "master.key"
    signing_key_file: Path = ROOT / ".secrets" / "idp.pem"
    demo_accounts_file: Path = ROOT / ".secrets" / "demo-accounts.json"
    ca_file: Path | None = None
    data_root: Path = ROOT / "data" / "dev"
    cookie_secure: bool = True
    session_seconds: int = 28800
    role_claim: str = "roles"
    role_mapping: dict[str, str] = {"analyst": "analyst", "admin": "admin"}
    docker_host: str = "http://127.0.0.1:12375"
    sandbox_image: str = "python@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2"

    @field_validator("public_url", "issuer_url", "issuer_internal_url")
    @classmethod
    def valid_url(cls, value: str, info) -> str:
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Service URL must be an HTTP(S) origin without credentials")
        if parsed.query or parsed.fragment or (info.field_name == "public_url" and parsed.path not in {"", "/"}):
            raise ValueError("Service URL must not contain a query/fragment; application URL must be an origin")
        return value.rstrip("/")

    @field_validator("session_seconds")
    @classmethod
    def fixed_session(cls, value: int) -> int:
        if value != 28800:
            raise ValueError("M01 sessions have a fixed eight-hour lifetime")
        return value

    @model_validator(mode="after")
    def secure_transport(self):
        if not self.cookie_secure or not all(url.startswith("https://") for url in (self.public_url, self.issuer_url)):
            raise ValueError("Public application and identity provider require HTTPS and secure cookies")
        if not self.role_mapping or not set(self.role_mapping.values()) <= {"analyst", "admin"}:
            raise ValueError("Role mapping must resolve to analyst or admin")
        return self

    @property
    def callback_url(self) -> str:
        return f"{self.public_url}/auth/callback"

    @property
    def client_secret(self) -> str:
        from app.core.security import cipher

        return cipher(self.master_key_file).decrypt(self.client_secret_file.read_bytes().strip()).decode()


@lru_cache
def get_settings() -> Settings:
    return Settings()
