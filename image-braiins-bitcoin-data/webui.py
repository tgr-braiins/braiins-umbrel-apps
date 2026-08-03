"""Bitcoin Data — difficulty epoch analytics for Umbrel.

Serves on :8080 (fronted by Umbrel's app_proxy):
- GET  /                dashboard: summary tiles, calendar views, records,
                        difficulty + adjustment charts, projection, epoch table
                        with export; styled per Braiins CDS v11
- GET  /signalling      BIP-110 signalling page: window counts, strip chart,
                        recent signalling blocks
- GET  /api             human-readable API documentation
- GET  /api/summary     JSON polled by the page: node state + current-epoch stats
- GET  /api/epochs      JSON: one row per difficulty epoch (all history)
- GET  /api/signalling  JSON: BIP-110 bit-4 counts per window + last 288 blocks
- GET  /export.csv      epoch table as CSV
- GET  /export.json     epoch table as JSON
- GET  /widgets/status  Umbrel home-screen widget (three-stats)
- GET  /widgets/halving Umbrel home-screen widget (text-with-progress)

Data comes from the user's own Bitcoin node (the official `bitcoin` Umbrel app)
over JSON-RPC. Difficulty only changes every 2016 blocks, so the full history is
one header per epoch boundary (~460 as of 2026): a one-time backfill, cached in
/data/epochs.json, then a 30 s tip poll that appends a row per retarget.
Per-block fees for the CURRENT epoch only come from getblockstats (recent
blocks, so pruned nodes are fine) and feed the fee-aware hashvalue.

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

# BIP-110 signals version bit 4 (BIP9-style: top bits 001), lock-in at 55% of
# a 2,016-block retarget period = 1,109 blocks. Bit overridable for future BIPs.
SIGNAL_BIT = int(os.environ.get("SIGNAL_BIT", "4"))
SIGNAL_THRESHOLD = 1109
SIGNAL_WINDOWS = (18, 36, 72, 144, 288)


def signals(version):
    return (version >> 29) == 1 and (version >> SIGNAL_BIT) & 1 == 1


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
    "fees": {},             # height -> totalfee sats, current epoch only
    "versions": {},         # height -> (time, version), last 2016 blocks
}


def load_cache():
    try:
        with open(CACHE) as f:
            data = json.load(f)
        fees = {int(k): v for k, v in data.get("fees", {}).items()}
        versions = {int(k): tuple(v) for k, v in data.get("versions", {}).items()}
        return data.get("chain"), [tuple(b) for b in data.get("boundaries", [])], fees, versions
    except (OSError, ValueError):
        return None, [], {}, {}


def save_cache(chain, boundaries, fees, versions):
    tmp = CACHE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"chain": chain, "boundaries": boundaries, "fees": fees,
                   "versions": versions}, f)
    os.replace(tmp, CACHE)


def sync_fees(fees, tip_height):
    """Fill height->totalfee (sats) for the current epoch; ~2016 getblockstats
    calls worst case on first run, spread over poll cycles (<=400 per cycle).
    Recent blocks only, so pruned nodes are fine."""
    epoch_start = tip_height // EPOCH_BLOCKS * EPOCH_BLOCKS
    for h in list(fees):
        if h < epoch_start or h > tip_height:
            del fees[h]
    fetched = 0
    for h in range(max(1, epoch_start), tip_height + 1):
        if h in fees:
            continue
        try:
            fees[h] = rpc("getblockstats", [h, ["totalfee"]])["totalfee"]
        except RpcError:
            break  # node busy/pruned beyond reach: retry next cycle
        fetched += 1
        if fetched >= 400:
            break
    return fetched


def sync_versions(versions, tip_height):
    """Fill height->(time, version) for the last 2,016 blocks, newest first so
    the short signalling windows are meaningful within one poll cycle.
    Two RPC calls per block, <=200 blocks per cycle."""
    floor = tip_height - EPOCH_BLOCKS + 1
    for h in list(versions):
        if h < floor or h > tip_height:
            del versions[h]
    fetched = 0
    for h in range(tip_height, max(0, floor) - 1, -1):
        if h in versions:
            continue
        try:
            hdr = rpc("getblockheader", [rpc("getblockhash", [h])])
        except RpcError:
            break
        versions[h] = (hdr["time"], hdr["version"])
        fetched += 1
        if fetched >= 200:
            break
    return fetched


def sync_loop():
    chain, boundaries, fees, versions = load_cache()
    with LOCK:
        STATE.update(chain=chain, boundaries=boundaries, fees=dict(fees),
                     versions=dict(versions))
    while True:
        try:
            ci = rpc("getblockchaininfo")
            if ci["chain"] != chain:
                chain, boundaries, fees, versions = ci["chain"], [], {}, {}
            tip_height = ci["blocks"]
            want = tip_height // EPOCH_BLOCKS + 1  # boundaries 0..want-1
            while len(boundaries) < want:
                h = len(boundaries) * EPOCH_BLOCKS
                hdr = rpc("getblockheader", [rpc("getblockhash", [h])])
                boundaries.append((h, hdr["time"], hdr["difficulty"]))
                if len(boundaries) % 100 == 0 or len(boundaries) == want:
                    save_cache(chain, boundaries, fees, versions)
                with LOCK:
                    STATE.update(chain=chain, boundaries=list(boundaries),
                                 backfill=(len(boundaries), want))
            tip_hdr = rpc("getblockheader", [ci["bestblockhash"]])
            changed = sync_fees(fees, tip_height)
            changed += sync_versions(versions, tip_height)
            if changed:
                save_cache(chain, boundaries, fees, versions)
            with LOCK:
                STATE.update(chain=chain, boundaries=list(boundaries),
                             tip=(tip_height, tip_hdr["time"]),
                             updated=time.time(), error=None,
                             ibd=ci.get("initialblockdownload", False),
                             verification=ci.get("verificationprogress", 1.0),
                             backfill=None, fees=dict(fees),
                             versions=dict(versions))
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
    # synthetic fees ~2-4% of the block reward, current epoch only
    reward_sats = subsidy_btc(tip_height) * 1e8
    fees = {h: int(reward_sats * (0.03 + 0.01 * math.sin(h / 37.0)))
            for h in range((n - 1) * EPOCH_BLOCKS, tip_height + 1)}
    # synthetic BIP-110 signalling: ~9% of the last 2,016 blocks, clustered
    versions = {}
    for i, h in enumerate(range(tip_height - EPOCH_BLOCKS + 1, tip_height + 1)):
        sig = (h * 2654435761) % 100 < 9
        versions[h] = (now - 300 - (tip_height - h) * 580,
                       0x20000000 | (1 << SIGNAL_BIT if sig else 0))
    return {
        "chain": "main", "boundaries": boundaries,
        "tip": (tip_height, now - 300), "updated": time.time(),
        "error": None, "ibd": False, "verification": 1.0, "backfill": None,
        "fees": fees, "versions": versions, "demo": True,
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
    # halving countdown (era boundaries are multiples of 210,000 blocks)
    halving_height = (tip[0] // HALVING_BLOCKS + 1) * HALVING_BLOCKS
    s.update({
        "halving_height": halving_height,
        "halving_blocks": halving_height - tip[0],
        "halving_eta": tip[1] + (halving_height - tip[0]) * (avg or TARGET_INTERVAL),
        "next_subsidy": subsidy_btc(halving_height),
        "era_progress": (tip[0] % HALVING_BLOCKS) / HALVING_BLOCKS,
    })
    # fee-aware hashvalue from current-epoch getblockstats (sats per block)
    fee_vals = list(state.get("fees", {}).values())
    if len(fee_vals) >= 10:
        avg_fee = sum(fee_vals) / len(fee_vals)
        fees_pct = avg_fee / (s["subsidy"] * 1e8)
        s.update({
            "avg_fee_sats": avg_fee,
            "fees_pct_of_reward": fees_pct,
            "hashvalue_with_fees": s["hashvalue"] * (1 + fees_pct),
            "fee_blocks": len(fee_vals),
        })
    closed = [r for r in rows if r["change"] is not None and not r["current"]]
    if closed:
        up = max(closed, key=lambda r: r["change"])
        down = min(closed, key=lambda r: r["change"])
        s["max_up"] = {"epoch": up["epoch"], "change": up["change"]}
        s["max_down"] = {"epoch": down["epoch"], "change": down["change"]}
    return s


def build_signalling(state):
    """BIP-110 view: bit-4 counts over short windows + the official
    current-retarget-period tally, plus per-block data for the strip chart."""
    out = {"bit": SIGNAL_BIT, "threshold_blocks": SIGNAL_THRESHOLD,
           "period_blocks": EPOCH_BLOCKS, "ready": False}
    tip = state["tip"]
    versions = state.get("versions", {})
    if not tip or not versions:
        return out
    tip_h = tip[0]
    windows = {}
    for w in SIGNAL_WINDOWS:
        have = [versions[h] for h in range(tip_h - w + 1, tip_h + 1) if h in versions]
        windows[str(w)] = {"have": len(have),
                           "signalling": sum(1 for v in have if signals(v[1]))}
    epoch_start = tip_h // EPOCH_BLOCKS * EPOCH_BLOCKS
    ehave = [versions[h] for h in range(epoch_start, tip_h + 1) if h in versions]
    blocks = [{"height": h, "time": versions[h][0], "signal": bool(signals(versions[h][1]))}
              for h in sorted(versions) if h > tip_h - 288]
    out.update(ready=True, tip_height=tip_h, windows=windows, blocks=blocks,
               epoch={"start": epoch_start, "elapsed": tip_h - epoch_start + 1,
                      "have": len(ehave),
                      "signalling": sum(1 for v in ehave if signals(v[1]))},
               backfilled=len(versions))
    return out


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


def widget_halving(state, rows):
    s = build_summary(state, rows)
    if "halving_blocks" not in s:
        return {"type": "text-with-progress", "refresh": "1h", "link": "",
                "title": "Halving countdown", "text": "—", "subtext": "",
                "progressLabel": "", "progress": 0}
    eta = time.strftime("%b %Y", time.gmtime(s["halving_eta"]))
    return {
        "type": "text-with-progress",
        "refresh": "1h",
        "link": "",
        "title": "Halving countdown",
        "text": "~ " + eta,
        "subtext": "%s blocks · subsidy %s → %s BTC" % (
            fmt_compact(s["halving_blocks"], 0), s["subsidy"], s["next_subsidy"]),
        "progressLabel": "Era %.1f%%" % (s["era_progress"] * 100),
        "progress": round(s["era_progress"], 4),
    }


# ---------------------------------------------------------------------------
# API docs: everything the dashboard shows is scriptable from the same origin.

API_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bitcoin Data — API</title>
<style>
:root { --bg: #fff; --fg: #161616; --dim: #525252; --line: #e0e0e0; --layer: #f4f4f4; --link: #0f62fe; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #262626; --fg: #f4f4f4; --dim: #c6c6c6; --line: #525252; --layer: #393939; --link: #78a9ff; }
}
body { margin: 0 auto; max-width: 46rem; padding: 2rem 1rem 4rem; background: var(--bg); color: var(--fg);
  font: 400 14px/1.5 -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif; }
h1 { font-size: 24px; font-weight: 400; } h2 { font-size: 16px; margin-top: 2rem; }
a { color: var(--link); }
code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; }
pre { background: var(--layer); padding: .75rem; overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
td, th { text-align: left; padding: .4rem .6rem; border-bottom: 1px solid var(--line); vertical-align: top; }
.dim { color: var(--dim); }
</style></head><body>
<h1>Bitcoin Data &mdash; API</h1>
<p class="dim">Same-origin JSON endpoints behind the dashboard. All data derives from your own
Bitcoin node; timestamps are unix seconds UTC. From another machine, use the app URL
(<code>http://umbrel.local:4549</code>).</p>
<h2>Endpoints</h2>
<table>
<tr><th>Path</th><th>Returns</th></tr>
<tr><td><code>GET <a href="api/summary">/api/summary</a></code></td><td>Node status and current-epoch stats:
<code>status</code> (ok&middot;waiting&middot;backfill&middot;ibd&middot;error&middot;demo), <code>tip_height</code>, <code>difficulty</code>,
<code>epoch</code>, <code>progress</code> 0&ndash;1, <code>projected_change</code>, <code>eta</code>,
<code>hashrate</code> H/s, <code>hashvalue</code> and <code>hashvalue_with_fees</code> (sats/day for 1 PH/s),
<code>avg_fee_sats</code> and <code>fees_pct_of_reward</code> (current-epoch average),
<code>subsidy</code> BTC, <code>halving_height</code>, <code>halving_blocks</code>, <code>halving_eta</code>,
<code>next_subsidy</code>, <code>era_progress</code>, <code>max_up</code>/<code>max_down</code> record adjustments.</td></tr>
<tr><td><code>GET <a href="api/epochs">/api/epochs</a></code></td><td><code>{rows: [&hellip;]}</code>, one row per difficulty
epoch since genesis: <code>epoch</code>, <code>start_height</code>, <code>start_time</code>, <code>end_time</code>,
<code>difficulty</code>, <code>change</code> (fraction vs previous), <code>blocks</code>, <code>avg_interval</code> s,
<code>hashrate</code> H/s, <code>hashvalue</code> sats/PH&middot;day, <code>current</code>. The last row is in progress.</td></tr>
<tr><td><code>GET <a href="api/signalling">/api/signalling</a></code></td><td>BIP-110 (version bit 4) signalling:
per-window counts (<code>windows</code>, last 18/36/72/144/288 blocks), the official current-retarget-period tally
(<code>epoch</code>, threshold 1,109 of 2,016), and per-block <code>blocks</code> for the last 288
(<code>height</code>, <code>time</code>, <code>signal</code>).</td></tr>
<tr><td><code>GET <a href="export.csv">/export.csv</a></code></td><td>Epoch table as CSV (full history, formatted timestamps).</td></tr>
<tr><td><code>GET <a href="export.json">/export.json</a></code></td><td>Same rows as the CSV, as a JSON array.</td></tr>
<tr><td><code>GET <a href="widgets/status">/widgets/status</a></code></td><td>umbrelOS three-stats widget payload.</td></tr>
<tr><td><code>GET <a href="widgets/halving">/widgets/halving</a></code></td><td>umbrelOS text-with-progress widget payload.</td></tr>
</table>
<h2>Example</h2>
<pre>curl -s http://umbrel.local:4549/api/summary | jq .difficulty
curl -s http://umbrel.local:4549/api/epochs | jq '.rows[-1]'</pre>
<p class="dim">Hashvalue = expected earnings of 1 PH/s in sats/day; the per-epoch value and exports
use the block subsidy only, <code>hashvalue_with_fees</code> adds the current epoch's observed average fees.
No authentication &mdash; the app is only reachable on your Umbrel's network. <a href="./">&larr; back to dashboard</a></p>
</body></html>"""

