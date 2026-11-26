# Braiins Umbrel Apps

An [Umbrel](https://umbrel.com) community app store for [Braiins](https://braiins.com) apps.

| App | What it does | Umbrel port |
|---|---|---|
| **Braiins Manager Agent** | Connects the miners on your local network to [Braiins Manager](https://manager.braiins.com), Braiins' fleet-management platform. | 4547 |
| **Braiins Toolbox** | [Braiins Toolbox](https://braiins.com/toolbox) with its own web interface. Scan your network for ASIC miners, monitor them live, and manage pools, power modes and Braiins OS installs in batch. Fully local, no cloud account. | 4548 |
| **Bitcoin Data** | Difficulty statistics computed from your own Bitcoin node: current difficulty, the projected next adjustment, network hashrate, history charts and a table of all epochs with CSV/JSON export. No third-party APIs. | 4549 |
| **Braiins Proxy** | Point your miners at your Umbrel: Braiins Proxy connects them to Braiins Pool, or any pool, over encrypted Stratum V2, with backup pools and a web interface showing miners, hashrate and pool state. Miners connect on port 3333. | 4550 |

> **Status: preview.** The store is on a personal GitHub account until it
> moves to a Braiins-owned organization. The store URL and the image location
> will change before general availability.

## For Umbrel users

### Add the store

1. In umbrelOS, open **App Store**, then **⋯ → Community App Stores**.
2. Add `https://github.com/tgr-braiins/braiins-umbrel-apps`.
3. Open the **Braiins** store and install the apps you want.

### Braiins Manager Agent

1. In [Braiins Manager](https://manager.braiins.com), add an agent
   (**Devices → Agents → Add agent**) and copy its **Agent ID** and **Secret key**.
2. Open the app on your Umbrel and paste both values. The agent starts, finds
   the miners on your network and sends their data to Braiins Manager.
3. Optional: add the home-screen widget (right-click the app tile → **Widgets**)
   to see the miner count and telemetry status on the Umbrel desktop.

### Braiins Toolbox

Open the app and scan your miners' IP range from the **Device List**.
Monitoring only reads from the miners. Nothing on a miner changes until you
start an action.

### Bitcoin Data

Install the official **Bitcoin** app first; Bitcoin Data needs it. On first
start it reads one block header per difficulty epoch from your node, which
usually takes seconds. After that it keeps itself current. Two home-screen
widgets are available: a halving countdown and current difficulty stats.

### Braiins Proxy

Open the app and enter your Braiins Pool username under **Pools** (or set up
another pool), then save. Set the pool URL of your miners to
`stratum+tcp://<your-umbrel-ip>:3333`; the worker names stay the ones the
miners send. Ports 3333–3339 on the Umbrel are reserved for miners, so another
app that uses one of them (another pool or proxy) can't run next to it.

Braiins Proxy updates through the Umbrel App Store like the other apps. Its
`braiins-proxy update` command and the web interface password are for installs
outside Umbrel. If the web interface shows a new version before Umbrel does,
the store's daily check picks it up within a day; you can also turn the check
off in the proxy's settings.

### Updates

When a new version is released, Umbrel shows an **Update** badge on the app.
Updating keeps your settings: Agent credentials, Toolbox IP ranges and pool
presets, and the Bitcoin Data cache.

Toolbox, the Manager Agent and Braiins Proxy check for their own new versions
and may say so inside the app. That in-app update can't run on Umbrel: wait
for the Update badge and update the app from the Umbrel App Store. The store
checks for new Braiins releases daily, so the badge usually follows within a
day.

### Check that an image is the real one

Every image released from this repo carries a signed build attestation: a
record, signed through GitHub and Sigstore, of which repository, workflow and
commit built it. With the [GitHub CLI](https://cli.github.com), take the image
reference from the app's `docker-compose.yml` (the part after `image:`) and run:

```sh
gh attestation verify \
  oci://ghcr.io/tgr-braiins/braiins-manager-agent@sha256:<digest> \
  --repo tgr-braiins/braiins-umbrel-apps \
  --signer-workflow tgr-braiins/braiins-umbrel-apps/.github/workflows/_release.yml
```

`Verification succeeded!` means the image was built by this repo's release
workflow, and the output names the commit. An image from anywhere else fails.
The Braiins binaries inside are downloaded from `downloads.braiins.com` and
checked against the sha256 in the official release feed during the build.
Images released before November 2026 have no attestation.

---

## For maintainers

Read [DECISIONS.md](DECISIONS.md) before you change how an app is packaged.
It explains the choices that look odd at first sight.

### Repo layout

Every app has two folders and a changelog:

| Path | Content |
|---|---|
| `<app-id>/` | The Umbrel package: `umbrel-app.yml`, `docker-compose.yml`, `icon.svg`, `gallery/`, `data/.gitkeep` |
| `image-<app-id>/` | Source of the app's Docker image, published as `ghcr.io/tgr-braiins/<app-id>` |
| `CHANGELOG-<app-id>.md` | Release notes of all versions. The manifest's `releaseNotes` holds only the current one. |

Other files:

- `umbrel-app-store.yml`: the store manifest. The store id is `braiins`, so
  every app id must start with `braiins-` (see DECISIONS.md).
- `.github/scripts/bump.py`: checks release feeds, bumps versions, writes
  changelogs and pins image digests. All workflows use it.
- `.github/workflows/`: CI and the release workflows (see [CI](#ci) and
  [Releases](#releases)).
- `image-<app-id>/smoke.sh`: the smoke test of an app's image, run by CI and
  by every release.
- `tests/`: unit tests (standard library only).

### How each image is built

All images are multi-arch (`linux/amd64` and `linux/arm64`), based on Alpine,
run as uid 1000 and keep their state in `/data`.

- **Manager Agent** (`image-braiins-manager-agent/`). It downloads the official
  `.deb` from the public feed (`downloads.braiins.com/braiins-manager-agent/index.json`),
  checks its sha256 and extracts the static `bma-daemon` binary. The agent has
  no user interface, so two files are added:
  - `webui.py`: a stdlib-only Python page on :8080 where the user enters the
    Agent ID and Secret key (saved to `/data/daemon.yaml`, mode 0600). It also
    serves `/status` for the page and `/widgets/status` for the home-screen widget.
  - `entrypoint.sh`: starts the daemon once the credentials exist, restarts it
    when they change, and restarts the web page if it stops.
- **Toolbox** (`image-braiins-toolbox/`). It downloads the official tarball from
  `downloads.braiins.com/braiins-toolbox/index.json`, checks its sha256 and runs
  the binary as it is, with `--gui-listen-address 0.0.0.0:8888`. Toolbox
  serves its own web interface, so there is no extra code. `HOME=/data` keeps
  all of its state on the volume.
- **Bitcoin Data** (`image-braiins-bitcoin-data/`). There is no upstream binary.
  The whole app is `webui.py` (stdlib-only Python) plus the Braiins Sans fonts.
  It reads the node through the RPC settings the official Bitcoin app exports.
  Run it locally with `DEMO_MODE=1` to get synthetic data without a node.
- **Braiins Proxy** (`image-braiins-proxy/`). Like Toolbox: it downloads the
  official tarball from `downloads.braiins.com/braiins-proxy/index.json`,
  checks its sha256 and runs the binary as it is (`braiins-proxy run`), with
  its web interface on :8080. The configuration (`/data/config.toml`) and the
  hashrate history (`/data/state/braiins-proxy/history.db`) are on the volume.

### CI

`ci.yml` runs on every pull request and every push to `main`. Its `ci-ok`
check must pass before anything merges:

- **unit:** compiles the Python, runs `tests/`, checks the shell scripts, and
  lints the workflows with actionlint.
- **images:** builds each app's image and runs its `smoke.sh`, on amd64 and on
  a native arm64 runner, so the arm64 image is actually run.
- **umbrel-lint:** the official Umbrel linter with `--check-images`, run from
  getumbrel/umbrel-apps at a fixed commit (`UMBREL_APPS_REF`) so a new
  upstream rule can't break unrelated PRs. `.github/scripts/umbrel_lint.py`
  accepts the errors a community store has by design (the empty `submission`)
  and fails on any other error. Move `UMBREL_APPS_REF` forward on purpose, in
  its own PR.

On PRs that the release workflows open, GitHub holds CI until a maintainer
clicks **Approve workflows to run** on the PR. Only that run counts for the
required `ci-ok` check.

Dependabot (`.github/dependabot.yml`) proposes weekly updates of the GitHub
Actions, pinned by commit SHA, and of the Alpine base image, pinned by digest.
A base-image update reaches users only with the app's next release.

### Releases

Every release arrives as a pull request. `_release.yml`, shared by all seven
release workflows, does the work in three jobs with separate permissions:

1. **prepare** (read-only): `bump.py` reads the feed or the manual inputs and
   edits the files. Feed content is untrusted: versions and checksums are
   validated before they reach the Dockerfile.
2. **image** (can push packages): builds the image, runs `smoke.sh`, pushes the
   multi-arch image, then pulls the pushed digest back and runs `smoke.sh` on
   it. The digest comes from the build itself, so the PR pins exactly what was
   tested. Last, it signs a build provenance attestation for that digest
   (`actions/attest-build-provenance`) and verifies it with
   `gh attestation verify`, as a user would.
3. **pr** (can open PRs): pins the digest, opens the PR and closes superseded
   bump PRs. Approve the held CI run on the PR, then merge once `ci-ok` passes.

A maintainer reviews and merges the PR. Runs for one app never overlap
(`concurrency: release-<app-id>`), and the daily check does nothing while a
bump PR for the same version is open, so the image in a PR doesn't change
under its reviewer.

| App | New upstream version | Packaging-only change | Version scheme |
|---|---|---|---|
| Manager Agent | `agent-update.yml`, daily | `wrapper-release.yml`, manual | Upstream version. Packaging releases use the next patch with a suffix: `4.15.1-1` |
| Toolbox | `toolbox-update.yml`, daily | `toolbox-wrapper-release.yml`, manual | Upstream version. Packaging releases add a revision: `26.09-1` |
| Bitcoin Data | (no upstream) | `braiins-bitcoin-data-release.yml`, manual | Plain semver: `1.13.1` |
| Braiins Proxy | `braiins-proxy-update.yml`, daily | `braiins-proxy-release.yml`, manual | Upstream version. Packaging releases use the next patch with a suffix: `0.1.2-1` |

- **Daily checks.** If several upstream versions ship before a bump PR is
  merged, the newest PR closes the older ones. Its changelog has an entry for
  every skipped version, so merging only the newest PR loses nothing.
- **Manual releases.** Start them from the **Actions** tab. Give a `version` to
  bump the manifest: existing installs then get the Update badge. Leave it empty
  to only rebuild and re-pin the image at the same version: new installs get the
  new image, existing installs get no badge. DECISIONS.md explains why the
  version schemes differ.
- **After merging.** Umbrel devices pick the change up at the next store refresh.
- **If a released image is broken:** revert the PR and release again with a
  new version string (for example the next `-N`), pinned to the previous good
  digest. Reverting alone gives existing installs no Update badge. Never
  delete old image versions from GHCR: older installs still pin them.

#### Adding a new app

The first image has to come from the release workflow before the app reaches
`main`: CI's Umbrel linter checks that the pinned image exists, and the store
would list an app whose image can't be pulled. `workflow_dispatch` only works
for workflows that exist on `main`, so:

1. Land the app's manual release workflow (`<app>-release.yml`) on `main` on
   its own. It fails if started before step 4, and has no schedule.
2. On a branch, add everything else: the app folders, the changelog, the
   `bump.py` entry, the daily check workflow if there is a feed, and the app
   in `ci.yml` (images matrix and linter list) and `dependabot.yml`.
   `tests/test_dockerfiles.py` fails if one of these is missing. Put a
   placeholder digest in the compose file.
3. Start the release workflow on that branch with an empty version:
   `gh workflow run <app>-release.yml --ref <branch>`. It builds, tests,
   attests and pushes the image, and opens a PR into the branch that pins the
   digest. Merge that PR.
4. Open the branch's PR into `main`; CI now passes, including the linter.

To release by hand, do what the workflows do:

1. Bump the version and the per-arch checksums in `image-<app-id>/Dockerfile`.
2. Build and push the multi-arch image:
   ```bash
   docker buildx build --platform linux/amd64,linux/arm64 --provenance=false \
     --tag ghcr.io/tgr-braiins/<app-id>:<version> --push image-<app-id>/
   ```
3. Pin the index digest in `docker-compose.yml` as `<tag>@sha256:…`
   (`docker buildx imagetools inspect ghcr.io/tgr-braiins/<app-id>:<version>`).
4. Bump `version` and `releaseNotes` in `umbrel-app.yml`, add a changelog
   entry, and open a PR.

Push the image before you merge the version bump. Umbrel always pulls the
image from the registry on install or update, so a missing image makes the
install fail. The GHCR packages must stay public.

### Build and test locally

Unit tests cover `bump.py`, the agent setup page and Bitcoin Data. They use
only the Python standard library and need no Docker and no node:

```bash
python3 -m unittest discover -s tests
```

To try an image, for example the Manager Agent (the others work the same way):

```bash
cd image-braiins-manager-agent
docker build -t bma-dev .     # amd64; use buildx for arm64
docker run -d --name bma-dev --user 1000:1000 --init -p 18080:8080 -v bma-dev-data:/data bma-dev
curl http://localhost:18080/status   # {"configured": false, "running": false}
```

Open http://localhost:18080 and paste test credentials from Braiins Manager.
The status should change to "Agent running" within about 10 seconds. Saving
new credentials must restart the daemon. Clean up with
`docker rm -f bma-dev && docker volume rm bma-dev-data`.

To run the official Umbrel linter locally as CI does:

```bash
git clone https://github.com/getumbrel/umbrel-apps /tmp/umbrel-apps
cp -r <app-id> /tmp/umbrel-apps/
cd /tmp/umbrel-apps && npm ci
node .tools/lint-apps.mjs <app-id> --check-images
```

Expected in a community store: errors for the empty `submission` field and
warnings for the committed icon and gallery. The official store's own CI runs
a stricter mode (`--changed`) for new apps; see
[Path to the official App Store](#path-to-the-official-app-store).

### Test on a real Umbrel

Add this store on the device and install the app. To test a change without
waiting for the store refresh, SSH in (`ssh umbrel@umbrel.local`, with the
dashboard password) and run:

```bash
cd ~/umbrel/app-stores/<this-store-dir> && git pull
umbreld client apps.update.mutate --appId <app-id>
umbreld client apps.state.query --appId <app-id>
sudo docker logs <app-id>_web_1
```

To test an image that isn't on GHCR yet, build it on the Umbrel, push it to a
local registry (`registry:2` on `127.0.0.1:5000`) and point the compose file at
`localhost:5000/<app-id>:<tag>`. umbreld ignores locally built images and
`pull_policy`, but it pulls from a local registry. The store folder is
replaced at the next store refresh after a push to `main`, so edits made there
by hand don't last.

### Umbrel platform rules learned on real hardware

- The manifest `port` is a fixed host-port claim, with no conflict detection
  on the device and no fallback. 4547–4550 are taken by this store; never
  change a released app's port. (The official linter checks new ports against
  the official store.)
- Community-store app ids must start with the store id (`braiins-`), or the
  app never appears in the store, and nothing is logged.
- The Update badge appears when the manifest `version` string changes. Nothing
  else, such as a new image digest, triggers it.
- The dashboard blocks `data:` URIs, so `icon` must be an https URL.
- Bridge networking is enough. The agent and Toolbox find miners by TCP scans
  of IP ranges.
- umbreld ignores Docker health checks. Home-screen widgets are the only
  native place to show live status.

### Path to the official App Store

The plan is to submit the apps to
[getumbrel/umbrel-apps](https://github.com/getumbrel/umbrel-apps). Its
`AGENTS.md` and `.agents/skills/` (`umbrel-package-app`, `umbrel-update-app`,
`umbrel-test-app`, `umbrel-develop-app`) hold the official packaging rules;
read them before you change a package. Once an app is accepted, updates
become PRs to their repo, so the workflows will need a variant that targets it.

Their CI lints new apps with `--changed`, which is stricter than our CI. As
of November 2026 it also requires `releaseNotes: ""` for a new app and warns
about the manifest `icon` and `gallery`.

Infrastructure, once:

1. Move this repo and the GHCR images to a Braiins-owned organization (see the
   `# TODO` lines in the compose files) and update `REGISTRY_OWNER` in the
   workflows. Keep the packages public and digest-pinned.
2. Start releases from the Braiins release pipelines (`repository_dispatch`)
   instead of daily polling.

For each app's submission PR:

3. Remove the committed `icon.svg` and `gallery/`, drop the manifest `icon:`
   and set `gallery: []`. The logo and screenshots go in the PR body.
4. Set `submission:` to the PR URL and `releaseNotes: ""`.
5. Add `storage:` with `dataRoot: data` (the rules ask for it in new apps; it
   can be added later but never removed). Our data already lives under
   `${APP_DATA_DIR}/data`; test it on umbrelOS 2.0 first.
6. Use the plain upstream version (`26.09`, not `26.09-1`). The official
   scheme for packaging-only releases is `-patch.N` (`4.15.0-patch.1`), and
   every image change must bump the version: our "re-pin at the same version"
   mode goes away. `bump.py` needs that scheme before the first official update.
7. Check again that the app's port is still free in the official store.
8. Retire or rename the community listing when the official app is live. Both
   stores use the same app ids, so a user with both stores would get a conflict.
9. Collect the evidence the reviewers expect (see `umbrel-test-app`): a fresh
   install on stated hardware, including arm64, restart and persistence, the
   main workflow, and screenshots. CI runs the arm64 images, but a device
   test on arm64 is still missing.

Questions to expect in review:

10. **Closed-source binaries and licensing.** The agent and Toolbox images use
    official signed artifacts from the public feed, checked by sha256, and
    anyone can rebuild them from public files. State the redistribution terms
    in the PR body; this repo has no LICENSE yet.
11. **Web ports without login on the shared Docker network.** This is an
    accepted risk, described in DECISIONS.md. The real fixes are in the
    products: a login option in Toolbox and a guard against overwriting the
    agent's Secret key. Ideally they ship before submission.
