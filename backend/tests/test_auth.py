import asyncio
import json
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit

import asyncpg
import httpx2
import pytest


def execute(environment, sql, *args):
    async def run():
        connection = await asyncpg.connect(
            environment["url"].set(drivername="postgresql").render_as_string(hide_password=False)
        )
        try:
            return await connection.fetch(sql, *args)
        finally:
            await connection.close()

    return asyncio.run(run())


def account(username="analyst_demo"):
    from app.core.config import get_settings

    return next(
        row
        for row in json.loads(get_settings().demo_accounts_file.read_text(encoding="utf-8"))
        if row["username"] == username
    )


def authorization(client, environment, username="analyst_demo", password=None):
    start = client.get(environment["app"] + "/auth/login")
    assert start.status_code == 303
    authorize_url = start.headers["location"]
    form = client.get(authorize_url)
    assert form.status_code == 200
    csrf = re.search(r'name="csrf" value="([^"]+)"', form.text).group(1)
    identity = account(username)
    response = client.post(
        environment["idp"] + "/authorize",
        data={
            "csrf": csrf,
            "username": username,
            "password": password or identity["password"],
            "role": identity["role"],
        },
        headers={"Origin": environment["idp"]},
    )
    return response, authorize_url


def login(client, environment, username="analyst_demo"):
    authorized, _ = authorization(client, environment, username)
    assert authorized.status_code == 303
    callback_url = authorized.headers["location"]
    callback = client.get(callback_url)
    assert callback.status_code == 303
    assert "error=" not in callback.headers["location"]
    response = client.get(environment["app"] + "/auth/me")
    assert response.status_code == 200
    return response.json(), callback_url, callback


@pytest.mark.parametrize(
    "username,role", [("analyst_demo", "analyst"), ("analyst_b", "analyst"), ("admin_demo", "admin")]
)
def test_real_oidc_roles_and_cookie(client, environment, username, role):
    user, _, response = login(client, environment, username)
    assert user["role"] == role and user["status"] == "active" and user["username"] == username
    assert json.loads(user["external_user_id"])[0] == environment["idp"]
    cookies = response.headers.get_list("set-cookie")
    session = next(value for value in cookies if value.startswith("ba_session="))
    assert all(value in session for value in ("HttpOnly", "Secure", "SameSite=lax", "Max-Age=28800"))
    assert "id_token" not in user and "access_token" not in user


def test_logout_requires_csrf_and_revokes(client, environment):
    user, _, _ = login(client, environment)
    url = environment["app"] + "/auth/logout"
    assert client.post(url).status_code == 403
    assert (
        client.post(url, headers={"Origin": "https://evil.invalid", "X-CSRF-Token": user["csrf_token"]}).status_code
        == 403
    )
    token = client.cookies.get("ba_session")
    response = client.post(url, headers={"Origin": environment["app"], "X-CSRF-Token": user["csrf_token"]})
    assert response.status_code == 303
    assert client.get(environment["app"] + "/auth/me").status_code == 401
    assert client.get(environment["app"] + "/auth/me", headers={"Cookie": f"ba_session={token}"}).status_code == 401


def test_wrong_password_and_idp_csrf(client, environment):
    response, _ = authorization(client, environment, password="wrong-password")
    assert response.status_code == 401 and "wrong-password" not in response.text
    assert client.get(environment["app"] + "/auth/me").status_code == 401
    assert client.post(environment["idp"] + "/authorize", data={}).status_code == 403


def test_login_interaction_limits_attempts(client, environment):
    response, _ = authorization(client, environment, password="wrong-password")
    csrf = re.search(r'name="csrf" value="([^"]+)"', response.text).group(1)
    for _ in range(4):
        response = client.post(
            environment["idp"] + "/authorize",
            data={"csrf": csrf, "username": "analyst_demo", "password": "wrong-password", "role": "analyst"},
            headers={"Origin": environment["idp"]},
        )
        assert response.status_code == 401
    identity = account()
    response = client.post(
        environment["idp"] + "/authorize",
        data={
            "csrf": csrf,
            "username": identity["username"],
            "password": identity["password"],
            "role": identity["role"],
        },
        headers={"Origin": environment["idp"]},
    )
    assert response.status_code == 403
    assert client.get(environment["app"] + "/auth/me").status_code == 401


