FROM alpine:3.24@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf AS fetch

# set by BuildKit per platform; a default here would override it and put the
# amd64 binary into the arm64 image
ARG TARGETARCH
ARG BRAIINS_PROXY_VERSION=0.1.1
ARG SHA256_AMD64=5ecf9b3476a622c5983c4c3a4bfe4686002e30b6bb27a5377fd9a956da4124b0
ARG SHA256_ARM64=717b8c8bdd0174643b2be884e96758870b8e5887c03d2d1ba4592e00914303d8

RUN apk add --no-cache ca-certificates curl

RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) BP_ARCH=x86_64;  SHA256=$SHA256_AMD64 ;; \
      arm64) BP_ARCH=aarch64; SHA256=$SHA256_ARM64 ;; \
      *) echo "unsupported arch: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/proxy.tar.gz "https://downloads.braiins.com/braiins-proxy/assets/${BRAIINS_PROXY_VERSION}/braiins-proxy-linux-${BP_ARCH}.tar.gz"; \
    echo "$SHA256  /tmp/proxy.tar.gz" | sha256sum -c -; \
    tar xzf /tmp/proxy.tar.gz -C /tmp; \
    install -m 0755 /tmp/braiins-proxy /braiins-proxy

# braiins-proxy is statically linked; ca-certificates is for its outbound TLS
# (pool connections, certificate renewal, the release check). socat is for
# the app's second container, which relays Umbrel's app proxy to the web
# interface (braiins-proxy/docker-compose.yml).
FROM alpine:3.24@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf

RUN apk add --no-cache ca-certificates socat \
    && mkdir -p /data && chown 1000:1000 /data

COPY --from=fetch /braiins-proxy /usr/bin/braiins-proxy

# The configuration (written by the web interface on every save) and the
# hashrate history (state/braiins-proxy/history.db) both live in /data.
# By default the web interface listens on every interface of the container.
# The Umbrel app runs on the host network and narrows this to Umbrel's Docker
# gateway address (braiins-proxy/docker-compose.yml).
ENV HOME=/data \
    XDG_STATE_HOME=/data/state \
    CONFIG_FILE=/data/config.toml \
    BRAIINS_PROXY_HTTP=0.0.0.0:8080
VOLUME /data
EXPOSE 8080 3333-3339

ENTRYPOINT ["/usr/bin/braiins-proxy"]
CMD ["run"]
