FROM alpine:3.24@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf AS fetch

# set by BuildKit per platform; a default here would override it and put the
# amd64 binary into the arm64 image
ARG TARGETARCH
ARG TOOLBOX_VERSION=26.11
ARG SHA256_AMD64=dc4df6f339ed0f3692c7114065349ae2f817ec514c63b9e20cbc4e21ab18b8bf
ARG SHA256_ARM64=fe99f692860d3d7c46c30bf2c841edaba5a2c6b71068014074c12a962e2c2b94

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
FROM alpine:3.24@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf

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
