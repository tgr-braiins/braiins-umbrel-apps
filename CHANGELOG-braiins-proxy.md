# Braiins Proxy (Umbrel) changelog

Upstream versions come from the public release feed,
https://downloads.braiins.com/braiins-proxy/index.json. Packaging releases
of this repo use the next patch with a suffix (`0.1.2-1`), see DECISIONS.md.

<!-- new entries are inserted directly below this line -->

## [0.1.1] - 2026-11-25

First package: Braiins Proxy 0.1.1 (rev 5158d572) from downloads.braiins.com
on Alpine 3.24. The web interface sits behind the Umbrel app proxy on port
4550; miners connect to the Umbrel on ports 3333-3339. The configuration and
the hashrate history are kept in the app's data folder.

The standalone install, the self-update command and the web interface
password are for installs outside a container and are not used here: Umbrel's
login protects the app, and updates come from this store.