# ---------------------------------------------------------------------------
# Page

BRAIINS_SYMBOL = (
    '<svg viewBox="0 0 864 864" width="14" height="14" aria-hidden="true">'
    '<polygon points="345.6 864 345.6 682.8 194.4 179.9 194.4 0 0 0 0 179.9 151.2 682.8 151.2 864 345.6 864" fill="#fff"/>'
    '<polygon points="864 864 864 682.8 712.8 179.9 712.8 0 518.4 0 518.4 179.9 669.6 682.8 669.6 864 864 864" fill="#fff"/>'
    "</svg>"
)

FAVICON = "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1024 1024'><rect width='1024' height='1024' rx='230' fill='%236B50FF'/><path d='M232 704 L392 512 L392 608 L552 384 L552 480 L712 288 L792 288' stroke='%23fff' stroke-width='56' fill='none' stroke-linecap='square'/></svg>"

CSS = """<style>
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
html { background: var(--background); scroll-behavior: smooth; }
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

/* Sticky in-page navigation (Carbon tabs on background) */
.pagenav { position: sticky; top: 0; z-index: 5; background: var(--background);
  display: flex; overflow-x: auto; border-bottom: 1px solid var(--border-subtle);
  margin-bottom: var(--spacing-06); }
.pagenav a { padding: var(--spacing-04) var(--spacing-05); white-space: nowrap;
  font: 400 14px/1 var(--font-brand); letter-spacing: .16px; color: var(--text-secondary);
  text-decoration: none; border-bottom: 2px solid transparent; margin-bottom: -1px; }
.pagenav a:hover { color: var(--text-primary); background: var(--layer-hover); }
.pagenav a.active { color: var(--text-primary); border-bottom-color: var(--violet-60); font-weight: 700; }
main section { scroll-margin-top: 52px; }

/* Stat tiles: flat layer fill, sharp corners, no shadow */
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(168px, 1fr)); gap: 1px; margin-bottom: var(--spacing-06); }
@media (max-width: 480px) { .tiles { grid-template-columns: 1fr; } }
.tile { background: var(--layer-01); padding: var(--spacing-05); min-height: 120px; }
.tile .label { font: 400 12px/1.3333 var(--font-sans); letter-spacing: .32px; color: var(--text-secondary); }
/* tooltip affordance: dotted underline + help cursor on anything with a title */
.info { text-decoration: underline dotted var(--border-strong); text-underline-offset: 3px; cursor: help; }
h3 .info { text-decoration-color: var(--border-strong); }
/* record-participation callout banner */
.highlights { display: none; flex-direction: column; gap: var(--spacing-02);
  background: var(--layer-01); border-left: 3px solid var(--violet-60);
  padding: var(--spacing-04) var(--spacing-05); margin-bottom: var(--spacing-06); }
.highlights.on { display: flex; }
.highlights .hl { font: 400 13px/1.4 var(--font-sans); letter-spacing: .16px; color: var(--text-primary); }
.highlights .hl b { font-weight: 700; }
.highlights .rank { color: var(--violet-60); font-weight: 700; }
.tile .value { font: 700 24px/1.29 var(--font-sans); letter-spacing: 0; margin: var(--spacing-03) 0 var(--spacing-02);
  white-space: nowrap; }
.tile .value small { font-size: 14px; font-weight: 400; color: var(--text-secondary); margin-left: .15em; white-space: nowrap; }
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
/* Range row sticks below the section nav so it stays reachable across the
   several charts it scopes (difficulty, adjustment, distribution). */
.filters { display: flex; align-items: center; gap: var(--spacing-05);
  position: sticky; top: 44px; z-index: 4; background: var(--background);
  padding: var(--spacing-03) 0; margin-bottom: var(--spacing-05);
  border-bottom: 1px solid var(--border-subtle); }
.filters .flabel { font: 400 12px/1.3333 var(--font-sans); letter-spacing: .32px; color: var(--text-secondary); }
.switcher { display: inline-flex; background: var(--layer-01); padding: 0; }
.switcher button { appearance: none; border: 0; border-radius: 0; cursor: pointer;
  height: 32px; padding: 0 var(--spacing-05); background: transparent;
  font: 400 14px/1 var(--font-brand); letter-spacing: .16px; color: var(--text-secondary);
  transition: background var(--duration-fast-02) var(--ease-productive); }
.switcher button:hover { background: var(--layer-hover); }
.switcher button:focus-visible { outline: 2px solid var(--focus); outline-offset: -2px; }
.switcher button[aria-pressed="true"] { background: var(--gray-100); color: #fff; }
.scopesep { width: 1px; align-self: stretch; background: var(--border-strong); margin: 0 var(--spacing-03); }

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
.chartlegend { display: flex; flex-wrap: wrap; gap: var(--spacing-03) var(--spacing-05);
  margin-top: var(--spacing-03); font: 400 12px/1.3333 var(--font-sans); letter-spacing: .32px;
  color: var(--text-secondary); }
.chartlegend span { display: inline-flex; align-items: center; gap: 6px; }
.chartlegend i { width: 12px; height: 3px; border-radius: 2px; display: inline-block; }
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

/* inline stat strip (CAGR row in By year) */
.statrow { display: flex; flex-wrap: wrap; gap: var(--spacing-06); margin-bottom: var(--spacing-05);
  font: 400 12px/1.3333 var(--font-sans); letter-spacing: .32px; color: var(--text-secondary); }
.statrow b { display: block; font: 700 16px/1.375 var(--font-sans); letter-spacing: 0;
  color: var(--text-primary); font-variant-numeric: tabular-nums; }

/* records: two ranked lists side by side */
.recgrid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: var(--spacing-06); }
/* run-record tables: 2×2 so the date range column isn't clipped */
.rungrid { display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); gap: var(--spacing-06); }

/* calendar: year rows x month columns, diverging tint behind text-token values */
.cal td, .cal th { height: 36px; padding: 0 var(--spacing-04); }
.cal td { font: 400 12px/1.3333 var(--font-sans); letter-spacing: .32px; }
.cal td.empty { color: var(--text-helper); background: var(--table-row); }
/* seasonal average rows sit above the per-year grid, set off by a divider */
.cal tr.seasonavg td { background: var(--layer-hover); }
.cal tr.seasonavg + tr:not(.seasonavg) td { border-top: 2px solid var(--border-strong); }
.note { font: 400 12px/1.3333 var(--font-sans); letter-spacing: .32px; color: var(--text-helper);
  margin: var(--spacing-03) 0 0; }
footer { margin-top: var(--spacing-07); font: 400 12px/1.3333 var(--font-sans);
  letter-spacing: .32px; color: var(--text-helper); text-align: center; }

/* signalling strip: one mark per block, colored when the bit is set */
.strip rect.on { fill: var(--data-pos); }
.strip rect.off { fill: var(--chart-axis); }
</style>"""

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bitcoin Data</title>
<link rel="icon" href="__FAVICON__">
__CSS__</head><body>
<header class="shell">
  <div class="mark">__SYMBOL__</div>
  <span class="name">Bitcoin Data<small>on Umbrel</small></span>
  <nav><a class="active" href="./">Difficulty</a><a href="signalling">Signalling</a><a href="api">API</a></nav>
