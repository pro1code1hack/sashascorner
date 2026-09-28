#!/usr/bin/env bash
# One command for everything.
#
#   ./start.sh              local dev, hot reload, all in this terminal (Ctrl-C stops all)
#   ./start.sh --bot        ... and the Telegram bot (needs CAFEOPS_TELEGRAM_* in .env)
#   ./start.sh docker       the production stack: build, migrate, start (docker-compose.yml)
#   ./start.sh docker-stop  stop it (data volumes are kept)
#   ./start.sh docker-logs  follow its logs
#
# Local dev runs, against the one ./cafeops.db:
#   ops api         http://127.0.0.1:8000   uvicorn --reload
#   back office     http://localhost:5178   vite, live API
#   scheduler       (no port)               APScheduler jobs
#   site api        http://127.0.0.1:8100   sashasite
#   public site     http://localhost:4321   astro dev
# Migrations for both Alembic histories run first. Each line of output is prefixed
# with the service it came from.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

API_PORT=${API_PORT:-8000}
WEB_PORT=${WEB_PORT:-5178}
SITE_API_PORT=${SITE_API_PORT:-8100}
SITE_WEB_PORT=${SITE_WEB_PORT:-4321}

die() { echo "start.sh: $*" >&2; exit 1; }

# --- docker ------------------------------------------------------------------
docker_up() {
  command -v docker >/dev/null || die "docker is not installed"
  docker info >/dev/null 2>&1 || die "the Docker daemon is not running (start Docker Desktop)"
  [ -f .env ] || die "no .env -- copy .env.example to .env and fill it in first"
  # `migrate` and `site-migrate` are one-shot services the others wait on, so a
  # plain `up` is the whole first run too.
  docker compose up -d --build
  docker compose ps
  echo
  echo "Back office: http://localhost (or https://\$CAFEOPS_DOMAIN)"
  echo "Logs:        ./start.sh docker-logs"
}

case "${1:-dev}" in
  docker)      docker_up; exit 0 ;;
  docker-stop) docker compose down; exit 0 ;;
  docker-logs) docker compose logs -f --tail=100; exit 0 ;;
  dev|--bot)   ;;
  -h|--help)   sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *)           die "unknown command '$1' (try --help)" ;;
esac

WITH_BOT=0
for a in "$@"; do [ "$a" = "--bot" ] && WITH_BOT=1; done

# --- local dev ---------------------------------------------------------------
command -v uv  >/dev/null || die "uv is not installed (https://docs.astral.sh/uv/)"
command -v npm >/dev/null || die "npm is not installed"

for port in $API_PORT $WEB_PORT $SITE_API_PORT $SITE_WEB_PORT; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    die "port $port is already in use: $(lsof -nP -iTCP:"$port" -sTCP:LISTEN | awk 'NR==2{print $1" pid "$2}'). Stop it first."
  fi
done

echo "== dependencies"
uv sync --quiet
(cd site/backend && uv sync --quiet)
[ -d web/node_modules ]      || (cd web && npm ci)
[ -d site/web/node_modules ] || (cd site/web && npm ci)

echo "== migrations"
uv run alembic upgrade head
(cd site/backend && uv run alembic upgrade head)

PIDS=()
cleanup() {
  trap - INT TERM EXIT
  echo; echo "== stopping"
  for pid in ${PIDS[@]+"${PIDS[@]}"}; do kill -- -"$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# run NAME DIR CMD... -- start in its own process group so Ctrl-C takes the
# children (uvicorn's reloader, vite's esbuild) down with it.
run() {
  local name=$1 dir=$2; shift 2
  (
    cd "$dir"
    exec "$@" 2>&1 | while IFS= read -r line; do printf '%-10s | %s\n' "$name" "$line"; done
  ) &
  PIDS+=($!)
}
set -m  # job control: each background job gets its own process group

echo "== starting"
run api       "$ROOT"            uv run cafeops serve --host 127.0.0.1 --port $API_PORT --reload
run scheduler "$ROOT"            uv run cafeops scheduler-run
run web       "$ROOT/web"        env VITE_LIVE=1 CAFEOPS_API_URL=http://127.0.0.1:$API_PORT \
                                   npx vite --port $WEB_PORT --strictPort
run site-api  "$ROOT/site/backend" uv run sashasite serve --port $SITE_API_PORT
run site-web  "$ROOT/site/web"   env SITE_API_URL=http://127.0.0.1:$SITE_API_PORT \
                                   npx astro dev --port $SITE_WEB_PORT --ignore-lock
if [ "$WITH_BOT" = 1 ]; then
  run bot     "$ROOT"            uv run cafeops bot-run
fi

cat <<EOF

  back office   http://localhost:$WEB_PORT
  ops api       http://127.0.0.1:$API_PORT/api/health
  public site   http://localhost:$SITE_WEB_PORT
  site api      http://127.0.0.1:$SITE_API_PORT/api/health

  Ctrl-C stops everything.

EOF

# Stay up until Ctrl-C, or until any one service dies -- then take the rest down
# rather than leave a half-running stack that looks fine.
# (Polled: macOS ships bash 3.2, which has no `wait -n`.)
while :; do
  for pid in "${PIDS[@]}"; do
    kill -0 "$pid" 2>/dev/null || { echo "start.sh: a service exited -- stopping the rest" >&2; exit 1; }
  done
  sleep 2
done
