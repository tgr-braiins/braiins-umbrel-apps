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

## [26.09] - 2026-09-19

Braiins **Toolbox 26.09** adds automatic refreshing of the device list, alongside device-list and command-line fixes.

## New
- **Auto Refresh**: a toggle on the Device List header keeps already-discovered devices up to date on its own. Pick the interval — 30 seconds to 1 hour — in Device Management > IP Address Ranges > Advanced Options. It re-runs the same work as "Refresh Data" (it re-probes known devices, it does not rescan the network), and the countdown to the next refresh starts only once the current one has finished, so refreshes never overlap or stack.

## Improvements
- CLI: `update`, `advanced set` and `advanced reset` now accept `-y`/`--yes` to skip the confirmation prompt.

## Bug fixes
- Fixed the device list showing a device count but no rows when a search or a filter narrowed the results while you were on a later page.
- Fixed Toolbox hanging at 100% CPU when a confirmation prompt appeared without an interactive terminal — for example when run from a script or with stdin redirected. It now prints a clear message and aborts; use `-y`/`--yes` to run it non-interactively.

## [26.06] - 2026-07-30

Initial Umbrel release, packaging Braiins Toolbox 26.06.
