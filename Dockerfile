FROM python:3.14-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DUTYBELL_DATABASE=/data/dutybell.db

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN python -m pip install --no-cache-dir . \
    && addgroup --system dutybell \
    && adduser --system --ingroup dutybell --home /nonexistent dutybell \
    && mkdir /data \
    && chown dutybell:dutybell /data

USER dutybell
VOLUME ["/data"]
EXPOSE 8742

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8742/healthz', timeout=2).read()"]

ENTRYPOINT ["dutybell"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8742", "--database", "/data/dutybell.db"]
