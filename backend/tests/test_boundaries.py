import asyncio
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime

import asyncpg
import httpx2
import pytest
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet, RSAKey
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_auth import execute, login

from app.contracts import Evidence, SendRequest, SubscriptionReady
from app.core.security import redact, request_fingerprint, safe_path


def test_common_role_and_owner_guards(client, environment):
    user_a, _, _ = login(client, environment)
    with httpx2.Client(verify=environment["tls"], timeout=15) as another:
        user_b, _, _ = login(another, environment, "analyst_b")
        admin, _, _ = login(another, environment, "admin_demo")
    conversation_id = execute(
        environment, "INSERT INTO conversations(user_id) VALUES($1) RETURNING id", int(user_a["id"])
    )[0]["id"]

    async def check():
        from app.auth import admin_user, owned_conversation
        from app.core.errors import ApiError
        from app.models import User

        engine = create_async_engine(environment["url"], hide_parameters=True)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                owner = await db.get(User, int(user_a["id"]))
                assert (await owned_conversation(conversation_id, owner, db)).id == conversation_id
                with pytest.raises(ApiError) as denied:
                    await admin_user(owner)
                assert denied.value.status == 403
                for actor in (user_b, admin):
                    user = await db.get(User, int(actor["id"]))
                    with pytest.raises(ApiError) as denied:
                        await owned_conversation(conversation_id, user, db)
                    assert denied.value.status == 404
                administrator = await db.get(User, int(admin["id"]))
                assert await admin_user(administrator) == administrator
        finally:
            await engine.dispose()

    asyncio.run(check())


@pytest.mark.parametrize(
    "variation", ["issuer", "audience", "expired", "nonce", "signature", "algorithm", "subject", "azp"]
)
def test_id_token_validation_rejects_invalid_claims(environment, variation):
    from app.auth import validate_identity_token
    from app.core.config import get_settings

    timestamp = int(datetime.now(UTC).timestamp())
    key = RSAKey.generate_key(2048, {"kid": "test"})
    keys = KeySet.import_key_set({"keys": [key.as_dict()]})
    claims = {
        "iss": environment["idp"],
        "aud": get_settings().client_id,
        "sub": "subject-a",
        "iat": timestamp,
        "exp": timestamp + 60,
        "nonce": "test-nonce",
    }
    if variation == "issuer":
        claims["iss"] = "https://evil.invalid"
    if variation == "audience":
        claims["aud"] = "another-client"
    if variation == "expired":
        claims["exp"] = timestamp - 60
    if variation == "nonce":
        claims["nonce"] = "another-request"
    if variation == "subject":
        claims["sub"] = ""
    if variation == "azp":
        claims["aud"] = [get_settings().client_id, "another-client"]
    signer = RSAKey.generate_key(2048, {"kid": "test"}) if variation == "signature" else key
    encoded = jwt.encode(
        {"alg": "RS512" if variation == "algorithm" else "RS256", "kid": "test"},
        claims,
        signer,
        algorithms=["RS256", "RS512"],
    )
    with pytest.raises((JoseError, ValueError)):
        validate_identity_token(encoded, keys, "test-nonce")


def test_storage_paths_reject_traversal_and_junction(tmp_path):
    assert safe_path(tmp_path, "user", "conversation").is_relative_to(tmp_path)
    for name in ("..", ".", "a/b", "a\\b", "C:", "", "a\x00b"):
        with pytest.raises(ValueError):
            safe_path(tmp_path, name)
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    junction = tmp_path / "junction"
    if sys.platform == "win32":
        # Fixed test paths only; Windows junction creation does not require symlink privileges.
        import os

        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)], capture_output=True)
        assert result.returncode == 0
        try:
            with pytest.raises(ValueError):
                safe_path(tmp_path, "junction", "hidden")
        finally:
            os.rmdir(junction)
    else:
        junction.symlink_to(outside, target_is_directory=True)
        with pytest.raises(ValueError):
            safe_path(tmp_path, "junction", "hidden")


