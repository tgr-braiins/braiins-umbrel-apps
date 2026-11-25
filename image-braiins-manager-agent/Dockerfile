FROM alpine:3.24 AS fetch

# set by BuildKit per platform; a default here would override it and put the
# amd64 binary into the arm64 image
ARG TARGETARCH
ARG BMA_VERSION=4.15.0
ARG SHA256_AMD64=f661a36a00a2f0caf2c3badbbc2b53e522942d4cb573a49c9412357c87dfd176
ARG SHA256_ARM64=f27bbf22b0efb5424ea9adfd7a63f241744bb167d13cac40fad1636577fcccd8

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
FROM alpine:3.24

RUN apk add --no-cache ca-certificates python3

COPY --from=fetch /bma-daemon /usr/bin/bma-daemon
COPY webui.py /usr/lib/bma/webui.py
# Braiins Sans (WOFF2, from design.braiins.com/braiins/typography), served by webui.py
COPY fonts/ /usr/lib/bma/fonts/
COPY entrypoint.sh /usr/lib/bma/entrypoint.sh
RUN chmod 0755 /usr/lib/bma/entrypoint.sh \
    # the daemon logs to this hardcoded path and rotates it by renaming files
    # next to it, so both the file and /var/log are writable for uid 1000
    && touch /var/log/bma.log && chown 1000:1000 /var/log /var/log/bma.log \
    && mkdir -p /data && chown 1000:1000 /data

VOLUME /data
EXPOSE 8080

ENTRYPOINT ["/usr/lib/bma/entrypoint.sh"]
