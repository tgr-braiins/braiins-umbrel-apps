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

## [26.09-1] - 2026-11-23

Maintenance release of the Umbrel package (Braiins Toolbox 26.09 unchanged): rebuilt on Alpine 3.24.

## [26.09] - 2026-09-14

Braiins **Toolbox 26.09** adds automatic refreshing of the device list, alongside device-list and command-line fixes.

## New
- **Auto Refresh**: a toggle on the Device List header keeps already-discovered devices up to date on its own. Pick the interval — 30 seconds to 1 hour — in Device Management > IP Address Ranges > Advanced Options. It re-runs the same work as "Refresh Data" (it re-probes known devices, it does not rescan the network), and the countdown to the next refresh starts only once the current one has finished, so refreshes never overlap or stack.

## Improvements
- CLI: `update`, `advanced set` and `advanced reset` now accept `-y`/`--yes` to skip the confirmation prompt.

## Bug fixes
- Fixed the device list showing a device count but no rows when a search or a filter narrowed the results while you were on a later page.
- Fixed Toolbox hanging at 100% CPU when a confirmation prompt appeared without an interactive terminal — for example when run from a script or with stdin redirected. It now prints a clear message and aborts; use `-y`/`--yes` to run it non-interactively.

## [26.08.1] - 2026-08-26

Braiins Toolbox 26.08.1 is a bugfix release addressing a GUI crash when acting on a large number of selected miners, a CLI panic during narrow-range scans, and a macOS self-update failure.

## Bug fixes
- Fixed a GUI error ("Cannot convert [object Set] to a BigInt") when using "Select all" on a large, filtered device list, which blocked bulk actions like Advanced Settings, Run a Command, and installing Braiins OS.
- Fixed a CLI panic when scanning a narrow IP range.
- Fixed a macOS self-update failure ("Permission denied") that could leave the GUI stuck at 100% or fail in the CLI when the installed app was owned by another user (e.g. an MDM-deployed install). The updater now asks for admin privileges to replace the app if needed.

## [26.08] - 2026-08-20

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