</header>
<main>
  <div class="titlerow">
    <h2>Difficulty</h2>
    <span id="pill"><span class="dot"></span><span id="pill-text">Connecting&hellip;</span></span>
  </div>

  <nav class="pagenav" id="pagenav" aria-label="Sections">
    <a href="#overview" class="active">Overview</a>
    <a href="#annual">Annual</a>
    <a href="#monthly">Monthly</a>
    <a href="#records">Records</a>
    <a href="#charts">Charts</a>
    <a href="#projection">Projection</a>
    <a href="#epochs">Epochs</a>
  </nav>

  <section id="overview">
  <div class="highlights" id="highlights"></div>
  <div class="tiles">
    <div class="tile"><div class="label info" title="Network difficulty — how hard it is to find a block, as a multiple of the easiest possible target. It retargets every 2,016 blocks (~2 weeks) to keep blocks near 10 minutes.">Difficulty</div>
      <div class="value" id="t-diff">—</div>
      <div class="delta" id="t-diff-delta"></div>
      <span class="copyline" id="t-diff-full" role="button" tabindex="0"
        title="Click to copy the exact value"></span>
      <div class="sub" id="t-diff-sub"></div></div>
    <div class="tile"><div class="label info" title="Projected difficulty change at the next retarget, extrapolated from this epoch's average block time. Only meaningful after ~10 blocks; a single retarget is capped at +300% / −75%.">Next adjustment</div>
      <div class="value" id="t-proj">—</div>
      <div class="sub" id="t-proj-sub"></div></div>
    <div class="tile"><div class="label info" title="How far into the current 2,016-block retarget period the chain is.">Epoch progress</div>
      <div class="value" id="t-prog">—</div>
      <div class="meter"><i id="t-prog-bar" style="width:0%"></i></div>
      <div class="sub" id="t-prog-sub"></div></div>
    <div class="tile"><div class="label info" title="Estimated network hashrate = difficulty × 2³² ÷ average block time this epoch. It is an estimate — true hashrate cannot be observed directly, only inferred from how fast blocks arrive.">Est. network hashrate</div>
      <div class="value" id="t-hash">—</div>
      <div class="sub" id="t-hash-sub">current epoch average</div></div>
    <div class="tile"><div class="label info" title="Expected earnings of 1 PH/s per day at current difficulty: the block subsidy plus this epoch's average fees. Historical and per-epoch table values are subsidy-only — past per-block fees aren't retained.">Hashvalue (1 PH/s)</div>
      <div class="value" id="t-hv">—<small>sats/day</small></div>
      <div class="sub" id="t-hv-sub"></div></div>
    <div class="tile"><div class="label info" title="Blocks until the next block-subsidy halving (every 210,000 blocks, ~4 years) and its projected date. The subsidy is the newly-issued BTC paid to the miner of each block.">Halving countdown</div>
      <div class="value" id="t-halv">—<small>blocks</small></div>
      <div class="sub" id="t-halv-sub"></div></div>
  </div>
  </section>

  <section id="annual">
  <div class="card">
    <div class="cardhead"><h3><span class="info" title="Difficulty in effect at 00:00 UTC on Jan 1 of each year, the implied hashrate at that difficulty, and the change across the year (year-to-date for the current year).">By year</span></h3></div>
    <div class="statrow" id="growth"></div>
    <div class="tablewrap"><table id="years">
      <thead><tr><th>Year</th><th>Difficulty on Jan 1</th><th>Exact value</th><th><span class="info" title="Hashrate implied by that Jan-1 difficulty at the 10-minute target (difficulty × 2³² ÷ 600).">Est. hashrate</span></th><th>Change over year</th></tr></thead>
      <tbody></tbody>
    </table></div>
    <p class="note">Difficulty in effect at 00:00 UTC on Jan 1; hashrate implied at the 10-minute target. The current year shows year-to-date. Click an exact value to copy it.</p>
  </div>
  </section>

  <section id="monthly">
  <div class="card">
    <div class="cardhead"><h3><span class="info" title="Difficulty change within each calendar month — from its first to its last moment (UTC). The top two rows average each month across the last 3 and 5 completed years (an incomplete current month is skipped).">Monthly change</span></h3>
      <span class="key"><span><i class="pos"></i>Increase</span><span><i class="neg"></i>Decrease</span></span></div>
    <div class="tablewrap"><table id="months" class="cal">
      <thead><tr></tr></thead><tbody></tbody>
    </table></div>
    <p class="note">Difficulty change from the first to the last moment of each month (UTC). * = month to date.</p>
  </div>
  </section>

  <section id="records">
  <div class="card">
    <div class="cardhead"><h3><span class="info" title="All-time extremes in the difficulty series: the largest single adjustments, the longest and largest consecutive runs, and the longest stretches below a prior all-time high.">Records</span></h3></div>
    <div class="recgrid">
      <div class="tablewrap"><table id="rec-up">
        <thead><tr><th>Largest increases</th><th>Epoch</th><th>Date</th></tr></thead><tbody></tbody>
      </table></div>
      <div class="tablewrap"><table id="rec-down">
        <thead><tr><th>Largest decreases</th><th>Epoch</th><th>Date</th></tr></thead><tbody></tbody>
      </table></div>
    </div>
    <p class="note" id="streaks"></p>
    <div class="rungrid" style="margin-top: var(--spacing-05)">
      <div class="tablewrap"><table id="run-linc">
        <thead><tr><th>Longest increases</th><th>Total change</th><th>Period</th></tr></thead><tbody></tbody>
      </table></div>
      <div class="tablewrap"><table id="run-binc">
        <thead><tr><th>Biggest increases</th><th>Epochs</th><th>Period</th></tr></thead><tbody></tbody>
      </table></div>
      <div class="tablewrap"><table id="run-ldec">
        <thead><tr><th>Longest decreases</th><th>Total change</th><th>Period</th></tr></thead><tbody></tbody>
      </table></div>
      <div class="tablewrap"><table id="run-ddec">
        <thead><tr><th>Deepest decreases</th><th>Epochs</th><th>Period</th></tr></thead><tbody></tbody>
      </table></div>
    </div>
    <p class="note">Runs of consecutive up- or down-adjustments (top 5 each). Total change compounds every step, so the longest run and the biggest/deepest run can differ.</p>
    <div class="tablewrap" style="margin-top: var(--spacing-05)"><table id="drawdowns">
      <thead><tr><th><span class="info" title="Stretches where difficulty stayed below a previous all-time high before setting a new one.">Longest without a new ATH</span></th><th>From</th><th>Until</th><th><span class="info" title="The deepest the difficulty fell below the prior ATH during that stretch.">Max drawdown</span></th></tr></thead>
      <tbody></tbody>
    </table></div>
    <p class="note">Stretches where difficulty stayed below its previous all-time high; drawdown is the deepest dip within the stretch.</p>
  </div>
  </section>

  <section id="charts">
  <div class="filters">
    <span class="flabel">Scope</span>
    <span class="switcher" id="range" role="group" aria-label="Scope: time window or halving era">
      <button data-r="all" aria-pressed="true">All</button>
      <button data-r="4y" aria-pressed="false">4y</button>
      <button data-r="3y" aria-pressed="false">3y</button>
      <button data-r="2y" aria-pressed="false">2y</button>
      <button data-r="1y" aria-pressed="false">1y</button>
      <button data-r="90d" aria-pressed="false">90d</button>
    </span>
  </div>

  <div class="card">
    <div class="cardhead"><h3><span class="info" title="Difficulty at each retarget over the selected range. Log scale makes multi-year exponential growth read as a straight line.">Difficulty over time</span></h3>
      <span><span class="switcher" id="scale" role="group" aria-label="Y scale">
        <button data-s="linear" aria-pressed="true">Linear</button>
        <button data-s="log" aria-pressed="false">Log</button>
      </span><span class="switcher" id="ribbon" role="group" aria-label="Difficulty ribbon"
        title="Difficulty Ribbon: a fan of moving averages of difficulty. When the fast (light) averages compress into or below the slow (dark) ones, hashrate is falling — historically a miner-capitulation signal.">
        <button data-rib="off" aria-pressed="true">Line</button>
        <button data-rib="on" aria-pressed="false">Ribbon</button>
      </span><button class="ghost" id="dl-diff" title="Download chart as PNG">PNG</button></span></div>
    <svg class="chart" id="chart-diff" height="300" role="img" aria-label="Difficulty over time"></svg>
    <div class="tooltip" id="tt-diff"></div>
  </div>

  <div class="card">
    <div class="cardhead"><h3><span class="info" title="The percentage difficulty change at each 2,016-block retarget — positive when blocks came faster than 10 min, negative when slower.">Adjustment per epoch</span></h3>
      <span><span class="key"><span><i class="pos"></i>Increase</span><span><i class="neg"></i>Decrease</span></span><button class="ghost" id="dl-adj" title="Download chart as PNG">PNG</button></span></div>
    <svg class="chart" id="chart-adj" height="260" role="img" aria-label="Difficulty adjustment per epoch"></svg>
    <div class="tooltip" id="tt-adj"></div>
  </div>

  <div class="card">
    <div class="cardhead"><h3><span class="info" title="For each year, difficulty's cumulative % change since Jan 1, plotted against day-of-year. Overlaying years shows how growth has differed across halving eras — recent (post-halving) years tend to climb faster.">Cumulative change by year</span></h3></div>
    <svg class="chart" id="chart-ytd" height="300" role="img" aria-label="Cumulative difficulty change since Jan 1, by year"></svg>
    <div class="chartlegend" id="ytd-legend"></div>
    <div class="tooltip" id="tt-ytd"></div>
  </div>

  <div class="card">
    <div class="cardhead"><h3><span class="info" title="Distribution of difficulty adjustment sizes as a density curve (each line's area sums to 100% of its group). Group by halving era or year to compare shapes; the Range filter above scopes which epochs are included.">Adjustment distribution</span></h3>
      <span class="switcher" id="histgroup" role="group" aria-label="Histogram grouping">
        <button data-g="all" aria-pressed="true">All</button>
        <button data-g="era" aria-pressed="false">By halving era</button>
        <button data-g="year" aria-pressed="false">By year</button>
      </span></div>
    <svg class="chart" id="chart-hist" height="280" role="img" aria-label="Distribution of difficulty adjustments"></svg>
    <div class="chartlegend" id="hist-legend"></div>
    <div class="tooltip" id="tt-hist"></div>
  </div>
  </section>

  <section id="projection">
  <div class="card">
    <div class="cardhead"><h3><span class="info" title="Compound extrapolation of a trailing growth window to future dates — a what-if, not a forecast. Hashvalue accounts for the halving schedule.">Projection</span></h3>
      <span class="switcher" id="basis" role="group" aria-label="Growth basis">
        <button data-b="91" aria-pressed="false">3m trend</button>
        <button data-b="182" aria-pressed="false">6m trend</button>
        <button data-b="365" aria-pressed="true">1y trend</button>
        <button data-b="730" aria-pressed="false">2y trend</button>
      </span></div>
    <div class="tablewrap"><table id="proj">
      <thead><tr><th>Horizon</th><th>Date</th><th>Difficulty</th><th>vs today</th><th>Hashvalue (1 PH/s)</th></tr></thead>
      <tbody></tbody>
    </table></div>
    <p class="note">Compound extrapolation of the trailing growth window &mdash; not a forecast.
      Hashvalue is subsidy-only and accounts for the halving schedule.</p>
  </div>
  </section>

  <section id="epochs">
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
  </section>

  <footer>__PKG__Data from your Bitcoin node &middot; difficulty retargets every 2,016 blocks &middot; <a href="api">API</a></footer>
