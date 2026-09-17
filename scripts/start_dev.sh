#!/usr/bin/env bash
# ============================================================
#  BidCraft 标书匠 - 一键启动开发环境（macOS / Linux）
#
#    后端: http://127.0.0.1:8000   (API 文档 http://127.0.0.1:8000/docs)
#    前端: http://127.0.0.1:5180
#
#  首次使用请先执行：
#    pip install -r backend/requirements.txt
#    (cd frontend && npm install)
#    python scripts/check_env.py --init-db
# ============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cleanup() {
  echo ""
  echo "正在停止服务..."
  [[ -n "${BACK_PID:-}" ]] && kill "$BACK_PID" 2>/dev/null || true
  [[ -n "${FRONT_PID:-}" ]] && kill "$FRONT_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "[1/2] 启动后端 FastAPI (127.0.0.1:8000)..."
( cd "$ROOT/backend" && python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 ) &
BACK_PID=$!

sleep 4

echo "[2/2] 启动前端 Vite (127.0.0.1:5180)..."
( cd "$ROOT/frontend" && npm run dev ) &
FRONT_PID=$!

echo ""
echo "本机访问:       http://127.0.0.1:5180"
echo "后端 API 文档:  http://127.0.0.1:8000/docs"
echo "按 Ctrl+C 停止全部服务"
echo ""

wait
