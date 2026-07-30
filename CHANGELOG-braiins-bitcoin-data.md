# Changelog — Bitcoin Data

Notable changes to the **Bitcoin Data** Umbrel app, newest first. Unlike the
other apps in this store, Bitcoin Data has no upstream binary — the app is this
repo's `image-braiins-bitcoin-data/webui.py` — so the version is plain semver
(`version` in `braiins-bitcoin-data/umbrel-app.yml`), bumped by the
[`braiins-bitcoin-data-release`](.github/workflows/braiins-bitcoin-data-release.yml) workflow.

<!-- new entries are inserted directly below this line -->

## [1.1.0] - 2026-07-30

Hashvalue for 1 PH/s (tile, epoch table, exports), click-to-copy exact difficulty values, a by-year table with YTD, and a year-by-month difficulty change grid.

## [1.0.0] - 2026-07-30

Initial release: difficulty epoch analytics from your own Bitcoin node —
summary tiles (difficulty, projected next adjustment, epoch progress,
estimated hashrate), difficulty and adjustment charts over the full chain
history, a sortable epoch table, CSV/JSON export, and a home-screen widget.