</main>
<script>
"use strict";
const S = { rows: [], summary: null, range: "all", scale: "linear", ribbon: false,
            sort: { key: "epoch", dir: -1 }, page: 0, basis: 365, histgroup: "all" };
// categorical palette for year / era series (CVD-aware, distinct in both themes)
const CAT_COL = ["--blue-60", "--orange-50", "--teal-60", "--purple-60",
                 "--green-50", "--yellow-30", "--red-60", "--blue-40", "--violet-70"];
// Difficulty-ribbon moving-average windows, in epochs (~2 weeks each): ~2 months
// to ~2.3 years. Light→dark = fast→slow.
const RIBBON_WIN = [2, 4, 7, 11, 17, 26, 40, 60];
const RIBBON_COL = ["--blue-20", "--blue-30", "--blue-40", "--blue-50",
                    "--blue-60", "--blue-70", "--blue-80", "--violet-70"];
const PAGE_SIZE = 25;
const RANGE_S = { "4y": 4 * 365.25 * 86400, "3y": 3 * 365.25 * 86400,
                  "2y": 2 * 365.25 * 86400, "1y": 365.25 * 86400, "90d": 90 * 86400 };

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
function fmtDateTime(ts) {
  return ts == null ? "—" : new Date(ts * 1000).toISOString().slice(0, 16).replace("T", " ") + " UTC";
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
// The epoch history (~470 rows) changes only every ~2 weeks, so cache it in
// localStorage and paint from it instantly on load; then refresh in the
// background. Removes the blank-page lag while the full history round-trips.
const CACHE_KEY = "bd-epochs-v1";
function hydrate() {
  try {
    const c = JSON.parse(localStorage.getItem(CACHE_KEY) || "null");
    if (c && c.rows && c.rows.length) { S.rows = c.rows; render(); }
  } catch (e) { /* ignore corrupt cache */ }
}
async function fetchAll() {
  try {
    const sr = await fetch("api/summary", { cache: "no-store" });
    S.summary = await sr.json(); render();               // tiles first — small + fast
    const er = await fetch("api/epochs", { cache: "no-store" });
    S.rows = (await er.json()).rows;
    try { localStorage.setItem(CACHE_KEY, JSON.stringify({ rows: S.rows, t: Date.now() })); } catch (e) { }
    render();
  } catch (e) { S.summary = { status: "error", message: "UI unreachable" }; render(); }
}

function tipTime() {
  return (S.summary && S.summary.tip_time) ||
         (S.rows.length ? S.rows[S.rows.length - 1].start_time : 0);
}
function eraSubsidy(r) { return 50 / Math.pow(2, Math.floor(r.start_height / 210000)); }
function filteredRows() {
  if (S.range === "all") return S.rows;
  if (S.range.slice(0, 4) === "era:") {              // scope = one halving era
    const subs = parseFloat(S.range.slice(4));
    return S.rows.filter(r => Math.abs(eraSubsidy(r) - subs) < 1e-9);
  }
  const cutoff = tipTime() - RANGE_S[S.range];         // scope = trailing window
  return S.rows.filter(r => (r.end_time || tipTime()) >= cutoff);
}
// Append one chip per halving era present in the data, into the scope switcher
// (mutually exclusive with the time chips via the shared group handler). Built
// once, since eras only appear as the chain advances.
let eraButtonsBuilt = false;
function buildEraButtons() {
  if (eraButtonsBuilt || !S.rows.length) return;
  eraButtonsBuilt = true;
  const grp = document.getElementById("range");
  const eras = [...new Set(S.rows.map(eraSubsidy))].sort((a, b) => b - a);  // 50→…→3.125
  const sep = document.createElement("span"); sep.className = "scopesep"; grp.appendChild(sep);
  for (const s of eras) {
    const b = document.createElement("button");
    b.dataset.r = "era:" + s; b.setAttribute("aria-pressed", "false");
    b.textContent = (s >= 1 ? s : +s.toFixed(4)) + "₿";   // ₿
    b.title = "Only the " + (s >= 1 ? s : +s.toFixed(4)) + " BTC subsidy era (a halving epoch)";
    grp.appendChild(b);
  }
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
    ? "in " + fmtInt(s.remaining) + " blocks · ~" + fmtDateTime(s.eta) : "");
  set("t-prog", s.progress != null ? (100 * s.progress).toFixed(1) + "%" : "—");
  document.getElementById("t-prog-bar").style.width = s.progress != null ? (100 * s.progress) + "%" : "0";
  set("t-prog-sub", s.elapsed != null ? fmtInt(s.elapsed) + " of 2,016 blocks" : "");
  // hashrate: big number + small unit (like the other tiles) so "EH/s" never wraps
  const hashEl = document.getElementById("t-hash");
  hashEl.textContent = "";
  if (s.hashrate == null) hashEl.textContent = "—";
  else {
    const parts = fmtCompact(s.hashrate, 1).split(" ");   // "917.5 E" -> ["917.5","E"]
    hashEl.appendChild(document.createTextNode(parts[0]));
    const u = document.createElement("small"); u.textContent = (parts[1] || "") + "H/s";
    hashEl.appendChild(u);
  }
  set("t-hash-sub", s.avg_interval != null
    ? "avg block " + fmtInterval(s.avg_interval) + " this epoch" : "current epoch average");
  // fees-inclusive is the headline; subsidy-only only bridges the fee backfill
  const hvVal = s.hashvalue_with_fees != null ? s.hashvalue_with_fees : s.hashvalue;
  const hv = document.getElementById("t-hv");
  hv.textContent = "";
  hv.appendChild(document.createTextNode(hvVal != null ? fmtFullInt(hvVal) : "—"));
  const unit = document.createElement("small"); unit.textContent = " sats/day";
  hv.appendChild(unit);
  set("t-hv-sub", s.hashvalue_with_fees != null
    ? "subsidy " + s.subsidy + " BTC + fees " + fmtPct(s.fees_pct_of_reward, 1) + " of reward"
    : (s.subsidy != null ? "subsidy only — reading this epoch's fees…" : ""));
  const halv = document.getElementById("t-halv");
  halv.textContent = "";
  halv.appendChild(document.createTextNode(s.halving_blocks != null ? fmtInt(s.halving_blocks) : "—"));
  const hu = document.createElement("small"); hu.textContent = " blocks";
  halv.appendChild(hu);
  set("t-halv-sub", s.halving_eta != null
    ? "~ " + fmtDate(s.halving_eta) + " · subsidy " + s.subsidy + " → " + s.next_subsidy + " BTC" : "");
  const full = document.getElementById("t-diff-full");
  delete full.dataset.copyOrig;
  full.textContent = s.difficulty != null ? fmtFullInt(s.difficulty) : "";
}

