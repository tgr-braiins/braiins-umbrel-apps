"""Bitcoin Data — difficulty epoch analytics for Umbrel.

Serves on :8080 (fronted by Umbrel's app_proxy):
- GET  /               dashboard: summary tiles, difficulty + adjustment charts,
                       epoch table with export; styled per Braiins CDS v11
- GET  /api/summary    JSON polled by the page: node state + current-epoch stats
- GET  /api/epochs     JSON: one row per difficulty epoch (all history)
- GET  /export.csv     epoch table as CSV
- GET  /export.json    epoch table as JSON
- GET  /widgets/status Umbrel home-screen widget (three-stats), fetched
                       server-side by umbrelOS

Data comes from the user's own Bitcoin node (the official `bitcoin` Umbrel app)
over JSON-RPC. Difficulty only changes every 2016 blocks, so the full history is
one header per epoch boundary (~460 as of 2026): a one-time backfill, cached in
/data/epochs.json, then a 30 s tip poll that appends a row per retarget.

DEMO_MODE=1 serves deterministic synthetic epochs for UI development without a
node.
"""
import base64
import json
import math
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RPC_HOST = os.environ.get("BITCOIN_RPC_HOST", "").strip()
RPC_PORT = os.environ.get("BITCOIN_RPC_PORT", "8332").strip()
RPC_USER = os.environ.get("BITCOIN_RPC_USER", "").strip()
RPC_PASS = os.environ.get("BITCOIN_RPC_PASS", "").strip()
DEMO_MODE = os.environ.get("DEMO_MODE", "") == "1"
DATA_DIR = os.environ.get("DATA_DIR", "/data")
CACHE = os.path.join(DATA_DIR, "epochs.json")
PACKAGE_VERSION = os.environ.get("APP_VERSION", "").strip()

FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
FONTS = ("braiinssans-regular.woff2", "braiinssans-bold.woff2")

EPOCH_BLOCKS = 2016
TARGET_INTERVAL = 600  # seconds per block the retarget aims for
TWO32 = 2 ** 32
HALVING_BLOCKS = 210000


def subsidy_btc(height):
    return 50 / 2 ** (height // HALVING_BLOCKS)


def hashvalue_sats(difficulty, height):
    """Expected subsidy earnings of 1 PH/s in sats/day (fees excluded)."""
    blocks_per_day = 1e15 * 86400 / (difficulty * TWO32)
    return blocks_per_day * subsidy_btc(height) * 1e8


class RpcError(Exception):
    pass


def rpc(method, params=()):
    url = "http://%s:%s/" % (RPC_HOST, RPC_PORT)
    body = json.dumps({"jsonrpc": "1.0", "id": "braiins-bitcoin-data", "method": method,
                       "params": list(params)}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": "Basic " + base64.b64encode(
            ("%s:%s" % (RPC_USER, RPC_PASS)).encode()).decode(),
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        # bitcoind returns RPC errors (e.g. -28 "warming up") with HTTP 500
        try:
            payload = json.loads(e.read())
        except Exception:
            raise RpcError("HTTP %s from bitcoind" % e.code)
    except OSError as e:
        raise RpcError("Bitcoin node unreachable (%s)" % e)
    if payload.get("error"):
        err = payload["error"]
        raise RpcError("%s (code %s)" % (err.get("message", "?"), err.get("code")))
    return payload["result"]


# ---------------------------------------------------------------------------
# State: boundaries[i] = (height, block time, difficulty) of block 2016*i.
# Guarded by LOCK; the sync thread is the only writer.

LOCK = threading.Lock()
STATE = {
    "chain": None,
    "boundaries": [],       # [(height, time, difficulty), ...]
    "tip": None,            # (height, time)
    "updated": None,        # unix ts of last successful poll
    "error": None,          # last RPC error string, cleared on success
    "ibd": False,
    "verification": 1.0,
    "backfill": None,       # (done, total) while backfilling, else None
}


def load_cache():
    try:
        with open(CACHE) as f:
            data = json.load(f)
        return data.get("chain"), [tuple(b) for b in data.get("boundaries", [])]
    except (OSError, ValueError):
        return None, []


def save_cache(chain, boundaries):
    tmp = CACHE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"chain": chain, "boundaries": boundaries}, f)
    os.replace(tmp, CACHE)


def sync_loop():
    chain, boundaries = load_cache()
    with LOCK:
        STATE["chain"], STATE["boundaries"] = chain, boundaries
    while True:
        try:
            ci = rpc("getblockchaininfo")
            if ci["chain"] != chain:
                chain, boundaries = ci["chain"], []
            tip_height = ci["blocks"]
            want = tip_height // EPOCH_BLOCKS + 1  # boundaries 0..want-1
            while len(boundaries) < want:
                h = len(boundaries) * EPOCH_BLOCKS
                hdr = rpc("getblockheader", [rpc("getblockhash", [h])])
                boundaries.append((h, hdr["time"], hdr["difficulty"]))
                if len(boundaries) % 100 == 0 or len(boundaries) == want:
                    save_cache(chain, boundaries)
                with LOCK:
                    STATE.update(chain=chain, boundaries=list(boundaries),
                                 backfill=(len(boundaries), want))
            tip_hdr = rpc("getblockheader", [ci["bestblockhash"]])
            with LOCK:
                STATE.update(chain=chain, boundaries=list(boundaries),
                             tip=(tip_height, tip_hdr["time"]),
                             updated=time.time(), error=None,
                             ibd=ci.get("initialblockdownload", False),
                             verification=ci.get("verificationprogress", 1.0),
                             backfill=None)
        except (RpcError, KeyError, ValueError) as e:
            with LOCK:
                STATE["error"] = str(e)
        time.sleep(10 if STATE["error"] else 30)


# ---------------------------------------------------------------------------
# Demo data: deterministic synthetic history (no Date-dependent seed).

def demo_state():
    def lcg(seed=42):
        s = seed
        while True:
            s = (s * 1103515245 + 12345) % (2 ** 31)
            yield s / (2 ** 31)
    rnd = lcg()
    n = 140
    now = int(time.time())
    boundaries, difficulty = [], 5e10
    ts, diffs = [], []
    for i in range(n):
        drift = 0.028 * math.sin(i / 9.0) + 0.022
        avg = TARGET_INTERVAL * math.exp((next(rnd) - 0.5) * 0.12 - drift)
        ts.append(avg)
        diffs.append(difficulty)
        difficulty *= min(4.0, max(0.25, TARGET_INTERVAL / avg))
    # ts[n-1] never elapses — epoch n-1 is in progress, 1204 blocks deep
    total = sum(a * EPOCH_BLOCKS for a in ts[:-1])
    t = now - 1204 * 580 - total
    for i in range(n):
        boundaries.append((i * EPOCH_BLOCKS, int(t), diffs[i]))
        t += ts[i] * EPOCH_BLOCKS
    tip_height = (n - 1) * EPOCH_BLOCKS + 1204
    return {
        "chain": "main", "boundaries": boundaries,
        "tip": (tip_height, now - 300), "updated": time.time(),
        "error": None, "ibd": False, "verification": 1.0, "backfill": None,
        "demo": True,
    }


# ---------------------------------------------------------------------------
# Derived views

def snapshot():
    with LOCK:
        return {k: (list(v) if isinstance(v, list) else v) for k, v in STATE.items()}


