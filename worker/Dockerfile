# ratchet-worker: one replay batch per container.
#   docker build -f worker/Dockerfile -t ratchet-worker:dev .
#   docker build -f worker/Dockerfile --build-arg WITH_HERMES=1 \
#       --build-arg HERMES_INSTALLER_SHA256=<sha256 of install.sh> -t ratchet-worker:hermes .
# No secrets are baked in: the Token Factory key arrives at run time in the worker's env and
# is only read by the in-process proxy sidecar, never by the sandboxed harness.
# Target size < 2 GB; the slim base + venv is ~250 MB, Hermes adds ~600 MB.

FROM ghcr.io/astral-sh/uv:0.13.1 AS uv

FROM python:3.11-slim AS runtime

ARG WITH_HERMES=0
ARG HERMES_REF=v0.22.2
# The adapter reports this instead of probing `hermes --version` per process.
ENV RATCHET_HERMES_VERSION=${HERMES_REF}
ARG HERMES_INSTALLER_SHA256=""

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    TZ=UTC

# libfaketime for frozen time inside sandboxes; a stable path so FAKETIME/LD_PRELOAD do not
# depend on the CPU architecture (ratchet.replay.sandbox.docker.FAKETIME_LIB).
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates faketime libfaketime tini \
 && ln -s "$(dpkg -L libfaketime | grep '/libfaketime\.so\.1$' | head -n1)" \
        /usr/local/lib/libfaketime.so.1 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /usr/local/bin/uv

RUN groupadd --gid 10001 ratchet \
 && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin ratchet

WORKDIR /app

# Dependencies first (cached layer), exactly as locked; then the project itself.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project
COPY ratchet ./ratchet
COPY worker ./worker
RUN uv sync --frozen --no-dev && rm -f /usr/local/bin/uv

# Optional Hermes harness. The installer is fetched to a file, checked
# against a pinned sha256 (no unpinned curl | sh), run non-interactively with
# the research's flags, and the checkout is pinned to HERMES_REF.
RUN if [ "$WITH_HERMES" = "1" ]; then \
      set -eu; \
      test -n "$HERMES_INSTALLER_SHA256"; \
      apt-get update; apt-get install -y --no-install-recommends curl git; \
      curl -fsSL -o /tmp/hermes-install.sh https://hermes-agent.nousresearch.com/install.sh; \
      echo "$HERMES_INSTALLER_SHA256  /tmp/hermes-install.sh" | sha256sum -c -; \
      su ratchet -s /bin/bash -c 'bash /tmp/hermes-install.sh --non-interactive --skip-browser --skip-computer-use'; \
      su ratchet -s /bin/bash -c "git -C ~/.hermes/hermes-agent checkout --quiet $HERMES_REF"; \
      ln -s /home/ratchet/.local/bin/hermes /usr/local/bin/hermes; \
      rm -f /tmp/hermes-install.sh; apt-get purge -y curl; rm -rf /var/lib/apt/lists/*; \
    fi

ENV PATH=/app/.venv/bin:$PATH
USER 10001:10001

ENTRYPOINT ["tini", "--", "python", "/app/worker/entrypoint.py"]
CMD ["--inputs", "/in", "--output", "/out"]
