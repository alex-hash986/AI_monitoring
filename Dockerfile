FROM python:3.12-slim-bookworm AS app

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 monitor \
    && install -d -o monitor -g monitor /var/lib/celery
COPY config.py alembic.ini ./
COPY src/ src/

USER monitor

FROM app AS worker
USER root
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN playwright install --with-deps chromium && chmod -R a+rX /opt/playwright
USER monitor
