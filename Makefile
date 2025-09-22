# ---------- config ----------
COMPOSE := docker compose -f deploy/docker-compose.dev.yml
API_SVC := api
OLLAMA_SVC := ollama
MILVUS_SVC := milvus

# HTTP defaults
BASE ?= http://localhost:8000
KEY  ?= devkey
CTJSON := -H 'Content-Type: application/json'
KEYHDR := -H 'X-API-Key: $(KEY)'

# ---------- phony ----------
.PHONY: up build down restart ps logs logs-api logs-milvus logs-ollama \
        shell-api pull-model ingest ingest-q ingest-status debug prune clean \
        logs-worker health seed-kb chat \
        coffee-espresso coffee-pourover \
        coach-turn coach-follow coach-stream

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

logs-worker:
	$(COMPOSE) logs -f worker

# Shell into API container
shell-api:
	$(COMPOSE) exec $(API_SVC) sh

# Pull a small model into Ollama (run once or when switching models)
pull-model:
	$(COMPOSE) exec $(OLLAMA_SVC) ollama pull phi3:mini

# ---------- RAG helpers ----------

# Ingest docs (background Celery or direct), no JSON body needed
ingest:
	curl -s -X POST "$(BASE)/ingest" $(KEYHDR)

# Robust queue + print HTTP code even if body isn't JSON
ingest-q:
	@resp="$$(curl -sS -w '\n%{http_code}' -X POST '$(BASE)/ingest' $(KEYHDR) -H 'Accept: application/json')"; \
	code="$${resp##*$'\n'}"; body="$${resp%$'\n'*}"; \
	ct="$$(printf '%s' "$$body" | head -c 1)"; \
	if [ "$$ct" = "{" ] || [ "$$ct" = "[" ]; then printf '%s\n' "$$body" | jq .; else printf '%s\n' "$$body"; fi; \
	echo "(HTTP $$code)"

ingest-status:
	@test -n "$(ID)" || (echo "Usage: make ingest-status ID=<task_id>" && exit 1)
	@curl -s "$(BASE)/ingest/status/$(ID)" $(KEYHDR) | jq .

# Inspect what retrieval returns: make debug Q="query"
debug:
	@test -n "$(Q)" || (echo 'Usage: make debug Q="your query"' && exit 1)
	@curl -s "$(BASE)/debug/retrieve?q=$$(python -c 'import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))' "$(Q)")" $(KEYHDR) | jq .

# ---------- Health & seeding ----------
health:
	curl -s "$(BASE)/health" | jq .

seed-kb:
	mkdir -p data/company_kb
	@printf "# Espresso Guide\nLight roasts 94–96°C, ~9 bar, ratio 1:2.2–1:2.5, 25–32s.\n" > data/company_kb/espresso_guide.md
	@printf "# Filter Coffee Basics\nPourover ~1:15–1:17, 92–96°C, 30–45s bloom, 2:30–3:30 total.\n" > data/company_kb/filter_basics.md
	@echo "Seeded sample coffee docs in ./data/company_kb"

# ---------- Chat (non-coach) ----------
# Make sure $(Q) is JSON-escaped safely
chat:
	@test -n "$(Q)" || (echo 'Usage: make chat Q="your question"' && exit 1)
	@QS=$$(printf '%s' "$(Q)" | jq -Rsa .); \
	printf '{"messages":[{"role":"user","content":%s}]}' "$$QS" \
	| curl -s -X POST "$(BASE)/chat" $(CTJSON) $(KEYHDR) --data-binary @- | jq .

# ---------- Coffee rules endpoints (robust JSON) ----------
coffee-espresso:
	@printf '%s' '{ "beverage":"espresso","machine":"espresso_pump","roast":"medium","dose_g":18,"water_temp_c":93,"pressure_bar":9 }' \
	| curl -s -X POST "$(BASE)/coffee/recommend" $(CTJSON) $(KEYHDR) --data-binary @- | jq .

