FROM node:22-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
WORKDIR /app/src
COPY src/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./
COPY --from=frontend /build/frontend/dist /app/frontend/dist
RUN useradd --create-home --uid 1000 app && mkdir -p /app/runtime && chown -R app:app /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 LOCAL_ONLY=0 PUBLIC_ACCESS=1 DATA_DIR=/app/runtime
USER app
EXPOSE 7860
CMD ["python", "-m", "scripts.serve_cloud"]
