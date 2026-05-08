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

## Tested setup

This proxy was developed and tested on a Fedora homelab server running Podman containers as systemd services via [quadlets](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html), with Caddy as the reverse proxy. All three components — Excalidraw, Ollama, and this proxy — run as containers on a shared Podman network (`infra_net`), with no GPU (CPU-only inference).

### Network layout

```
LAN client
  └─▶ Caddy :443  (TLS termination, internal CA)
        ├─▶ excalidraw.home.arpa   → systemd-excalidraw:80       (custom-built Excalidraw)
        ├─▶ excalidraw-ai.home.arpa → systemd-excalidraw-ai-proxy:8080  (this proxy)
        └─▶ ollama.home.arpa       → systemd-ollama:11434        (Ollama)
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

**Excalidraw** (`excalidraw.container`) — built from the `Containerfile` in this repo with `VITE_APP_AI_BACKEND` baked in:
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
# Build images
podman build -t localhost/excalidraw-ai-proxy:latest .
podman build -t localhost/excalidraw-ai:latest /path/to/excalidraw-containerfile/

# Install quadlets and start
sudo cp excalidraw-ollama-proxy.container /etc/containers/systemd/
sudo systemctl daemon-reload
sudo systemctl start excalidraw-ai-proxy.service
```

### Caddy config

Both HTTP and HTTPS blocks are needed. The `flush_interval -1` is required for SSE streaming to work — without it Caddy buffers the response and Excalidraw times out.

```caddy
http://excalidraw-ai.home.arpa {
    redir https://excalidraw-ai.home.arpa{uri} permanent
}

https://excalidraw-ai.home.arpa {
    tls internal
    reverse_proxy systemd-excalidraw-ai-proxy:8080 {
        flush_interval -1
    }
}
```

### Excalidraw Containerfile

`VITE_APP_AI_BACKEND` must be set at build time (it's compiled into the JS bundle by Vite). The `Containerfile` in this repo builds Excalidraw from source and sets it to point at the proxy:

```dockerfile
ENV VITE_APP_AI_BACKEND=https://excalidraw-ai.home.arpa
```

Adjust the URL to wherever you expose the proxy.

### Model

Tested with `qwen2.5-coder:32b` on a CPU-only host (56 cores, 251 GB RAM). Generation takes ~10 seconds with a warm model. Smaller models like `qwen2.5-coder:7b` are faster but produce lower-quality diagrams.

If your Excalidraw build hardcodes `gpt-4o` as the model name, create an alias so Ollama recognises it:
```bash
ollama cp qwen2.5-coder:32b gpt-4o
```

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE).
