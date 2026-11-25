# HistBias Probe – single-container image. Data (DB + secret.key) lives in the /data volume.
#   docker build -t histbias-probe .
#   docker run --rm -p 127.0.0.1:8000:8000 -v histbias-data:/data histbias-probe
# Keep the "127.0.0.1:" prefix: anyone who can reach the port can use your stored API keys.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.8 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first (cached layer), then the app.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN uv sync --frozen --no-dev

RUN useradd --system --uid 10001 --home-dir /app histbias \
    && mkdir /data && chown histbias /data
USER histbias

# Inside the container the server must listen on all interfaces; publish the port on the
# host's loopback only (see above). HISTBIAS_IN_CONTAINER adjusts the startup warning.
ENV HISTBIAS_DATA_DIR=/data \
    HISTBIAS_HOST=0.0.0.0 \
    HISTBIAS_PORT=8000 \
    HISTBIAS_IN_CONTAINER=true

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=4)"

# First start: creates the DB, secret.key (0600) and loads the seed data; later starts only
# apply new migrations.
CMD ["histbias", "serve", "--init"]
