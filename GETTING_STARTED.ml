# Getting Started (Local)

This guide walks you through cloning and running the project from scratch on your machine.

---

## 0) Prerequisites

- **Docker Desktop** installed and running  
  • Recommended resources: **8 CPUs**, **12–16 GB RAM**  
- **Git** installed  
- (Optional) `jq` for pretty-printing JSON  
  - macOS: `brew install jq`

> Apple Silicon (M1/M2/M3) is supported. The compose file sets `platform: linux/arm64` where required.

---

## 1) Clone the repo

Replace `<YOUR_GITHUB_URL>` with your repository URL.

```bash
git clone <YOUR_GITHUB_URL> slm-coach
cd slm-coach
```

You should now see:

```
api/  data/  deploy/  Makefile  README.md
```

---

## 2) Build & start all services

```bash
make up
```

This builds the API image and starts containers for:
- **Ollama** (LLM runtime)
- **Milvus** (+ MinIO + etcd) — vector database
- **Redis** — Celery broker/backend
- **API** — FastAPI service
- **Worker** — Celery worker

Check status:
```bash
make ps
```

Tail API logs (optional):
```bash
make logs-api
```

---

## 3) Pull a small local model (Ollama)

```bash
make pull-model
```

This pulls `phi3:mini` into the `ollama` container (fast and lightweight).

---

## 4) Seed example documents (for RAG)

```bash
make seed-kb
```

Creates sample files under `./data/company_kb/` (mounted to `/data` in containers).

---

## 5) Ingest the docs

Queue the background ingestion task:
```bash
make ingest-q KEY=devkey
```

Watch the worker process it:
```bash
make logs-worker
```

> If you see `/data/company_kb not found`, ensure `./data/company_kb` exists on host
> and that both `api` and `worker` mount `../data:/data` in `deploy/docker-compose.dev.yml`.

---

## 6) Health check

```bash
make health
```

You should get a small JSON response.

---

## 7) Coffee Coach — advice-only (streaming)

### Espresso example
```bash
make coach-stream-espresso KEY=devkey MACHINE=espresso_pump ROAST=medium DOSE=18 TEMP=93 PRESSURE=9
```

You’ll see short **recommendation** lines streamed (no questions).

To hide the `data:` prefixes in the terminal:
```bash
make coach-stream-espresso KEY=devkey MACHINE=espresso_pump ROAST=medium DOSE=18 TEMP=93 PRESSURE=9 | sed -E 's/^data: //; /^$/d'
```

### Pourover example
```bash
make coach-stream-pourover KEY=devkey ROAST=light DOSE=20 TEMP=93
```

---

## 8) Other helpful targets

- Ask the (non-coach) chat endpoint (if present):
  ```bash
  make chat Q="your question" KEY=devkey
  ```
- Inspect retrieval for a query:
  ```bash
  make debug Q="espresso temperature" KEY=devkey
  ```
- Follow all logs:
  ```bash
  make logs
  ```

---

## 9) Apply code changes

Rebuild & restart the API after editing `api/`:

```bash
make restart
```
(or `make build` then `make up`)

---

## 10) Stop & clean up

```bash
make down
```

Optional cleanup (careful):
```bash
make prune
```

---

## Configuration (defaults)

- **API Key**: endpoints expect `X-API-Key: devkey`.  
  Change/disable via `API_KEYS` in `deploy/docker-compose.dev.yml`.
- **Model**: `LLM_MODEL=phi3:mini`.  
  The **coach** file provided uses deterministic rules and **does not** call the LLM.
- **Embeddings**:
  - Local (default): `EMBEDDINGS_KIND=local`, `EMBEDDINGS_MODEL=sentence-transformers/all-MiniLM-L6-v2`
  - Hosted (optional):
    ```yaml
    EMBEDDINGS_KIND: openai
    OPENAI_EMBED_MODEL: text-embedding-3-small
    OPENAI_API_KEY: <your key>
    ```

---

## Troubleshooting

- **Slow first response**: containers warming up and initial model/embedding loads; subsequent calls are faster.
- **Duplicate sources**: multiple chunks may come from the same file; the coach dedupes filenames in advice output.
- **Redis/Milvus errors**: check services with `make ps` and logs with `make logs-milvus` / `make logs-worker`.
- **401 Invalid or missing API key**: include `-H 'X-API-Key: devkey'` (Make targets already include it).