def build_rows(state):
    """One dict per epoch; the last row is the in-progress epoch."""
    b, tip = state["boundaries"], state["tip"]
    rows = []
    for i, (height, start_time, difficulty) in enumerate(b):
        prev_diff = b[i - 1][2] if i > 0 else None
        change = (difficulty / prev_diff - 1) if prev_diff else None
        row = {"epoch": i, "start_height": height, "start_time": start_time,
               "difficulty": difficulty, "change": change,
               "hashvalue": hashvalue_sats(difficulty, height),
               "end_time": None, "blocks": None, "avg_interval": None,
               "hashrate": None, "current": False}
        if i + 1 < len(b):
            end_time = b[i + 1][1]
            duration = max(1, end_time - start_time)
            row.update(end_time=end_time, blocks=EPOCH_BLOCKS,
                       avg_interval=duration / EPOCH_BLOCKS,
                       hashrate=difficulty * TWO32 * EPOCH_BLOCKS / duration)
        elif tip:
            elapsed = tip[0] - height
            row.update(current=True, blocks=elapsed)
            if elapsed > 0:
                duration = max(1, tip[1] - start_time)
                row.update(avg_interval=duration / elapsed,
                           hashrate=difficulty * TWO32 * elapsed / duration)
        rows.append(row)
    return rows


def build_summary(state, rows):
    status, message = "ok", ""
    if state.get("demo"):
        status, message = "demo", "Demo data — synthetic epochs, not from a node"
    elif state["error"] and not state["updated"]:
        status, message = "waiting", "Waiting for the Bitcoin node"
    elif state["error"]:
        status, message = "error", state["error"]
    elif state["backfill"]:
        status = "backfill"
        message = "Reading epoch history %d/%d" % state["backfill"]
    elif state["ibd"]:
        status = "ibd"
        message = "Node syncing — %.1f%% verified" % (state["verification"] * 100)
    elif not state["tip"]:
        status, message = "waiting", "Waiting for the Bitcoin node"
    s = {"status": status, "message": message, "chain": state["chain"],
         "updated": state["updated"], "epochs": len(rows)}
    if not rows or not state["tip"]:
        return s
    cur, tip = rows[-1], state["tip"]
    elapsed = cur["blocks"] or 0
    remaining = EPOCH_BLOCKS - elapsed
    avg = cur["avg_interval"]
    projected = None
    if avg and elapsed >= 10:
        projected = min(4.0, max(0.25, TARGET_INTERVAL / avg)) - 1
    s.update({
        "tip_height": tip[0], "tip_time": tip[1],
        "difficulty": cur["difficulty"], "epoch": cur["epoch"],
        "last_change": cur["change"],
        "elapsed": elapsed, "remaining": remaining,
        "progress": elapsed / EPOCH_BLOCKS,
        "avg_interval": avg, "hashrate": cur["hashrate"],
        "projected_change": projected,
        "eta": tip[1] + remaining * (avg or TARGET_INTERVAL),
        "hashvalue": hashvalue_sats(cur["difficulty"], tip[0]),
        "subsidy": subsidy_btc(tip[0]),
    })
    closed = [r for r in rows if r["change"] is not None and not r["current"]]
    if closed:
        up = max(closed, key=lambda r: r["change"])
        down = min(closed, key=lambda r: r["change"])
        s["max_up"] = {"epoch": up["epoch"], "change": up["change"]}
        s["max_down"] = {"epoch": down["epoch"], "change": down["change"]}
    return s


def fmt_compact(x, digits=1):
    if x is None:
        return "—"
    for value, suffix in ((1e18, "E"), (1e15, "P"), (1e12, "T"), (1e9, "G"),
                          (1e6, "M"), (1e3, "k")):
        if abs(x) >= value:
            return "%.*f%s" % (digits, x / value, suffix)
    return "%.*f" % (digits, x)


def iso_utc(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts)) if ts else ""


def export_rows(rows):
    out = []
    for r in rows:
        out.append({
            "epoch": r["epoch"],
            "start_height": r["start_height"],
            "start_time_utc": iso_utc(r["start_time"]),
            "end_time_utc": iso_utc(r["end_time"]),
            "blocks": r["blocks"],
            "avg_block_interval_s": round(r["avg_interval"], 1) if r["avg_interval"] else None,
            "difficulty": r["difficulty"],
            "change_pct": round(r["change"] * 100, 4) if r["change"] is not None else None,
            "est_hashrate_hs": ("%.4g" % r["hashrate"]) if r["hashrate"] else None,
            "hashvalue_sats_per_phs_day": round(r["hashvalue"], 1),
            "in_progress": r["current"],
        })
    return out


def export_csv(rows):
    cols = ["epoch", "start_height", "start_time_utc", "end_time_utc", "blocks",
            "avg_block_interval_s", "difficulty", "change_pct",
            "est_hashrate_hs", "hashvalue_sats_per_phs_day", "in_progress"]
    lines = [",".join(cols)]
    for r in export_rows(rows):
        lines.append(",".join("" if r[c] is None else str(r[c]) for c in cols))
    return ("\n".join(lines) + "\n").encode()


def widget_status(state, rows):
    s = build_summary(state, rows)
    difficulty = fmt_compact(s.get("difficulty"))
    progress = "%.0f%%" % (s["progress"] * 100) if "progress" in s else "—"
    projected = s.get("projected_change")
    projected = "%+.1f%%" % (projected * 100) if projected is not None else "—"
    # three-stats items are {icon, text, subtext}; icon = Tabler slug
    # (see DECISIONS.md "Widget icons"), no `title` field exists.
    return {
        "type": "three-stats",
        "refresh": "10s",
        "link": "",
        "items": [
            {"icon": "activity", "text": difficulty, "subtext": "Difficulty"},
            {"icon": "hourglass", "text": progress, "subtext": "Epoch %s" % s.get("epoch", "—")},
            {"icon": "trending-up", "text": projected, "subtext": "Next adjust"},
        ],
    }


# ---------------------------------------------------------------------------
# Page

BRAIINS_SYMBOL = (
    '<svg viewBox="0 0 864 864" width="14" height="14" aria-hidden="true">'
    '<polygon points="345.6 864 345.6 682.8 194.4 179.9 194.4 0 0 0 0 179.9 151.2 682.8 151.2 864 345.6 864" fill="#fff"/>'
    '<polygon points="864 864 864 682.8 712.8 179.9 712.8 0 518.4 0 518.4 179.9 669.6 682.8 669.6 864 864 864" fill="#fff"/>'
    "</svg>"
)

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bitcoin Data</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1024 1024'><rect width='1024' height='1024' rx='230' fill='%236B50FF'/><path d='M232 704 L392 512 L392 608 L552 384 L552 480 L712 288 L792 288' stroke='%23fff' stroke-width='56' fill='none' stroke-linecap='square'/></svg>">
<style>
/* Braiins CDS v11 (IBM Carbon v11) — token values from the design system
   mirror (colors_and_type.css), matching the conventions established in
   braiins-manager-agent/webui.py: violet-60 primary action + focus, blue link
   tokens, White / Gray 90 themes via prefers-color-scheme, Braiins Sans
   served locally (400/700 only, so Carbon 600 resolves to bold).
   Chart data colors follow the dataviz method: single-series line = blue,
   adjustment polarity = warm/cool diverging pair blue<->orange, validated
   with the palette checker in both modes (light 60-steps, dark 50-steps). */
@font-face { font-family: "Braiins Sans"; font-style: normal; font-weight: 400;
  font-display: swap; src: url(fonts/braiinssans-regular.woff2) format("woff2"); }
@font-face { font-family: "Braiins Sans"; font-style: normal; font-weight: 700;
  font-display: swap; src: url(fonts/braiinssans-bold.woff2) format("woff2"); }
