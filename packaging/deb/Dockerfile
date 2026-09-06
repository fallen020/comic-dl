FROM debian:13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        debhelper \
        python3 \
        python3-pip \
        unzip \
    && rm -rf /var/lib/apt/lists/*
