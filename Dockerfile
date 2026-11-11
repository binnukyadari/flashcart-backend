FROM python:3.12-slim AS builder
WORKDIR /build
COPY requirements.lock pyproject.toml ./
COPY src ./src
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --require-hashes -r requirements.lock \
    && /opt/venv/bin/pip install --no-cache-dir --no-deps .

FROM python:3.12-slim
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app
COPY --from=builder /opt/venv /opt/venv
USER 10001:10001
WORKDIR /tmp
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c 'import os,urllib.request; urllib.request.urlopen("http://127.0.0.1:" + os.environ.get("PORT", "8080") + "/health/live", timeout=3)'
CMD ["sh", "-c", "exec uvicorn flashcart.api:create_app --factory --host 0.0.0.0 --port ${PORT:-8080}"]
