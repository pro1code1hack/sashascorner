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
# Sasha's Corner Rewards reads these at runtime: pass artwork and stickers (assets/pass,
# served at /api/loyalty/pass-assets and drawn into every strip) and the café facts the
# pass prints (site/web/src/data/info.json). A build without them would hand out passes
# with no stamps, so fail here instead. Certificates are NOT in the image: they are
# mounted from ./secrets/wallet (docker-compose.yml), and .dockerignore keeps them out.
RUN for f in icon.png icon@2x.png logo.png logo@2x.png google-logo.png \
        stickers/reward.svg stickers/slot-1.svg stickers/slot-8.svg; do \
      test -s "/app/assets/pass/$f" || { echo "missing assets/pass/$f" >&2; exit 1; }; \
    done \
    && test -s /app/site/web/src/data/info.json \
    && test ! -e /app/secrets

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


# --------------------------------------------------------------------------
# The dashboard, built here so the box needs no Node at runtime
# --------------------------------------------------------------------------
# Built into the Caddy image rather than bind-mounted from the host: a bind mount would
# mean whoever deploys has to remember to run `npm run build` first, and a stale or
# missing `web/dist` would serve the previous release -- or a blank page -- with nothing
# saying so. Baking it in makes the image the single artefact that is either right or
# does not build.
# NOTE: `runtime` above is the application image. These two stages come AFTER it, so
# the Dockerfile's *default* target is `caddy` -- which is why docker-compose.yml pins
# `target: runtime` on every app service explicitly. Appending a stage here without that
# pin silently makes every service build the wrong image, and the failure looks like a
# missing executable rather than a wrong image.
FROM node:22-alpine AS web-builder
WORKDIR /web
# Live by default: same-origin requests go to /api/* through this Caddy. The recorded
# fixtures in web/fixtures are opt-in only (VITE_FIXTURES=1, and VITE_LIVE empty), so
# an image can never ship sample figures by accident.
# VITE_LIVE=1 => same-origin live mode: requests go to /api/* through this Caddy,
# behind the single shared password. VITE_API_BASE is for a DIFFERENT origin only,
# and must be a bare origin -- the request paths already carry the /api prefix.
ARG VITE_API_BASE=""
ARG VITE_LIVE="1"
ARG VITE_FIXTURES=""
ENV VITE_API_BASE=$VITE_API_BASE
ENV VITE_LIVE=$VITE_LIVE
ENV VITE_FIXTURES=$VITE_FIXTURES
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
# Members' card art is a copy of assets/pass/stickers (cafeops wallet assets).
RUN test -s public/stickers/slot-1.svg && test -s public/stickers/reward.svg
RUN npm run build


FROM caddy:2-alpine AS caddy
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=web-builder /web/dist /srv/web