def test_evidence_shared_contract_and_control_frame():
    evidence = {
        "evidence_id": "e1",
        "source_type": "file",
        "source_name": "data.csv",
        "evidence_text": "counts",
        "related_metric": "visits",
        "confidence": "high",
        "source_locator": {"attachment_id": "a1", "file_name": "data.csv", "position": {"row": 2}},
    }
    assert Evidence.model_validate(evidence).source_locator.attachment_id == "a1"
    for field in evidence:
        with pytest.raises(ValidationError):
            Evidence.model_validate({k: v for k, v in evidence.items() if k != field})
    with pytest.raises(ValidationError):
        Evidence.model_validate({**evidence, "source_type": "database"})
    with pytest.raises(ValidationError):
        SubscriptionReady(conversation_id="1", created_at=datetime.now(UTC), task_id="1")


def test_enterprise_issuer_path_and_mapping_validation():
    from app.core.config import Settings

    settings = Settings(
        database_url="postgresql+asyncpg://localhost/test",
        public_url="https://app.example",
        issuer_url="https://identity.example/realms/business",
        issuer_internal_url="https://identity.example/realms/business",
        role_mapping={"company_analyst": "analyst", "company_admin": "admin"},
    )
    assert settings.issuer_url.endswith("/realms/business")
    with pytest.raises(ValidationError):
        Settings(database_url="postgresql+asyncpg://localhost/test", role_mapping={"unknown": "superadmin"})


def test_event_payload_is_required_and_input_references_match():
    from app.contracts import InputReference, TaskEvent

    common = {"conversation_id": "c1", "task_id": "t1", "event_id": 1, "created_at": "2026-10-23T00:00:00Z"}
    assert (
        TaskEvent.model_validate(
            {**common, "type": "message_delta", "message_id": "m1", "seq_no": 1, "delta_text": "hello"}
        ).root.delta_text
        == "hello"
    )
    with pytest.raises(ValidationError):
        TaskEvent.model_validate({**common, "type": "message_delta"})
    assert InputReference(type="attachment", attachment_id="a1").attachment_id == "a1"
    with pytest.raises(ValidationError):
        InputReference(type="output", attachment_id="a1")


def test_request_fingerprint_fixed_fixture():
    from app.core.config import ROOT

    for fixture in json.loads((ROOT / "backend/tests/fixtures/request-fingerprints.json").read_text(encoding="utf-8")):
        assert request_fingerprint(fixture["input"]) == fixture["sha256"]
        assert hashlib.sha256(fixture["normalized"].encode()).hexdigest() == fixture["sha256"]
    with pytest.raises(ValidationError):
        SendRequest(
            conversation_id="1",
            client_request_id="req",
            content=" question ",
            data_source_id="1",
            attachment_ids=["a", "a"],
        )
    with pytest.raises(ValidationError):
        SendRequest(conversation_id="1", client_request_id="req space", content="question", data_source_id="1")
    assert redact(
        {"password": "hidden", "nested": [{"credential_ref": "local-key"}], "url": "postgresql://user:secret@host/db"}
    ) == {
        "password": "[REDACTED]",
        "nested": [{"credential_ref": "[REDACTED]"}],
        "url": "postgresql://[REDACTED]@host/db",
    }


def test_database_integrity_migrations(environment):
    rows = execute(environment, "SELECT column_name FROM information_schema.columns WHERE table_name='analysis_tasks'")
    assert {
        "request_fingerprint",
        "config_snapshot_json",
        "attachment_snapshot_json",
        "cancel_requested_at",
        "final_report_scope_json",
    } <= {row["column_name"] for row in rows}
    row = execute(
        environment,
        "INSERT INTO data_sources(connection_target,database_name,schema_name) VALUES('{}','demo','business') RETURNING id",
    )[0]
    with pytest.raises(asyncpg.RaiseError):
        execute(environment, "UPDATE data_sources SET schema_name='other' WHERE id=$1", row["id"])
    foreign_keys = execute(environment, "SELECT conname FROM pg_constraint WHERE conname='fk_messages_task'")
    assert len(foreign_keys) == 1
