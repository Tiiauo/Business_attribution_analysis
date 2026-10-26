import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.core.security import log_event


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


def install_errors(app: FastAPI, form_redirect_origin: str | None = None) -> None:
    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex
        if request.headers.get("content-type", "").split(";", 1)[0] in {
            "application/json",
            "application/x-www-form-urlencoded",
        }:
            chunks, size = [], 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > 256_000:
                    return JSONResponse(
                        status_code=413,
                        content={
                            "code": "REQUEST_TOO_LARGE",
                            "message": "请求内容超过大小限制。",
                            "request_id": request.state.request_id,
                        },
                        headers={"Cache-Control": "no-store", "X-Request-ID": request.state.request_id},
                    )
                chunks.append(chunk)
            request._body = b"".join(chunks)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin"
        response.headers["Cache-Control"] = "no-store"
        form_action = "'self'" + (f" {form_redirect_origin}" if form_redirect_origin else "")
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; img-src 'self' data:; "
            f"script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action {form_action}"
        )
        log_event(
            "http_request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            request_id=request.state.request_id,
        )
        return response

    @app.exception_handler(ApiError)
    async def api_error(request: Request, error: ApiError):
        return JSONResponse(
            status_code=error.status,
            content={"code": error.code, "message": error.message, "request_id": request.state.request_id},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        code, message = "INVALID_REQUEST", "请求参数不符合接口约定，请检查后重试。"
        if request.url.path == "/api/chat/send":
            for detail in error.errors():
                if detail["loc"] == ("body", "content"):
                    code, message = (
                        ("FIELD_TOO_LONG", "正文最多20000个Unicode码点。")
                        if detail["type"] == "string_too_long"
                        else ("EMPTY_MESSAGE", "请输入非空问题。")
                    )
                    break
                if detail["loc"] == ("body", "data_source_id"):
                    code, message = "DATA_SOURCE_REQUIRED", "请选择已启用的数据源。"
        return JSONResponse(
            status_code=400,
            content={
                "code": code,
                "message": message,
                "request_id": request.state.request_id,
            },
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException):
        return JSONResponse(
            status_code=error.status_code,
            content={
                "code": "RESOURCE_NOT_FOUND" if error.status_code == 404 else "INVALID_REQUEST",
                "message": "资源不存在或请求无效。",
                "request_id": request.state.request_id,
            },
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception):
        log_event("request_failed", error_type=type(error).__name__, request_id=request.state.request_id)
        return JSONResponse(
            status_code=500,
            content={
                "code": "INTERNAL_ERROR",
                "message": "服务暂时无法完成操作，请稍后重试。",
                "request_id": getattr(request.state, "request_id", uuid.uuid4().hex),
            },
        )
