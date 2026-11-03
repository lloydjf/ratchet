# syntax=docker/dockerfile:1.7
# ratchet-control: dashboard (8080) + capture proxy (8081) + mock tools (8082) + orchestrator
# and watcher in one `ratchet serve` process (docs/design/orchestrator-api-cli.md §1).
#
#   docker build -f deploy/control.Dockerfile -t ratchet-control:dev .
#   docker build -f deploy/control.Dockerfile --target control-docker -t ratchet-control:docker .
#
# Build context is the repo root. Only named paths are copied (never `COPY . .`), and
# .dockerignore keeps .env, var/, data/ and keys out of the context as a second line of
# defense. No secret is baked in: keys arrive at run time via env_file / EnvironmentFile.
# Runtime state lives in the /srv/ratchet volume (data/, skills/, tmp/, pii.key).

# --- 1. dashboard: React/Vite build -> /src/dashboard/dist ------------------------------------
# Static output, identical for every target platform: it builds once on the build host's
# native platform, so a multi-arch publish does not run npm under emulation.
FROM --platform=$BUILDPLATFORM node:25-slim AS dashboard
WORKDIR /src/dashboard
COPY dashboard/package.json dashboard/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY dashboard/ ./
RUN npm run build

# --- 2. uv binary, pinned to the same version as worker/Dockerfile ----------------------------
FROM ghcr.io/astral-sh/uv:0.8.18 AS uv

# --- 3. control: python 3.11-slim runtime ----------------------------------------------------
FROM python:3.11-slim AS control

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    TZ=UTC

# git: GitPython shells out to it (skills repo main/candidate). tini: PID 1 signal handling.
# Versions float with the pinned base image's Debian release (rebuilt by CI and dependabot).
# hadolint ignore=DL3008
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates git tini \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 10001 ratchet \
 && useradd --uid 10001 --gid 10001 --home-dir /srv/ratchet --no-create-home \
        --shell /usr/sbin/nologin ratchet

WORKDIR /app

# Dependencies first (cached layer), exactly as locked, no dev group; then the project.
COPY --from=uv /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY ratchet ./ratchet
# pyproject names this build hook; hatch loads it even for the editable install below.
COPY scripts/build_wheel.py ./scripts/build_wheel.py
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev \
 && rm -f /usr/local/bin/uv

# Runtime inputs read through relative settings defaults (scenarios, Tavily fixtures, SPA).
COPY scenarios ./scenarios
COPY tests/fixtures/tavily ./tests/fixtures/tavily
COPY --from=dashboard /src/dashboard/dist ./dashboard/dist
COPY --chmod=0755 deploy/control-entrypoint.sh /usr/local/bin/ratchet-entrypoint

RUN mkdir -p /srv/ratchet/data /srv/ratchet/skills /srv/ratchet/tmp \
 && chown -R 10001:10001 /srv/ratchet

# Paths are absolute so a host .env written for `uv run` (./var/...) cannot redirect them;
# docker-compose sets the same values explicitly. TMPDIR sits on the volume so replay
# scratch dirs are visible to a host Docker daemon when the docker runner is used.
ENV PATH=/app/.venv/bin:$PATH \
    HOME=/srv/ratchet \
    TMPDIR=/srv/ratchet/tmp \
    RATCHET_DATA_DIR=/srv/ratchet/data \
    RATCHET_SKILLS_REPO=/srv/ratchet/skills \
    RATCHET_PII_KEY_FILE=/srv/ratchet/pii.key \
    RATCHET_DASHBOARD_HOST=0.0.0.0 \
    RATCHET_DASHBOARD_PORT=8080 \
    RATCHET_DASHBOARD_DIST=/app/dashboard/dist \
    RATCHET_SCENARIOS_DIR=/app/scenarios \
    RATCHET_TAVILY_FIXTURES_DIR=/app/tests/fixtures/tavily

VOLUME ["/srv/ratchet"]
USER 10001:10001
EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=5s --start-period=90s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; r = urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4); sys.exit(0 if r.status == 200 else 1)"]

ENTRYPOINT ["tini", "--", "ratchet-entrypoint"]
CMD ["serve"]

# --- 4. control-docker: + the Docker CLI, for RATCHET_LOCAL_RUNNER_MODE=docker ----------------
# Used by deploy/docker-compose.docker-runner.yml only. Mounting the host Docker socket makes
# this container root-equivalent on the host; prefer the VM's systemd unit for the demo host.
FROM control AS control-docker
COPY --from=docker:29-cli /usr/local/bin/docker /usr/local/bin/docker
