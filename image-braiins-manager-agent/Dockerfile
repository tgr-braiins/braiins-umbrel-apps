FROM alpine:3.21 AS fetch

ARG TARGETARCH=amd64
ARG BMA_VERSION=4.13.1
ARG SHA256_AMD64=5da996df654640631f93be82b5eea4776cfed23247d83685cedee43b077f1d94
ARG SHA256_ARM64=d00c7fc8d64366a5a87cb1978967ba7f500745c07dc4f26fdda051c510b8f806

RUN apk add --no-cache ca-certificates curl dpkg

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

# bma-daemon is statically linked; the base only exists to run webui.py
FROM alpine:3.21

RUN apk add --no-cache ca-certificates python3

COPY --from=fetch /bma-daemon /usr/bin/bma-daemon
COPY webui.py /usr/lib/bma/webui.py
# Braiins Sans (WOFF2, from design.braiins.com/braiins/typography), served by webui.py
COPY fonts/ /usr/lib/bma/fonts/
COPY entrypoint.sh /usr/lib/bma/entrypoint.sh
RUN chmod 0755 /usr/lib/bma/entrypoint.sh \
    # the daemon logs to this hardcoded path; pre-create it writable for uid 1000
    && touch /var/log/bma.log && chown 1000:1000 /var/log/bma.log \
    && mkdir -p /data && chown 1000:1000 /data

VOLUME /data
EXPOSE 8080

ENTRYPOINT ["/usr/lib/bma/entrypoint.sh"]
