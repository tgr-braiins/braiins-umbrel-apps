# Braiins Umbrel Apps

Umbrel community app store for [Braiins](https://braiins.com) apps, currently:

- **Braiins Manager Agent** — connects mining devices on your local network to
  [Braiins Manager](https://manager.braiins.com), Braiins' fleet-management
  platform.

> **Status: test/preview.** This store lives on a personal account while we
> finalize hosting under a Braiins-owned organization. Expect the store URL and
> image location to change before general availability.

## Install (Umbrel users)

1. In umbrelOS, open **App Store → ⋯ → Community App Stores**.
2. Add `https://github.com/tgr-braiins/braiins-umbrel-apps`.
3. Open the Braiins store and install **Braiins Manager Agent**.
4. In [Braiins Manager](https://manager.braiins.com), add a new agent
   (Devices → Agents → Add agent) to get an **Agent ID** and **Secret key**.
5. Open the app on your Umbrel and paste both values. The agent starts, scans
   your network for miners, and streams telemetry to Braiins Manager.
6. Optional: add the app's home-screen widget (right-click the app tile →
   Widgets) for live miner count and telemetry status on your Umbrel desktop.

## Repo layout

- `umbrel-app-store.yml` — store manifest (store id `braiins`)
- `braiins-braiins-manager-agent/` — the Umbrel app: manifest
  (`umbrel-app.yml`), `docker-compose.yml`, `icon.svg`
- `image/` — source of the Docker image
  (`ghcr.io/tgr-braiins/braiins-manager-agent`)

## How the image works

The image does **not** build the agent from source. It downloads the official
signed release `.deb` from the public feed
(`https://downloads.braiins.com/braiins-manager-agent/index.json`), verifies its
sha256, and extracts the `bma-daemon` binary. On top of that it adds:

- `image/webui.py` — stdlib-only Python on :8080: the config page where the
  user enters the Agent ID / Secret key (written to `/data/daemon.yaml`, mode
  0600), `/status` (JSON for the page's live stats — miner count and telemetry
  activity parsed from the daemon log), and `/widgets/status` (Umbrel
  home-screen widget)
- `image/entrypoint.sh` — supervisor that starts the daemon once the config
  exists and restarts it when the config changes; the daemon log is kept as a
  real file for the stats parsing, mirrored to `docker logs`, size-capped

Credentials persist in the Umbrel app-data volume across app updates and are
removed on uninstall.

## Developing

### Build and smoke-test locally

```bash
cd image
docker build -t bma-umbrel-dev .          # plain build gives amd64 (TARGETARCH defaults); use buildx for arm64
docker run -d --name bma-dev --user 1000:1000 --init -p 18080:8080 -v bma-dev-data:/data bma-umbrel-dev  # mirror prod: unprivileged uid
curl http://localhost:18080/status         # {"configured": false, "running": false}
```

Open http://localhost:18080, paste test credentials from Braiins Manager
(Devices → Agents → Add agent) — the status pill should turn "Agent running"
within ~10 s and `docker logs bma-dev` should show the daemon polling.
Changing credentials in the UI must restart the daemon (new PID).

Cleanup: `docker rm -f bma-dev && docker volume rm bma-dev-data`.

### Push a new image version to ghcr

The package lives at `ghcr.io/tgr-braiins/braiins-manager-agent` and **must stay
public** (umbreld pulls anonymously). You need a GitHub PAT with
`write:packages` for an account with access to the package:

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u <github-user> --password-stdin
cd image
docker buildx create --use                 # once; QEMU needed for arm64 on x86 hosts:
docker run --privileged --rm tonistiigi/binfmt --install arm64
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --tag ghcr.io/tgr-braiins/braiins-manager-agent:<version> \
  --push .
```

Use the upstream agent version from `umbrel-app.yml` as the tag (e.g. `4.10.0`), then pin the index digest in `docker-compose.yml`.
`image/.gitlab-ci.yml` automates exactly this; wire it into CI when the repo
finds its final home.

### Test on a real Umbrel

Add this repo as a community app store (App Store → ⋯ → Community App Stores)
and install the app. To iterate without waiting for the store's periodic git
refresh, SSH in (`ssh umbrel@umbrel.local`, dashboard password) and run:

```bash
cd umbrel/app-stores/<this-store-dir> && git pull
umbreld client apps.update.mutate --appId braiins-braiins-manager-agent
# state check:
umbreld client apps.state.query --appId braiins-braiins-manager-agent
# container logs (needs sudo):
sudo docker logs braiins-braiins-manager-agent_web_1
```

An app install/update **always pulls the image from ghcr** — push the image
before bumping the compose tag, or the install fails.

## Release flow

1. New agent version is published to the public download feed.
2. Bump `BMA_VERSION` and the per-arch `.deb` checksums in `image/Dockerfile`.
3. Build + push the multi-arch image, tagged with the upstream agent version:
   `docker buildx build --platform linux/amd64,linux/arm64 --tag <registry>/braiins-manager-agent:<X.Y.Z> --push image/`
   (`image/.gitlab-ci.yml` is a reference pipeline for automating this).
4. Pin the **multi-arch index digest** in `docker-compose.yml`
   (`tag@sha256:…` — get it from `docker buildx imagetools inspect <image>:<tag>`),
   bump `version` in `umbrel-app.yml`, push this repo.
5. Umbrel shows an "Update" badge to users; updating pulls the new image and
   recreates the containers, keeping the configured credentials.

## Compliance with the official Umbrel App Store

**Read this before changing the package.** The package deliberately follows the
official packaging contract from
[getumbrel/umbrel-apps](https://github.com/getumbrel/umbrel-apps) — that repo's
`AGENTS.md` / `CLAUDE.md` point to repo-local skills in `.claude/skills/`
(`umbrel-package-app`, `umbrel-update-app`, `umbrel-test-app`) which are the
authoritative, reviewed packaging rules. If you work on this package with a
coding agent, have it read those first. Highlights this package implements:

- images multi-arch (`linux/amd64` + `linux/arm64`), publicly pullable,
  pinned as `tag@sha256:<index digest>`
- headless upstream fronted by a setup/status web page behind `app_proxy` —
  no SSH/CLI needed for normal use
- runs unprivileged (`user: "1000:1000"`, `init: true`); no host mounts,
  host networking, or Docker socket
- user state under `${APP_DATA_DIR}/data` (bind-mount source committed as
  `data/.gitkeep`); manifest `version` is the upstream agent version
- home-screen widget (`widgets:`) backed by `web:8080/widgets/status`

Validate any change with the official linter (checks manifest shape, port
uniqueness, image pinning/multi-arch, compose wiring):

```bash
git clone --depth 1 https://github.com/getumbrel/umbrel-apps /tmp/umbrel-apps
cp -r braiins-braiins-manager-agent /tmp/umbrel-apps/
cd /tmp/umbrel-apps && npm ci && npm run lint:apps -- braiins-braiins-manager-agent --check-images
```

Known intentional deviations (community store vs. official submission):

- `icon` + committed `icon.svg` / `gallery/` — required here (community stores
  serve assets from the repo); for an official-store PR these are removed and
  screenshots/logo go in the PR body (Umbrel hosts final assets).
- `submission: ""` — becomes the PR URL at official submission time.

## Notes for developers picking this up

- **Umbrel constraints** (learned by testing on real hardware):
  - umbreld force-pulls images on install/update — the image must be publicly
    pullable; `pull_policy` is ignored.
  - The manifest `port` (currently **4547**) is a static host-port claim.
    There is no conflict detection or fallback in the platform.
  - The dashboard CSP (`img-src * blob:`) blocks `data:` URIs — the manifest
    `icon` must be an http(s) URL.
  - Bridge networking is sufficient; miner discovery is range-based TCP
    scanning, no host networking needed.
- **TODO before GA**: move repo + image to a Braiins-owned org/registry;
  wire the image build + version bump into the agent release pipeline;
  submit to the official Umbrel app store (getumbrel/umbrel-apps) — the
  package already passes their linter except the intentional deviations
  listed above.
