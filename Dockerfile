# chrome-cell — headed Chrome for agent harnesses
# Live view via KasmVNC, persistent profile, CDP bound to localhost
# and proxied to the pod network on 9223.
ARG BASE_TAG=ubuntujammy
FROM ghcr.io/linuxserver/baseimage-kasmvnc:${BASE_TAG}

ARG TARGETARCH

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      fonts-liberation \
      fonts-noto \
      fonts-noto-color-emoji \
      fonts-noto-cjk \
      gnupg \
      socat \
      wget \
 && rm -rf /var/lib/apt/lists/*

# Official Chrome on amd64. Distro Chromium on arm64 (no Google .deb).
RUN set -eux; \
    if [ "${TARGETARCH}" = "amd64" ]; then \
      wget -qO- https://dl.google.com/linux/linux_signing_key.pub \
        | gpg --dearmor -o /usr/share/keyrings/google-linux.gpg; \
      echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-linux.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
        > /etc/apt/sources.list.d/google-chrome.list; \
      apt-get update; \
      apt-get install -y --no-install-recommends google-chrome-stable; \
      ln -sf /usr/bin/google-chrome-stable /usr/local/bin/chrome-bin; \
      rm -rf /var/lib/apt/lists/*; \
    else \
      apt-get update; \
      apt-get install -y --no-install-recommends chromium chromium-sandbox; \
      ln -sf /usr/bin/chromium /usr/local/bin/chrome-bin; \
      rm -rf /var/lib/apt/lists/*; \
    fi

COPY root/ /

RUN chmod +x /usr/local/bin/chrome-cell /defaults/autostart

# LSIO web UI: 3000/http 3001/https
# Chrome CDP: 9222 loopback only
# CDP proxy: 9223 (pod network)
EXPOSE 3000 3001 9223

ENV TITLE="chrome-cell" \
    CHROME_PROFILE_DIR=/config/chrome \
    CDP_PORT=9222 \
    CDP_PROXY_PORT=9223 \
    LAUNCH_URL=about:blank \
    CHROME_SANDBOX=0
