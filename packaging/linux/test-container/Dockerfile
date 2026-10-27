# Headless Linux backend test image (H11 groundwork, GO lane-qualification 3). Not a product package and not a clean
# machine: it runs the Python backend's tests on Ubuntu 24.04 to show what already works off macOS. See README.md.
FROM ubuntu:24.04
COPY --from=ghcr.io/astral-sh/uv:0.7.19 /uv /uvx /usr/local/bin/
ENV DEBIAN_FRONTEND=noninteractive UV_PYTHON_INSTALL_DIR=/opt/python UV_PYTHON_PREFERENCE=only-managed \
    UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy UV_CACHE_DIR=/opt/uv-cache
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends ca-certificates git ffmpeg bubblewrap \
      libegl1 libgl1 libgl1-mesa-dri libglib2.0-0t64 libxkbcommon0 libfontconfig1 libdbus-1-3 >/dev/null \
    && rm -rf /var/lib/apt/lists/*
# Only the lock and project files, so this layer is rebuilt only when dependencies change.
# The build context is services/ (scripts/linux-container-test.sh), so nothing else of the repository is sent.
COPY pyproject.toml uv.lock /deps/
RUN cd /deps && uv sync --locked --python 3.13 --all-groups --no-install-project && rm -rf /opt/uv-cache
RUN useradd -m tester && mkdir -p /work /out && chown tester /work /out
USER tester