// -- records: ranked adjustments + streaks --------------------------------------
function renderRecords() {
  const changed = S.rows.filter(r => r.change != null);
  if (!changed.length) return;
  const fill = (id, list) => {
    const tb = document.querySelector(id + " tbody");
    tb.textContent = "";
    for (const r of list) {
      const tr = document.createElement("tr");
      const c1 = document.createElement("td");
      c1.style.textAlign = "left";
      const dot = document.createElement("span");
      dot.className = "dirdot " + (r.change >= 0 ? "up" : "down");
      c1.appendChild(dot);
      c1.appendChild(document.createTextNode(fmtPct(r.change)));
      const c2 = document.createElement("td");
      c2.textContent = String(r.epoch);
      const c3 = document.createElement("td"); c3.textContent = fmtDate(r.start_time);
      tr.append(c1, c2, c3);
      tb.appendChild(tr);
    }
  };
  const sorted = changed.slice().sort((a, b) => b.change - a.change);
  fill("#rec-up", sorted.slice(0, 5));
  fill("#rec-down", sorted.slice(-5).reverse());
  let cur = 0, prevSign = 0;
  for (const r of changed) {
    const sign = r.change >= 0 ? 1 : -1;
    cur = sign === prevSign ? cur + 1 : 1;
    prevSign = sign;
  }
  document.getElementById("streaks").textContent =
    "Current streak: " + cur + " consecutive " + (prevSign >= 0 ? "increases" : "decreases") +
    ". The in-progress epoch counts — its adjustment was fixed at the last retarget.";
  // consecutive runs: group changed epochs by sign of the adjustment, compound
  // the change across each run; a run's magnitude ≠ its length
  const runs = [];
  let run = null;
  for (const r of changed) {
    const sign = r.change >= 0 ? 1 : -1;
    if (!run || run.sign !== sign) {
      run = { sign, len: 0, cum: 1, from: r.start_time, to: r.start_time };
      runs.push(run);
    }
    run.len += 1;
    run.cum *= 1 + r.change;
    run.to = r.start_time;
  }
  const ups = runs.filter(r => r.sign > 0), downs = runs.filter(r => r.sign < 0);
  // four top-5 tables. `primary` picks what the first column shows: the run
  // length (for longest) or the compounded % (for biggest/deepest).
  const fillRuns = (id, list, primary) => {
    const tb = document.querySelector(id + " tbody");
    tb.textContent = "";
    for (const r of list) {
      const tr = document.createElement("tr");
      const c0 = document.createElement("td"); c0.style.textAlign = "left";
      if (primary === "len") {
        c0.textContent = r.len + " epochs";
      } else {
        const dot = document.createElement("span");
        dot.className = "dirdot " + (r.sign > 0 ? "up" : "down"); c0.appendChild(dot);
        c0.appendChild(document.createTextNode(fmtPct(r.cum - 1, 1)));
      }
      const c1 = document.createElement("td");
      if (primary === "len") {
        const dot = document.createElement("span");
        dot.className = "dirdot " + (r.sign > 0 ? "up" : "down"); c1.appendChild(dot);
        c1.appendChild(document.createTextNode(fmtPct(r.cum - 1, 1)));
      } else {
        c1.textContent = r.len;
      }
      const c2 = document.createElement("td");
      c2.textContent = fmtDate(r.from) + " → " + fmtDate(r.to);
      tr.append(c0, c1, c2);
      tb.appendChild(tr);
    }
  };
  const byLen = (a, b) => b.len - a.len, byCum = (a, b) => b.cum - a.cum;
  fillRuns("#run-linc", ups.slice().sort(byLen).slice(0, 5), "len");
  fillRuns("#run-binc", ups.slice().sort(byCum).slice(0, 5), "cum");
  fillRuns("#run-ldec", downs.slice().sort(byLen).slice(0, 5), "len");
  fillRuns("#run-ddec", downs.slice().sort((a, b) => a.cum - b.cum).slice(0, 5), "cum");
  // top-5 stretches without a new all-time-high difficulty
  let ath = -Infinity, from = null, low = Infinity;
  const gaps = [];
  for (const r of S.rows) {
    if (r.difficulty > ath) {
      if (from != null)
        gaps.push({ from, to: r.start_time, depth: low / ath - 1, ongoing: false });
      ath = r.difficulty; from = r.start_time; low = ath;
    } else low = Math.min(low, r.difficulty);
  }
  gaps.push({ from, to: tipTime(), depth: low / ath - 1, ongoing: true });
  gaps.sort((a, b) => (b.to - b.from) - (a.to - a.from));
  const dtb = document.querySelector("#drawdowns tbody");
  dtb.textContent = "";
  for (const g of gaps.slice(0, 5)) {
    const tr = document.createElement("tr");
    const dur = document.createElement("td");
    dur.style.textAlign = "left";
    dur.textContent = Math.round((g.to - g.from) / 86400) + " days" + (g.ongoing ? " · ongoing" : "");
    const f = document.createElement("td"); f.textContent = fmtDate(g.from);
    const t = document.createElement("td"); t.textContent = g.ongoing ? "—" : fmtDate(g.to);
    const d = document.createElement("td");
    if (g.depth < 0) {
      const dot = document.createElement("span");
      dot.className = "dirdot down";
      d.appendChild(dot);
    }
    d.appendChild(document.createTextNode(fmtPct(Math.min(0, g.depth), 1)));
    tr.append(dur, f, t, d);
    dtb.appendChild(tr);
  }

  // highlights: does the CURRENT pattern place in any of the top-5 records?
  const box = document.getElementById("highlights");
  box.textContent = ""; box.classList.remove("on");
  const ord = k => k + (["th", "st", "nd", "rd"][k % 10 > 3 || (k % 100 >= 11 && k % 100 <= 13) ? 0 : k % 10]);
  const rankIn = (arr, item) => { const i = arr.indexOf(item); return i >= 0 && i < 5 ? i + 1 : null; };
  const add = (dir, html) => {
    const p = document.createElement("p"); p.className = "hl";
    const dot = document.createElement("span");
    dot.className = "dirdot " + dir; dot.style.marginRight = "6px";
    p.appendChild(dot); p.insertAdjacentHTML("beforeend", html); box.appendChild(p);
  };
  const say = (k, what) => "<span class='rank'>" + ord(k) + "</span> " + what;
  // latest single adjustment
  const latest = changed[changed.length - 1];
  if (latest) {
    const up = latest.change >= 0, dir = up ? "up" : "down";
    const k = rankIn(up ? sorted : sorted.slice().reverse(), latest);
    if (k) add(dir, "Latest adjustment <b>" + fmtPct(latest.change) + "</b> is the " +
      say(k, "largest " + (up ? "increase" : "decrease") + " on record."));
  }
  // ongoing consecutive run
  const cr = runs[runs.length - 1];
  if (cr && cr.len >= 2) {
    const up = cr.sign > 0, dir = up ? "up" : "down", word = up ? "increases" : "decreases";
    const kL = rankIn((up ? ups : downs).slice().sort(byLen), cr);
    const kM = rankIn((up ? ups.slice().sort(byCum) : downs.slice().sort((a, b) => a.cum - b.cum)), cr);
    if (kL) add(dir, "Current run of <b>" + cr.len + " consecutive " + word + "</b> is the " +
      say(kL, "longest such run on record."));
    if (kM) add(dir, "Current run's <b>" + fmtPct(cr.cum - 1, 1) + "</b> total is the " +
      say(kM, (up ? "biggest increase" : "deepest decrease") + " run on record."));
  }
  // ongoing stretch below the all-time high
  const og = gaps.find(g => g.ongoing);
  if (og) {
    const kG = rankIn(gaps, og), days = Math.round((og.to - og.from) / 86400);
    if (kG && days >= 30) add("down", "Difficulty has been below its all-time high for <b>" +
      days + " days</b> — the " + say(kG, "longest such stretch on record."));
  }
  if (box.children.length) box.classList.add("on");
}

