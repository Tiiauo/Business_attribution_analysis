import hmac
import json
import ssl
from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import urlsplit

import httpx2
from authlib.integrations.httpx_client import AsyncOAuth2Client
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from joserfc import jwt
from joserfc.jwk import KeySet
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts import UserInfo
from app.core.audit import record_event
from app.core.config import get_settings
from app.core.database import get_db
from app.core.errors import ApiError
from app.core.security import cipher, csrf_token, digest, log_event, random_token
from app.models import Conversation, LoginAttempt, User, UserSession
from app.runtime import AuthConfig, published_runtime, resolve_credential

router = APIRouter(prefix="/auth", tags=["authentication"])
DB = Annotated[AsyncSession, Depends(get_db)]


def now() -> datetime:
    return datetime.now(UTC)


def tls_context():
    settings = get_settings()
    context = ssl.create_default_context()
    if settings.ca_file:
        context.load_verify_locations(cafile=str(settings.ca_file))
    return context


def endpoint_address(value: str, settings=None) -> str:
    settings = settings or get_settings()
    if value.startswith(settings.issuer_url + "/"):
        return settings.issuer_internal_url + value[len(settings.issuer_url) :]
    if not value.startswith("https://"):
        raise ValueError("Provider endpoints must use HTTPS")
    return value


async def provider_metadata(settings=None) -> dict:
    settings = settings or get_settings()
    async with httpx2.AsyncClient(verify=tls_context(), timeout=10, follow_redirects=False) as client:
        response = await client.get(f"{settings.issuer_internal_url}/.well-known/openid-configuration")
        response.raise_for_status()
        document = response.json()
    if document.get("issuer") != settings.issuer_url:
        raise ValueError("Provider issuer mismatch")
    for name in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not isinstance(document.get(name), str):
            raise ValueError("Provider metadata is incomplete")
    endpoint = urlsplit(document["authorization_endpoint"])
    issuer = urlsplit(settings.issuer_url)
    if (endpoint.scheme, endpoint.netloc) != (issuer.scheme, issuer.netloc):
        raise ValueError("Authorization endpoint must belong to configured issuer")
    return document


async def current_session(request: Request, db: DB) -> tuple[User, UserSession]:
    token = request.cookies.get("ba_session", "")
    if not token or len(token) > 200:
        raise ApiError(401, "UNAUTHENTICATED", "请先通过认证中心登录。")
    row = (
        await db.execute(
            select(User, UserSession)
            .join(UserSession, User.id == UserSession.user_id)
            .where(
                UserSession.token_hash == digest(token),
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > now(),
            )
        )
    ).first()
    if row is None:
        raise ApiError(401, "SESSION_EXPIRED", "登录已失效，请重新登录。")
    user, session = row
    if user.status != "active":
        raise ApiError(403, "ACCOUNT_DISABLED", "账户已停用，请联系管理员。")
    return user, session


async def current_user(pair: Annotated[tuple[User, UserSession], Depends(current_session)]) -> User:
    return pair[0]


async def admin_user(user: Annotated[User, Depends(current_user)]) -> User:
    if user.role != "admin":
        raise ApiError(403, "FORBIDDEN", "此操作需要管理员权限。")
    return user


async def owned_conversation(
    conversation_id: int, user: User, db: AsyncSession, *, lock=False, include_deleted=False
) -> Conversation:
    query = select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == user.id)
    if not include_deleted:
        query = query.where(Conversation.status != "deleted")
    row = await db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问。")
    return row


def validate_origin(request: Request) -> None:
    if request.headers.get("origin") != get_settings().public_url:
        raise ApiError(403, "CSRF_INVALID", "请求来源无效，请刷新页面后重试。")


def validate_csrf(request: Request) -> None:
    validate_origin(request)
    expected = csrf_token(request.cookies.get("ba_session", ""), get_settings().master_key_file)
    if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), expected):
        raise ApiError(403, "CSRF_INVALID", "请求校验失效，请刷新后重试。")


async def write_user(request: Request, user: Annotated[User, Depends(current_user)]) -> User:
    validate_csrf(request)
    return user


