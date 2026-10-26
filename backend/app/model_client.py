import asyncio
import json
from urllib.parse import urlsplit

import httpx2

from app.auth import tls_context
from app.core.errors import ApiError


def chat_endpoint(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ApiError(400, "CONFIG_INVALID", "base_url 须为不含密钥、查询或片段的 HTTPS 地址。")
    base_url = base_url.rstrip("/")
    return base_url if base_url.endswith("/chat/completions") else base_url + "/chat/completions"


def model_error(status: int):
    if status in {401, 403}:
        return ApiError(502, "MODEL_AUTH_FAILED", "模型鉴权失败，请核对 api_key、模型名称及服务权限。")
    if status == 429:
        return ApiError(503, "MODEL_RATE_LIMITED", "模型服务限流，请稍后重试。")
    return ApiError(502, "MODEL_UNAVAILABLE", f"模型服务返回 HTTP {status}，请核对 base_url 与模型名称。")


def headers(secret: str, session_id: str) -> dict:
    return {
        "Authorization": "Bearer " + secret,
        "User-Agent": "business-attribution/0.1",
        "x-opencode-session": session_id,
    }


async def chat_once(
    config: dict, secret: str, messages: list, *, max_tokens=16, session_id="connection-test", transport=None
) -> dict:
    if not secret or not config.get("model_id"):
        raise ApiError(400, "MODEL_NOT_CONFIGURED", "请填写 api_key 和模型名称。")
    url = chat_endpoint(config["api_url"])
    try:
        async with asyncio.timeout(min(config["timeout_seconds"], 30)):
            async with httpx2.AsyncClient(
                verify=tls_context(), timeout=30, follow_redirects=False, transport=transport
            ) as client:
                response = await client.post(
                    url,
                    headers=headers(secret, session_id),
                    json={
                        "model": config["model_id"],
                        "messages": messages,
                        "max_tokens": max_tokens,
                        "stream": False,
                        **({"thinking": {"type": "disabled"}} if config["model_id"].startswith("deepseek-") else {}),
                    },
                )
                if response.status_code != 200:
                    raise model_error(response.status_code)
                result = response.json()
                if (
                    not isinstance(result, dict)
                    or not result.get("choices")
                    or not isinstance(result["choices"][0].get("message"), dict)
                ):
                    raise ValueError()
                return result
    except (httpx2.TimeoutException, TimeoutError):
        raise ApiError(504, "MODEL_TIMEOUT", "模型调用超时，请稍后重试或调整调用时限。") from None
    except httpx2.ConnectError:
        raise ApiError(502, "MODEL_CONNECTION_FAILED", "无法建立安全连接，请检查网络、代理及服务端证书。") from None
    except (httpx2.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        raise ApiError(502, "MODEL_INVALID_RESPONSE", "模型未返回兼容的有效响应，请核对服务协议。") from None


async def stream_chat(
    config: dict, secret: str, messages: list, *, tools: list, max_tokens: int, session_id: str, transport=None
):
    url = chat_endpoint(config["api_url"])
    payload = {
        "model": config["model_id"],
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if tools:
        payload["tools"] = tools
    if config["model_id"].startswith("deepseek-"):
        payload["thinking"] = {"type": "disabled"}
    try:
        async with asyncio.timeout(config["timeout_seconds"]):
            async with httpx2.AsyncClient(
                verify=tls_context(), timeout=config["timeout_seconds"], follow_redirects=False, transport=transport
            ) as client:
                async with client.stream("POST", url, headers=headers(secret, session_id), json=payload) as response:
                    if response.status_code != 200:
                        raise model_error(response.status_code)
                    size, ended = 0, False
                    async for line in response.aiter_lines():
                        size += len(line.encode("utf-8"))
                        if size > 4_000_000:
                            raise ApiError(502, "MODEL_OUTPUT_TOO_LARGE", "模型响应超过输出上限。")
                        if not line.startswith("data:"):
                            continue
                        value = line[5:].strip()
                        if value == "[DONE]":
                            ended = True
                            break
                        yield json.loads(value)
                    if not ended:
                        raise ApiError(502, "MODEL_STREAM_INTERRUPTED", "模型连接中断，已保存的部分内容可查看。")
    except (httpx2.TimeoutException, TimeoutError):
        raise ApiError(504, "MODEL_TIMEOUT", "模型调用超时，已保存的部分内容可查看。") from None
    except httpx2.ConnectError:
        raise ApiError(502, "MODEL_CONNECTION_FAILED", "无法建立安全连接，请检查网络、代理及服务端证书。") from None
    except (httpx2.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        raise ApiError(502, "MODEL_INVALID_RESPONSE", "模型返回的数据格式不符合接口协议。") from None