:root {
  --violet-60: #6b50ff; --violet-70: #5739e0; --violet-80: #4326b3;
  --gray-10: #f4f4f4; --gray-20: #e0e0e0; --gray-30: #c6c6c6;
  --gray-40: #a8a8a8; --gray-50: #8d8d8d; --gray-60: #6f6f6f;
  --gray-70: #525252; --gray-80: #393939; --gray-90: #262626; --gray-100: #161616;
  --blue-20: #d0e2ff; --blue-30: #a6c8ff; --blue-40: #78a9ff;
  --blue-50: #4589ff; --blue-60: #0f62fe; --blue-70: #0043ce; --blue-80: #002d9c;
  --orange-50: #eb6200; --orange-60: #ba4e00;
  --red-40: #ff8389; --red-60: #da1e28;
  --green-40: #42be65; --green-50: #24a148;
  --yellow-30: #f1c21b;

  --font-sans: "Braiins Sans", -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif;
  --font-brand: "Braiins Sans", -apple-system, BlinkMacSystemFont, sans-serif;
  --font-mono: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;

  --spacing-02: .25rem; --spacing-03: .5rem; --spacing-04: .75rem;
  --spacing-05: 1rem; --spacing-06: 1.5rem; --spacing-07: 2rem; --spacing-09: 3rem;
  --radius-pill: 1000px;
  --duration-fast-02: 110ms;
  --ease-productive: cubic-bezier(.2, 0, .38, .9);

  /* semantic tokens — White theme */
  --background: #ffffff;
  --layer-01: var(--gray-10);
  --layer-02: #ffffff;
  --layer-hover: #e8e8e8;
  --border-subtle: var(--gray-20);
  --border-strong: var(--gray-50);
  --text-primary: var(--gray-100);
  --text-secondary: var(--gray-70);
  --text-helper: var(--gray-60);
  --text-on-color: #ffffff;
  --support-error: var(--red-60);
  --support-success: var(--green-50);
  --support-warning: var(--yellow-30);
  --link-primary: var(--blue-60);
  --link-primary-hover: var(--blue-70);
  --focus: var(--violet-60);
  --button-primary: var(--violet-60);
  --button-primary-hover: var(--violet-70);
  --button-primary-active: var(--violet-80);
  --table-head: var(--gray-20);
  --table-row: var(--gray-10);
  /* chart tokens (validated, see header comment) */
  --data-pos: var(--blue-60);
  --data-neg: var(--orange-60);
  --chart-grid: var(--gray-20);
  --chart-axis: var(--gray-50);
  --meter-track: var(--blue-20);
}
@media (prefers-color-scheme: dark) {
  :root { /* Gray 90 theme */
    --background: var(--gray-90);
    --layer-01: var(--gray-80);
    --layer-02: var(--gray-90);
    --layer-hover: #474747;
    --border-subtle: var(--gray-70);
    --border-strong: var(--gray-40);
    --text-primary: var(--gray-10);
    --text-secondary: var(--gray-30);
    --text-helper: var(--gray-40);
    --support-error: var(--red-40);
    --support-success: var(--green-40);
    --link-primary: var(--blue-40);
    --link-primary-hover: var(--blue-30);
    --table-head: var(--gray-70);
    --table-row: var(--gray-80);
    --data-pos: var(--blue-50);
    --data-neg: var(--orange-50);
    --chart-grid: var(--gray-80);
    --chart-axis: var(--gray-50);
    --meter-track: var(--blue-80);
  }
}
* { box-sizing: border-box; }
html { background: var(--background); }
body { margin: 0; font: 400 14px/1.4286 var(--font-sans); letter-spacing: .16px;
  background: var(--background); color: var(--text-primary); }

/* Carbon UI shell header: 48px, inverse, full-bleed */
.shell { height: 48px; background: var(--gray-100); color: #fff;
  display: flex; align-items: center; padding: 0 var(--spacing-05);
  border-bottom: 1px solid var(--gray-80); }
.shell .mark { background: var(--violet-60); width: 28px; height: 28px;
  border-radius: 6px; display: flex; align-items: center; justify-content: center;
  margin-right: var(--spacing-04); flex-shrink: 0; }
.shell .name { font: 700 14px/1 var(--font-brand); letter-spacing: 0; margin-right: var(--spacing-06); }
.shell .name small { font-weight: 400; color: var(--gray-40); margin-left: .5em; }
.shell nav { align-self: stretch; display: flex; }
.shell nav a { display: flex; align-items: center; padding: 0 var(--spacing-05);
  font: 400 14px/1 var(--font-brand); color: var(--gray-20); text-decoration: none;
  border-bottom: 3px solid transparent; border-top: 3px solid transparent; }
.shell nav a.active { color: #fff; border-bottom-color: var(--violet-60); }

main { max-width: 66rem; margin: 0 auto; padding: var(--spacing-07) var(--spacing-05) var(--spacing-09); }

.titlerow { display: flex; align-items: baseline; justify-content: space-between;
  flex-wrap: wrap; gap: var(--spacing-03); margin-bottom: var(--spacing-06); }
h2 { font: 400 28px/1.29 var(--font-sans); letter-spacing: 0; margin: 0; }
#pill { display: inline-flex; align-items: center; gap: var(--spacing-03);
  font: 400 12px/1.3333 var(--font-sans); letter-spacing: .32px;
  padding: var(--spacing-02) var(--spacing-04); border-radius: var(--radius-pill);
  box-shadow: inset 0 0 0 1px var(--border-subtle); color: var(--text-secondary); }
#pill .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--border-strong); flex-shrink: 0; }
#pill.ok .dot { background: var(--support-success); }
#pill.backfill .dot, #pill.ibd .dot { background: var(--support-warning); }
#pill.error .dot { background: var(--support-error); }
#pill.demo .dot { background: var(--violet-60); }

/* Stat tiles: flat layer fill, sharp corners, no shadow */
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1px; margin-bottom: var(--spacing-06); }
@media (max-width: 480px) { .tiles { grid-template-columns: 1fr; } }
.tile { background: var(--layer-01); padding: var(--spacing-05); min-height: 120px; }
.tile .label { font: 400 12px/1.3333 var(--font-sans); letter-spacing: .32px; color: var(--text-secondary); }
.tile .value { font: 700 28px/1.29 var(--font-sans); letter-spacing: 0; margin: var(--spacing-03) 0 var(--spacing-02); }
.tile .value small { font-size: 16px; font-weight: 400; color: var(--text-secondary); margin-left: .15em; }
.tile .sub { font: 400 12px/1.3333 var(--font-sans); letter-spacing: .32px; color: var(--text-helper); }
.tile .delta { display: inline-flex; align-items: center; gap: 6px;
  font: 400 12px/1.3333 var(--font-sans); letter-spacing: .32px; color: var(--text-secondary); }
.dirdot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.dirdot.up { background: var(--data-pos); }
.dirdot.down { background: var(--data-neg); }
/* click-to-copy: exact values in mono, tooltips enhance, copy never gated */
.copyline { display: inline-block; font: 400 12px/1.3333 var(--font-mono);
  letter-spacing: 0; color: var(--text-secondary); cursor: pointer;
  border-bottom: 1px dotted var(--border-strong); margin-top: var(--spacing-02);
  font-variant-numeric: tabular-nums; }
.copyline:hover { color: var(--link-primary); border-bottom-color: var(--link-primary); }
.copyline:focus-visible { outline: 2px solid var(--focus); outline-offset: 1px; }
td.copyable { cursor: pointer; }
td.copyable:hover { text-decoration: underline dotted var(--border-strong); }
.meter { height: 8px; background: var(--meter-track); margin: var(--spacing-03) 0 var(--spacing-03); }
.meter i { display: block; height: 100%; background: var(--data-pos);
  transition: width var(--duration-fast-02) var(--ease-productive); }

