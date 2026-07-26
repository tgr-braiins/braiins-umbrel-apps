# Design decisions

Short rationale for everything that might otherwise earn a "wtf, why?".
Format: what we did → why → when to revisit.

## Packaging from the release `.deb`, not from source

The image downloads the official signed `.deb` from the public feed and
extracts `bma-daemon`. The `.deb` is the official distribution artifact —
signed, checksummed, published for both x86_64 and aarch64 — so anyone can
rebuild this image from public artifacts alone. Corollary: this repo never
contains agent code or patches. If Umbrel support needs the agent to behave
differently (e.g. the local status API mentioned below), that change is made
in the agent product and reaches this package through the next release.

## The daemon binary is statically linked → Alpine base

`bma-daemon` ships statically linked — zero runtime library dependencies.
The base image exists solely to run `webui.py`, so Alpine + python3 (~83 MB
total) replaced Debian slim (156 MB). Don't add glibc-dependent tooling to the
image; if something needs glibc, check whether it's actually needed at all.

## Hand-rolled stdlib-only web UI (`webui.py`)

Upstream is headless; the Umbrel App Store requires browser-first setup (no
SSH/CLI for normal use). A single ~300-line Python file with no framework keeps
the image small, the attack surface minimal, and the dependency count at zero.
Deliberately not a JS app, not Flask, not a separate UI container.

## Agent stats come from parsing the daemon log (known hack)

The daemon exposes no local status API. Miner count, telemetry activity, and
error surfacing (UI + home-screen widget) are regex-parsed from the tail of
`/var/log/bma.log` (`N miners polled`, `batch sent items=`, `WARN/ERROR`
lines). This is the most brittle part of the package: a daemon logging change
silently degrades stats to "—" (the app otherwise keeps working). The right fix
is a local status endpoint in the daemon itself — requested upstream. Replace
the parsing the moment that exists.

## Daemon log is a real file mirrored to stdout

The daemon writes `/var/log/bma.log` (hardcoded path). Earlier versions
symlinked it to stdout; now it's a real file so the web UI can parse it, with
`tail -F` mirroring to `docker logs` and a 50 MB truncation cap in the
entrypoint loop (no logrotate in the container). The file is pre-created in the
image owned by uid 1000 because `/var/log` itself isn't writable for an
unprivileged user.

## Entrypoint is a supervisor loop, not just `exec`

