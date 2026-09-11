# The nightly evals runner. promptfoo plus the generator. Platform-owned.
# Real path: FROM the platform hardened base image.
FROM node:22-bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-yaml ca-certificates curl \
 && ARCH=$(dpkg --print-architecture) \
 && curl -fsSL "https://dl.k8s.io/release/v1.34.1/bin/linux/${ARCH}/kubectl" -o /usr/local/bin/kubectl \
 && chmod +x /usr/local/bin/kubectl \
 && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
RUN npm install -g promptfoo@0.123.0 && npm cache clean --force
RUN useradd -u 10001 -m app
WORKDIR /app
COPY build.py run.sh ./
USER 10001
ENV HOME=/tmp PROMPTFOO_DISABLE_TELEMETRY=1 PROMPTFOO_DISABLE_UPDATE=1
CMD ["./run.sh"]