/* Filter row: one row, above everything it scopes */
.filters { display: flex; align-items: center; gap: var(--spacing-05); margin-bottom: var(--spacing-05); }
.filters .flabel { font: 400 12px/1.3333 var(--font-sans); letter-spacing: .32px; color: var(--text-secondary); }
.switcher { display: inline-flex; background: var(--layer-01); padding: 0; }
.switcher button { appearance: none; border: 0; border-radius: 0; cursor: pointer;
  height: 32px; padding: 0 var(--spacing-05); background: transparent;
  font: 400 14px/1 var(--font-brand); letter-spacing: .16px; color: var(--text-secondary);
  transition: background var(--duration-fast-02) var(--ease-productive); }
.switcher button:hover { background: var(--layer-hover); }
.switcher button:focus-visible { outline: 2px solid var(--focus); outline-offset: -2px; }
.switcher button[aria-pressed="true"] { background: var(--gray-100); color: #fff; }

.card { background: var(--layer-01); padding: var(--spacing-05); margin-bottom: var(--spacing-06); position: relative; }
.cardhead { display: flex; align-items: center; justify-content: space-between;
  flex-wrap: wrap; gap: var(--spacing-03); margin-bottom: var(--spacing-04); }
h3 { font: 700 16px/1.375 var(--font-sans); letter-spacing: 0; margin: 0; }
.key { display: inline-flex; gap: var(--spacing-05);
  font: 400 12px/1.3333 var(--font-sans); letter-spacing: .32px; color: var(--text-secondary); }
.key i { display: inline-block; width: 12px; height: 12px; margin-right: 6px; vertical-align: -2px; }
.key .pos { background: var(--data-pos); }
.key .neg { background: var(--data-neg); }
svg.chart { display: block; width: 100%; }
svg.chart text { font: 400 11px/1 var(--font-sans); fill: var(--text-helper); }
svg.chart .grid { stroke: var(--chart-grid); stroke-width: 1; }
svg.chart .zero { stroke: var(--chart-axis); stroke-width: 1; }
svg.chart .lbl { font-weight: 700; fill: var(--text-secondary); }
.tooltip { position: absolute; pointer-events: none; z-index: 3; display: none;
  background: var(--gray-100); color: var(--gray-10); padding: var(--spacing-03) var(--spacing-04);
  font: 400 12px/1.5 var(--font-sans); letter-spacing: .32px;
  box-shadow: 0 2px 6px rgba(0,0,0,.2); max-width: 240px; }
.tooltip .tval { font-weight: 700; color: #fff; }
.tooltip .trow { display: flex; align-items: center; gap: 8px; white-space: nowrap; }
.tooltip .tkey { display: inline-block; width: 12px; height: 2px; flex-shrink: 0; }

/* Carbon data table */
.tablewrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font: 400 14px/1.2857 var(--font-sans); letter-spacing: .16px; }
thead th { background: var(--table-head); height: 40px; text-align: right; padding: 0 var(--spacing-05);
  font-weight: 700; color: var(--text-primary); white-space: nowrap; cursor: pointer; user-select: none; }
thead th:first-child { text-align: left; }
thead th .arrow { font-weight: 400; color: var(--text-secondary); }
tbody td { height: 44px; padding: 0 var(--spacing-05); border-bottom: 1px solid var(--border-subtle);
  background: var(--table-row); text-align: right; white-space: nowrap;
  font-variant-numeric: tabular-nums; }
tbody td:first-child { text-align: left; }
tbody tr:hover td { background: var(--layer-hover); }
tbody td .dirdot { display: inline-block; margin-right: 6px; }
.tfoot { display: flex; align-items: center; justify-content: space-between;
  padding-top: var(--spacing-04); font: 400 12px/1.3333 var(--font-sans);
  letter-spacing: .32px; color: var(--text-secondary); }
.pager { display: inline-flex; gap: 1px; }
.pager button { appearance: none; border: 0; border-radius: 0; cursor: pointer;
  height: 32px; padding: 0 var(--spacing-04); background: var(--layer-01);
  color: var(--text-primary); font: 400 14px/1 var(--font-brand); letter-spacing: .16px; }
.pager button:hover { background: var(--layer-hover); }
.pager button:disabled { color: var(--text-helper); cursor: default; background: var(--layer-01); }
.pager button:focus-visible { outline: 2px solid var(--focus); outline-offset: -2px; }

/* Ghost buttons (export) */
.ghost { appearance: none; border: 0; border-radius: 0; cursor: pointer; text-decoration: none;
  display: inline-flex; align-items: center; height: 32px; padding: 0 var(--spacing-05);
  background: transparent; color: var(--link-primary);
  font: 400 14px/1 var(--font-brand); letter-spacing: .16px;
  transition: background var(--duration-fast-02) var(--ease-productive); }
.ghost:hover { background: var(--layer-hover); color: var(--link-primary-hover); }
.ghost:focus-visible { outline: 2px solid var(--focus); outline-offset: -2px; }

/* calendar: year rows x month columns, diverging tint behind text-token values */
.cal td, .cal th { height: 36px; padding: 0 var(--spacing-04); }
.cal td { font: 400 12px/1.3333 var(--font-sans); letter-spacing: .32px; }
.cal td.empty { color: var(--text-helper); background: var(--table-row); }
.note { font: 400 12px/1.3333 var(--font-sans); letter-spacing: .32px; color: var(--text-helper);
  margin: var(--spacing-03) 0 0; }
footer { margin-top: var(--spacing-07); font: 400 12px/1.3333 var(--font-sans);
  letter-spacing: .32px; color: var(--text-helper); text-align: center; }
</style></head><body>
<header class="shell">
  <div class="mark">__SYMBOL__</div>
  <span class="name">Bitcoin Data<small>on Umbrel</small></span>
  <nav><a class="active" href="">Difficulty</a></nav>
</header>
<main>
  <div class="titlerow">
    <h2>Difficulty</h2>
    <span id="pill"><span class="dot"></span><span id="pill-text">Connecting&hellip;</span></span>
  </div>

  <div class="tiles">
    <div class="tile"><div class="label">Difficulty</div>
      <div class="value" id="t-diff">—</div>
      <div class="delta" id="t-diff-delta"></div>
      <span class="copyline" id="t-diff-full" role="button" tabindex="0"
        title="Click to copy the exact value"></span>
      <div class="sub" id="t-diff-sub"></div></div>
    <div class="tile"><div class="label">Next adjustment (projected)</div>
      <div class="value" id="t-proj">—</div>
      <div class="sub" id="t-proj-sub"></div></div>
    <div class="tile"><div class="label">Epoch progress</div>
      <div class="value" id="t-prog">—</div>
      <div class="meter"><i id="t-prog-bar" style="width:0%"></i></div>
      <div class="sub" id="t-prog-sub"></div></div>
    <div class="tile"><div class="label">Est. network hashrate</div>
      <div class="value" id="t-hash">—</div>
      <div class="sub" id="t-hash-sub">current epoch average</div></div>
    <div class="tile"><div class="label">Hashvalue (1 PH/s)</div>
      <div class="value" id="t-hv">—<small>sats/day</small></div>
      <div class="sub" id="t-hv-sub">subsidy only, fees excluded</div></div>
  </div>

  <div class="card">
    <div class="cardhead"><h3>By year</h3></div>
    <div class="tablewrap"><table id="years">
      <thead><tr><th>Year</th><th>Difficulty on Jan 1</th><th>Exact value</th><th>Change over year</th></tr></thead>
      <tbody></tbody>
    </table></div>
    <p class="note">Difficulty in effect at 00:00 UTC on Jan 1. The current year shows year-to-date. Click an exact value to copy it.</p>
  </div>

  <div class="card">
    <div class="cardhead"><h3>Monthly change</h3>
      <span class="key"><span><i class="pos"></i>Increase</span><span><i class="neg"></i>Decrease</span></span></div>
    <div class="tablewrap"><table id="months" class="cal">
      <thead><tr></tr></thead><tbody></tbody>
    </table></div>
    <p class="note">Difficulty change from the first to the last moment of each month (UTC). * = month to date.</p>
  </div>

  <div class="filters">
    <span class="flabel">Range</span>
    <span class="switcher" id="range" role="group" aria-label="Date range">
      <button data-r="all" aria-pressed="true">All</button>
      <button data-r="4y" aria-pressed="false">4y</button>
      <button data-r="1y" aria-pressed="false">1y</button>
      <button data-r="90d" aria-pressed="false">90d</button>
    </span>
  </div>

  <div class="card">
    <div class="cardhead"><h3>Difficulty over time</h3>
      <span class="switcher" id="scale" role="group" aria-label="Y scale">
        <button data-s="linear" aria-pressed="true">Linear</button>
        <button data-s="log" aria-pressed="false">Log</button>
      </span></div>
    <svg class="chart" id="chart-diff" height="300" role="img" aria-label="Difficulty over time"></svg>
    <div class="tooltip" id="tt-diff"></div>
  </div>

  <div class="card">
    <div class="cardhead"><h3>Adjustment per epoch</h3>
      <span class="key"><span><i class="pos"></i>Increase</span><span><i class="neg"></i>Decrease</span></span></div>
    <svg class="chart" id="chart-adj" height="260" role="img" aria-label="Difficulty adjustment per epoch"></svg>
    <div class="tooltip" id="tt-adj"></div>
  </div>

  <div class="card">
    <div class="cardhead"><h3>Epochs</h3>
      <span><a class="ghost" href="export.csv" download>Export CSV</a><a class="ghost" href="export.json" download>Export JSON</a></span></div>
    <div class="tablewrap"><table id="table">
      <thead><tr></tr></thead><tbody></tbody>
    </table></div>
    <div class="tfoot"><span id="t-count"></span>
      <span class="pager"><button id="pg-prev">&larr; Newer</button><button id="pg-next">Older &rarr;</button></span></div>
    <p class="note">Est. hashrate is derived from difficulty and observed block intervals. Exports always contain the full history regardless of the selected range.</p>
  </div>

  <footer>__PKG__Data from your Bitcoin node &middot; difficulty retargets every 2,016 blocks</footer>
</main>
<script>
"use strict";
const S = { rows: [], summary: null, range: "all", scale: "linear",
            sort: { key: "epoch", dir: -1 }, page: 0 };
const PAGE_SIZE = 25;
const RANGE_S = { "4y": 4 * 365.25 * 86400, "1y": 365.25 * 86400, "90d": 90 * 86400 };

// -- formatting (text wears text tokens; marks carry the color) --------------
function fmtCompact(x, d) {
  if (x == null) return "—";
  const units = [[1e18, "E"], [1e15, "P"], [1e12, "T"], [1e9, "G"], [1e6, "M"], [1e3, "k"]];
  for (const [v, s] of units) if (Math.abs(x) >= v) return (x / v).toFixed(d == null ? 1 : d) + " " + s;
  return x.toFixed(d == null ? 1 : d);
}
function fmtHash(x) { return x == null ? "—" : fmtCompact(x, 1) + "H/s"; }
function fmtPct(x, d) {
  if (x == null) return "—";
  const s = (100 * x).toFixed(d == null ? 2 : d);
  return (x > 0 && parseFloat(s) !== 0 ? "+" : "") + s + "%";
}
function fmtInt(x) { return x == null ? "—" : x.toLocaleString("en-US"); }
function fmtDate(ts) {
  return ts == null ? "—" : new Date(ts * 1000).toISOString().slice(0, 10);
}
function fmtDur(s) {
  if (s == null) return "—";
  const d = Math.floor(s / 86400), h = Math.round((s % 86400) / 3600);
  return d + "d " + h + "h";
}
function fmtInterval(s) {
  if (s == null) return "—";
  return Math.floor(s / 60) + "m " + Math.round(s % 60).toString().padStart(2, "0") + "s";
}
function ago(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 90) return Math.round(s) + " s ago";
  if (s < 5400) return Math.round(s / 60) + " min ago";
  return Math.round(s / 3600) + " h ago";
}
function fmtFullInt(x) { return x == null ? "—" : Math.round(x).toLocaleString("en-US"); }

