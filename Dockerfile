# arxivist web UI container.
# Build:  docker build -t arxivist .
# Run:    docker run --rm -p 8000:8000 \
#             -e ANTHROPIC_API_KEY=sk-... \
#             -v "$PWD/config.yaml:/config/config.yaml:ro" \
#             arxivist
# Bedrock instead of an API key: pass AWS creds/region as env or mount ~/.aws.
FROM python:3.12-slim

# Keep Python lean and unbuffered so logs stream to `docker logs`.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ARXIVIST_WORKDIR=/data \
    PORT=8000

WORKDIR /app

# Install dependencies first for better layer caching.
COPY pyproject.toml README.md ./
COPY arxivist ./arxivist
RUN pip install --no-cache-dir ".[all]"

# Per-session upload/organize workspaces live here (mount a volume to persist).
RUN mkdir -p /data && useradd -m -u 10001 arxivist && chown -R arxivist /data /app
USER arxivist

EXPOSE 8000

# Config is optional: mount one at /config/config.yaml to enable the LLM/topics.
# Shell form so ${PORT} expands; falls back to running without --config if absent.
CMD ["sh", "-c", "arxivist serve --host 0.0.0.0 --port ${PORT} --workdir ${ARXIVIST_WORKDIR} $([ -f /config/config.yaml ] && echo --config /config/config.yaml)"]
