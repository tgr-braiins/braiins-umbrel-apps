# Design decisions

The reasons behind anything here that might otherwise earn a "wtf, why?".
Each entry says what we did, why, and when to revisit it.

The first part applies to every app in the store. The app-specific decisions
follow: [Manager Agent](#braiins-manager-agent),
[Toolbox](#braiins-toolbox), [Bitcoin Data](#bitcoin-data),
[Braiins Proxy](#braiins-proxy).

---

# All apps

## App ids start with `braiins-`

umbreld silently drops community-store apps whose id doesn't start with the
store id (`app-repository.ts`: `app.id.startsWith(meta.id)`; our store id is
`braiins`). The app is cloned onto the device but never appears in the store
UI, and nothing is logged. Bitcoin Data 1.0.0 first shipped as `bitcoin-data`
and hit exactly this. Display names are free ("Bitcoin Data").

## `ARG TARGETARCH` never gets a default

The agent and Toolbox Dockerfiles pick the release file for the platform from
`TARGETARCH`, which BuildKit sets for each platform of a multi-arch build.
Until November 2026 both declared `ARG TARGETARCH=amd64`, and that default won:
the published arm64 images contained the x86-64 binary and could not start on
an arm64 Umbrel. Nothing caught it because arm64 was built but never run. Now
CI runs every image on a native arm64 runner, and a unit test refuses a
default on any platform `ARG`.

## Official artifacts only, never source or patches

The agent and Toolbox images download the official signed release files from
the public feeds on `downloads.braiins.com` and check their sha256 in the
Dockerfile. Anyone can rebuild the images from public files alone, and this
repo never contains product code or patches. If Umbrel needs a product to
behave differently (for example a local status endpoint), the change is made
in the product and reaches this store with its next release.

## Static binaries → Alpine base

`bma-daemon` and the Toolbox binary are statically linked. The base image only
provides `ca-certificates` and, where a web page is ours, `python3`. Alpine
replaced Debian slim (the agent image went from 156 MB to about 83 MB). Don't
add glibc-dependent tools; if something needs glibc, check first whether it is
needed at all.

## Stdlib-only Python for our own web pages

Where we write the web page ourselves (the agent setup page, all of Bitcoin
Data), it is one Python file with no framework and no dependencies, served by
the app container. This keeps images small and the attack surface minimal.
Deliberately not a JS app, not Flask, not a separate UI container.

## Fixed ports 4547–4550

Umbrel's manifest `port` is a fixed host-port claim, with no conflict detection
or fallback anywhere in the platform: an install simply fails if the port is
taken. The store uses a block: 4547 agent, 4548 Toolbox, 4549 Bitcoin Data,
4550 Braiins Proxy,
each checked free in the official store when it was claimed. The compose
`APP_PORT` is the container-internal port and can never conflict. **Never
change a released app's port**: users' bookmarks and the app URL depend on it.
The official linter now checks a new app's ports against the whole official
store.

## Images: multi-arch, public, pinned by index digest

Images are built for `linux/amd64` and `linux/arm64` and pushed to public GHCR
packages, because umbreld pulls anonymously and always pulls on install and
update (`pull_policy` is ignored). Compose pins the multi-arch index digest
(`tag@sha256:…`), so installs are reproducible even if a tag is overwritten.
The release workflow takes that digest from the build's own metadata, after
the pushed image has passed the smoke test, never by looking the tag up later.

The Alpine base image is pinned by digest too, and Dependabot proposes
updates. Without the pin, a rebuild of the same version could silently pick up
a different base.

## Provenance: GitHub attestations, not BuildKit's

Users need a way to tell our images from a copy that claims to be Braiins.
The release workflow signs a SLSA build provenance attestation for each
pushed index digest with `actions/attest-build-provenance` (keyless, through
GitHub's OIDC token and Sigstore), then verifies it with
`gh attestation verify`. The README shows users the same command.

The attestation lives in GitHub's attestation store (`push-to-registry:
false`). `gh attestation verify oci://…` finds it by digest, so nothing extra
is pushed to GHCR.

BuildKit's own provenance stays off (`--provenance=false`): it adds manifests
to the image index that the GHCR UI lists as an `unknown/unknown`
architecture, and nothing checks them.

What this proves: the image was built by `_release.yml` in this repo from a
named commit. It does not prove that the repo belongs to Braiins. Today it is
under a personal account, so the trust anchor is only as strong as that
account. Moving the repo and packages to the Braiins GitHub org, with a
verified domain and the store URL published on braiins.com, fixes that. The
attestation's `--repo` and `--signer-workflow` then change to the org path.

## The Update badge only looks at the version string

umbrelOS shows the Update badge when the store's manifest `version` differs
from the installed one (`ui/src/hooks/use-apps-with-updates.ts`:
`availableApp.version !== app.version`). There is no ordering, no semver and
no digest or content comparison; the git-commit check in `app-repository.ts`
only decides when to re-pull the *store*. Consequences:

- A packaging change that keeps `version` reaches new installs only. That is
  why every manual release workflow has two modes: bump the version (existing
  installs get the badge) or re-pin the digest at the same version.
- Version ordering matters only inside our own `bump.py`, when the daily check
  asks whether the feed is newer than the manifest. The per-app schemes below
  exist to keep that answer right.
- These schemes are for this community store. The official store uses
  `-patch.N` for packaging-only releases and requires a version bump for every
  image change (README, "Path to the official App Store").

## Updates only through Umbrel, never self-update

The agent's and Toolbox's own update mechanisms can't apply inside the
container (no dpkg or systemd; the Toolbox binary is owned by root while the
process runs as uid 1000). This is by design: updates come only as Umbrel app
updates with a new image and version, and `/data` persists across them.

## Icon and gallery are committed files with https URLs (community store only)

The dashboard CSP is `img-src * blob:`, and in CSP `*` excludes `data:` URIs,
so inline icons silently fall back to a placeholder. A community store serves
its assets from the repo through raw.githubusercontent URLs. For an
official-store submission these files and the manifest `icon`/`gallery`
entries are removed: Umbrel's team creates and hosts official assets, and
screenshots go in the PR body. Their linter warns about committed assets for
this reason.

## Widgets, not Docker health checks

umbreld ignores Docker health status; app state follows the app lifecycle
(checked in the umbreld source). The `widgets:` mechanism is the only native
place to show live status: umbrelOS fetches `web:8080/widgets/<id>` itself
(no auth, no app_proxy) and renders the JSON on the home screen. A
HEALTHCHECK would only improve `docker ps` output for support.

Widget icons are [Tabler Icons](https://tabler.io/icons) slugs, not our own
SVGs. umbrelOS copies `@tabler/icons` **2.39.0** to
`/generated-tabler-icons/<slug>.svg`, plus four built-in `system-widget-*`
icons. An unknown slug renders as an empty box, so a slug must exist *in
2.39.0*, not just in today's Tabler set. A `three-stats` item is
`{icon, text, subtext}`: there is **no `title` field** (an early agent version
set `title`, which never rendered). `text` is the emphasized value, `subtext`
the muted caption.

Widget-bearing apps declare `manifestVersion: 1.1`, the version that
introduced widgets; with `1`, older umbrelOS versions would install the app
with a broken widget. Toolbox has no widget and stays on `1`.

## No hooks

Nothing here needs lifecycle scripts. Installs get `data/` from the committed
package (owned by uid 1000 via umbreld), configuration is written by the app at
run time, and there are no migrations. Add hooks only for real migrations of
existing installs (see the official `umbrel-package-app` skill for hook
semantics), not for anything compose or the package can express.

## Web pages without login on the shared Docker network (accepted risk)

Each app's `web` port has no login of its own. app_proxy protects only the
browser path, so a malicious app installed on the same Umbrel could call the
ports directly. Umbrel's threat model treats apps on one device as mutually
trusted, so this is accepted today. The risk differs per app (see below); the
right fixes are in the products. Don't "fix" it here by publishing raw ports
or turning off app_proxy auth: both only widen exposure.

## Styling: Braiins CDS v11 tokens, fonts served locally

Our own pages use Braiins CDS v11 (IBM Carbon v11) color, type and spacing
token values, written inline in `webui.py`; there is no CSS framework.
Semantic tokens switch between the White and Gray 90 themes with
`prefers-color-scheme`. The primary action and focus ring are violet-60
(brand), a deliberate override of the token file's blue-60; links keep the CDS
blue link tokens. Braiins Sans (regular and bold WOFF2, converted from the
public visualbook OTFs at design.braiins.com/braiins/typography) is committed
in each image folder's `fonts/`, baked into the image and served by `webui.py`
itself. An Umbrel box must not depend on a Braiins web host to render a local
page, and calling an external CDN from a self-hosted node is exactly what
Umbrel users install apps to avoid. Revisit if the visualbook adds a semibold
(Carbon's 600-weight styles currently resolve to bold) or the token values
change.

---

# Braiins Manager Agent

## Our setup page in front of a headless daemon

The agent has no user interface, and the Umbrel App Store requires setup in
the browser (no SSH or CLI for normal use). `webui.py` provides the page where
the user enters the Agent ID and Secret key.

## Stats come from parsing the daemon log (known hack)

The daemon has no local status API. The miner count, telemetry activity and
errors shown on the page and in the widget are parsed with regexes from the
tail of `/var/log/bma.log` (`N miners polled`, `batch sent items=`,
`WARN`/`ERROR` lines). This is the most brittle part of the package: a change
to the daemon's log lines silently turns the stats into "—", while the agent
itself keeps working. The right fix is a local status endpoint in the daemon,
which has been requested. Replace the parsing as soon as it exists.

## The daemon log is a real file mirrored to stdout

The daemon writes `/var/log/bma.log` (the path is hardcoded). It is a real
file so the page can parse it; `tail -F` mirrors it to `docker logs`, and the
entrypoint loop truncates it at 50 MB as a last resort. The daemon also
rotates the log itself (it embeds the `file-rotate` crate), which renames
files next to the log, so the image makes both the file and `/var/log` owned
by uid 1000. Without the directory, rotation would fail once the daemon's size
limit is reached.

## The entrypoint is a supervisor loop, not just `exec`

Saving credentials must restart the daemon without restarting the container:
a container restart would drop the page mid-interaction and bounce
app_proxy's target. Docker can't watch files, so the entrypoint checks the
config's modification time every 5 s and starts or restarts `bma-daemon` when
it changes. It also restarts the setup page if that stops. `init: true` in
compose handles signals as PID 1 above it.

On `docker stop`, the script signals the daemon and **waits** for it before
exiting. Under `init: true` this shell is the container's main process: once
it exits, everything left in the container is killed at once, which used to
cut the daemon's shutdown short. The loop sleeps with `sleep 5 & wait`, so the
signal is handled immediately rather than after the sleep.

## Credentials: plaintext YAML, mode 0600, included in backups

`/data/daemon.yaml` mirrors the stock `.deb`'s `/etc/braiins-manager-agent/`
config: the same two keys and the same trust model. Umbrel has no secret store
for apps. The file is mode 0600, owned by uid 1000, and written atomically.
It is deliberately NOT in `backupIgnore`: restoring a backup should bring the
agent back paired.

Whoever sets these credentials decides whose Braiins Manager the miners
report to, so a save is accepted only from the setup page itself. The page's
`fetch` sends an `X-BMA-Setup: 1` header: a plain HTML form can't send a custom
header, and a script on another site would need a CORS preflight, which the
server never grants. Requests whose `Sec-Fetch-Site` is not `same-origin` are
refused too; that includes another Umbrel app's page on a different port. The
check doesn't depend on `Host` or `Origin`, which app_proxy may rewrite.
Remaining risk: another *container* on the shared network can still call
`web:8080` directly (see "Web pages without login" above). Candidate fix:
require the current Secret key to overwrite an existing config.

## Version scheme: upstream version; packaging releases use the next patch

The manifest `version` is the upstream agent version users recognize (an
earlier `4.10.0-build-N` scheme was retired). A packaging-only release with a
badge uses a pre-release suffix on the *next* patch: `4.15.1-1` while shipping
agent 4.15.0. In `bump.py` that sorts above 4.15.0 and below a real upstream
4.15.1, so upstream always wins when it ships. Never jump to a whole upstream
version (for example `4.16.0`): it would collide when that agent is released.

## The page shows two versions

The footer shows both the **Umbrel app version** and the **agent daemon
version**, because a packaging release changes the first and not the second
(`4.14.1-1` packaged daemon `4.14.0`). The daemon version comes from
`bma-daemon --version`. The app version can't be read in the container, which
has no access to the store manifest, so compose passes it as `APP_VERSION`;
`bump.py pin` keeps it equal to the manifest `version` on every release.
Without `APP_VERSION` (plain `docker run`, local development, CI smoke test)
the footer leaves it out.

---

# Braiins Toolbox

## The official binary as it is, no extra code

Toolbox's web interface is its own embedded web server
(`--gui-listen-address`), so the container runs the binary directly: no
entrypoint script, no web page of ours. The tarball comes from the public feed
(`downloads.braiins.com/braiins-toolbox/index.json`).

## `HOME=/data` instead of per-path flags

All Toolbox state (web interface config with saved IP ranges, pool presets,
logs, install UUID) follows the XDG defaults under `$HOME`. Setting
`HOME=/data` keeps all of it on one volume and stays correct if a new Toolbox
version adds state files. Listing `--gui-config-path`,
`--pool-presets-file-path` and `--logfile-path` would miss a new one some day.
Logs are kept out of Umbrel backups with `backupIgnore`
(`data/.local/share/braiins-toolbox/logs/*`).

## Port 4548, not Toolbox's native 8888

8888 is already taken in the official store.

## No home-screen widget (yet)

Toolbox's internal HTTP API is undocumented and unversioned, and it has no
local status endpoint. A widget scraping undocumented endpoints would break
silently on any Toolbox release. Add one when Toolbox exposes a stable status
endpoint.

## Biggest risk on the shared network

`web:8888` has no login, and Toolbox can reconfigure and reflash every miner on
the LAN, so a malicious app on the same Umbrel could do a lot of damage. A
login option in the Toolbox product is the right fix.

## Miner passwords

Toolbox asks for non-default miner passwords in the web interface per session
or action (`-p` on the CLI) and doesn't store them in the current setup.
Nothing password-like lands in `/data`. If a future version adds a credential
store, revisit backups and file permissions then.

## Version scheme: packaging releases are revisions (`26.09-1`)

The agent's scheme fakes a pre-release of the next patch. Toolbox can't: it
uses CalVer and *does* publish real patch releases (`23.04.1`), so a made-up
`26.09.1-1` would read as an upstream hotfix that doesn't exist. Toolbox
packaging releases are Debian-style revisions of the version actually
shipped: `26.09-1`, `26.09-2`.

In `bump.py` a Toolbox suffix sorts *above* its plain version and *below* the
next upstream one (`26.06 < 26.06-1 < 26.06.1 < 26.07`), the opposite of the
agent rule. Without that, after a `26.06-1` release the daily check would see
plain `26.06` as newer and open a downgrade PR.

---

# Bitcoin Data

## No upstream: the repo is the app

Bitcoin Data packages no Braiins binary. The whole app is
`image-braiins-bitcoin-data/webui.py` plus the Braiins Sans fonts. So there is
no release feed (`feed: None` in `bump.py`) and no daily update workflow.
Releases are started by hand with `braiins-bitcoin-data-release.yml`, and
versions are plain semver (`1.13.1`) with no suffix scheme, since every
release is a packaging release.

## All data comes from the user's own node

The manifest has `dependencies: [bitcoin]`, and compose passes the official
Bitcoin app's exported `APP_BITCOIN_NODE_IP`, `APP_BITCOIN_RPC_PORT`,
`APP_BITCOIN_RPC_USER` and `APP_BITCOIN_RPC_PASS` (the same contract mempool
uses). No third-party API is ever called: the point of the app is that the
numbers come from a chain the user verified. When the node is unreachable or
still syncing, the page shows a labeled waiting or syncing state instead of
falling back to a public API.

## Errors keep the loop alive, and stale data is shown as an error

The background loop that polls the node catches every error, so one broken
reply can't stop it for good. The page reports an error after 5 minutes
without a successful poll instead of keeping "ok" next to old data. If the
node is on a shorter chain than the cache (for example after a reindex), the
epochs above the node's tip are dropped.

The cache is treated the same way. A cache that is malformed in any way (a
hand edit, a format from another version) gives an empty start instead of
killing the sync, and a failed save (a full disk, a read-only `/data`) is
only logged, so live updates go on. The HTTP handler drops a client that
stays idle for 15 s, because any container on the network can open
connections.

## The first blocks of an epoch use the previous epoch

Until 10 blocks into an epoch (`MIN_SAMPLE`), its own average says almost
nothing: one block 5 s after the retarget meant a hundred times the real
hashrate and a halving "in 7 days", and block times may even run backwards.
So for those blocks the page shows the previous epoch's hashrate, uses the
600 s target for the retarget and halving dates, and shows no projected
change.

## Fees are read newest block first

Fees come from `getblockstats` for the current epoch only. A pruned node
(the default keeps about 288 blocks) has deleted the oldest blocks of the
epoch, and the loop stops at the first block it can't read. Reading from the
tip down means it stops below the blocks the node still has, and the fee
average uses those.

## One header per epoch, JSON cache, no database

Difficulty only changes at 2016-block retarget boundaries, so the full history
is one `getblockheader` per boundary (about 460 in 2026). Against a local node
that takes seconds. The result is cached in `/data/epochs.json` (atomic write
and rename, saved every 100 headers so a restart resumes). The cache can
always be rebuilt from the node, so a plain JSON file is enough; losing
`/data` costs one re-read. A 30 s tip poll adds a boundary at each retarget.
The projected next adjustment uses the current epoch's average block interval,
clamped to the consensus ×4/÷4 bounds. Consensus compares the time of the
epoch's 2015 intervals (first to last block) with 2016 target intervals, so
the projection includes the factor 2016/2015: an epoch at exactly 600 s per
block raises difficulty by 0.05%.

## Chart colors are validated, not eyeballed

Difficulty going up is neither good nor bad, so the adjustment chart doesn't
use the CDS semantic green and red. It uses a warm/cool diverging pair: Carbon
blue for increases and Carbon orange for decreases, blue-60/orange-60 in light
mode and blue-50/orange-50 in dark mode (the 40 steps fall outside the dark
mode lightness band). Both pairs pass colorblind-safety checks (CVD ΔE ≥ 8
under protanopia and deuteranopia simulation, contrast ≥ 3:1 against both
surfaces).

## The name is "Bitcoin Data", not "Difficulty"

The page has a navigation slot for more data modules later; difficulty is
only the first.

---

# Braiins Proxy

## The public release, not the Farm Proxy release candidates

Braiins Proxy (called "braiins sv2 proxy" internally, and Farm Proxy in its
own log lines) is packaged from the public feed
(`downloads.braiins.com/braiins-proxy/index.json`), the same way as Toolbox.
Earlier tests on a home Umbrel used internal 26.11 release candidates that
were never published; their app id was `braiins-farm-proxy`. The public
product has its own version line (0.1.1 was the first), so the app id is
`braiins-proxy` and nothing from the test packages carries over.

## The official binary as it is

The binary serves its own web interface and API, so the container runs
`braiins-proxy run` with no entrypoint script. `CONFIG_FILE`, `HOME` and
`XDG_STATE_HOME` put the configuration and the history database on `/data`;
the history is left out of Umbrel backups (`backupIgnore: data/state/*`), the
configuration is kept. A missing configuration is not an error: the proxy
starts with a Braiins Pool configuration that the web interface then edits.

## No web interface password, and its update command is not used

The proxy asks for a password only when a `gui-password` file exists, which
`braiins-proxy install` creates. In the container there is none, so the web
interface is open to whatever reaches it: Umbrel's app proxy, behind Umbrel's
login, and other containers on the shared Docker network (see "Host
network"). That is the same accepted risk as the other apps ("Web pages without
login on the shared Docker network").

`braiins-proxy update` replaces the binary of a systemd service and is not
used here; updates come from this store like every other app ("Updates only
through Umbrel, never self-update"). The proxy's own release check is on by
default and shows a message with `sudo braiins-proxy update`. It is left on:
it tells the user a new version exists, and the store's daily check follows
within a day. If that message causes confusion, ask the proxy team for a way
to turn it off from the environment, so we don't have to write a configuration
file the proxy considers its own.

## Host network, so the proxy shows miners a reachable address

The web interface tells the user which address to set on the miners. The
proxy builds it from its own network interfaces (the address of the default
route first) and has no setting or environment variable to override it
(0.1.1). In a bridge container the only interface is Docker's, so the first
package showed `stratum2+tcp://10.21.0.2:3333/...`, which no miner can reach.

The proxy container therefore runs with `network_mode: host`. It then finds
the Umbrel's LAN address, leaves out the Docker bridges, and offers a choice
when there are several (LAN and Tailscale, for example). Alternatives that
were rejected:

- A name: Umbrel gives apps `umbrel.local`, but miner firmware has no mDNS
  resolver (tested with Braiins OS on a BMM 100; BOS-4004 only makes the
  miner announce its own name).
- The address the browser used: it is the Tailscale address when the user
  comes in over the VPN, which is why the proxy's interface ignores it.
- A helper container that finds the address and passes it in: the proxy has
  no input for it. If it gets one, a bridge container with published ports
  becomes possible again.

Consequences:

- **The web interface listens on `10.21.0.1:24550` only.** That is the host's
  address on Umbrel's Docker network (`GATEWAY_IP` in umbrelOS, fixed
  together with the `10.21.0.0/16` subnet that official apps hardcode). It is
  not on the LAN, where it would be open without a login. Other containers
  can reach it, as before. The port is an arbitrary free one below the
  ephemeral range. The literal address is used rather than `$GATEWAY_IP`
  because older umbrelOS versions don't export that variable.
- **A second container, `web`, relays the app proxy to that address.** The
  official linter requires `APP_HOST` to be a service of the compose file
  (`app_proxy.host`), so the app proxy can't point at `10.21.0.1` directly.
  `web` runs `socat` from the same image on the bridge network and forwards
  port 8080 to `10.21.0.1:24550`, the way the official Formicaio app does.
  Only the web interface goes through it; miners connect to the proxy on the
  host directly. If the proxy gets an address setting, drop the relay and
  the host network together.
- **The mining port is whatever the web interface sets** (3333 by default),
  bound directly on the host. Nothing is published by Docker, so no range is
  reserved. Another program on that port stops the proxy from listening.
- **The proxy shares the host's network namespace.** It runs the official
  binary as uid 1000 without extra capabilities, but it can reach services
  that listen only on the host's loopback. Home Assistant, Plex and Pi-hole
  in the official store run the same way.

## Version scheme: like the agent

Upstream uses semver. A packaging release of ours is a pre-release of the next
patch (`0.1.2-1`), so the next upstream version (`0.1.2`) still sorts above
it and the daily check picks it up.

## The first release notes are ours

The feed's description of 0.1.1 describes the standalone Linux install: a
one-command install, a self-updating service and a password-protected web
interface. None of that applies on Umbrel, so the first package has its own
notes. Later versions take the feed description, like Toolbox.

## The built-in certificate renews itself

The proxy identifies itself to Braiins' license service
(`license.braiins.com`) with a client certificate built into the binary. At
start it logs "Certificate is valid for N days" (36 days for 0.1.1 on
2026-11-25, i.e. until 1 January 2027), and `/metrics` exports
`remaining_certificate_validity_days` and `cert_refresh_total`. The
certificate is renewed automatically while the proxy runs (confirmed November 2026),
so the date in the log is not an end of life and needs no
release from us. If the proxy ever logs "Certificate expired", check that it
can reach Braiins (outbound HTTPS) before anything else.
