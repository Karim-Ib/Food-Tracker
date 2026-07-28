.PHONY: up down reset logs ps psql up-prod logs-prod bootstrap verify test migrate

# ---------------- Local dev (auto-loads docker-compose.override.yml) ----------------

up:
	docker compose up -d

down:
	docker compose down

# Nuclear option: also drops the postgres volume.
# Use this when you change init scripts and need a fresh DB.
reset:
	docker compose down -v

logs:
	docker compose logs -f

ps:
	docker compose ps

psql:
	docker compose exec postgres psql -U $${POSTGRES_SUPERUSER:-postgres} -d foodbot

# ---------------- Production (explicit -f flags, no override.yml) ----------------

up-prod:
	docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

logs-prod:
	docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f

# ---------------- First-time setup helper ----------------

bootstrap:
	@test -f .env || (cp .env.example .env && \
		echo "→ Created .env from .env.example. Edit it, then re-run 'make bootstrap'." && \
		exit 1)
	docker compose up -d postgres
	@echo "→ Waiting for Postgres to be healthy..."
	@until docker compose exec -T postgres pg_isready -U postgres -d foodbot >/dev/null 2>&1; do \
		sleep 1; \
	done
	@echo "→ Postgres ready."
	@$(MAKE) verify

verify:
	@echo "→ Verifying schemas and roles..."
	@docker compose exec -T postgres psql -U postgres -d foodbot -c "\dn" | grep -E "(app|analytics)" >/dev/null || (echo "✗ Schemas missing"; exit 1)
	@docker compose exec -T postgres psql -U postgres -d foodbot -c "\du" | grep -E "(app_user|dbt_user)" >/dev/null || (echo "✗ Roles missing"; exit 1)
	@docker compose exec -T postgres psql -U postgres -d foodbot -c "\dx" | grep pg_trgm >/dev/null || (echo "✗ pg_trgm extension missing"; exit 1)
	@echo "✓ All checks passed."

restart-bot:
	docker compose restart bot

restart-api:
	docker compose restart api

# ---------------- Schema and tests ----------------

# Applied as app_user (not the superuser) so default-privilege grants take effect.
migrate:
	docker compose exec api alembic upgrade head

# Runs on the host venv, not in a container: the weight-model tests are pure
# numpy — no database, no event loop — so there is nothing to containerize.
# Needs the dev extra once: pip install -e ".[dev]"
test:
	python -m pytest -q