@router.get("/login")
async def login(request: Request, db: DB):
    settings = get_settings()
    auth = (await published_runtime(db)).auth
    try:
        metadata = await provider_metadata(auth)
    except (httpx2.HTTPError, ValueError, KeyError):
        return callback_error(request, "AUTH_PROVIDER_UNAVAILABLE")
    state, browser, nonce, verifier = (random_token() for _ in range(4))
    async with AsyncOAuth2Client(
        auth.client_id, redirect_uri=auth.callback_url, scope="openid profile", code_challenge_method="S256"
    ) as client:
        url, _ = client.create_authorization_url(
            metadata["authorization_endpoint"], state=state, nonce=nonce, code_verifier=verifier
        )
    db.add(
        LoginAttempt(
            state_hash=digest(state),
            browser_hash=digest(browser),
            nonce=nonce,
            verifier_encrypted=cipher(settings.master_key_file).encrypt(verifier.encode()).decode(),
            expires_at=now() + timedelta(minutes=5),
            auth_config=auth.model_dump(),
        )
    )
    await db.commit()
    response = RedirectResponse(url, status_code=303)
    response.set_cookie(
        "ba_login", browser, max_age=300, secure=settings.cookie_secure, httponly=True, samesite="lax", path="/auth"
    )
    return response


async def exchange_identity(code: str, attempt: LoginAttempt, db: AsyncSession) -> dict:
    settings = get_settings()
    auth = AuthConfig.model_validate(attempt.auth_config) if attempt.auth_config else (await published_runtime(db)).auth
    metadata = await provider_metadata(auth)
    verifier = cipher(settings.master_key_file).decrypt(attempt.verifier_encrypted.encode()).decode()
    async with AsyncOAuth2Client(
        auth.client_id,
        await resolve_credential(db, auth.credential_ref),
        redirect_uri=auth.callback_url,
        token_endpoint_auth_method="client_secret_basic",
        verify=tls_context(),
        timeout=10,
    ) as client:
        token = await client.fetch_token(
            endpoint_address(metadata["token_endpoint"], auth),
            grant_type="authorization_code",
            code=code,
            code_verifier=verifier,
        )
    async with httpx2.AsyncClient(verify=tls_context(), timeout=10) as client:
        response = await client.get(endpoint_address(metadata["jwks_uri"], auth))
        response.raise_for_status()
        keys = KeySet.import_key_set(response.json())
    return validate_identity_token(token["id_token"], keys, attempt.nonce, auth)


def validate_identity_token(encoded: str, keys: KeySet, nonce: str, settings=None) -> dict:
    settings = settings or get_settings()
    decoded = jwt.decode(encoded, keys, algorithms=["RS256"])
    jwt.JWTClaimsRegistry(
        iss={"essential": True, "value": settings.issuer_url},
        aud={"essential": True, "value": settings.client_id},
        sub={"essential": True},
        exp={"essential": True},
        iat={"essential": True},
        nonce={"essential": True, "value": nonce},
    ).validate(decoded.claims)
    if not isinstance(decoded.claims["sub"], str) or not decoded.claims["sub"]:
        raise ValueError("Invalid subject")
    if isinstance(decoded.claims["aud"], list) and len(decoded.claims["aud"]) > 1:
        if decoded.claims.get("azp") != settings.client_id:
            raise ValueError("Invalid authorized party")
    return decoded.claims


