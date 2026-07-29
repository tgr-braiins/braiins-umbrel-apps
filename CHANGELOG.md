# Changelog

Notable changes to the **Braiins Manager Agent** Umbrel app, newest first. The
version number is the upstream agent version (`version` in
`braiins-braiins-manager-agent/umbrel-app.yml`).

Each entry is the release description from the public release feed — the same
text shown as the in-app update notes. This file keeps the history, since the
manifest's `releaseNotes` only ever holds the current version and is
overwritten on each bump. Entries are added automatically by
`.github/scripts/bump.py` when the [`agent-update`](.github/workflows/agent-update.yml)
workflow opens a bump PR.

<!-- new entries are inserted directly below this line -->

## [4.11.1-4] - 2026-07-29

Setup page restyled to the Braiins design system: light and dark themes (follows your device setting), Braiins Sans now bundled with the app. The page makes no external requests.

## [4.11.1-3] - 2026-07-29

The app's settings page now shows the Braiins icon in the browser tab (favicon).

Packaging-only update; the Braiins Manager Agent itself is unchanged at 4.11.0.

## [4.11.1-2] - 2026-07-26

The app's settings page now shows the Umbrel app version in the footer, alongside the agent daemon version, so you can tell which package revision you're running.

Packaging-only update; the Braiins Manager Agent itself is unchanged at 4.11.0.

## [4.11.1-1] - 2026-07-26

Home-screen widget refresh: the Umbrel widget now shows an icon for each stat — agent status, miners found, and telemetry — matching umbrelOS's own live-usage widgets.

This is a packaging-only update; the Braiins Manager Agent itself is unchanged at 4.11.0.

## [4.11.0] - 2026-07-24

The Braiins Manager Agent **4.11.0** release adds support for Auradine Teraflux and minor improvements.

### New Features
- Support for Auradine Teraflux: Braiins Manager now supports miners running Auradine Teraflux firmware, including monitoring and management actions such as pause, resume, reboot, locate, and pool configuration.

### Improvements
- MARA FW: Miners running MARA firmware now report their control board serial number.

For help, bug reports, or feature requests, please create a [support ticket](https://help.braiins.com/en/support/tickets/new)

## [4.10.0] - 2026-07-23

Agent 4.10.0. The app page and a new home-screen widget show live agent activity: miners found and when telemetry was last sent to Braiins Manager. The app now runs unprivileged and ships for both x86 and ARM devices.
