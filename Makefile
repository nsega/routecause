# RouteCause — build/run targets.
# The inference-lab environment must be booted alongside this repo:
#   cd ../inference-lab && make up && make load
# and ANTHROPIC_API_KEY must be set in .env.

LAB  ?= ../inference-lab
POOL ?= vllm-sim-pool
PORT ?= 8000

.PHONY: install test demo serve tunnel diagnose lab-up lab-load reset clean help

help:
	@echo "make install   - create venv + install deps (uv)"
	@echo "make test      - run the test suite (pytest)"
	@echo "make demo      - end-to-end S1 diagnosis against the lab, then grade it"
	@echo "make serve     - run the FastAPI service on :$(PORT)"
	@echo "make tunnel    - expose :$(PORT) via a cloudflared quick tunnel (public URL)"
	@echo "make diagnose  - diagnose POOL=$(POOL) and save the report"
	@echo "make lab-up / lab-load / reset - drive the inference-lab environment"

install:
	uv sync --extra dev

test: install
	uv run pytest -q

## demo: fresh-clone quickstart — reaches a successful S1 diagnosis (RUBRIC B2)
demo: install
	./workflow/demo.sh

serve: install
	uv run uvicorn routecause.service:app --host 0.0.0.0 --port $(PORT)

tunnel:
	cloudflared tunnel --url http://localhost:$(PORT)

diagnose: install
	uv run routecause diagnose $(POOL) --save

lab-up:
	$(MAKE) -C $(LAB) up
lab-load:
	$(MAKE) -C $(LAB) load
reset:
	$(MAKE) -C $(LAB) reset

clean:
	rm -rf reports .pytest_cache
