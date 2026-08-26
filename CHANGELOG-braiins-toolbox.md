# Changelog — Braiins Toolbox

Notable changes to the **Braiins Toolbox** Umbrel app, newest first. The
version number is the upstream Toolbox version (`version` in
`braiins-toolbox/umbrel-app.yml`), optionally with a `-N` wrapper
revision suffix for packaging-only releases (e.g. `26.06-1`).

Each upstream entry is the release description from the public release feed —
the same text shown as the in-app update notes. This file keeps the history,
since the manifest's `releaseNotes` only ever holds the current version and is
overwritten on each bump. Entries are added automatically by
`.github/scripts/bump.py` via the [`toolbox-update`](.github/workflows/toolbox-update.yml)
and [`toolbox-wrapper-release`](.github/workflows/toolbox-wrapper-release.yml)
workflows.

<!-- new entries are inserted directly below this line -->

## [26.08] - 2026-08-26

Braiins **Toolbox Release 26.08** brings a reworked Toolbox architecture for a more efficient GUI, native Apple Silicon support, and expanded miner compatibility.

## GUI
- **Faster, smarter device list**: filtering, sorting, and search now run on the backend and stream live, so working with more devices will be more responsive.
- Scans can now be cancelled mid-way and no longer collide with an action you're already running on the same devices.
- Toolbox now reliably runs as a single window by default — opening a second window shows a notice instead of starting a duplicate instance.

## CLI & GUI
- **New miner support**: Auradine Teraflux and MARA FW (Kaonsu) miners can now be discovered, monitored, and controlled.
- **Native Apple Silicon build**: Toolbox for macOS is now a native Apple Silicon (M-series) app.
- **Fixed**: a valid custom BOS+ contract key could be wrongly rejected as invalid; Toolbox now checks and explains contract key problems before running the action.

## Under the hood
Toolbox's device list now runs on a rebuilt backend: instead of a perpetual background scan feeding the GUI, the server holds a persistent view of your fleet and streams filtered, sorted, paginated results live. This is what makes the device list responsive at scale and is the foundation several of the improvements above build on.

**IMPORTANT!** This release ends macOS x86_64 builds in favor of a native Apple Silicon (aarch64) build. If you're upgrading an existing macOS installation, Toolbox's in-app updater cannot detect this release for you — please download the new Apple Silicon build manually.

## [26.06] - 2026-07-30

Initial Umbrel release, packaging Braiins Toolbox 26.06.
