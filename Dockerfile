# syntax=docker/dockerfile:1
# Multi-stage image for the Manuvi web app. No secrets at build time or in the image.
FROM python:3.13-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install .

FROM python:3.13-slim AS runtime
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MANUVI_APP_MODULE=src.webapp.hosted:create_app
RUN groupadd --system --gid 10001 manuvi \
    && useradd --system --uid 10001 --gid manuvi --home-dir /app --shell /usr/sbin/nologin manuvi \
    && mkdir -p /app /var/lib/manuvi \
    && chown manuvi:manuvi /app /var/lib/manuvi
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY --chown=manuvi:manuvi src ./src
COPY --chown=manuvi:manuvi config ./config
COPY --chown=root:root --chmod=0755 deploy/entrypoint.sh /usr/local/bin/entrypoint.sh
USER manuvi
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"]
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["sh", "-c", "exec uvicorn --factory \"$MANUVI_APP_MODULE\" --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
