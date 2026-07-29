FROM alpine:3.21 AS fetch

ARG TARGETARCH=amd64
ARG TOOLBOX_VERSION=26.06
ARG SHA256_AMD64=5db559b707fc97a96f10453fd9ed33dd9d11b45a84c9cda8350af4f16409196b
ARG SHA256_ARM64=2faa817e89d25d831d0d6e5e41e7d07cc183db1ccfe0c416a5302885490c9660

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