// -- growth: CAGR over trailing windows + doubling time -------------------------
function renderGrowth() {
  const box = document.getElementById("growth");
  box.textContent = "";
  if (!S.rows.length || !S.summary || S.summary.difficulty == null) return;
  const now = tipTime(), cur = S.summary.difficulty;
  const stat = (big, label, tip) => {
    const div = document.createElement("div");
    const b = document.createElement("b"); b.textContent = big;
    div.appendChild(b);
    const lbl = document.createElement("span");
    if (tip) { lbl.className = "info"; lbl.title = tip; }
    lbl.textContent = label;
    div.appendChild(lbl);
    box.appendChild(div);
  };
  for (const [label, years] of [["1y", 1], ["2y", 2], ["4y", 4]]) {
    const then = diffAt(now - years * 365.25 * 86400);
    if (then == null) continue;
    stat(fmtPct(Math.pow(cur / then, 1 / years) - 1, 1) + "/yr", label + " CAGR",
      "Compound annual growth rate: the constant yearly rate that takes difficulty from its value " +
      years + " year(s) ago to today.");
  }
  const g1 = diffAt(now - 365.25 * 86400);
  if (g1 != null && cur > g1) {
    const dbl = Math.log(2) / Math.log(cur / g1);
    stat(dbl < 2 ? Math.round(dbl * 12) + " months" : dbl.toFixed(1) + " years",
      "doubling time at 1y pace",
      "How long difficulty would take to double if it kept growing at the last 12 months' rate.");
  }
}

// -- projection: compound extrapolation of a trailing window --------------------
function renderProjection() {
  const tb = document.querySelector("#proj tbody");
  tb.textContent = "";
  const s = S.summary;
  if (!S.rows.length || !s || s.difficulty == null) return;
  const now = tipTime(), cur = s.difficulty;
  const then = diffAt(now - S.basis * 86400);
  if (then == null) {
    const tr = document.createElement("tr"), td = document.createElement("td");
    td.colSpan = 5; td.textContent = "Not enough history for this window";
    tr.appendChild(td); tb.appendChild(tr);
    return;
  }
  const daily = Math.pow(cur / then, 1 / S.basis);
  for (const [label, days] of [["+3 months", 91], ["+6 months", 182],
                               ["+1 year", 365], ["+2 years", 730]]) {
    const proj = cur * Math.pow(daily, days);
    const height = (s.tip_height || 0) + Math.round(days * 144);
    const subsidy = 50 / Math.pow(2, Math.floor(height / 210000));
    const hv = 1e15 * 86400 / (proj * 4294967296) * subsidy * 1e8;
    const tr = document.createElement("tr");
    const cells = [label, fmtDate(now + days * 86400), fmtCompact(proj, 2),
                   fmtPct(proj / cur - 1, 0), fmtFullInt(hv) + " sats/day"];
    cells.forEach((c, i) => {
      const td = document.createElement("td");
      if (i === 0) td.style.textAlign = "left";
      td.textContent = c;
      tr.appendChild(td);
    });
    tb.appendChild(tr);
  }
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
    const hr = document.createElement("td");
    hr.textContent = fmtHash(start * 4294967296 / 600);  // implied by difficulty
    tr.appendChild(hr);
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

  // seasonal averages: for each month, the mean of that month's change across
  // the most recent 3 / 5 COMPLETED years (so Jan = Jan'26,'25,'24; and an
  // incomplete current month like Aug'26 is skipped, falling back to Aug'25…).
  const season = Array.from({ length: 12 }, () => []);   // season[m] = [change] year-desc
  for (let y = y1; y >= y0; y--) {
    for (let m = 0; m < 12; m++) {
      const t0 = Date.UTC(y, m, 1) / 1000, t1 = Date.UTC(y, m + 1, 1) / 1000;
      if (t1 > now || t0 < first) continue;
      season[m].push(valAt(t1) / valAt(t0) - 1);
    }
  }
  const avgRow = (label, n) => {
    const tr = document.createElement("tr"); tr.className = "seasonavg";
    const c0 = document.createElement("td"); c0.textContent = label;
    c0.style.textAlign = "left"; c0.style.fontWeight = "700"; tr.appendChild(c0);
    for (let m = 0; m < 12; m++) {
      const vals = season[m].slice(0, n);
      const td = document.createElement("td");
      if (!vals.length) { td.className = "empty"; td.textContent = ""; }
      else changeCell(td, vals.reduce((a, b) => a + b, 0) / vals.length, "");
      tr.appendChild(td);
    }
    mbody.appendChild(tr);
  };
  avgRow("3Y avg", 3);
  avgRow("5Y avg", 5);

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
  // difficulty ribbon: a fan of moving averages of the difficulty series
  if (S.ribbon) {
    const diffs = S.rows.map(r => r.difficulty);
    const ma = w => {
      const out = []; let sum = 0;
      for (let i = 0; i < diffs.length; i++) {
        sum += diffs[i]; if (i >= w) sum -= diffs[i - w];
        out[i] = sum / Math.min(i + 1, w);
      }
      return out;
    };
    RIBBON_WIN.forEach((w, k) => {
      const arr = ma(w);
      let p = "";
      for (const r of rows) p += (p ? "L" : "M") + x(r.start_time) + " " + y(arr[r.epoch]);
      el("path", { d: p, fill: "none", stroke: cssVar(RIBBON_COL[k]),
        "stroke-width": 1.5, "stroke-linejoin": "round", opacity: 0.9 }, svg);
    });
  }
  // step-after path: difficulty holds constant across each epoch. Faint when the
  // ribbon is on so the moving-average fan reads as the primary series.
  let d = "M" + x(rows[0].start_time) + " " + y(rows[0].difficulty);
  for (let i = 1; i < rows.length; i++) {
    d += "H" + x(rows[i].start_time) + "V" + y(rows[i].difficulty);
  }
  d += "H" + x(t1);
  el("path", { d, fill: "none", stroke: cssVar(S.ribbon ? "--chart-axis" : "--data-pos"),
    "stroke-width": S.ribbon ? 1 : 2, "stroke-linejoin": "round", "stroke-linecap": "round",
    opacity: S.ribbon ? 0.5 : 1 }, svg);
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

// -- cumulative change since Jan 1, one line per year ---------------------------
function drawCumYTD() {
  const svg = document.getElementById("chart-ytd"), card = svg.parentNode;
  const legend = document.getElementById("ytd-legend"), tt = document.getElementById("tt-ytd");
  svg.textContent = ""; legend.textContent = "";
  const rows = S.rows;
  if (!rows.length) return;
  const now = tipTime(), curYear = new Date(now * 1000).getUTCFullYear();
  const y0 = new Date(rows[0].start_time * 1000).getUTCFullYear();
  // which years to draw: those present in the current scope (time window or
  // era). Lines are still computed from the full data so each year is complete.
  const shownYears = new Set(filteredRows().map(r => new Date(r.start_time * 1000).getUTCFullYear()));
  const series = [];
  for (let yr = y0; yr <= curYear; yr++) {
    if (!shownYears.has(yr)) continue;
    const jan1 = Date.UTC(yr, 0, 1) / 1000;
    const base = diffAt(Math.max(jan1, rows[0].start_time));
    if (base == null) continue;
    const pts = [];
    if (jan1 >= rows[0].start_time) pts.push([0, 0]);
    for (const r of rows) {
      const t = r.start_time;
      if (t < jan1 || new Date(t * 1000).getUTCFullYear() !== yr) continue;
      pts.push([(t - jan1) / 86400, r.difficulty / base - 1]);
    }
    const endT = Math.min(now, Date.UTC(yr + 1, 0, 1) / 1000);
    pts.push([(endT - jan1) / 86400, diffAt(endT) / base - 1]);
    if (pts.length > 1) series.push({ yr, pts });
  }
  if (!series.length) return;
  const W = card.clientWidth - 32, H = 300, m = { t: 16, r: 16, b: 26, l: 48 };
  svg.setAttribute("width", W); svg.setAttribute("height", H);
  const pw = W - m.l - m.r, ph = H - m.t - m.b;
  let lo = 0, hi = 0;
  for (const s of series) for (const p of s.pts) { lo = Math.min(lo, p[1]); hi = Math.max(hi, p[1]); }
  hi *= 1.05; lo *= 1.05;
  const x = doy => m.l + pw * doy / 366;
  const y = v => m.t + ph * (1 - (v - lo) / (hi - lo || 1));
  for (const v of niceTicks(lo, hi, 5)) {
    el("line", { class: "grid", x1: m.l, x2: m.l + pw, y1: y(v), y2: y(v) }, svg);
    el("text", { x: m.l - 6, y: y(v) + 4, "text-anchor": "end" }, svg).textContent = fmtPct(v, 0);
  }
  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  for (let mo = 0; mo < 12; mo += 2) {
    const doy = (Date.UTC(2025, mo, 1) - Date.UTC(2025, 0, 1)) / 86400e3;
    el("text", { x: x(doy), y: H - 8, "text-anchor": "middle" }, svg).textContent = MON[mo];
  }
  el("line", { class: "zero", x1: m.l, x2: m.l + pw, y1: y(0), y2: y(0) }, svg);
  series.forEach((s, i) => {
    const col = cssVar(CAT_COL[(s.yr - y0) % CAT_COL.length]);
    const cur = s.yr === curYear;
    let p = "";
    for (const pt of s.pts) p += (p ? "L" : "M") + x(pt[0]) + " " + y(pt[1]);
    el("path", { d: p, fill: "none", stroke: col, "stroke-width": cur ? 2.5 : 1.5,
      "stroke-linejoin": "round", opacity: cur ? 1 : 0.85 }, svg);
    const sp = document.createElement("span");
    const sw = document.createElement("i"); sw.style.background = col;
    sp.append(sw, document.createTextNode(s.yr + (cur ? " (YTD)" : "")));
    legend.appendChild(sp);
  });
}

// -- adjustment distribution histogram, optionally grouped ----------------------
function drawHist() {
  const svg = document.getElementById("chart-hist"), card = svg.parentNode;
  const legend = document.getElementById("hist-legend");
  svg.textContent = ""; legend.textContent = "";
  // respect the Range filter, and recompute the x-axis from the filtered data
  const changed = filteredRows().filter(r => r.change != null);
  if (changed.length < 2) return;
  let lo = Infinity, hi = -Infinity;
  for (const r of changed) { lo = Math.min(lo, r.change); hi = Math.max(hi, r.change); }
  // nice bin width ≈ range/45 so the curve is smooth at any zoom
  const raw = Math.max((hi - lo) / 45, 1e-4);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const BIN = ([1, 2, 2.5, 5, 10].map(s => s * mag).find(s => s >= raw)) || mag;
  const b0 = Math.floor(lo / BIN), b1 = Math.ceil(hi / BIN) + 1, nbins = Math.max(2, b1 - b0);
  const groupOf = r => {
    if (S.histgroup === "year") return String(new Date(r.start_time * 1000).getUTCFullYear());
    if (S.histgroup === "era") {
      const subs = 50 / Math.pow(2, Math.floor(r.start_height / 210000));
      return (subs >= 1 ? subs : +(subs).toFixed(4)) + " BTC era";
    }
    return "All";
  };
  const groups = [], bins = {};
  for (const r of changed) {
    const g = groupOf(r);
    if (!(g in bins)) { bins[g] = new Array(nbins).fill(0); groups.push(g); }
    bins[g][Math.min(nbins - 1, Math.floor(r.change / BIN) - b0)] += 1;
  }
  groups.sort();
  // normalise each group to a density (share of its own adjustments per bin) so
  // eras/years with very different counts compare on shape
  let maxD = 0;
  const dens = {};
  for (const g of groups) {
    const total = bins[g].reduce((a, b) => a + b, 0) || 1;
    dens[g] = bins[g].map(c => c / total);
    maxD = Math.max(maxD, ...dens[g]);
  }
  const W = card.clientWidth - 32, H = 280, m = { t: 16, r: 16, b: 30, l: 44 };
  svg.setAttribute("width", W); svg.setAttribute("height", H);
  const pw = W - m.l - m.r, ph = H - m.t - m.b;
  const xc = i => m.l + pw * (i + 0.5) / nbins;                 // bin-centre x
  const y = d => m.t + ph * (1 - d / (maxD * 1.08 || 1));
  for (const v of niceTicks(0, maxD * 1.08, 4)) {
    el("line", { class: "grid", x1: m.l, x2: m.l + pw, y1: y(v), y2: y(v) }, svg);
    el("text", { x: m.l - 6, y: y(v) + 4, "text-anchor": "end" }, svg).textContent = (v * 100).toFixed(0) + "%";
  }
  // x ticks: a handful across the range, always including 0
  const step = Math.max(1, Math.round(nbins / 8));
  for (let i = 0; i <= nbins; i++) {
    const pct = (b0 + i) * BIN;
    const atZero = Math.abs(pct) < BIN / 2;
    if (i % step === 0 || atZero) {
      const px = m.l + pw * i / nbins;
      el("line", { class: atZero ? "zero" : "grid", x1: px, x2: px, y1: m.t, y2: m.t + ph }, svg);
      el("text", { x: px, y: H - 8, "text-anchor": "middle" }, svg).textContent = fmtPct(pct, 0);
    }
  }
  const single = S.histgroup === "all";
  groups.forEach((g, gi) => {
    const col = single ? cssVar("--data-pos") : cssVar(CAT_COL[gi % CAT_COL.length]);
    // frequency polygon (density line) through bin centres
    let p = "";
    for (let i = 0; i < nbins; i++) p += (p ? "L" : "M") + xc(i) + " " + y(dens[g][i]);
    if (single) {   // fill under the single "All" curve
      el("path", { d: p + "L" + xc(nbins - 1) + " " + y(0) + "L" + xc(0) + " " + y(0) + "Z",
        fill: col, opacity: 0.12 }, svg);
    }
    el("path", { d: p, fill: "none", stroke: col, "stroke-width": 2, "stroke-linejoin": "round" }, svg);
    if (!single) {
      const sp = document.createElement("span");
      const sw = document.createElement("i"); sw.style.background = col;
      sp.append(sw, document.createTextNode(g));
      legend.appendChild(sp);
    }
  });
}

// -- table ---------------------------------------------------------------------
const COLS = [
  { key: "epoch", label: "Epoch", fmt: r => String(r.epoch) },
  { key: "start_height", label: "Start height", fmt: r => fmtInt(r.start_height) },
  { key: "start_time", label: "Start (UTC)", fmt: r => fmtDate(r.start_time) },
  { key: "end_time", label: "End (UTC)", fmt: r => r.current ? "in progress" : fmtDate(r.end_time) },
  { key: "duration", label: "Duration", fmt: r => fmtDur(durOf(r)) },
  { key: "avg_interval", label: "Avg block", fmt: r => fmtInterval(r.avg_interval) },
  { key: "difficulty", label: "Difficulty", fmt: r => fmtCompact(r.difficulty, 2) },
  { key: "change", label: "Change", fmt: null },   // rendered with a direction dot
  { key: "hashrate", label: "Est. hashrate", fmt: r => fmtHash(r.hashrate) },
  { key: "hashvalue", label: "Hashvalue (sats)", fmt: r => fmtFullInt(r.hashvalue) },
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

// -- chart PNG download: inline computed styles, embed brand font ---------------
let fontCssPromise = null;
function fontCss() {
  if (!fontCssPromise) fontCssPromise = Promise.all(
    ["fonts/braiinssans-regular.woff2", "fonts/braiinssans-bold.woff2"].map(u =>
      fetch(u).then(r => r.arrayBuffer()).then(buf => {
        let bin = "";
        const bytes = new Uint8Array(buf);
        for (let i = 0; i < bytes.length; i += 0x8000)
          bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
        return btoa(bin);
      })))
    .then(b64 =>
      [400, 700].map((w, i) =>
        '@font-face{font-family:"Braiins Sans";font-weight:' + w +
        ';src:url(data:font/woff2;base64,' + b64[i] + ') format("woff2")}').join(""));
  return fontCssPromise;
}
async function downloadChart(svgId, name) {
  const src = document.getElementById(svgId);
  const W = parseInt(src.getAttribute("width"), 10), H = parseInt(src.getAttribute("height"), 10);
  if (!W) return;
  const clone = src.cloneNode(true);
  clone.setAttribute("xmlns", NS);
  const a = src.querySelectorAll("*"), b = clone.querySelectorAll("*");
  for (let i = 0; i < a.length; i++) {   // CSS classes don't travel with the clone
    const cs = getComputedStyle(a[i]);
    b[i].setAttribute("fill", cs.fill);
    b[i].setAttribute("stroke", cs.stroke);
    b[i].setAttribute("stroke-width", cs.strokeWidth);
    if (a[i].tagName === "text")
      b[i].setAttribute("style", "font:" + cs.fontWeight + " " + cs.fontSize +
        " 'Braiins Sans', sans-serif");
  }
  const style = document.createElementNS(NS, "style");
  style.textContent = await fontCss();
  clone.insertBefore(style, clone.firstChild);
  const bg = el("rect", { width: W, height: H, fill: cssVar("--layer-01") });
  clone.insertBefore(bg, style.nextSibling);
  const url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(clone)],
    { type: "image/svg+xml" }));
  const img = new Image();
  img.onload = () => {
    const canvas = document.createElement("canvas");
    canvas.width = W * 2; canvas.height = H * 2;   // 2x for crisp slides
    canvas.getContext("2d").drawImage(img, 0, 0, W * 2, H * 2);
    URL.revokeObjectURL(url);
    canvas.toBlob(png => {
      const link = document.createElement("a");
      link.href = URL.createObjectURL(png);
      link.download = name;
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 5000);
    }, "image/png");
  };
  img.src = url;
}

