# Shared Python runtime for central-api, historian, site controllers, simulators and the lab probe.
FROM python:3.12.8-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    ORCHID_CONFIG=/app/config/platform.yaml \
    ORCHID_SECRETS_DIR=/run/orchid-secrets

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --disable-pip-version-check -r /app/requirements.txt \
 && useradd --uid 10001 --system --no-create-home --shell /usr/sbin/nologin orchid \
 && mkdir -p /data /app/config \
 && chown 10001:10001 /data

COPY orchid /app/orchid

USER 10001
HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=6 \
  CMD python -c "import os,sys,time; p='/tmp/orchid-health'; sys.exit(0 if os.path.exists(p) and time.time()-os.path.getmtime(p) < 15 else 1)"
