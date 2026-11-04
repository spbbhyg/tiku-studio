#!/usr/bin/env bash
# tiku-studio 一键启动（Linux / macOS / Git Bash）
set -u
cd "$(dirname "$0")"

if [ -x .venv/bin/python ]; then
  PY=.venv/bin/python
elif [ -x .venv/Scripts/python.exe ]; then
  PY=.venv/Scripts/python.exe
else
  PY=""
fi

need_rebuild=0
if [ -z "$PY" ]; then
  need_rebuild=1
elif ! "$PY" -c "import fastapi, uvicorn, fitz, PIL, docx, httpx, rapidocr_onnxruntime" >/dev/null 2>&1; then
  echo "[1/3] 运行环境不完整（缺包或与 Python 版本不匹配），重建 .venv ..."
  need_rebuild=1
fi

if [ "$need_rebuild" = "1" ]; then
  BASEPY="$(command -v python3 || true)"
  [ -n "$BASEPY" ] || BASEPY="$(command -v python || true)"
  if [ -z "$BASEPY" ]; then
    echo "[错误] 未找到 Python 3.11+，请先安装。"; exit 1
  fi
  rm -rf .venv
  "$BASEPY" -m venv .venv
  if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=.venv/Scripts/python.exe; fi
  "$PY" -m pip install --upgrade pip -q -i https://pypi.tuna.tsinghua.edu.cn/simple
  "$PY" -m pip install -q -r apps/api/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple \
    || "$PY" -m pip install -q -r apps/api/requirements.txt
  if ! "$PY" -c "import fastapi, uvicorn, fitz, PIL, docx, httpx, rapidocr_onnxruntime" >/dev/null 2>&1; then
    echo "[错误] 依赖安装失败，请检查网络。"; exit 1
  fi
  "$PY" -c 'import sys;print(sys.version.split()[0])' > .venv/.deps-ok
fi

echo "[2/3] 后端环境 OK"
if [ ! -f apps/web/dist/index.html ]; then
  if command -v npm >/dev/null 2>&1; then
    echo "[3/3] 构建前端 ..."
    (cd apps/web && npm install --no-fund --no-audit && npm run build) || { echo "[错误] 前端构建失败"; exit 1; }
  else
    echo "[警告] 没有前端产物，也没有 npm。"; exit 1
  fi
fi

echo "  tiku-studio: http://localhost:8100  (Ctrl+C 退出)"
exec "$PY" -m uvicorn app.main:app --app-dir apps/api --host 0.0.0.0 --port 8100
