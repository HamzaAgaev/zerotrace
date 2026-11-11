FROM node:22-bookworm-slim AS frontend
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm test && npm run build

FROM python:3.12-slim AS python-base
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app:/app/backend
COPY deploy/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir -r /tmp/requirements.lock

FROM python-base AS development
COPY . /app

FROM development AS runtime
COPY --from=frontend /ui/dist /app/frontend/dist
EXPOSE 8000
CMD ["python", "-m", "app.server", "--config", "/app/deploy/config.json"]
