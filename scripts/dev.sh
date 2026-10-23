#!/bin/bash
# 一条命令拉起 Termitaria。agent 角色来自 SwarmSpec 和运行中创建的项目。
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [[ ! -x .venv/bin/python ]]; then
  echo "缺少 .venv。先执行: uv sync" >&2
  exit 1
fi
if [[ ! -d web/node_modules ]]; then
  echo "缺少 web/node_modules。先执行: npm install --prefix web" >&2
  exit 1
fi

if lsof -nP -iTCP:8787 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "127.0.0.1:8787 已被占用。先停掉旧的控制面，再执行 make up" >&2
  exit 1
fi
if lsof -nP -iTCP:3000 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "3000 已被占用。先停掉旧的 Next.js，再执行 make up" >&2
  exit 1
fi

if lsof -nP -iTCP:4222 -sTCP:LISTEN >/dev/null 2>&1; then
  docker compose up -d qdrant falkordb postgres
else
  docker compose up -d nats qdrant falkordb postgres
fi
export MEMORY_BACKEND="${MEMORY_BACKEND:-agent}"
export GRAPH_CANNED="${GRAPH_CANNED:-0}"

for _ in $(seq 1 30); do
  if curl -sf http://127.0.0.1:8222/healthz >/dev/null; then
    break
  fi
  sleep 1
done
curl -sf http://127.0.0.1:8222/healthz >/dev/null

pids=()
cleanup() {
  for pid in "${pids[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait || true
}
trap cleanup EXIT INT TERM

go run ./cmd/runtime &
pids+=($!)
for _ in $(seq 1 60); do
  if curl -sf http://127.0.0.1:8787/api/health >/dev/null; then
    break
  fi
  if ! kill -0 "${pids[0]}" 2>/dev/null; then
    echo "runtime exited before gateway was ready" >&2
    exit 1
  fi
  sleep 1
done
curl -sf http://127.0.0.1:8787/api/health >/dev/null

.venv/bin/python -m services.worker.main &
pids+=($!)
.venv/bin/python -m services.memory.main &
pids+=($!)
.venv/bin/python -m services.graph.main &
pids+=($!)
npm run dev --prefix web &
pids+=($!)

echo "up: web http://127.0.0.1:3000  gateway http://127.0.0.1:8787"
while true; do
  for pid in "${pids[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "process $pid exited" >&2
      exit 1
    fi
  done
  sleep 2
done
