# Headless Rockbox UI simulator toolchain for the kiosk contract tests.
# Native arch (arm64 on Apple Silicon): the simulator is a host binary.
FROM debian:bookworm-slim
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gcc g++ make perl python3 zip unzip git file \
        libsdl2-dev ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /work
