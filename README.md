# excalidraw-ollama-proxy

A self-hosted AI backend that lets [Excalidraw](https://github.com/excalidraw/excalidraw) use a local [Ollama](https://ollama.com) instance for its **text-to-diagram** and **diagram-to-code** AI features — no Excalidraw+ subscription or OpenAI API key required.

## Background

Excalidraw's AI features (`VITE_APP_AI_BACKEND`) call a backend that speaks a **custom streaming protocol** — not the standard OpenAI SSE format. Each chunk is expected as:

```json
{"type": "content", "delta": "<mermaid text chunk>"}
```

with a final `{"type": "done"}` terminator. This is undocumented and differs from Ollama's OpenAI-compatible `/v1/chat/completions` output, which is why simply pointing `VITE_APP_AI_BACKEND` at Ollama doesn't work. This proxy bridges the two.

## How it works

```
Browser (Excalidraw)
  └─▶ POST /v1/ai/text-to-diagram/chat-streaming
        └─▶ excalidraw-ollama-proxy   (translates format)
              └─▶ Ollama /v1/chat/completions
```

The proxy:
- Accepts Excalidraw's request format
- Injects a Mermaid-focused system prompt if none is present
- Streams Ollama's response, translating each chunk to Excalidraw's `{"type":"content","delta":"..."}` format
- Sends `{"type":"done"}` at the end

## Requirements

- [Ollama](https://ollama.com) running and accessible from the proxy
- A model pulled in Ollama — `qwen2.5-coder:32b` works well for diagram generation
- A custom-built Excalidraw image with `VITE_APP_AI_BACKEND` pointing at this proxy

## Setup

### 1. Pull a model

```bash
ollama pull qwen2.5-coder:32b
```

If your model name doesn't match what Excalidraw sends (it hardcodes `gpt-4o` in some builds), create an alias:

```bash
ollama cp qwen2.5-coder:32b gpt-4o
```

### 2. Configure the proxy

Environment variables (defaults shown):

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_BASE` | `http://localhost:11434` | Ollama base URL |
| `MODEL` | `qwen2.5-coder:32b` | Model to use for generation |

### 3. Run with Docker / Podman

```bash
docker build -t excalidraw-ollama-proxy .
docker run -p 8080:8080 \
  -e OLLAMA_BASE=http://your-ollama-host:11434 \
  -e MODEL=qwen2.5-coder:32b \
  excalidraw-ollama-proxy
```

### 4. Build Excalidraw with a custom AI backend URL

The `VITE_APP_AI_BACKEND` variable is baked in at build time. Build Excalidraw pointing at your proxy:

```bash
git clone --depth=1 https://github.com/excalidraw/excalidraw.git
cd excalidraw
VITE_APP_AI_BACKEND=http://your-proxy-host:8080 yarn build
```

Or use the included `Containerfile` for a full multi-stage build that does this automatically — edit the `ENV VITE_APP_AI_BACKEND` line before building.

## API endpoints

### `POST /v1/ai/text-to-diagram/chat-streaming`

Accepts the same request body Excalidraw sends:

```json
{
  "messages": [
    {"role": "user", "content": "A flowchart showing user login flow"}
  ]
}
```

Returns a `text/event-stream` SSE response in Excalidraw's expected format:

```
data: {"type":"content","delta":"flowchart TD\n"}
data: {"type":"content","delta":"  A[User] --> B[Login]"}
data: {"type":"done"}
```

### `POST /v1/ai/diagram-to-code/generate`

Accepts `{"texts": "..."}` and returns `{"code": "..."}` with generated code.

### `GET /health`

Returns `{"status": "ok"}`.

## Systemd quadlet (Podman)

An example quadlet file is included (`excalidraw-ollama-proxy.container`) for running this as a systemd service via Podman on Fedora/RHEL.

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE).