// navigator.clipboard needs a secure context; umbrel.local is plain http,
// so fall back to a temporary textarea + execCommand there.
function copyText(text, el) {
  const done = () => {
    if (!el) return;
    const orig = el.dataset.copyOrig || el.textContent;
    el.dataset.copyOrig = orig;
    el.textContent = "Copied";
    setTimeout(() => { el.textContent = orig; }, 1200);
  };
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(done);
  } else {
    const ta = document.createElement("textarea");
    ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); done(); } finally { ta.remove(); }
  }
}

// difficulty in effect at unix time t (last epoch starting at or before t)
function diffAt(t) {
  const rows = S.rows;
  if (!rows.length || t < rows[0].start_time) return null;
  let lo = 0, hi = rows.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (rows[mid].start_time <= t) lo = mid; else hi = mid - 1;
  }
  return rows[lo].difficulty;
}

// -- data ---------------------------------------------------------------------
async function fetchAll() {
  try {
    const [er, sr] = await Promise.all([
      fetch("api/epochs", { cache: "no-store" }), fetch("api/summary", { cache: "no-store" })]);
    S.rows = (await er.json()).rows;
    S.summary = await sr.json();
  } catch (e) { S.summary = { status: "error", message: "UI unreachable" }; }
  render();
}

function tipTime() {
  return (S.summary && S.summary.tip_time) ||
         (S.rows.length ? S.rows[S.rows.length - 1].start_time : 0);
}
function filteredRows() {
  if (S.range === "all") return S.rows;
  const cutoff = tipTime() - RANGE_S[S.range];
  return S.rows.filter(r => (r.end_time || tipTime()) >= cutoff);
}

// -- pill & tiles --------------------------------------------------------------
function renderPill() {
  const pill = document.getElementById("pill"), txt = document.getElementById("pill-text");
  const s = S.summary || {};
  pill.className = s.status === "waiting" ? "" : (s.status || "");
  if (s.status === "ok") {
    txt.textContent = "Live · block " + fmtInt(s.tip_height) +
      (s.updated ? " · updated " + ago(s.updated) : "");
  } else txt.textContent = s.message || "Connecting…";
}
function setDelta(el, change, suffix) {
  el.textContent = "";
  if (change == null) return;
  const dot = document.createElement("span");
  dot.className = "dirdot " + (change >= 0 ? "up" : "down");
  el.appendChild(dot);
  el.appendChild(document.createTextNode(fmtPct(change) + (suffix || "")));
}
function renderTiles() {
  const s = S.summary || {};
  const set = (id, v) => { document.getElementById(id).textContent = v; };
  set("t-diff", fmtCompact(s.difficulty));
  setDelta(document.getElementById("t-diff-delta"), s.last_change, " vs previous epoch");
  set("t-diff-sub", s.epoch != null ? "epoch " + s.epoch + " · since block " + fmtInt(s.epoch * 2016) : "");
  set("t-proj", fmtPct(s.projected_change, 2));
  set("t-proj-sub", s.remaining != null
    ? "in " + fmtInt(s.remaining) + " blocks · ~" + fmtDate(s.eta) : "");
  set("t-prog", s.progress != null ? (100 * s.progress).toFixed(1) + "%" : "—");
  document.getElementById("t-prog-bar").style.width = s.progress != null ? (100 * s.progress) + "%" : "0";
  set("t-prog-sub", s.elapsed != null ? fmtInt(s.elapsed) + " of 2,016 blocks" : "");
  set("t-hash", fmtHash(s.hashrate));
  set("t-hash-sub", s.avg_interval != null
    ? "avg block " + fmtInterval(s.avg_interval) + " this epoch" : "current epoch average");
  const hv = document.getElementById("t-hv");
  hv.textContent = "";
  hv.appendChild(document.createTextNode(s.hashvalue != null ? fmtFullInt(s.hashvalue) : "—"));
  const unit = document.createElement("small"); unit.textContent = " sats/day";
  hv.appendChild(unit);
  set("t-hv-sub", s.subsidy != null
    ? "subsidy " + s.subsidy + " BTC · fees excluded" : "subsidy only, fees excluded");
  const full = document.getElementById("t-diff-full");
  delete full.dataset.copyOrig;
  full.textContent = s.difficulty != null ? fmtFullInt(s.difficulty) : "";
}