coffee-pourover:
	@printf '%s' '{ "beverage":"pourover","machine":"pourover_kettle","roast":"light","dose_g":20 }' \
	| curl -s -X POST "$(BASE)/coffee/recommend" $(CTJSON) $(KEYHDR) --data-binary @- | jq .

# ---------- Coach (RAG + rules) ----------
coach-turn:
	@printf '%s' '{ "messages":[{"role":"user","content":"I have a lever espresso machine and a light roast. Help me dial in."}] }' \
	| curl -s -X POST "$(BASE)/coach/turn" $(CTJSON) $(KEYHDR) --data-binary @- | jq .

coach-follow:
	@printf '%s' '{ "messages":[{"role":"user","content":"I have a lever espresso machine and a light roast. Help me dial in."},{"role":"assistant","content":"What dose, temperature, and pressure are you using?"},{"role":"user","content":"Dose 18g, temp 95C, pressure ~7 bar."}] }' \
	| curl -s -X POST "$(BASE)/coach/turn" $(CTJSON) $(KEYHDR) --data-binary @- | jq .

# Streaming coach (use -N so curl doesn't buffer)
coach-stream:
	@printf '%s' '{ "messages":[{"role":"user","content":"Pourover with medium roast, can you recommend settings?"},{"role":"assistant","content":"What dose and water temperature will you use?"},{"role":"user","content":"Dose 20g, temp 93C."}] }' \
	| curl -N -X POST "$(BASE)/coach/stream" $(CTJSON) $(KEYHDR) --data-binary @-

# ---------- Docker cleanup ----------
prune:
	docker system prune -f
	docker volume prune -f

# Remove containers, networks, and images for a completely fresh start (careful)
clean: down
	docker image prune -a -f
	docker volume prune -f


# ---------- Coach: parameterized streaming ----------
# Usage example:
# make coach-stream-espresso MACHINE=espresso_pump ROAST=medium DOSE=18 TEMP=93 PRESSURE=9
coach-stream-espresso:
	@test -n "$(MACHINE)" || (echo 'Set MACHINE=espresso_pump|espresso_lever|pod' && exit 1)
	@test -n "$(ROAST)"   || (echo 'Set ROAST=light|medium|dark' && exit 1)
	@test -n "$(DOSE)"    || (echo 'Set DOSE (g), e.g. DOSE=18' && exit 1)
	@test -n "$(TEMP)"    || (echo 'Set TEMP (C), e.g. TEMP=93' && exit 1)
	@test -n "$(PRESSURE)"|| (echo 'Set PRESSURE (bar), e.g. PRESSURE=9' && exit 1)
	@printf '%s' '{ "messages":[{"role":"user","content":"Espresso on a $(MACHINE), $(ROAST) roast. Dose $(DOSE) g, brew temp $(TEMP) C, pressure $(PRESSURE) bar. Please give me a dialed-in recipe."}] }' \
	| curl -N -X POST "$(BASE)/coach/stream" $(CTJSON) $(KEYHDR) --data-binary @-

# Pourover streamed recommendation with params:
# make coach-stream-pourover ROAST=light DOSE=20 TEMP=93
coach-stream-pourover:
	@test -n "$(ROAST)" || (echo 'Set ROAST=light|medium|dark' && exit 1)
	@test -n "$(DOSE)"  || (echo 'Set DOSE (g), e.g. DOSE=20' && exit 1)
	@test -n "$(TEMP)"  || (echo 'Set TEMP (C), e.g. TEMP=93' && exit 1)
	@printf '%s' '{ "messages":[{"role":"user","content":"Pourover with $(ROAST) roast. Dose $(DOSE) g, water temperature $(TEMP) C. Please give me a dialed-in recipe."}] }' \
	| curl -N -X POST "$(BASE)/coach/stream" $(CTJSON) $(KEYHDR) --data-binary @-
