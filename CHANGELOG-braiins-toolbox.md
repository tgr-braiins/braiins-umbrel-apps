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

## [26.08.1] - 2026-09-05

Braiins Toolbox 26.08.1 is a bugfix release addressing a GUI crash when acting on a large number of selected miners, a CLI panic during narrow-range scans, and a macOS self-update failure.

## Bug fixes
- Fixed a GUI error ("Cannot convert [object Set] to a BigInt") when using "Select all" on a large, filtered device list, which blocked bulk actions like Advanced Settings, Run a Command, and installing Braiins OS.
- Fixed a CLI panic when scanning a narrow IP range.
- Fixed a macOS self-update failure ("Permission denied") that could leave the GUI stuck at 100% or fail in the CLI when the installed app was owned by another user (e.g. an MDM-deployed install). The updater now asks for admin privileges to replace the app if needed.

## [26.06] - 2026-07-30

Initial Umbrel release, packaging Braiins Toolbox 26.06.