@router.get("/callback")
async def callback(request: Request, db: DB):
    settings = get_settings()
    failure = "AUTH_CALLBACK_INVALID"
    state, code = request.query_params.get("state", ""), request.query_params.get("code", "")
    browser = request.cookies.get("ba_login", "")
    if (
        not state
        or not browser
        or not code
        or max(len(state), len(browser), len(code)) > 200
        or any(len(request.query_params.getlist(name)) != 1 for name in request.query_params)
    ):
        return callback_error(request, failure)
    attempt = (
        await db.execute(
            update(LoginAttempt)
            .where(
                LoginAttempt.state_hash == digest(state),
                LoginAttempt.browser_hash == digest(browser),
                LoginAttempt.expires_at > now(),
                LoginAttempt.consumed_at.is_(None),
            )
            .values(consumed_at=now())
            .returning(LoginAttempt)
        )
    ).scalar_one_or_none()
    await db.commit()
    if attempt is None:
        return callback_error(request, failure)
    auth = AuthConfig.model_validate(attempt.auth_config) if attempt.auth_config else (await published_runtime(db)).auth
    if request.query_params.get("iss", auth.issuer_url) != auth.issuer_url:
        return callback_error(request, failure)
    try:
        claims = await exchange_identity(code, attempt, db)
        roles = claims.get(auth.role_claim, [])
        roles = [roles] if isinstance(roles, str) else roles
        if not isinstance(roles, list) or not all(isinstance(role, str) for role in roles):
            raise ValueError("Invalid role claim")
        mapped = {auth.role_mapping[role] for role in roles if role in auth.role_mapping}
        if not mapped.intersection({"analyst", "admin"}):
            return callback_error(request, "AUTH_ROLE_UNMAPPED")
        role = "admin" if "admin" in mapped else "analyst"
        external_id = json.dumps([auth.issuer_url, claims["sub"]], ensure_ascii=False, separators=(",", ":"))
        username = str(claims.get("preferred_username") or claims["sub"])[:200]
        display_name = str(claims.get("name") or username)[:200]
        statement = insert(User).values(
            external_user_id=external_id, username=username, display_name=display_name, role=role, status="active"
        )
        statement = statement.on_conflict_do_update(
            index_elements=[User.external_user_id],
            set_={"username": username, "display_name": display_name, "role": role, "updated_at": now()},
        ).returning(User)
        user = (await db.execute(statement)).scalar_one()
        if user.status != "active":
            await db.rollback()
            return callback_error(request, "ACCOUNT_DISABLED")
        session_token = random_token()
        db.add(
            UserSession(
                user_id=user.id,
                token_hash=digest(session_token),
                expires_at=now() + timedelta(seconds=settings.session_seconds),
            )
        )
        record_event(db, "login_succeeded", operator_id=user.id, target_type="user", target_id=str(user.id))
        await db.commit()
    except Exception as error:
        await db.rollback()
        log_event("auth_callback_rejected", error_type=type(error).__name__, request_id=request.state.request_id)
        return callback_error(request, "AUTH_PROVIDER_INVALID")
    response = RedirectResponse(f"{settings.public_url}/auth/complete", status_code=303)
    response.set_cookie(
        "ba_session",
        session_token,
        max_age=settings.session_seconds,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
        path="/",
    )
    response.delete_cookie("ba_login", path="/auth", secure=settings.cookie_secure, httponly=True, samesite="lax")
    log_event("login_succeeded", operator_id=user.id, request_id=request.state.request_id)
    return response


def callback_error(request: Request, code: str) -> RedirectResponse:
    settings = get_settings()
    response = RedirectResponse(
        f"{settings.public_url}/auth/complete?error={code}&request_id={request.state.request_id}", status_code=303
    )
    response.delete_cookie("ba_login", path="/auth", secure=settings.cookie_secure, httponly=True, samesite="lax")
    return response


@router.get("/me", response_model=UserInfo)
async def me(request: Request, pair: Annotated[tuple[User, UserSession], Depends(current_session)]):
    user, session = pair
    return UserInfo(
        id=str(user.id),
        external_user_id=user.external_user_id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        status=user.status,
        csrf_token=csrf_token(request.cookies["ba_session"], get_settings().master_key_file),
        expires_at=session.expires_at,
    )


@router.post("/logout")
async def logout(request: Request, db: DB, pair: Annotated[tuple[User, UserSession], Depends(current_session)]):
    settings = get_settings()
    validate_csrf(request)
    await db.execute(update(UserSession).where(UserSession.id == pair[1].id).values(revoked_at=now()))
    record_event(db, "logout_succeeded", operator_id=pair[0].id, target_type="user", target_id=str(pair[0].id))
    await db.commit()
    response = RedirectResponse(f"{settings.public_url}/login", status_code=303)
    response.delete_cookie("ba_session", secure=settings.cookie_secure, httponly=True, samesite="lax", path="/")
    return response
