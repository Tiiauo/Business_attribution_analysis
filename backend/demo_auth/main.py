import asyncio
import base64
import hashlib
import hmac
import html
import json
import re
from contextlib import asynccontextmanager
from datetime import timedelta
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from joserfc import jwt
from joserfc.jwk import RSAKey
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from app.auth import DB, now
from app.core.config import ROOT, get_settings
from app.core.database import Session, engine
from app.core.errors import ApiError, install_errors
from app.core.security import digest, random_token
from app.models import AuthorizationCode, AuthorizationInteraction, DemoAccessToken, DemoIdentity
from demo_auth.passwords import hash_password, verify_password


@asynccontextmanager
async def lifespan(app):
    settings = get_settings()
    signing_key()
    _ = settings.client_secret
    async with Session() as db:
        for account in json.loads(settings.demo_accounts_file.read_text(encoding="utf-8")):
            await db.execute(
                insert(DemoIdentity)
                .values(
                    username=account["username"],
                    display_name=account["display_name"],
                    role=account["role"],
                    password_hash=await asyncio.to_thread(hash_password, account["password"]),
                    status="active",
                )
                .on_conflict_do_nothing(index_elements=[DemoIdentity.username])
            )
        await db.commit()
    yield
    await engine.dispose()


app = FastAPI(title="独立演示认证服务", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
# Browsers apply form-action to the 303 redirect as well as the form submission.
install_errors(app, form_redirect_origin=get_settings().public_url)


@app.exception_handler(ApiError)
async def authorization_error(request: Request, error: ApiError):
    if request.url.path != "/authorize":
        return JSONResponse(
            {"code": error.code, "message": error.message, "request_id": request.state.request_id},
            status_code=error.status,
        )
    origin = html.escape(get_settings().public_url, quote=True)
    return HTMLResponse(
        f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>授权已失效</title>
<link rel="icon" href="data:,"><link rel="stylesheet" href="/auth.css"></head><body>
{idp_toolbar()}<main class="callback-main"><section class="callback-card" aria-live="polite">
{icon('<circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/>', 32)}
<h1>授权已失效</h1><p role="alert">{html.escape(error.message)}</p>
<a class="secondary" href="{origin}/auth/login">{ARROW}重新登录</a>
<p class="footnote">请求编号：{request.state.request_id}</p></section></main></body></html>''',
        status_code=error.status,
    )


def signing_key() -> RSAKey:
    return RSAKey.import_key(get_settings().signing_key_file.read_bytes(), {"kid": "demo-rsa-1", "use": "sig"})


@app.get("/.well-known/openid-configuration")
async def metadata():
    issuer = get_settings().issuer_url
    return {
        "issuer": issuer,
        "authorization_endpoint": issuer + "/authorize",
        "token_endpoint": issuer + "/token",
        "jwks_uri": issuer + "/jwks",
        "userinfo_endpoint": issuer + "/userinfo",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic"],
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": ["openid", "profile"],
    }


@app.get("/jwks")
async def jwks():
    return {"keys": [signing_key().as_dict(private=False)]}


@app.get("/health/ready", include_in_schema=False)
async def ready():
    async with Session() as db:
        if await db.scalar(text("SELECT version_num FROM alembic_version")) != "0003_m02":
            raise ApiError(503, "DEPENDENCY_UNAVAILABLE", "认证数据库尚未就绪。")
    return {"status": "ready"}


@app.get("/auth.css", include_in_schema=False)
async def stylesheet():
    return FileResponse(ROOT / "frontend" / "public" / "auth.css", media_type="text/css")


@app.get("/fonts/{weight}.ttf", include_in_schema=False)
async def font(weight: str):
    if weight not in {"noto-sans-sc-400", "noto-sans-sc-500", "noto-sans-sc-600"}:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "资源不存在。")
    return FileResponse(ROOT / "frontend" / "public" / "fonts" / (weight + ".ttf"), media_type="font/ttf")


def icon(paths: str, size: int) -> str:
    return f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{paths}</svg>'


SHIELD = icon(
    '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/>',
    36,
)
CHART = icon(
    '<path d="M12 16v5M16 14.639V21M20 10.656V21m2-18-8.646 8.646a.5.5 0 0 1-.708 0L9.354 8.354a.5.5 0 0 0-.707 0L2 15m2 3.463V21m4-6.344V21"/>',
    23,
)
INFO = icon('<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>', 18)
ARROW = icon('<path d="M5 12h14m-7-7 7 7-7 7"/>', 16)


def idp_toolbar() -> str:
    return (
        '<header class="toolbar"><div class="brand"><span class="accent">'
        + CHART
        + "</span><strong>经营归因分析系统</strong></div></header>"
    )


def authorization_form(csrf: str, error: str = "", selected_role: str = "analyst") -> HTMLResponse:
    alert = (
        f'<div class="danger-box" role="alert">{INFO}<div><strong>登录未完成</strong>'
        f"<p>{html.escape(error)}</p></div></div>"
        if error
        else ""
    )
    selected = " selected" if selected_role == "admin" else ""
    return HTMLResponse(f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>授权登录 · 演示认证服务</title>
<link rel="icon" href="data:,"><link rel="stylesheet" href="/auth.css"></head><body>
{idp_toolbar()}<main class="idp-main"><section class="idp-card" aria-labelledby="auth-title">
<div class="accent idp-icon">{SHIELD}</div><h1 id="auth-title">授权登录</h1><p class="subtitle">演示认证服务</p>
<div class="info">{INFO}<div><strong>应用请求访问基本身份</strong><p>授权后返回经营归因分析系统。账号凭证由认证服务校验。</p></div></div>
{alert}<form method="post" action="/authorize" class="auth-form">
<input type="hidden" name="csrf" value="{html.escape(csrf, quote=True)}">
<div class="auth-field"><label for="identity-role">测试身份</label><select id="identity-role" name="role"><option value="analyst">分析用户 · analyst</option><option value="admin"{selected}>管理员 · admin</option></select></div>
<div class="auth-field"><label for="username">登录名</label><input id="username" name="username" autocomplete="username" placeholder="analyst_demo" maxlength="100" required></div>
<div class="auth-field"><label for="password">密码</label><input id="password" name="password" type="password" autocomplete="current-password" maxlength="200" required></div>
<button class="primary" type="submit">{ARROW}登录并授权</button></form>
<p class="footnote footnote--card">管理员测试身份同样通过本服务认证。<br>此界面仅表达演示授权流程，不展示真实凭证。</p>
</section></main></body></html>''')


@app.get("/authorize")
async def authorize(request: Request, db: DB):
    params = dict(request.query_params)
    settings = get_settings()
    if (
        params.get("client_id") != settings.client_id
        or params.get("redirect_uri") != settings.callback_url
        or params.get("response_type") != "code"
        or set(params.get("scope", "").split()) != {"openid", "profile"}
        or params.get("code_challenge_method") != "S256"
        or not re.fullmatch(r"[A-Za-z0-9_-]{43}", params.get("code_challenge", ""))
        or any(not 16 <= len(params.get(name, "")) <= 200 for name in ("state", "nonce"))
        or any(len(request.query_params.getlist(name)) != 1 for name in params)
    ):
        raise ApiError(400, "INVALID_AUTHORIZATION_REQUEST", "授权参数无效，请返回应用重新登录。")
    browser, csrf = random_token(), random_token()
    db.add(
        AuthorizationInteraction(
            token_hash=digest(browser),
            csrf_hash=digest(csrf),
            parameters=params,
            expires_at=now() + timedelta(minutes=5),
        )
    )
    await db.commit()
    response = authorization_form(csrf)
    response.set_cookie("ba_idp", browser, max_age=300, secure=True, httponly=True, samesite="lax", path="/authorize")
    return response


@app.post("/authorize")
async def submit_authorization(request: Request, db: DB):
    if request.headers.get("origin") != get_settings().issuer_url:
        raise ApiError(403, "CSRF_INVALID", "授权请求来源无效，请重新登录。")
    form = await request.form(max_fields=4, max_files=0)
    if (
        set(form) != {"csrf", "username", "password", "role"}
        or any(not isinstance(value, str) or len(value) > 200 for _, value in form.multi_items())
        or any(len(form.getlist(name)) != 1 for name in form)
    ):
        raise ApiError(400, "INVALID_REQUEST", "登录参数无效。")
    browser = request.cookies.get("ba_idp", "")
    interaction = await db.scalar(
        select(AuthorizationInteraction)
        .where(
            AuthorizationInteraction.token_hash == digest(browser),
            AuthorizationInteraction.consumed_at.is_(None),
            AuthorizationInteraction.expires_at > now(),
            AuthorizationInteraction.failed_attempts < 5,
        )
        .with_for_update()
    )
    if interaction is None or not hmac.compare_digest(interaction.csrf_hash, digest(form["csrf"])):
        raise ApiError(403, "AUTHORIZATION_EXPIRED", "授权已失效，请返回应用重新登录。")
    identity = await db.scalar(select(DemoIdentity).where(DemoIdentity.username == form["username"]))
    # Constant-cost verification also for unknown users.
    encoded = identity.password_hash if identity else await asyncio.to_thread(hash_password, "unavailable")
    valid = await asyncio.to_thread(verify_password, form["password"], encoded)
    if not valid or identity is None or identity.status != "active" or identity.role != form["role"]:
        interaction.failed_attempts += 1
        await db.commit()
        response = authorization_form(form["csrf"], "用户名、密码或身份类型不正确，或账户不可用。", form["role"])
        response.status_code = 401
        return response
    interaction.consumed_at = now()
    code = random_token()
    db.add(
        AuthorizationCode(
            code_hash=digest(code),
            identity_id=identity.id,
            parameters=interaction.parameters,
            expires_at=now() + timedelta(seconds=60),
        )
    )
    await db.commit()
    response = RedirectResponse(
        get_settings().callback_url
        + "?"
        + urlencode({"code": code, "state": interaction.parameters["state"], "iss": get_settings().issuer_url}),
        status_code=303,
    )
    response.delete_cookie("ba_idp", path="/authorize", secure=True, httponly=True, samesite="lax")
    return response


def oauth_error(error: str, status: int = 400) -> JSONResponse:
    headers = {"WWW-Authenticate": "Basic"} if status == 401 else {}
    return JSONResponse({"error": error}, status_code=status, headers=headers)


@app.post("/token")
async def token(request: Request, db: DB):
    settings = get_settings()
    try:
        scheme, encoded = request.headers.get("authorization", "").split(" ", 1)
        from urllib.parse import unquote_plus

        client_id, secret = base64.b64decode(encoded, validate=True).decode().split(":", 1)
        if (
            scheme.lower() != "basic"
            or unquote_plus(client_id) != settings.client_id
            or not hmac.compare_digest(unquote_plus(secret), settings.client_secret)
        ):
            return oauth_error("invalid_client", 401)
    except (ValueError, UnicodeError):
        return oauth_error("invalid_client", 401)
    form = await request.form(max_fields=5, max_files=0)
    if (
        set(form) != {"grant_type", "code", "redirect_uri", "code_verifier"}
        or any(not isinstance(value, str) or len(value) > 500 for _, value in form.multi_items())
        or any(len(form.getlist(name)) != 1 for name in form)
        or form["grant_type"] != "authorization_code"
        or form["redirect_uri"] != settings.callback_url
        or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", form["code_verifier"])
    ):
        return oauth_error("invalid_request")
    code = await db.scalar(
        select(AuthorizationCode)
        .where(
            AuthorizationCode.code_hash == digest(form["code"]),
            AuthorizationCode.consumed_at.is_(None),
            AuthorizationCode.expires_at > now(),
        )
        .with_for_update()
    )
    if code is None:
        return oauth_error("invalid_grant")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()).rstrip(b"=").decode()
    if not hmac.compare_digest(code.parameters["code_challenge"], challenge):
        return oauth_error("invalid_grant")
    identity = await db.get(DemoIdentity, code.identity_id)
    if identity is None or identity.status != "active":
        return oauth_error("invalid_grant")
    code.consumed_at = now()
    timestamp = int(now().timestamp())
    claims = {
        "iss": settings.issuer_url,
        "sub": str(identity.id),
        "aud": settings.client_id,
        "iat": timestamp,
        "exp": timestamp + 300,
        "auth_time": timestamp,
        "nonce": code.parameters["nonce"],
        "preferred_username": identity.username,
        "name": identity.display_name,
        "roles": [identity.role],
    }
    id_token = jwt.encode({"alg": "RS256", "kid": "demo-rsa-1"}, claims, signing_key())
    access_token = random_token()
    db.add(
        DemoAccessToken(
            token_hash=digest(access_token), identity_id=identity.id, expires_at=now() + timedelta(minutes=5)
        )
    )
    await db.commit()
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": 300,
        "id_token": id_token,
        "scope": "openid profile",
    }


@app.get("/userinfo")
async def userinfo(request: Request, db: DB):
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or len(value) > 200:
        raise ApiError(401, "INVALID_TOKEN", "身份令牌无效。")
    identity = await db.scalar(
        select(DemoIdentity)
        .join(DemoAccessToken)
        .where(
            DemoAccessToken.token_hash == digest(value),
            DemoAccessToken.expires_at > now(),
            DemoIdentity.status == "active",
        )
    )
    if identity is None:
        raise ApiError(401, "INVALID_TOKEN", "身份令牌无效。")
    return {
        "sub": str(identity.id),
        "name": identity.display_name,
        "preferred_username": identity.username,
        "roles": [identity.role],
    }
