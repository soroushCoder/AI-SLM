# ---------- config ----------
COMPOSE := docker compose -f deploy/docker-compose.dev.yml
API_SVC := api
OLLAMA_SVC := ollama
MILVUS_SVC := milvus

# ---------- phony ----------
.PHONY: up build down restart ps logs logs-api logs-milvus logs-ollama \
        shell-api pull-model ingest chat debug prune clean

# Build images and start all services (rebuilds API if Dockerfile or deps changed)
up:
	$(COMPOSE) up -d --build

# (Re)build API image only
build:
	$(COMPOSE) build --no-cache $(API_SVC)

# Stop everything
down:
	$(COMPOSE) down

# Quick restart API (use after code changes if not using --reload)
restart:
	$(COMPOSE) up -d --no-deps --build $(API_SVC)

# Process list
ps:
	$(COMPOSE) ps

# Logs (follow)
logs:
	$(COMPOSE) logs -f

logs-api:
	$(COMPOSE) logs -f $(API_SVC)

logs-milvus:
	$(COMPOSE) logs -f $(MILVUS_SVC)

logs-ollama:
	$(COMPOSE) logs -f $(OLLAMA_SVC)

# Shell into API container
shell-api:
	$(COMPOSE) exec $(API_SVC) sh

# Pull a small model into Ollama (run once or when switching models)
pull-model:
	$(COMPOSE) exec $(OLLAMA_SVC) ollama pull phi3:mini

# Ingest docs from host ./data/company_kb into Milvus
ingest:
	curl -X POST http://localhost:8000/ingest

# Ask a quick question: make chat Q="your question here"
chat:
	curl -s -X POST http://localhost:8000/chat \
	  -H 'Content-Type: application/json' \
	  -d '{"messages":[{"role":"user","content":"$(Q)"}]}' | jq .

# Inspect what retrieval returns: make debug Q="query"
debug:
	curl -s "http://localhost:8000/debug/retrieve?q=$$(python -c 'import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))' "$(Q)")" | jq .

# Clean unused stuff (careful)
prune:
	docker system prune -f
	docker volume prune -f

# Remove containers, networks, and images for a completely fresh start (careful)
clean: down
	docker image prune -a -f
	docker volume prune -f

logs-worker:
	$(COMPOSE) logs -f worker
# | jq pipes the output of the previous command into jq, a command-line JSON processor. You use it to pretty-print JSON, pick fields, transform, or filter results.
ingest-q:
	@curl -s -X POST http://localhost:8000/ingest | jq .

ingest-status:
	@test -n "$(ID)" || (echo "Usage: make ingest-status ID=<task_id>" && exit 1)
	@curl -s http://localhost:8000/ingest/status/$(ID) | jq .

