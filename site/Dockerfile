# syntax=docker/dockerfile:1.7
#
# Sasha's Corner public website. Build context is `site/` (see deploy/compose.site.yml).
#
# Stages, and which one each compose service builds -- ALWAYS pin `target:`, never rely
# on "the last stage wins" (the ops Dockerfile learned that the hard way):
#
#   py-base      python 3.12 + uv
#   api-build    backend venv (sashasite + deps)
#   data         runs `sashasite menu-export` / `info-export` from config/*.toml, so
#                the static HTML is built from the same menu the API serves
#   web          node: npm ci + astro build, with the fresh JSON copied in
#   api          target for `site-api`: non-root, `sashasite serve` on :8100
#   static       target for `site-static`: a tiny image holding dist/ + the Caddy
#                snippet, which it copies into the `site_static` volume Caddy reads
#
# The exports read only backend/config/*.toml, never the database, so the build needs
# no volume and no secrets.

ARG PYTHON_VERSION=3.12
ARG NODE_VERSION=22

# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS py-base
COPY --from=ghcr.io/astral-sh/uv:0.10.4 /uv /uvx /usr/local/bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app/backend

# ---------------------------------------------------------------------------
FROM py-base AS api-build
# Dependencies first (cached across code-only changes), then the project itself.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=backend/uv.lock,target=uv.lock \
    --mount=type=bind,source=backend/pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev
COPY backend/ ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# ---------------------------------------------------------------------------
FROM api-build AS data
RUN mkdir -p /out \
    && .venv/bin/sashasite menu-export --out /out/menu.json \
    && .venv/bin/sashasite info-export --out /out/info.json

# ---------------------------------------------------------------------------
FROM node:${NODE_VERSION}-alpine AS web
WORKDIR /site/web
# Canonical origin baked into every <link rel=canonical>, og:url and the sitemap.
# astro.config.mjs falls back to https://sashascorner.co.uk when this is empty.
ARG SITE_PUBLIC_URL=""
# Swetrix analytics (src/scripts/track.ts). Build-time only: Astro inlines PUBLIC_* into
# the pages, so changing either needs `docker compose build site-static`. Empty = no
# analytics at all (the script is never loaded). PUBLIC_SWETRIX_API only for self-hosted.
ARG PUBLIC_SWETRIX_PID=""
ARG PUBLIC_SWETRIX_API=""
ENV PUBLIC_SWETRIX_PID=$PUBLIC_SWETRIX_PID \
    PUBLIC_SWETRIX_API=$PUBLIC_SWETRIX_API
COPY web/package.json web/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY web/ ./
COPY --from=data /out/menu.json /out/info.json ./src/data/
# The stamp stickers are copies of assets/pass/stickers/ (outside this build context),
# kept in sync by `cafeops wallet assets`. Fail the build rather than ship a web card
# and scanner with missing stamps.
RUN for n in 1 2 3 4 5 6 7 8; do test -s "public/stickers/slot-$n.svg" || { echo "missing public/stickers/slot-$n.svg: run cafeops wallet assets" >&2; exit 1; }; done \
    && test -s public/stickers/reward.svg
RUN if [ -n "$SITE_PUBLIC_URL" ]; then export SITE_PUBLIC_URL; else unset SITE_PUBLIC_URL; fi \
    && npm run build

# ---------------------------------------------------------------------------
FROM py-base AS api
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
# Same uid/gid as the ops image's `cafeops` user: both processes write the one SQLite
# file (and its -wal/-shm siblings) on the shared `cafeops_data` volume. A different
# uid would get "attempt to write a readonly database" on the first booking.
RUN groupadd --system --gid 1000 site \
    && useradd --system --uid 1000 --gid site --create-home --home-dir /app site
COPY --from=api-build --chown=site:site /app/backend /app/backend
ENV PATH="/app/backend/.venv/bin:${PATH}"
USER site
EXPOSE 8100
CMD ["sashasite", "serve", "--host", "0.0.0.0", "--port", "8100"]

# ---------------------------------------------------------------------------
FROM alpine:3.20 AS static
COPY --from=web /site/web/dist /site/public
COPY deploy/Caddyfile.site /site/site.caddy
# Copy the release into the volume Caddy serves. Files are added/overwritten, never
# deleted first, so a visitor holding the previous HTML can still fetch the previous
# hashed /_astro/* files during a deploy. Prune occasionally (see deploy/README.md).
CMD ["sh", "-c", "mkdir -p /srv/site/public && cp -a /site/public/. /srv/site/public/ && cp /site/site.caddy /srv/site/site.caddy && echo 'site-static: release copied' "]