// -- wiring ---------------------------------------------------------------------
function render() {
  buildEraButtons();
  renderPill(); renderTiles(); renderCalendar(); renderRecords(); renderGrowth();
  drawDiff(); drawAdj(); drawCumYTD(); drawHist(); renderProjection(); renderTable();
}

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
  drawDiff(); drawAdj(); drawCumYTD(); drawHist(); renderTable();
});
document.getElementById("scale").addEventListener("click", ev => {
  const b = ev.target.closest("button"); if (!b) return;
  S.scale = b.dataset.s;
  for (const x of ev.currentTarget.querySelectorAll("button"))
    x.setAttribute("aria-pressed", String(x === b));
  drawDiff();
});
document.getElementById("ribbon").addEventListener("click", ev => {
  const b = ev.target.closest("button"); if (!b) return;
  S.ribbon = b.dataset.rib === "on";
  for (const x of ev.currentTarget.querySelectorAll("button"))
    x.setAttribute("aria-pressed", String(x === b));
  drawDiff();
});
document.getElementById("histgroup").addEventListener("click", ev => {
  const b = ev.target.closest("button"); if (!b) return;
  S.histgroup = b.dataset.g;
  for (const x of ev.currentTarget.querySelectorAll("button"))
    x.setAttribute("aria-pressed", String(x === b));
  drawHist();
});
document.getElementById("basis").addEventListener("click", ev => {
  const b = ev.target.closest("button"); if (!b) return;
  S.basis = parseInt(b.dataset.b, 10);
  for (const x of ev.currentTarget.querySelectorAll("button"))
    x.setAttribute("aria-pressed", String(x === b));
  renderProjection();
});
document.getElementById("dl-diff").addEventListener("click",
  () => downloadChart("chart-diff", "bitcoin-difficulty.png"));
