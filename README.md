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

## Files in this repo

| File | Purpose |
|---|---|
| `app.py` | The proxy (FastAPI) |
| `Containerfile` | Builds the proxy image |
| `Containerfile.excalidraw` | Builds a custom Excalidraw image with `VITE_APP_AI_BACKEND` baked in |
| `excalidraw-ollama-proxy.container` | Podman quadlet for the proxy |
| `pyproject.toml` | Python project config (uv) |
| `tests/` | Test suite |

## Requirements

- [Ollama](https://ollama.com) running and accessible from the proxy
- A model pulled in Ollama — `qwen2.5-coder:32b` works well for diagram generation
- A custom-built Excalidraw image with `VITE_APP_AI_BACKEND` pointing at this proxy

## Setup

### 1. Pull a model

```bash
ollama pull qwen2.5-coder:32b
```

### 2. Configure the proxy

Environment variables:

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_BASE` | `http://systemd-ollama:11434` | Ollama base URL. Override for non-quadlet deployments (e.g. `http://localhost:11434`) |
| `MODEL` | `qwen2.5-coder:32b` | Model to use for generation |

### 3. Build and run the proxy

```bash
podman build -t excalidraw-ollama-proxy .
podman run -p 8080:8080 \
  -e OLLAMA_BASE=http://your-ollama-host:11434 \
  -e MODEL=qwen2.5-coder:32b \
  excalidraw-ollama-proxy
```

### 4. Build Excalidraw with the proxy URL baked in

`VITE_APP_AI_BACKEND` is compiled into the JS bundle at build time — it cannot be set at runtime. Use `Containerfile.excalidraw` (included in this repo). Before building, edit the two `ENV` lines to match your setup:

```dockerfile
ENV VITE_APP_AI_BACKEND=https://your-proxy-host
# Remove the next line if you are not self-hosting the room server
ENV VITE_APP_WS_SERVER_URL=wss://your-room-server
```

Then build:

```bash
podman build -f Containerfile.excalidraw -t excalidraw-custom .
podman run -p 80:80 excalidraw-custom
```

Or build manually without the Containerfile:

```bash
git clone --depth=1 https://github.com/excalidraw/excalidraw.git
cd excalidraw
VITE_APP_AI_BACKEND=https://your-proxy-host yarn build:app:docker
```

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

## Logging

The proxy logs at `DEBUG` level by default, which includes request bodies and the Ollama response status. To reduce verbosity in production, edit the `logging.basicConfig(level=logging.DEBUG)` line in `app.py`.

## Tested setup

This proxy was developed and tested on a Fedora homelab server running Podman containers as systemd services via [quadlets](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html), with Caddy as the reverse proxy. All three components — Excalidraw, Ollama, and this proxy — run as containers on a shared Podman network (`infra_net`), with no GPU (CPU-only inference).

### Network layout

```
LAN client
  └─▶ Caddy :443  (TLS termination, internal CA)
        ├─▶ excalidraw.home.arpa    → systemd-excalidraw:80              (custom-built Excalidraw)
        ├─▶ excalidraw-ai.home.arpa → systemd-excalidraw-ai-proxy:8080  (this proxy)
        └─▶ ollama.home.arpa        → systemd-ollama:11434               (Ollama)
```

Containers resolve each other by name on `infra_net`. The proxy reaches Ollama at `http://systemd-ollama:11434` — never via the public hostname.

### Quadlets

**Ollama** (`ollama.container`):
```ini
[Container]
Image=docker.io/ollama/ollama:latest
Network=infra_net
Volume=/srv/ollama/models:/root/.ollama:Z
```

**Excalidraw** (`excalidraw.container`) — image built from `Containerfile.excalidraw` in this repo:
```ini
[Container]
Image=localhost/excalidraw-ai:latest
Network=infra_net
```

**This proxy** (`excalidraw-ollama-proxy.container`) — included in this repo:
```ini
[Container]
Image=localhost/excalidraw-ai-proxy:latest
Network=infra_net
Environment=OLLAMA_BASE=http://systemd-ollama:11434
Environment=MODEL=qwen2.5-coder:32b
```

Build and start:
```bash
# Build proxy image
podman build -t localhost/excalidraw-ai-proxy:latest .

# Build Excalidraw image (edit VITE_APP_AI_BACKEND in Containerfile.excalidraw first)
podman build -f Containerfile.excalidraw -t localhost/excalidraw-ai:latest .

# Install quadlet and start
sudo cp excalidraw-ollama-proxy.container /etc/containers/systemd/
sudo systemctl daemon-reload
sudo systemctl start excalidraw-ollama-proxy.service
```

### Caddy config

Both HTTP and HTTPS blocks are needed. The `flush_interval -1` is required for SSE streaming to work — without it Caddy buffers the response and Excalidraw times out.

```caddy
http://excalidraw-ai.home.arpa {
    redir https://excalidraw-ai.home.arpa{uri} permanent
}

https://excalidraw-ai.home.arpa {
    tls internal
    reverse_proxy systemd-excalidraw-ollama-proxy:8080 {
        flush_interval -1
    }
}
```

### Model

Tested with `qwen2.5-coder:32b` on a CPU-only host (56 cores, 251 GB RAM). Generation takes ~10 seconds with a warm model. Smaller models like `qwen2.5-coder:7b` are faster but produce lower-quality diagrams.

The proxy always sends the model name from the `MODEL` env var to Ollama — it ignores whatever model name Excalidraw puts in the request body. No model aliases are needed.

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE).
