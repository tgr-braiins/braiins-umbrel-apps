FROM alpine:3.21 AS fetch

ARG TARGETARCH=amd64
ARG BMA_VERSION=4.11.0
ARG SHA256_AMD64=08356dcd1ca6bee3840ec8cb036650c5ad300b94e8cf8f4a49a3a3ca87383bd7
ARG SHA256_ARM64=623481430e4a43dd4c2396c7a98e49751059227eaa7e7f4310b741179021d59c

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
COPY entrypoint.sh /usr/lib/bma/entrypoint.sh
RUN chmod 0755 /usr/lib/bma/entrypoint.sh \
    # the daemon logs to this hardcoded path; pre-create it writable for uid 1000
    && touch /var/log/bma.log && chown 1000:1000 /var/log/bma.log \
    && mkdir -p /data && chown 1000:1000 /data

VOLUME /data
EXPOSE 8080

ENTRYPOINT ["/usr/lib/bma/entrypoint.sh"]
