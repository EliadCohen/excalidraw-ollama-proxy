"""
Proxy that translates Excalidraw AI endpoints to Ollama's OpenAI-compatible API.

Excalidraw calls:
  POST /v1/ai/text-to-diagram/chat-streaming  (SSE stream)
  POST /v1/ai/diagram-to-code/generate        (JSON)

Both are forwarded to Ollama's /v1/chat/completions endpoint.
"""

import json
import logging
import os
import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = FastAPI()

OLLAMA_BASE = os.getenv("OLLAMA_BASE", "http://systemd-ollama:11434")
MODEL = os.getenv("MODEL", "qwen2.5-coder:32b")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)

TTD_SYSTEM = (
    "You are an expert at converting natural language descriptions into Mermaid diagram syntax. "
    "Respond ONLY with valid Mermaid syntax. No explanations, no markdown code fences, "
    "no commentary. Just raw Mermaid diagram code starting with the diagram type keyword "
    "(e.g. flowchart, sequenceDiagram, classDiagram, etc.)."
)

D2C_SYSTEM = (
    "You are an expert developer. Convert the given diagram or wireframe description into "
    "clean, working code. Respond with only the code, no explanations or markdown fences."
)


@app.post("/v1/ai/text-to-diagram/chat-streaming")
async def text_to_diagram(request: Request):
    body = await request.json()
    logger.debug("text-to-diagram request body: %s", body)
    messages = body.get("messages", [])

    if not messages or messages[0].get("role") != "system":
        messages = [{"role": "system", "content": TTD_SYSTEM}] + messages

    async def stream():
        # Excalidraw expects {"type":"content","delta":"..."} chunks, not OpenAI format.
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream(
                    "POST",
                    f"{OLLAMA_BASE}/v1/chat/completions",
                    json={"model": MODEL, "messages": messages, "stream": True},
                ) as resp:
                    logger.debug("Ollama status: %s", resp.status_code)
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data = line[6:]
                        if data == "[DONE]":
                            yield 'data: {"type":"done"}\n\n'
                            break
                        try:
                            chunk = json.loads(data)
                            delta = chunk["choices"][0]["delta"].get("content", "")
                            if delta:
                                yield f'data: {json.dumps({"type":"content","delta":delta})}\n\n'
                        except Exception:
                            pass
        except Exception:
            logger.exception("Error streaming from Ollama")
            yield 'data: {"type":"error","error":{"message":"Proxy error"}}\n\n'

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/v1/ai/diagram-to-code/generate")
async def diagram_to_code(request: Request):
    body = await request.json()
    logger.debug("diagram-to-code request body: %s", body)
    texts = body.get("texts", "")

    prompt = f"Convert this diagram description to code:\n\n{texts}"

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{OLLAMA_BASE}/v1/chat/completions",
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": D2C_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
            },
        )
        result = resp.json()
        code = result["choices"][0]["message"]["content"]

    return JSONResponse({"code": code})


@app.get("/health")
async def health():
    return {"status": "ok"}
