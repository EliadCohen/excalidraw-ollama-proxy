import json
import pytest
import respx
import httpx
from httpx import AsyncClient, ASGITransport

from app import app, OLLAMA_BASE, TTD_SYSTEM


def ollama_sse(*contents: str) -> bytes:
    """Build a fake Ollama /v1/chat/completions SSE response body."""
    chunks = []
    for content in contents:
        data = json.dumps({
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": content}, "finish_reason": None}],
        })
        chunks.append(f"data: {data}")
    chunks.append(
        "data: " + json.dumps({
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {"content": ""}, "finish_reason": "stop"}],
        })
    )
    chunks.append("data: [DONE]")
    return ("\n\n".join(chunks) + "\n\n").encode()


def parse_sse_events(body: str) -> list[dict]:
    """Extract and parse JSON objects from SSE data lines."""
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


@pytest.fixture
def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

async def test_health(client):
    async with client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# text-to-diagram: SSE format translation
# ---------------------------------------------------------------------------

@respx.mock
async def test_sse_chunks_translated_to_excalidraw_format(client):
    respx.post(f"{OLLAMA_BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(200, content=ollama_sse("flowchart TD\n", "  A --> B\n"))
    )
    async with client:
        resp = await client.post(
            "/v1/ai/text-to-diagram/chat-streaming",
            json={"messages": [{"role": "user", "content": "two nodes"}]},
        )

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]

    events = parse_sse_events(resp.text)
    content_events = [e for e in events if e.get("type") == "content"]
    done_events = [e for e in events if e.get("type") == "done"]

    assert content_events, "expected at least one content event"
    assert len(done_events) == 1
    assert all("delta" in e for e in content_events)
    full = "".join(e["delta"] for e in content_events)
    assert "flowchart" in full


@respx.mock
async def test_done_is_last_event(client):
    respx.post(f"{OLLAMA_BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(200, content=ollama_sse("A --> B"))
    )
    async with client:
        resp = await client.post(
            "/v1/ai/text-to-diagram/chat-streaming",
            json={"messages": [{"role": "user", "content": "test"}]},
        )

    events = parse_sse_events(resp.text)
    assert events[-1] == {"type": "done"}


@respx.mock
async def test_empty_delta_chunks_are_dropped(client):
    """finish_reason chunk has empty content — should not produce a content event."""
    respx.post(f"{OLLAMA_BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(200, content=ollama_sse("hello"))
    )
    async with client:
        resp = await client.post(
            "/v1/ai/text-to-diagram/chat-streaming",
            json={"messages": [{"role": "user", "content": "test"}]},
        )

    events = parse_sse_events(resp.text)
    content_events = [e for e in events if e.get("type") == "content"]
    assert all(e["delta"] for e in content_events), "empty delta slipped through"


# ---------------------------------------------------------------------------
# text-to-diagram: system prompt handling
# ---------------------------------------------------------------------------

@respx.mock
async def test_system_prompt_injected_when_absent(client):
    captured: dict = {}

    def capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, content=ollama_sse("flowchart TD"))

    respx.post(f"{OLLAMA_BASE}/v1/chat/completions").mock(side_effect=capture)

    async with client:
        await client.post(
            "/v1/ai/text-to-diagram/chat-streaming",
            json={"messages": [{"role": "user", "content": "a diagram"}]},
        )

    messages = captured["body"]["messages"]
    assert messages[0]["role"] == "system"
    assert "Mermaid" in messages[0]["content"]
    assert messages[0]["content"] == TTD_SYSTEM


@respx.mock
async def test_existing_system_prompt_is_preserved(client):
    captured: dict = {}
    custom_system = "Only output YAML, nothing else."

    def capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, content=ollama_sse("flowchart TD"))

    respx.post(f"{OLLAMA_BASE}/v1/chat/completions").mock(side_effect=capture)

    async with client:
        await client.post(
            "/v1/ai/text-to-diagram/chat-streaming",
            json={"messages": [
                {"role": "system", "content": custom_system},
                {"role": "user", "content": "a diagram"},
            ]},
        )

    messages = captured["body"]["messages"]
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == custom_system


@respx.mock
async def test_correct_model_sent_to_ollama(client):
    captured: dict = {}

    def capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, content=ollama_sse("flowchart TD"))

    respx.post(f"{OLLAMA_BASE}/v1/chat/completions").mock(side_effect=capture)

    async with client:
        await client.post(
            "/v1/ai/text-to-diagram/chat-streaming",
            json={"messages": [{"role": "user", "content": "test"}]},
        )

    assert captured["body"]["model"] == "qwen2.5-coder:32b"
    assert captured["body"]["stream"] is True


# ---------------------------------------------------------------------------
# diagram-to-code
# ---------------------------------------------------------------------------

@respx.mock
async def test_diagram_to_code_returns_code(client):
    respx.post(f"{OLLAMA_BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={
            "choices": [{"message": {"content": "def hello(): pass"}}]
        })
    )
    async with client:
        resp = await client.post(
            "/v1/ai/diagram-to-code/generate",
            json={"texts": "a simple Python function"},
        )

    assert resp.status_code == 200
    assert resp.json() == {"code": "def hello(): pass"}


@respx.mock
async def test_diagram_to_code_sends_user_text(client):
    captured: dict = {}

    def capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "pass"}}]})

    respx.post(f"{OLLAMA_BASE}/v1/chat/completions").mock(side_effect=capture)

    async with client:
        await client.post(
            "/v1/ai/diagram-to-code/generate",
            json={"texts": "login flowchart"},
        )

    user_msg = next(m for m in captured["body"]["messages"] if m["role"] == "user")
    assert "login flowchart" in user_msg["content"]


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

async def test_cors_preflight(client):
    async with client:
        resp = await client.options(
            "/v1/ai/text-to-diagram/chat-streaming",
            headers={
                "Origin": "https://excalidraw.home.arpa",
                "Access-Control-Request-Method": "POST",
            },
        )
    assert resp.status_code == 200
    assert "access-control-allow-origin" in resp.headers
