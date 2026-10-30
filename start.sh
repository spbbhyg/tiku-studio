#!/usr/bin/env bash
# tiku-studio 一键启动（Linux / macOS / Git Bash）
set -e
cd "$(dirname "$0")"

PY=.venv/bin/python
[ "$(uname -s)" = "MINGW64_NT"* ] || [ -f .venv/Scripts/python.exe ] && PY=.venv/Scripts/python.exe

if ! command -v python3 >/dev/null 2>&1 && ! command -v python >/dev/null 2>&1; then
  echo "[错误] 未找到 Python 3，请先安装。"; exit 1
fi
if [ ! -f "$PY" ]; then
  echo "[1/3] 创建虚拟环境..."
  python3 -m venv .venv 2>/dev/null || python -m venv .venv
fi
if [ ! -f .venv/.deps-ok ]; then
  echo "[2/3] 安装后端依赖..."
  "$PY" -m pip install -q -r apps/api/requirements.txt && touch .venv/.deps-ok
fi
if [ ! -f apps/web/dist/index.html ]; then
  if command -v npm >/dev/null 2>&1; then
    echo "[3/3] 构建前端..."
    (cd apps/web && npm install --no-fund --no-audit && npm run build)
  else
    echo "[提示] 未安装 Node.js，跳过前端构建。"
  fi
fi
echo "  tiku-studio: http://localhost:8100  (Ctrl+C 退出)"
"$PY" -m uvicorn app.main:app --app-dir apps/api --host 0.0.0.0 --port 8100