@pytest.mark.parametrize("variation", ["missing", "state", "code", "expired", "browser", "issuer", "duplicate"])
def test_callback_rejects_tampering(client, environment, variation):
    response, _ = authorization(client, environment)
    url = response.headers["location"]
    if variation == "missing":
        url = environment["app"] + "/auth/callback"
    elif variation == "state":
        url = re.sub(r"state=[^&]+", "state=invalid", url)
    elif variation == "code":
        url = re.sub(r"code=[^&]+", "code=invalid", url)
    elif variation == "expired":
        execute(environment, "UPDATE login_attempts SET expires_at=now()-interval '1 second' WHERE consumed_at IS NULL")
    elif variation == "browser":
        client.cookies.clear()
    elif variation == "issuer":
        url = re.sub(r"iss=.*", "iss=https%3A%2F%2Fevil.invalid", url)
    elif variation == "duplicate":
        url += "&state=another"
    callback = client.get(url)
    assert "error=" in callback.headers["location"]
    assert not any(value.startswith("ba_session=") for value in callback.headers.get_list("set-cookie"))
    assert client.get(environment["app"] + "/auth/me").status_code == 401


def test_callback_and_authorization_code_are_single_use(client, environment):
    _, callback_url, _ = login(client, environment)
    assert "error=" in client.get(callback_url).headers["location"]
    code = parse_qs(urlsplit(callback_url).query)["code"][0]
    from app.core.config import get_settings

    response = client.post(
        environment["idp"] + "/token",
        auth=(get_settings().client_id, get_settings().client_secret),
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": get_settings().callback_url,
            "code_verifier": "x" * 43,
        },
    )
    assert response.status_code == 400 and response.json()["error"] == "invalid_grant"


def test_concurrent_callback_establishes_only_one_session(client, environment):
    response, _ = authorization(client, environment)
    callback_url = response.headers["location"]
    cookies = dict(client.cookies)

    def callback():
        with httpx2.Client(verify=environment["tls"], timeout=15, cookies=cookies) as requester:
            return requester.get(callback_url)

    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: callback(), range(2)))
    assert sum("error=" not in reply.headers["location"] for reply in replies) == 1
    assert (
        sum(any(value.startswith("ba_session=") for value in reply.headers.get_list("set-cookie")) for reply in replies)
        == 1
    )


def test_pkce_expired_code_and_client_credentials(client, environment):
    response, _ = authorization(client, environment)
    code = parse_qs(urlsplit(response.headers["location"]).query)["code"][0]
    from app.core.config import get_settings

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": get_settings().callback_url,
        "code_verifier": "x" * 43,
    }
    url = environment["idp"] + "/token"
    assert client.post(url, data=data).status_code == 401
    response = client.post(url, data=data, auth=(get_settings().client_id, get_settings().client_secret))
    assert response.status_code == 400 and response.json()["error"] == "invalid_grant"
    execute(
        environment,
        "UPDATE demo_authorization_codes SET expires_at=now()-interval '1 second' WHERE code_hash IS NOT NULL",
    )
    assert client.post(url, data=data, auth=(get_settings().client_id, get_settings().client_secret)).status_code == 400


def test_session_expiry_and_disabled_user(client, environment):
    user, _, _ = login(client, environment)
    execute(environment, "UPDATE users SET status='disabled' WHERE id=$1", int(user["id"]))
    try:
        assert client.get(environment["app"] + "/auth/me").status_code == 403
        response, _ = authorization(client, environment)
        callback = client.get(response.headers["location"])
        assert "ACCOUNT_DISABLED" in callback.headers["location"]
    finally:
        execute(environment, "UPDATE users SET status='active' WHERE id=$1", int(user["id"]))
    execute(
        environment, "UPDATE user_sessions SET expires_at=now()-interval '1 second' WHERE user_id=$1", int(user["id"])
    )
    assert client.get(environment["app"] + "/auth/me").status_code == 401


def test_restart_preserves_session_migrations_and_audit(client, environment):
    user, _, _ = login(client, environment)
    process = environment["processes"]["backend"]
    process.terminate()
    process.wait(timeout=10)
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True, capture_output=True)
    subprocess.run([sys.executable, "-m", "alembic", "check"], check=True, capture_output=True)
    environment["start"](
        "backend",
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "18010", "--no-access-log"],
    )
    for _ in range(30):
        try:
            response = client.get(environment["app"] + "/auth/me")
            if response.status_code == 200:
                break
        except httpx2.HTTPError:
            pass
        time.sleep(0.2)
    else:
        pytest.fail("Backend did not recover with persisted login session")
    assert response.json()["id"] == user["id"]
    rows = execute(environment, "SELECT operator_id,log_content FROM task_logs WHERE operator_id=$1", int(user["id"]))
    assert rows and all("password" not in row["log_content"] for row in rows)


def test_errors_have_request_id_and_bound_body(client, environment):
    response = client.get(environment["app"] + "/auth/me")
    assert response.status_code == 401 and set(response.json()) == {"code", "message", "request_id"}
    assert response.headers["cache-control"] == "no-store"
    response = client.post(
        environment["app"] + "/auth/logout", content=b"x" * 256001, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413 and response.json()["code"] == "REQUEST_TOO_LARGE"
