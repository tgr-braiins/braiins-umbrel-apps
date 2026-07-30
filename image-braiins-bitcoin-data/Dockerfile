# Bitcoin Data has no upstream binary — the whole app is webui.py (stdlib-only
# Python) plus locally served fonts, so the image is just Alpine + python3.
FROM alpine:3.21

RUN apk add --no-cache python3 \
    && mkdir -p /data && chown 1000:1000 /data

COPY webui.py /usr/lib/braiins-bitcoin-data/webui.py
# Braiins Sans (WOFF2, from design.braiins.com/braiins/typography), served by webui.py
COPY fonts/ /usr/lib/braiins-bitcoin-data/fonts/

VOLUME /data
EXPOSE 8080

CMD ["python3", "/usr/lib/braiins-bitcoin-data/webui.py"]
