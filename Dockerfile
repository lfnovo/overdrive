FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.9.27 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.12-slim
LABEL org.opencontainers.image.source="https://github.com/lfnovo/overdrive" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.description="A self-hosted CRM for humans and AI agents"
ENV PATH="/app/.venv/bin:$PATH" PYTHONPATH=/app PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    STORAGE_PATH=/data/attachments
WORKDIR /app
RUN groupadd --gid 10001 overdrive && useradd --uid 10001 --gid overdrive --no-create-home overdrive \
    && mkdir -p /data/attachments && chown -R overdrive:overdrive /data
COPY --from=builder /app/.venv /app/.venv
COPY overdrive ./overdrive
COPY migrations ./migrations
COPY scripts ./scripts
COPY schemas.surrealql LICENSE ./
COPY third_party ./third_party
USER overdrive
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"
CMD ["uvicorn", "overdrive.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
