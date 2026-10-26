import json

import httpx2
import pytest

from app.core.errors import ApiError
from app.model_client import chat_endpoint, chat_once, stream_chat


def test_endpoint_normalization_and_rejection():
    assert chat_endpoint("https://service.invalid/v1/") == "https://service.invalid/v1/chat/completions"
    assert chat_endpoint("https://service.invalid/v1/chat/completions") == "https://service.invalid/v1/chat/completions"
    for address in ("http://service.invalid", "https://secret@service.invalid", "https://service.invalid?api_key=x"):
        with pytest.raises(ApiError):
            chat_endpoint(address)
    from app.core.security import redact

    assert redact({"api_key": "hidden", "prompt_tokens": 35, "total_tokens": "hidden"}) == {
        "api_key": "[REDACTED]",
        "prompt_tokens": 35,
        "total_tokens": "[REDACTED]",
    }


@pytest.mark.asyncio
async def test_real_protocol_request_without_persisting_secret():
    def respond(request):
        assert request.headers["authorization"] == "Bearer synthetic-key"
        assert request.headers["x-opencode-session"] == "fixture-session"
        payload = json.loads(request.content)
        assert payload["model"] == "fixture-model" and payload["max_tokens"] == 16
        return httpx2.Response(200, json={"choices": [{"message": {"content": "OK"}}], "usage": {"total_tokens": 3}})

    config = {"api_url": "https://service.invalid/v1", "model_id": "fixture-model", "timeout_seconds": 1}
    result = await chat_once(
        config,
        "synthetic-key",
        [{"role": "user", "content": "OK"}],
        transport=httpx2.MockTransport(respond),
        session_id="fixture-session",
    )
    assert result["choices"][0]["message"]["content"] == "OK" and "synthetic-key" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code", [(401, "MODEL_AUTH_FAILED"), (429, "MODEL_RATE_LIMITED"), (500, "MODEL_UNAVAILABLE")]
)
async def test_sanitized_provider_errors(status, code):
    config = {"api_url": "https://service.invalid/v1", "model_id": "fixture", "timeout_seconds": 1}
    with pytest.raises(ApiError) as error:
        await chat_once(
            config,
            "synthetic-key",
            [],
            transport=httpx2.MockTransport(
                lambda _: httpx2.Response(status, text="sensitive-provider-body-synthetic-key")
            ),
        )
    assert error.value.code == code and "synthetic" not in error.value.message


@pytest.mark.asyncio
async def test_stream_completion_and_interruption():
    config = {"api_url": "https://service.invalid/v1", "model_id": "fixture", "timeout_seconds": 1}
    for suffix, interrupted in (("data: [DONE]\n\n", False), ("", True)):
        transport = httpx2.MockTransport(
            lambda _, suffix=suffix: httpx2.Response(
                200, text='data: {"choices":[{"delta":{"content":"part"}}]}\n\n' + suffix
            )
        )
        seen = []
        try:
            async for chunk in stream_chat(
                config, "synthetic-key", [], tools=[], max_tokens=16, session_id="fixture", transport=transport
            ):
                seen.append(chunk)
        except ApiError as error:
            assert interrupted and error.code == "MODEL_STREAM_INTERRUPTED"
        else:
            assert not interrupted
        assert seen[0]["choices"][0]["delta"]["content"] == "part"


async def test_deepseek_request_disables_unbudgeted_thinking():
    config = {"api_url": "https://service.invalid/v1", "model_id": "deepseek-v4.1-flash", "timeout_seconds": 1}

    def respond(request):
        assert json.loads(request.content)["thinking"] == {"type": "disabled"}
        return httpx2.Response(200, text='data: {"choices":[]}\n\ndata: [DONE]\n\n')

    assert [
        chunk
        async for chunk in stream_chat(
            config,
            "synthetic",
            [],
            tools=[],
            max_tokens=4096,
            session_id="fixture",
            transport=httpx2.MockTransport(respond),
        )
    ] == [{"choices": []}]
