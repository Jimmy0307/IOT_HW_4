# Thin wrappers; every target is a cross-platform Python/Docker command (Windows: run the commands directly).
PY ?= python

.PHONY: bootstrap up down clean test verify verify-fresh ui-qa audit-verify secret-scan compose

bootstrap:      ## generate CA/certs, MQTT credentials, dynsec, JWT + audit keys (idempotent)
	$(PY) scripts/bootstrap_secrets.py

compose:        ## re-render docker-compose.yml from config/platform.yaml
	$(PY) scripts/render_compose.py

up: bootstrap   ## build and start the full lab
	docker compose up -d --build

down:           ## stop the lab (keep volumes)
	docker compose --profile test down

clean:          ## stop the lab and delete volumes (historian DB, broker persistence)
	docker compose --profile test down -v --remove-orphans

test:           ## unit tests only (no Docker)
	$(PY) -m pytest tests/unit -q

verify:         ## full acceptance run: tests, stack gates, browser QA, audit, secret scan
	$(PY) scripts/verify_all.py

verify-fresh:   ## same, starting from empty volumes
	$(PY) scripts/verify_all.py --fresh

ui-qa:
	$(PY) scripts/ui_qa.py

audit-verify:
	$(PY) scripts/verify_audit_chain.py --snapshot evidence/generated/raw/audit-snapshot.db

secret-scan:
	$(PY) scripts/secret_scan.py