// -- calendar: annual table + year-by-month diverging grid ----------------------
function tint(varName, mag) {
  // series-hue wash behind text-token values; capped so text stays readable
  const hex = cssVar(varName);
  const r = parseInt(hex.slice(1, 3), 16), g = parseInt(hex.slice(3, 5), 16), b = parseInt(hex.slice(5, 7), 16);
  const a = Math.min(0.35, 0.04 + mag * 2.2);
  return "rgba(" + r + "," + g + "," + b + "," + a + ")";
}
function changeCell(td, change, suffix) {
  td.style.background = tint(change >= 0 ? "--data-pos" : "--data-neg", Math.abs(change));
  td.style.textAlign = "right";
  td.appendChild(document.createTextNode(fmtPct(change, 1) + (suffix || "")));
  td.title = fmtPct(change, 2);
}
function renderCalendar() {
  if (!S.rows.length) return;
  const rows = S.rows, now = tipTime();
  const curDiff = rows[rows.length - 1].difficulty;
  const first = rows[0].start_time;
  const valAt = t => t >= now ? curDiff : (diffAt(t) != null ? diffAt(t) : rows[0].difficulty);
  const y0 = new Date(first * 1000).getUTCFullYear();
  const y1 = new Date(now * 1000).getUTCFullYear();

  // annual table, newest first; current year is YTD
  const ytbody = document.querySelector("#years tbody");
  ytbody.textContent = "";
  for (let y = y1; y >= y0; y--) {
    const t0 = Date.UTC(y, 0, 1) / 1000, t1 = Date.UTC(y + 1, 0, 1) / 1000;
    const start = valAt(Math.max(t0, first)), end = valAt(t1), ytd = t1 > now;
    const tr = document.createElement("tr");
    const cells = [String(y) + (t0 < first ? " (from genesis)" : ""), fmtCompact(start, 2)];
    for (const c of cells) {
      const td = document.createElement("td"); td.textContent = c; tr.appendChild(td);
    }
    const exact = document.createElement("td");
    exact.className = "copyable"; exact.textContent = fmtFullInt(start);
    exact.title = "Click to copy " + Math.round(start);
    exact.addEventListener("click", () => copyText(String(Math.round(start)), exact));
    tr.appendChild(exact);
    const ch = document.createElement("td");
    changeCell(ch, end / start - 1, ytd ? " YTD" : "");
    tr.appendChild(ch);
    ytbody.appendChild(tr);
  }
  document.querySelector("#years thead th:first-child").style.textAlign = "left";

  // monthly grid: rows = years (newest first), columns = Jan..Dec
  const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  const mhead = document.querySelector("#months thead tr");
  mhead.textContent = "";
  const th0 = document.createElement("th"); th0.textContent = "Year";
  th0.style.textAlign = "left"; mhead.appendChild(th0);
  for (const m of MONTHS) { const th = document.createElement("th"); th.textContent = m; mhead.appendChild(th); }
  const mbody = document.querySelector("#months tbody");
  mbody.textContent = "";
  for (let y = y1; y >= y0; y--) {
    const tr = document.createElement("tr");
    const td0 = document.createElement("td"); td0.textContent = String(y);
    td0.style.textAlign = "left"; tr.appendChild(td0);
    for (let m = 0; m < 12; m++) {
      const t0 = Date.UTC(y, m, 1) / 1000, t1 = Date.UTC(y, m + 1, 1) / 1000;
      const td = document.createElement("td");
      if (t0 > now || t1 <= first) {
        td.className = "empty"; td.textContent = "";
      } else {
        const mtd = t1 > now;
        changeCell(td, valAt(t1) / valAt(Math.max(t0, first)) - 1, mtd ? "*" : "");
      }
      tr.appendChild(td);
    }
    mbody.appendChild(tr);
  }
}

// -- svg helpers ----------------------------------------------------------------
const NS = "http://www.w3.org/2000/svg";
function el(name, attrs, parent) {
  const e = document.createElementNS(NS, name);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(e);
  return e;
}
function niceTicks(min, max, n) {
  if (min === max) { max = min + 1; }
  const span = max - min, step0 = span / n, mag = Math.pow(10, Math.floor(Math.log10(step0)));
  let step = mag;
  for (const m of [1, 2, 2.5, 5, 10]) if (step0 <= m * mag) { step = m * mag; break; }
  const ticks = [];
  for (let v = Math.ceil(min / step) * step; v <= max + 1e-9 * span; v += step) ticks.push(v);
  return ticks;
}
function xTicks(t0, t1, n) {
  const ticks = [], span = t1 - t0;
  for (let i = 0; i <= n; i++) ticks.push(t0 + span * i / n);
  return ticks;
}
function drawXAxis(svg, t0, t1, xOf, n, yPx) {
  const span = t1 - t0;
  let prev = null;
  for (const t of xTicks(t0, t1, n)) {
    const label = xLabel(t, span);
    if (label === prev) continue;   // dedupe consecutive identical labels
    prev = label;
    el("text", { x: xOf(t), y: yPx, "text-anchor": "middle" }, svg).textContent = label;
  }
}
function xLabel(ts, span) {
  const d = new Date(ts * 1000);
  if (span > 3 * 365 * 86400) return String(d.getUTCFullYear());
  const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  return months[d.getUTCMonth()] + " " + String(d.getUTCFullYear()).slice(2);
}
function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
function showTip(tt, card, px, py, build) {
  tt.textContent = "";
  build(tt);
  tt.style.display = "block";
  const cw = card.clientWidth, tw = tt.offsetWidth;
  tt.style.left = Math.min(Math.max(4, px + 12), cw - tw - 4) + "px";
  tt.style.top = Math.max(4, py - tt.offsetHeight - 10) + "px";
}
function tipRow(tt, keyColor, label, value) {
  const row = document.createElement("div");
  row.className = "trow";
  if (keyColor) {
    const k = document.createElement("span");
    k.className = "tkey"; k.style.background = keyColor;
    row.appendChild(k);
  }
  const v = document.createElement("span");
  v.className = "tval"; v.textContent = value;
  row.appendChild(v);
  row.appendChild(document.createTextNode(" " + label));
  tt.appendChild(row);
}

