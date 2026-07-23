FROM debian:bookworm-slim AS fetch

ARG TARGETARCH=amd64
ARG BMA_VERSION=4.10.0
ARG SHA256_AMD64=016034edb0008f03bc6e5f9a2729c25d7aa559cc3f9051909ed855074d2299b3
ARG SHA256_ARM64=dbd2f869632bd732cd65ccc767cf1f99d5b485c93201b0b315de19bdf6262783

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl && rm -rf /var/lib/apt/lists/*

RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) DEB_ARCH=x86_64;  SHA256=$SHA256_AMD64 ;; \
      arm64) DEB_ARCH=aarch64; SHA256=$SHA256_ARM64 ;; \
      *) echo "unsupported arch: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/bma.deb "https://downloads.braiins.com/braiins-manager-agent/assets/${BMA_VERSION}/braiins-manager-agent-linux-${DEB_ARCH}.deb"; \
    echo "$SHA256  /tmp/bma.deb" | sha256sum -c -; \
    dpkg-deb -x /tmp/bma.deb /tmp/bma; \
    install -m 0755 /tmp/bma/usr/bin/bma-daemon /bma-daemon

FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates python3 procps && rm -rf /var/lib/apt/lists/*

COPY --from=fetch /bma-daemon /usr/bin/bma-daemon
COPY webui.py /usr/lib/bma/webui.py
COPY entrypoint.sh /usr/lib/bma/entrypoint.sh
RUN chmod 0755 /usr/lib/bma/entrypoint.sh

VOLUME /data
EXPOSE 8080

ENTRYPOINT ["/usr/lib/bma/entrypoint.sh"]
