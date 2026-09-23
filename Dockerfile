# syntax=docker/dockerfile:1.7
#
# One image, three commands (api / bot / scheduler) -- see docker-compose.yml. There is
# deliberately no multi-worker / multi-replica anything baked in here: spec 3 is 40
# transactions a day against one SQLite file with a single writer, and a second app
# instance would contend on that file rather than help. See CLAUDE.md 3 and
# ARCHITECTURE.md 8F.8 before changing that.

ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim-bookworm AS base
# Astral's static uv binary, not `pip install uv` -- no resolver of its own to bootstrap.
COPY --from=ghcr.io/astral-sh/uv:0.10.4 /uv /uvx /usr/local/bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

# ---------------------------------------------------------------------------
# builder: resolve and install dependencies into /app/.venv, then the project itself.
# Split into two `uv sync` calls so the (large, slow-changing) dependency layer stays
# cached across rebuilds that only touch application code.
# ---------------------------------------------------------------------------
FROM base AS builder
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# ---------------------------------------------------------------------------
# runtime: slim, non-root, no build toolchain. `sqlite3` is the CLI the backup and
# restore scripts shell out to (see deploy/backup.sh) -- it is not the Python stdlib
# module, which is already in the base image and unrelated.
# ---------------------------------------------------------------------------
FROM base AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
        sqlite3 \
        curl \
        rsync \
        openssh-client \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 1000 cafeops \
    && useradd --system --uid 1000 --gid cafeops --create-home --home-dir /app cafeops

WORKDIR /app
COPY --from=builder --chown=cafeops:cafeops /app /app

ENV PATH="/app/.venv/bin:${PATH}"

# The SQLite file lives on a mounted volume, never inside the image -- an image
# rebuild or a `docker compose down -v`-free redeploy must never touch café data.
# /backups exists here too so a freshly created `cafeops_backups` named volume is
# seeded with cafeops:cafeops ownership (Docker copies a new named volume's initial
# content, including ownership, from whatever already exists at that path in the
# image) -- without this, the `backup` service's non-root user can create the volume
# but not write into it, and the failure only shows up the first time it actually runs.
RUN mkdir -p /data /backups && chown cafeops:cafeops /data /backups
VOLUME ["/data"]

USER cafeops
EXPOSE 8000

# No image-level HEALTHCHECK: the three services answer to very different probes
# (HTTP health endpoint vs. two headless processes with no port at all), so each is
# defined per-service in docker-compose.yml instead of guessed at here.

# Default command is the API; docker-compose.yml overrides `command:` for bot and
# scheduler. Binds 0.0.0.0 here because in the container the only thing that can reach
# port 8000 is Caddy, over the private compose network -- see docker-compose.yml's
# comment on why `api` publishes no host port of its own.
CMD ["cafeops", "serve", "--host", "0.0.0.0", "--port", "8000"]