Saving credentials must restart the daemon without restarting the container
(container restart would drop the web UI mid-interaction and needlessly bounce
app_proxy's target). Docker can't watch files, so the entrypoint polls the
config mtime every 5 s, (re)starts `bma-daemon`, and restarts it on change.
`init: true` in compose provides PID 1 signal handling above it.

## Credentials: plaintext YAML, mode 0600, included in backups

`/data/daemon.yaml` mirrors the stock `.deb`'s `/etc/braiins-manager-agent/`
config — same two keys, same trust model. Umbrel offers no app secret store.
Mode 0600, owned by uid 1000. Deliberately NOT in `backupIgnore`: restoring a
backup should bring the agent back paired. Known accepted risk (pre-GA review
item): `web:8080` is unauthenticated on Umbrel's shared Docker network —
app_proxy auth only covers the browser path — so a malicious co-installed app
could replace the credentials. Mitigation candidates: require the current
Secret key to overwrite an existing config.

## Port 4547

Umbrel's manifest `port` is a static host-port claim with no conflict
detection or fallback anywhere in the platform — installs simply fail if it's
taken. 4547 was chosen as unclaimed across the official store (checked
2026-07-23; official review re-checks on submission). `APP_PORT: 8080` in
compose is the *container-internal* port and can never conflict. Don't change
4547 after release; users' bookmarks and the app URL depend on it.

## Icon and gallery are committed files + https URLs (community store only)

The dashboard CSP is `img-src * blob:` — in CSP, `*` excludes `data:` URIs, so
inline icons silently fall back to a placeholder. Community stores serve assets
from the repo via raw.githubusercontent URLs. For an official-store submission
these files and the manifest `icon`/`gallery` entries are REMOVED — Umbrel's
team creates and hosts official assets; screenshots go in the PR body (their
linter warns about committed assets for exactly this reason).

## `manifestVersion: 1.1`

Widgets are app-framework behavior introduced after 1.0. Every official
widget-bearing app declares ≥ 1.1; declaring 1 would let older umbrelOS
versions install the app with a broken widget. Bump further only when adopting
newer framework features.

## Widget icons are Tabler slugs, not our own SVGs

umbrelOS renders the home-screen widget itself; the app only returns JSON. A
`three-stats` item is `{icon, text, subtext}` — there is **no `title` field**
(an earlier version set `title`, which silently never rendered, and showed no
icons). `icon` is a [Tabler Icons](https://tabler.io/icons) slug: umbrelOS
copies `@tabler/icons` (pinned **2.39.0**) to `/generated-tabler-icons/<slug>.svg`
and serves that, plus four built-in `system-widget-*` icons. An unknown slug
renders as an empty placeholder box, so a slug must exist *in 2.39.0* — not just
in the current Tabler set. We use `plug-connected` / `cpu` / `cloud-upload`;
verify replacements against 2.39.0 before changing them. `text` is the
emphasized value, `subtext` the muted caption below it.

## Home-screen widget instead of Docker HEALTHCHECK

umbreld ignores Docker health status entirely (app state is lifecycle-driven —
verified in umbreld source). The `widgets:` mechanism is the only *native*
live-status surface: umbrelOS fetches `web:8080/widgets/status` server-side
(no auth, no app_proxy) and renders it on the home screen. A HEALTHCHECK would
only improve `docker ps` output for support; add it if support workflows want
it, but don't expect the dashboard to react.

## Image tag = upstream agent version; wrapper releases pick a mode

Official rule: manifest `version` is the upstream version users recognize —
the earlier `4.10.0-build-N` scheme was retired. Compose always pins the
**multi-arch index digest** (`tag@sha256:…`), so installs are reproducible
regardless of tag mutation.

The Update badge is the constraint that shapes wrapper-only releases (a repo
change with no new agent): umbrelOS computes "update available" in the frontend
by comparing the installed manifest `version` against the store's — the
`apps.list` route exposes only `version`, with no content/digest comparison
(the git-commit check in `app-repository.ts` only decides when to re-pull the
*store*, not per-app updates). So a wrapper change that keeps `version` gets
**no** badge for existing installs. `wrapper-release.yml` therefore offers two
modes: re-push the same tag with a new digest (reproducible, but new installs
only), or bump `version` — using a pre-release suffix on the next patch
(`4.11.1-1`) that sorts above the current agent version yet below a real
upstream `4.11.1`, so upstream always wins when it ships. Don't fake a whole
upstream version (e.g. jumping to `4.12.0`); it would collide when that agent
actually releases.

## `--provenance=false` on buildx

BuildKit attaches provenance attestation manifests by default; ghcr's UI lists
them as a confusing `unknown/unknown` architecture. We disable them for a
legible package page. If supply-chain attestation ever becomes a requirement,
re-enable deliberately and document the ghcr UI artifact.

## No agent self-update inside the container

The agent's built-in update feed can't apply inside a
container (no dpkg/systemd). Updates flow exclusively through Umbrel app
updates: new image + version bump → users get the Update badge, `/data`
persists. This is the correct model, not a limitation to fix.

## No hooks

Nothing here needs lifecycle scripts: installs get `data/` from the committed
package scaffolding (owned by uid 1000 via umbreld), config is rendered by the
web UI at runtime, and there are no migrations. Add hooks only for real
existing-install migrations (see the official `umbrel-package-app` skill for
hook semantics), not for scaffolding compose/templates can express.