// -- difficulty chart: single-series step line (blue), crosshair + tooltip ------
function drawDiff() {
  const svg = document.getElementById("chart-diff"), card = svg.parentNode;
  const tt = document.getElementById("tt-diff");
  svg.textContent = "";
  const rows = filteredRows();
  if (!rows.length) return;
  const W = card.clientWidth - 32, H = 300, m = { t: 20, r: 20, b: 30, l: 56 };
  svg.setAttribute("width", W); svg.setAttribute("height", H);
  const pw = W - m.l - m.r, ph = H - m.t - m.b;
  const t0 = rows[0].start_time, t1 = tipTime();
  const x = t => m.l + pw * (t - t0) / Math.max(1, t1 - t0);
  const vals = rows.map(r => r.difficulty);
  let y, yTickVals;
  if (S.scale === "log") {
    const lo = Math.log10(Math.min(...vals)), hi = Math.log10(Math.max(...vals));
    const pad = Math.max(0.05, (hi - lo) * 0.05);
    const dlo = lo - pad, dhi = hi + pad;
    y = v => m.t + ph * (1 - (Math.log10(v) - dlo) / (dhi - dlo));
    yTickVals = [];
    let step = Math.max(1, Math.ceil((dhi - dlo) / 6));
    for (let p = Math.ceil(dlo); p <= dhi; p += step) yTickVals.push(Math.pow(10, p));
  } else {
    const hi = Math.max(...vals) * 1.05;
    y = v => m.t + ph * (1 - v / hi);
    yTickVals = niceTicks(0, hi, 4);
  }
  for (const v of yTickVals) {
    el("line", { class: "grid", x1: m.l, x2: m.l + pw, y1: y(v), y2: y(v) }, svg);
    el("text", { x: m.l - 8, y: y(v) + 4, "text-anchor": "end" }, svg)
      .textContent = fmtCompact(v, 0);
  }
  drawXAxis(svg, t0, t1, x, Math.min(6, Math.max(2, Math.floor(pw / 90))), H - 8);
  // step-after path: difficulty holds constant across each epoch
  let d = "M" + x(rows[0].start_time) + " " + y(rows[0].difficulty);
  for (let i = 1; i < rows.length; i++) {
    d += "H" + x(rows[i].start_time) + "V" + y(rows[i].difficulty);
  }
  d += "H" + x(t1);
  el("path", { d, fill: "none", stroke: cssVar("--data-pos"),
    "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }, svg);
  // end marker: >=8px dot with a 2px surface ring, endpoint direct label
  const last = rows[rows.length - 1];
  const ex = x(t1), ey = y(last.difficulty);
  el("circle", { cx: ex, cy: ey, r: 4.5, fill: cssVar("--data-pos"),
    stroke: cssVar("--layer-01"), "stroke-width": 2 }, svg);
  const lbl = el("text", { class: "lbl", x: ex - 8, y: ey - 10, "text-anchor": "end" }, svg);
  lbl.textContent = fmtCompact(last.difficulty);
  // crosshair snaps to the nearest epoch boundary
  const cross = el("line", { class: "zero", y1: m.t, y2: m.t + ph, visibility: "hidden" }, svg);
  const hit = el("rect", { x: m.l, y: m.t, width: pw, height: ph, fill: "transparent" }, svg);
  hit.addEventListener("pointermove", ev => {
    const rect = svg.getBoundingClientRect(), px = ev.clientX - rect.left;
    let best = 0, bd = 1e18;
    for (let i = 0; i < rows.length; i++) {
      const dd = Math.abs(x(rows[i].start_time) - px);
      if (dd < bd) { bd = dd; best = i; }
    }
    const r = rows[best], cx = x(r.start_time);
    cross.setAttribute("x1", cx); cross.setAttribute("x2", cx);
    cross.setAttribute("visibility", "visible");
    showTip(tt, card, cx, ev.clientY - rect.top, box => {
      const h = document.createElement("div");
      h.textContent = "Epoch " + r.epoch + " · " + fmtDate(r.start_time) +
        (r.current ? " (in progress)" : "");
      box.appendChild(h);
      tipRow(box, cssVar("--data-pos"), "difficulty", fmtCompact(r.difficulty, 2));
      if (r.change != null) tipRow(box, null, "vs previous", fmtPct(r.change));
      if (r.hashrate != null) tipRow(box, null, "est. hashrate", fmtHash(r.hashrate));
    });
  });
  hit.addEventListener("pointerleave", () => {
    cross.setAttribute("visibility", "hidden"); tt.style.display = "none";
  });
}

// -- adjustment chart: diverging bars (blue up / orange down), per-bar tooltip --
function drawAdj() {
  const svg = document.getElementById("chart-adj"), card = svg.parentNode;
  const tt = document.getElementById("tt-adj");
  svg.textContent = "";
  const rows = filteredRows().filter(r => r.change != null);
  if (!rows.length) return;
  const W = card.clientWidth - 32, H = 260, m = { t: 20, r: 20, b: 30, l: 56 };
  svg.setAttribute("width", W); svg.setAttribute("height", H);
  const pw = W - m.l - m.r, ph = H - m.t - m.b;
  const lo = Math.min(0, ...rows.map(r => r.change)) * 1.15;
  const hi = Math.max(0, ...rows.map(r => r.change)) * 1.15;
  const y = v => m.t + ph * (hi - v) / (hi - lo || 1);
  const slot = pw / rows.length;
  const bw = Math.min(24, Math.max(2, slot - 2));   // <=24px thick, 2px surface gap
  for (const v of niceTicks(lo, hi, 5)) {
    el("line", { class: "grid", x1: m.l, x2: m.l + pw, y1: y(v), y2: y(v) }, svg);
    el("text", { x: m.l - 8, y: y(v) + 4, "text-anchor": "end" }, svg)
      .textContent = fmtPct(v, Math.abs(hi - lo) < 0.1 ? 1 : 0);
  }
  const t0 = rows[0].start_time, t1 = rows[rows.length - 1].start_time;
  const span = Math.max(1, t1 - t0);
  drawXAxis(svg, t0, t1, t => m.l + pw * (t - t0) / span,
    Math.min(6, Math.max(2, Math.floor(pw / 90))), H - 8);
  el("line", { class: "zero", x1: m.l, x2: m.l + pw, y1: y(0), y2: y(0) }, svg);
  // selective direct labels: extremes + the latest bar only
  const iMax = rows.reduce((a, r, i) => rows[a].change >= r.change ? a : i, 0);
  const iMin = rows.reduce((a, r, i) => rows[a].change <= r.change ? a : i, 0);
  const labeled = new Set([iMax, iMin, rows.length - 1]);
  rows.forEach((r, i) => {
    const cx = m.l + slot * (i + 0.5), x0 = cx - bw / 2;
    const yv = y(r.change), y0 = y(0);
    const up = r.change >= 0, hgt = Math.abs(y0 - yv), rad = Math.min(4, hgt, bw / 2);
    // 4px rounded data-end, square at the baseline
    let d;
    if (up) d = "M" + x0 + " " + y0 + "V" + (yv + rad) +
      "a" + rad + " " + rad + " 0 0 1 " + rad + " " + (-rad) + "h" + (bw - 2 * rad) +
      "a" + rad + " " + rad + " 0 0 1 " + rad + " " + rad + "V" + y0 + "Z";
    else d = "M" + x0 + " " + y0 + "V" + (yv - rad) +
      "a" + rad + " " + rad + " 0 0 0 " + rad + " " + rad + "h" + (bw - 2 * rad) +
      "a" + rad + " " + rad + " 0 0 0 " + rad + " " + (-rad) + "V" + y0 + "Z";
    const bar = el("path", { d, fill: cssVar(up ? "--data-pos" : "--data-neg") }, svg);
    if (labeled.has(i) && slot >= 18) {
      el("text", { class: "lbl", x: cx, y: up ? yv - 6 : yv + 14, "text-anchor": "middle" }, svg)
        .textContent = fmtPct(r.change, 1);
    }
    // hit target wider than the mark (>=24px)
    const hw = Math.max(24, slot);
    const hit = el("rect", { x: cx - hw / 2, y: m.t, width: hw, height: ph, fill: "transparent" }, svg);
    hit.addEventListener("pointermove", ev => {
      const rect = svg.getBoundingClientRect();
      bar.setAttribute("opacity", "0.8");
      showTip(tt, card, cx, ev.clientY - rect.top, box => {
        const h = document.createElement("div");
        h.textContent = "Epoch " + r.epoch + " · " + fmtDate(r.start_time);
        box.appendChild(h);
        tipRow(box, cssVar(up ? "--data-pos" : "--data-neg"), "adjustment", fmtPct(r.change));
        tipRow(box, null, "difficulty", fmtCompact(r.difficulty, 2));
      });
    });
    hit.addEventListener("pointerleave", () => {
      bar.removeAttribute("opacity"); tt.style.display = "none";
    });
  });
}

// -- table ---------------------------------------------------------------------
const COLS = [
  { key: "epoch", label: "Epoch", fmt: r => String(r.epoch) + (r.current ? " · now" : "") },
  { key: "start_height", label: "Start height", fmt: r => fmtInt(r.start_height) },
  { key: "start_time", label: "Start (UTC)", fmt: r => fmtDate(r.start_time) },
  { key: "end_time", label: "End (UTC)", fmt: r => r.current ? "in progress" : fmtDate(r.end_time) },
  { key: "duration", label: "Duration", fmt: r => fmtDur(durOf(r)) },
  { key: "avg_interval", label: "Avg block", fmt: r => fmtInterval(r.avg_interval) },
  { key: "difficulty", label: "Difficulty", fmt: r => fmtCompact(r.difficulty, 2) },
  { key: "change", label: "Change", fmt: null },   // rendered with a direction dot
  { key: "hashrate", label: "Est. hashrate", fmt: r => fmtHash(r.hashrate) },
  { key: "hashvalue", label: "Hashvalue", fmt: r => fmtCompact(r.hashvalue, 1) + " sats" },
];
function durOf(r) {
  return r.avg_interval != null && r.blocks ? r.avg_interval * r.blocks : null;
}
function sortVal(r, key) {
  if (key === "duration") return durOf(r);
  return r[key];
}
function renderTable() {
  const rows = filteredRows().slice().sort((a, b) => {
    const va = sortVal(a, S.sort.key), vb = sortVal(b, S.sort.key);
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    return (va < vb ? -1 : va > vb ? 1 : 0) * S.sort.dir;
  });
  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  S.page = Math.min(S.page, pages - 1);
  const slice = rows.slice(S.page * PAGE_SIZE, (S.page + 1) * PAGE_SIZE);

  const tr = document.querySelector("#table thead tr");
  tr.textContent = "";
  for (const c of COLS) {
    const th = document.createElement("th");
    th.textContent = c.label + " ";
    if (S.sort.key === c.key) {
      const a = document.createElement("span");
      a.className = "arrow";
      a.textContent = S.sort.dir > 0 ? "↑" : "↓";
      th.appendChild(a);
    }
    th.addEventListener("click", () => {
      if (S.sort.key === c.key) S.sort.dir *= -1;
      else S.sort = { key: c.key, dir: -1 };
      S.page = 0; renderTable();
    });
    tr.appendChild(th);
  }
  const tbody = document.querySelector("#table tbody");
  tbody.textContent = "";
  for (const r of slice) {
    const row = document.createElement("tr");
    for (const c of COLS) {
      const td = document.createElement("td");
      if (c.key === "change") {
        if (r.change == null) td.textContent = "—";
        else {
          const dot = document.createElement("span");
          dot.className = "dirdot " + (r.change >= 0 ? "up" : "down");
          td.appendChild(dot);
          td.appendChild(document.createTextNode(fmtPct(r.change)));
        }
      } else if (c.key === "difficulty") {
        td.className = "copyable";
        td.textContent = c.fmt(r);
        td.title = fmtFullInt(r.difficulty) + " — click to copy";
        td.addEventListener("click", () => copyText(String(Math.round(r.difficulty)), td));
      } else td.textContent = c.fmt(r);
      row.appendChild(td);
    }
    tbody.appendChild(row);
  }
  document.getElementById("t-count").textContent =
    rows.length + " epochs · page " + (S.page + 1) + " of " + pages;
  document.getElementById("pg-prev").disabled = S.page === 0;
  document.getElementById("pg-next").disabled = S.page >= pages - 1;
}

// -- wiring ---------------------------------------------------------------------
function render() { renderPill(); renderTiles(); renderCalendar(); drawDiff(); drawAdj(); renderTable(); }

const diffFull = document.getElementById("t-diff-full");
function copyCurrentDifficulty() {
  const s = S.summary;
  if (s && s.difficulty != null) copyText(String(Math.round(s.difficulty)), diffFull);
}
diffFull.addEventListener("click", copyCurrentDifficulty);
diffFull.addEventListener("keydown", ev => {
  if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); copyCurrentDifficulty(); }
});

