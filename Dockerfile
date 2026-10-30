# ---- 前端构建 ----
FROM node:20-alpine AS web
WORKDIR /build
COPY apps/web/package.json apps/web/package-lock.json* ./
RUN npm install --no-fund --no-audit
COPY apps/web/ ./
RUN npm run build

# ---- 运行镜像 ----
FROM python:3.12-slim
WORKDIR /app
COPY apps/api/requirements.txt apps/api/requirements.txt
RUN pip install --no-cache-dir -r apps/api/requirements.txt && \
    pip install --no-cache-dir playwright && \
    python -m playwright install chromium --with-deps || \
    python -m playwright install chromium
COPY apps/api/ apps/api/
COPY packages/schema/ packages/schema/
COPY seed/ seed/
COPY --from=web /build/dist apps/web/dist/
RUN mkdir -p data
ENV TIKU_DATA_DIR=/app/data TIKU_PORT=8100
EXPOSE 8100
CMD ["python", "-m", "uvicorn", "app.main:app", "--app-dir", "apps/api", "--host", "0.0.0.0", "--port", "8100"]