document.getElementById("dl-adj").addEventListener("click",
  () => downloadChart("chart-adj", "bitcoin-difficulty-adjustments.png"));
document.getElementById("pg-prev").addEventListener("click", () => { S.page--; renderTable(); });
document.getElementById("pg-next").addEventListener("click", () => { S.page++; renderTable(); });

// section nav: highlight the section under the reader
const NAV_SECTIONS = Array.from(document.querySelectorAll("main section"));
const NAV_LINKS = Array.from(document.querySelectorAll("#pagenav a"));
function updateNav() {
  const yy = window.scrollY + 90;
  let cur = NAV_SECTIONS[0];
  for (const sec of NAV_SECTIONS) if (sec.offsetTop <= yy) cur = sec;
  for (const a of NAV_LINKS)
    a.classList.toggle("active", a.getAttribute("href") === "#" + cur.id);
}
window.addEventListener("scroll", updateNav, { passive: true });
let rsz;
window.addEventListener("resize", () => { clearTimeout(rsz); rsz = setTimeout(() => { drawDiff(); drawAdj(); drawCumYTD(); drawHist(); }, 150); });
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { drawDiff(); drawAdj(); drawCumYTD(); drawHist(); });

hydrate();
fetchAll();
setInterval(fetchAll, 60000);
</script>
</body></html>"""

SIGNAL_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bitcoin Data — Signalling</title>
<link rel="icon" href="__FAVICON__">
__CSS__</head><body>
<header class="shell">
  <div class="mark">__SYMBOL__</div>
  <span class="name">Bitcoin Data<small>on Umbrel</small></span>
  <nav><a href="./">Difficulty</a><a class="active" href="signalling">Signalling</a><a href="api">API</a></nav>
</header>
<main>
  <div class="titlerow">
    <h2>BIP-110 signalling</h2>
    <span id="pill"><span class="dot"></span><span id="pill-text">Connecting&hellip;</span></span>
  </div>

  <div class="tiles" id="win-tiles">
    <div class="tile"><div class="label">Current retarget period</div>
      <div class="value" id="t-epoch">—</div>
      <div class="meter"><i id="t-epoch-bar" style="width:0%"></i></div>
      <div class="sub" id="t-epoch-sub"></div></div>
  </div>

  <div class="card">
    <div class="cardhead"><h3>Signalling blocks — last 288</h3>
      <span class="key"><span><i class="pos"></i>Signalling bit 4</span></span></div>
    <svg class="chart strip" id="strip" height="120" role="img" aria-label="Signalling blocks, last 288"></svg>
    <p class="note" id="strip-note"></p>
  </div>

  <div class="card">
    <div class="cardhead"><h3>Recent signalling blocks</h3></div>
    <div class="tablewrap"><table id="sig-table">
      <thead><tr><th>Height</th><th>Time (UTC)</th><th>Age</th></tr></thead>
      <tbody></tbody>
    </table></div>
    <p class="note" id="sig-note"></p>
  </div>

  <footer>BIP-110 lock-in requires 1,109 of 2,016 blocks (55%) in one retarget period &middot; version bit 4 &middot; data from your Bitcoin node</footer>
</main>
<script>
"use strict";
const WINDOWS = [18, 36, 72, 144, 288];
function fmtInt(x) { return x == null ? "—" : x.toLocaleString("en-US"); }
function fmtDateTime(ts) {
  return ts == null ? "—" : new Date(ts * 1000).toISOString().slice(0, 16).replace("T", " ");
}
function ago(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 90) return Math.round(s) + " s ago";
  if (s < 5400) return Math.round(s / 60) + " min ago";
  return Math.round(s / 3600) + " h ago";
}
function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
const NS = "http://www.w3.org/2000/svg";

function renderPill(summary) {
  const pill = document.getElementById("pill"), txt = document.getElementById("pill-text");
  const s = summary || {};
  pill.className = s.status === "waiting" ? "" : (s.status || "");
  if (s.status === "ok") {
    txt.textContent = "Live · block " + fmtInt(s.tip_height) +
      (s.updated ? " · updated " + ago(s.updated) : "");
  } else txt.textContent = s.message || "Connecting…";
}

function render(d, summary) {
  renderPill(summary);
  if (!d.ready) return;
  // current-period tile: the number that decides lock-in
  const e = d.epoch;
  document.getElementById("t-epoch").textContent =
    fmtInt(e.signalling) + " / " + fmtInt(d.threshold_blocks);
  document.getElementById("t-epoch-bar").style.width =
    Math.min(100, 100 * e.signalling / d.threshold_blocks) + "%";
  document.getElementById("t-epoch-sub").textContent =
    e.elapsed + " blocks elapsed · " +
    (e.have ? (100 * e.signalling / e.have).toFixed(1) : "0") +
    "% of observed blocks signal";
  // one tile per short window
  const tiles = document.getElementById("win-tiles");
  while (tiles.children.length > 1) tiles.removeChild(tiles.lastChild);
  for (const w of WINDOWS) {
    const win = d.windows[String(w)];
    const tile = document.createElement("div"); tile.className = "tile";
    const label = document.createElement("div"); label.className = "label";
    label.textContent = "Last " + w + " blocks";
    const value = document.createElement("div"); value.className = "value";
    value.textContent = String(win.signalling);
    const sub = document.createElement("div"); sub.className = "sub";
    sub.textContent = win.have < w
      ? "reading headers… " + win.have + "/" + w
      : (100 * win.signalling / w).toFixed(1) + "% of window";
    tile.append(label, value, sub);
    tiles.appendChild(tile);
  }
  drawStrip(d);
  // table of recent signalling blocks, newest first
  const tb = document.querySelector("#sig-table tbody");
  tb.textContent = "";
  const sig = d.blocks.filter(b => b.signal).reverse();
  for (const b of sig.slice(0, 25)) {
    const tr = document.createElement("tr");
    const h = document.createElement("td"); h.style.textAlign = "left";
    h.textContent = fmtInt(b.height);
    const t = document.createElement("td"); t.textContent = fmtDateTime(b.time);
    const a = document.createElement("td"); a.textContent = ago(b.time);
    tr.append(h, t, a);
    tb.appendChild(tr);
  }
  document.getElementById("sig-note").textContent = sig.length
    ? sig.length + " signalling blocks in the last 288" + (sig.length > 25 ? " (showing 25 newest)" : "")
    : "No signalling blocks in the last 288.";
}

function drawStrip(d) {
  const svg = document.getElementById("strip"), card = svg.parentNode;
  svg.textContent = "";
  const blocks = d.blocks;
  if (!blocks.length) return;
  const W = card.clientWidth - 32, H = 120, m = { t: 10, r: 8, b: 26, l: 8 };
  svg.setAttribute("width", W); svg.setAttribute("height", H);
  const pw = W - m.l - m.r, ph = H - m.t - m.b;
  const n = 288, first = d.tip_height - n + 1;
  const slot = pw / n, bw = Math.max(1, slot - 1);
  for (const b of blocks) {
    const x = m.l + slot * (b.height - first);
    const r = document.createElementNS(NS, "rect");
    r.setAttribute("x", x); r.setAttribute("width", bw);
    // non-signalling blocks stay as short baseline ticks so density reads at a glance
    r.setAttribute("y", b.signal ? m.t : m.t + ph - 10);
    r.setAttribute("height", b.signal ? ph : 10);
    r.setAttribute("class", b.signal ? "on" : "off");
    const title = document.createElementNS(NS, "title");
    title.textContent = "Block " + fmtInt(b.height) + " · " + fmtDateTime(b.time) + " UTC" +
      (b.signal ? " · signalling" : "");
    r.appendChild(title);
    svg.appendChild(r);
  }
  // height axis: a few ticks
  for (let i = 0; i <= 4; i++) {
    const h = first + Math.round(n * i / 4);
    const t = document.createElementNS(NS, "text");
    t.setAttribute("x", m.l + slot * (h - first));
    t.setAttribute("y", H - 8);
    t.setAttribute("text-anchor", i === 0 ? "start" : i === 4 ? "end" : "middle");
    t.textContent = fmtInt(Math.min(h, d.tip_height));
    svg.appendChild(t);
  }
  document.getElementById("strip-note").textContent =
    "Backfilled " + fmtInt(d.backfilled) + " of " + fmtInt(d.period_blocks) +
    " block headers. Hover a mark for details.";
}

async function fetchAll() {
  try {
    const [dr, sr] = await Promise.all([
      fetch("api/signalling", { cache: "no-store" }),
      fetch("api/summary", { cache: "no-store" })]);
    render(await dr.json(), await sr.json());
  } catch (e) {
    renderPill({ status: "error", message: "UI unreachable" });
  }
}
let rsz;
window.addEventListener("resize", () => { clearTimeout(rsz); rsz = setTimeout(fetchAll, 150); });
fetchAll();
setInterval(fetchAll, 30000);
</script>
</body></html>"""

PAGE = PAGE.replace("__CSS__", CSS).replace("__FAVICON__", FAVICON)
SIGNAL_PAGE = SIGNAL_PAGE.replace("__CSS__", CSS).replace("__FAVICON__", FAVICON)


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
        if path.endswith("/widgets/halving"):
            self._send(json.dumps(widget_halving(state, build_rows(state))).encode(),
                       "application/json")
            return
        if path.rstrip("/").endswith("/api"):
            self._send(API_PAGE.encode(), "text/html; charset=utf-8")
            return
        if path.endswith("/api/signalling"):
            self._send(json.dumps(build_signalling(state)).encode(), "application/json")
            return
        if path.rstrip("/").endswith("/signalling"):
            page = SIGNAL_PAGE.replace("__SYMBOL__", BRAIINS_SYMBOL)
            self._send(page.encode(), "text/html; charset=utf-8")
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
