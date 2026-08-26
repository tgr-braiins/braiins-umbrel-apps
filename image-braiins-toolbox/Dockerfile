FROM alpine:3.21 AS fetch

ARG TARGETARCH=amd64
ARG TOOLBOX_VERSION=26.08
ARG SHA256_AMD64=f46102447bf74291cc1bd82f215e815949a730b1c6aa56786cef32f7c9621f58
ARG SHA256_ARM64=3ae029644d226218b3797cf66baec179f03be722db4c97eeb24d5039237b4ca0

RUN apk add --no-cache ca-certificates curl

RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) TB_ARCH=x86_64;  SHA256=$SHA256_AMD64 ;; \
      arm64) TB_ARCH=aarch64; SHA256=$SHA256_ARM64 ;; \
      *) echo "unsupported arch: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/toolbox.tar.gz "https://downloads.braiins.com/braiins-toolbox/assets/${TOOLBOX_VERSION}/braiins-toolbox-linux-${TB_ARCH}.tar.gz"; \
    echo "$SHA256  /tmp/toolbox.tar.gz" | sha256sum -c -; \
    tar xzf /tmp/toolbox.tar.gz -C /tmp; \
    install -m 0755 /tmp/braiins-toolbox /braiins-toolbox

# braiins-toolbox is statically linked (static-pie); ca-certificates is for its
# outbound HTTPS (BOS firmware downloads during installs, release feed checks)
FROM alpine:3.21

RUN apk add --no-cache ca-certificates \
    && mkdir -p /data && chown 1000:1000 /data

COPY --from=fetch /braiins-toolbox /usr/bin/braiins-toolbox

# all toolbox state (config.toml, pool_presets.toml, logs) resolves via XDG
# dirs under $HOME, so a single /data volume persists everything
ENV HOME=/data
VOLUME /data
EXPOSE 8888

ENTRYPOINT ["/usr/bin/braiins-toolbox"]
CMD ["--gui-listen-address", "0.0.0.0:8888", "--max-log-size", "100MB"]