document.getElementById("range").addEventListener("click", ev => {
  const b = ev.target.closest("button"); if (!b) return;
  S.range = b.dataset.r; S.page = 0;
  for (const x of ev.currentTarget.querySelectorAll("button"))
    x.setAttribute("aria-pressed", String(x === b));
  drawDiff(); drawAdj(); renderTable();
});
document.getElementById("scale").addEventListener("click", ev => {
  const b = ev.target.closest("button"); if (!b) return;
  S.scale = b.dataset.s;
  for (const x of ev.currentTarget.querySelectorAll("button"))
    x.setAttribute("aria-pressed", String(x === b));
  drawDiff();
});
document.getElementById("pg-prev").addEventListener("click", () => { S.page--; renderTable(); });
document.getElementById("pg-next").addEventListener("click", () => { S.page++; renderTable(); });
let rsz;
window.addEventListener("resize", () => { clearTimeout(rsz); rsz = setTimeout(() => { drawDiff(); drawAdj(); }, 150); });
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { drawDiff(); drawAdj(); });

fetchAll();
setInterval(fetchAll, 60000);
</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, body, ctype, cache="no-store"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        name = path.rsplit("/", 1)[-1]
        if "/fonts/" in path and name in FONTS:
            try:
                with open(os.path.join(FONTS_DIR, name), "rb") as f:
                    self._send(f.read(), "font/woff2", cache="public, max-age=604800")
            except OSError:
                self.send_error(404)
            return
        state = demo_state() if DEMO_MODE else snapshot()
        if path.endswith("/widgets/status"):
            self._send(json.dumps(widget_status(state, build_rows(state))).encode(),
                       "application/json")
            return
        if path.endswith("/api/epochs"):
            rows = build_rows(state)
            self._send(json.dumps({"rows": rows, "updated": state["updated"]}).encode(),
                       "application/json")
            return
        if path.endswith("/api/summary"):
            self._send(json.dumps(build_summary(state, build_rows(state))).encode(),
                       "application/json")
            return
        if path.endswith("/export.csv"):
            self.send_response(200)
            body = export_csv(build_rows(state))
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition",
                             'attachment; filename="bitcoin-difficulty-epochs.csv"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path.endswith("/export.json"):
            body = json.dumps(export_rows(build_rows(state)), indent=1).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Disposition",
                             'attachment; filename="bitcoin-difficulty-epochs.json"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        pkg = "Umbrel app %s &middot; " % PACKAGE_VERSION if PACKAGE_VERSION else ""
        page = PAGE.replace("__SYMBOL__", BRAIINS_SYMBOL).replace("__PKG__", pkg)
        self._send(page.encode(), "text/html; charset=utf-8")

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    if not DEMO_MODE:
        threading.Thread(target=sync_loop, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Handler).serve_forever()
