# Changelog — Bitcoin Data

Notable changes to the **Bitcoin Data** Umbrel app, newest first. Unlike the
other apps in this store, Bitcoin Data has no upstream binary — the app is this
repo's `image-braiins-bitcoin-data/webui.py` — so the version is plain semver
(`version` in `braiins-bitcoin-data/umbrel-app.yml`), bumped by the
[`braiins-bitcoin-data-release`](.github/workflows/braiins-bitcoin-data-release.yml) workflow.

<!-- new entries are inserted directly below this line -->

## [1.10.0] - 2026-08-03

The Scope selector now lets you filter every chart by halving era (50 BTC … 3.125 BTC) as well as by time window, and the Cumulative-change-by-year chart now responds to the selection.

## [1.9.0] - 2026-08-03

Adjustment distribution is now an overlaid density curve (per halving era or year), far clearer than the old bars, and the Range filter scopes it with axes that recompute to the selected window. The Range control is now sticky so it stays reachable across all the charts.

## [1.8.0] - 2026-08-03

Difficulty chart gains a Difficulty Ribbon toggle (moving-average fan; compression flags miner capitulation). New 'Cumulative change by year' chart overlays each year's YTD difficulty change. New 'Adjustment distribution' histogram with All / by-halving-era / by-year grouping. Faster loads: the epoch history is cached locally and painted instantly, then refreshed.

## [1.7.0] - 2026-08-03

Metric definitions: hover any dotted-underlined label for a plain-language explanation (difficulty, projected adjustment, hashrate, hashvalue, halving, CAGR, drawdowns and more). New callout at the top of Overview when the current difficulty pattern ranks in a top-5 record — the latest adjustment, an ongoing consecutive run, or an ongoing stretch below the all-time high.

## [1.6.0] - 2026-08-03

Monthly change now shows 3-year and 5-year averages per calendar month (e.g. January = mean of the last three completed Januaries), completed months only. Records now has four top-5 tables: longest and biggest consecutive increases, longest and deepest consecutive decreases.

## [1.5.0] - 2026-08-03

Records now include the longest and biggest consecutive difficulty increases and the longest and deepest consecutive decreases. Monthly change adds 3-year and 5-year averages over completed months. New 2y and 3y chart ranges. Headline stat tiles reformatted so hashrate and the next-adjustment label no longer wrap.

## [1.4.0] - 2026-07-30

New Signalling page tracking BIP-110 (version bit 4): counts over the last 18/36/72/144/288 blocks, current retarget period vs the 1,109-block lock-in threshold, a strip chart of the last 288 blocks, and recent signalling blocks. Records now includes the five longest stretches without a new difficulty ATH, with max drawdown.

## [1.3.0] - 2026-07-30

Exact projected retarget time, fees-inclusive hashvalue by default, bare-integer hashvalue column, longest wait without a new difficulty ATH, implied hashrate in the annual view, and separate Annual/Monthly navigation.

## [1.2.0] - 2026-07-30

Halving countdown tile and home-screen widget, fee-aware hashvalue from your node's current-epoch fees, all-time adjustment records and streaks, CAGR and doubling time, trailing-growth projections, chart PNG export, API documentation at /api, and in-page navigation.

## [1.1.0] - 2026-07-30

Hashvalue for 1 PH/s (tile, epoch table, exports), click-to-copy exact difficulty values, a by-year table with YTD, and a year-by-month difficulty change grid.

## [1.0.0] - 2026-07-30

Initial release: difficulty epoch analytics from your own Bitcoin node —
summary tiles (difficulty, projected next adjustment, epoch progress,
estimated hashrate), difficulty and adjustment charts over the full chain
history, a sortable epoch table, CSV/JSON export, and a home-screen widget.
