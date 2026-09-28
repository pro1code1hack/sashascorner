# Convenience wrappers around the commands in README.md / docs/OPERATIONS.md.
# Nothing here is required -- every target is a one-line alias for a command you could
# type yourself. Prefer reading README.md first; this exists so the common ones don't
# have to be remembered or retyped.

.PHONY: help dev start stop sync migrate seed drift-backfill info \
        lint fmt typecheck \
        build up down ps logs \
        migrate-docker seed-docker drift-backfill-docker \
        backup restore

help:
	@echo "Everything at once (see start.sh):"
	@echo "  make dev                 all services locally with hot reload (./start.sh)"
	@echo "  make start               the whole Docker stack (./start.sh docker)"
	@echo "  make stop                stop the Docker stack"
	@echo ""
	@echo "Local (uv), no Docker:"
	@echo "  make sync                installs cafeops + dependencies (uv sync)"
	@echo "  make migrate             alembic upgrade head"
	@echo "  make seed                cafeops seed --workbook sashas_corner_finance__LEGACY_.xlsx"
	@echo "  make drift-backfill      cafeops drift --backfill  (REQUIRED after any reseed)"
	@echo "  make info                cafeops info"
	@echo "  make lint                ruff check + format --check"
	@echo "  make fmt                 ruff format (writes)"
	@echo "  make typecheck           mypy --strict on domain/ and services/"
	@echo ""
	@echo "Docker Compose:"
	@echo "  make build               docker compose build"
	@echo "  make migrate-docker      run alembic upgrade head inside the api image"
	@echo "  make seed-docker         run cafeops seed inside the api image"
	@echo "  make drift-backfill-docker  run cafeops drift --backfill inside the api image"
	@echo "  make up                  docker compose up -d"
	@echo "  make down                docker compose down"
	@echo "  make ps                  docker compose ps"
	@echo "  make logs SERVICE=api    docker compose logs -f (default: api)"
	@echo ""
	@echo "Backup / restore (see docs/OPERATIONS.md before using restore for real):"
	@echo "  make backup              deploy/backup.sh, using CAFEOPS_DB_PATH from .env"
	@echo "  make restore FILE=...    deploy/restore.sh FILE"

# --- everything at once -------------------------------------------------------

dev:
	./start.sh

start:
	./start.sh docker

stop:
	./start.sh docker-stop

# --- local, no Docker -------------------------------------------------------

sync:
	uv sync

migrate:
	uv run alembic upgrade head

seed:
	uv run cafeops seed --workbook sashas_corner_finance__LEGACY_.xlsx

drift-backfill:
	uv run cafeops drift --backfill

info:
	uv run cafeops info

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff format .

typecheck:
	uv run mypy cafeops/domain/ cafeops/services/

# --- Docker Compose ----------------------------------------------------------

build:
	docker compose build

migrate-docker:
	docker compose run --rm api alembic upgrade head

seed-docker:
	docker compose run --rm api cafeops seed --workbook sashas_corner_finance__LEGACY_.xlsx

drift-backfill-docker:
	docker compose run --rm api cafeops drift --backfill

up:
	docker compose up -d

down:
	docker compose down

ps:
	docker compose ps

SERVICE ?= api
logs:
	docker compose logs -f $(SERVICE)

# --- backup / restore --------------------------------------------------------
# Both scripts also run standalone (systemd, or by hand) -- see docs/OPERATIONS.md.
# These targets just source .env first so CAFEOPS_DB_PATH etc. are set the same way
# the systemd units set them via EnvironmentFile.

backup:
	set -a && . ./.env && set +a && ./deploy/backup.sh

restore:
	@if [ -z "$(FILE)" ]; then echo "usage: make restore FILE=path/to/backup.db.gz"; exit 1; fi
	set -a && . ./.env && set +a && ./deploy/restore.sh "$(FILE)"
