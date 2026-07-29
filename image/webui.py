"""Config UI and status endpoints for Braiins Manager Agent on Umbrel.

Serves on :8080 (fronted by Umbrel's app_proxy):
- GET  /               config page to enter/replace the Agent ID and Secret key
                       (generated in Braiins Manager when adding an agent);
                       styled per design.braiins.com/braiins
- POST /               validates the credentials and writes /data/daemon.yaml
                       (mode 0600); the entrypoint supervisor (re)starts
                       bma-daemon when the file changes
- GET  /status         JSON polled by the page every 3 s: daemon state plus
                       miner count / telemetry activity parsed from the daemon
                       log tail
- GET  /widgets/status Umbrel home-screen widget (three-stats), fetched
                       server-side by umbrelOS
"""
import html
import json
import os
import re
import subprocess
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONFIG = "/data/daemon.yaml"
FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
FONTS = ("braiinssans-regular.woff2", "braiinssans-bold.woff2")
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$")

BRAIINS_SYMBOL = (
    '<svg viewBox="0 0 864 864" width="28" height="28" aria-hidden="true">'
    '<polygon points="345.6 864 345.6 682.8 194.4 179.9 194.4 0 0 0 0 179.9 151.2 682.8 151.2 864 345.6 864" fill="#fff"/>'
    '<polygon points="864 864 864 682.8 712.8 179.9 712.8 0 518.4 0 518.4 179.9 669.6 682.8 669.6 864 864 864" fill="#fff"/>'
    "</svg>"
)

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Braiins Manager Agent</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1024 1024'><rect width='1024' height='1024' rx='230' fill='%236B50FF'/><g transform='translate(296 296) scale(.5)'><polygon points='345.6 864 345.6 682.8 194.4 179.9 194.4 0 0 0 0 179.9 151.2 682.8 151.2 864 345.6 864' fill='%23fff'/><polygon points='864 864 864 682.8 712.8 179.9 712.8 0 518.4 0 518.4 179.9 669.6 682.8 669.6 864 864 864' fill='%23fff'/></g></svg>">
<style>
/* Braiins CDS v11 (IBM Carbon v11) — token values loaded from the design
   system mirror (colors_and_type.css + component previews), not invented.
   Violet-60 primary action and focus ring per the CDS component previews
   (product decision 2026-07-29; the token file's blue-60 button was
   overridden). Links follow the CDS link tokens (blue). Semantic
   tokens re-resolve for light (White) / dark (Gray 90) via
   prefers-color-scheme; markup is theme-agnostic.
   Braiins Sans (regular + bold) comes from the visualbook
   (design.braiins.com/braiins/typography) and is served by this process —
   no external requests. The visualbook ships 400/700 only, so the Carbon
   600 (semibold) styles resolve to the bold face. */
@font-face { font-family: "Braiins Sans"; font-style: normal; font-weight: 400;
  font-display: swap; src: url(fonts/braiinssans-regular.woff2) format("woff2"); }
@font-face { font-family: "Braiins Sans"; font-style: normal; font-weight: 700;
  font-display: swap; src: url(fonts/braiinssans-bold.woff2) format("woff2"); }
:root {
  --violet-60: #6b50ff;
  --violet-70: #5739e0;
  --gray-10: #f4f4f4;  --gray-20: #e0e0e0;  --gray-30: #c6c6c6;
  --gray-40: #a8a8a8;  --gray-50: #8d8d8d;  --gray-60: #6f6f6f;
  --gray-70: #525252;  --gray-80: #393939;  --gray-90: #262626;
  --gray-100: #161616;
  --blue-30: #a6c8ff;  --blue-40: #78a9ff;
  --blue-60: #0f62fe;  --blue-70: #0043ce;
  --red-40: #ff8389;   --red-60: #da1e28;
  --green-40: #42be65; --green-50: #24a148;
  --violet-80: #4326b3;
  --yellow-30: #f1c21b;

  --font-sans: "Braiins Sans", -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif;
  --font-brand: "Braiins Sans", -apple-system, BlinkMacSystemFont, sans-serif;

  --spacing-02: .25rem; --spacing-03: .5rem; --spacing-04: .75rem;
  --spacing-05: 1rem;   --spacing-06: 1.5rem; --spacing-07: 2rem;
  --spacing-09: 3rem;
  --radius-pill: 1000px;
  --duration-fast-02: 110ms;
  --ease-productive: cubic-bezier(.2, 0, .38, .9);

  /* semantic tokens — White theme */
  --background: #ffffff;
  --layer-01: var(--gray-10);
  --field-01: var(--gray-10);
  --border-subtle: var(--gray-20);
  --border-strong: var(--gray-50);
  --text-primary: var(--gray-100);
  --text-secondary: var(--gray-70);
  --text-placeholder: var(--gray-40);
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
}
@media (prefers-color-scheme: dark) {
  :root { /* Gray 90 theme */
    --background: var(--gray-90);
    --layer-01: var(--gray-80);
    --field-01: var(--gray-80);
    --border-subtle: var(--gray-70);
    --border-strong: var(--gray-40);
    --text-primary: var(--gray-10);
    --text-secondary: var(--gray-30);
    --text-placeholder: var(--gray-50);
    --text-helper: var(--gray-40);
    /* light semantic variants for dark backgrounds */
    --support-error: var(--red-40);
    --support-success: var(--green-40);
    --link-primary: var(--blue-40);
    --link-primary-hover: var(--blue-30);
  }
}
* { box-sizing: border-box; }
body {
  font: 400 14px/1.4286 var(--font-sans); letter-spacing: .16px; /* body-01 */
  background: var(--background); color: var(--text-primary);
  max-width: 32rem; margin: 0 auto; padding: var(--spacing-09) var(--spacing-05);
}
header { display: flex; align-items: center; gap: var(--spacing-04); margin-bottom: var(--spacing-07); }
.mark { background: var(--violet-60); width: 44px; height: 44px;
  border-radius: 10px; /* app-icon corner ratio (230/1024) at 44px, not a component radius */
  display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.mark svg { width: 22px; height: 22px; }
h1 { font: 600 16px/1.5 var(--font-brand); letter-spacing: 0; margin: 0; } /* brand-label-lg (heading-02 values, brand family) */
h1 small { display: block; font: 400 12px/1.3333 var(--font-sans); letter-spacing: .32px; color: var(--text-helper); }
/* Carbon tile: flat layer fill, sharp corners, no shadow */
.card { background: var(--layer-01); padding: var(--spacing-05); }
#pill { display: inline-flex; align-items: center; gap: var(--spacing-03);
  font: 400 12px/1.3333 var(--font-sans); letter-spacing: .32px;
  padding: var(--spacing-02) var(--spacing-04); border-radius: var(--radius-pill);
  box-shadow: inset 0 0 0 1px var(--border-subtle);
  margin-bottom: var(--spacing-05); color: var(--text-secondary); }
#pill .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--border-strong); }
#pill.running .dot { background: var(--support-success); }
#pill.starting .dot { background: var(--support-warning); }
#pill.error .dot { background: var(--support-error); }
p.help { color: var(--text-secondary); margin: 0 0 var(--spacing-05); }
p.help a { color: var(--link-primary); text-decoration: none; }
p.help a:hover { color: var(--link-primary-hover); text-decoration: underline; }
label { display: block; margin: var(--spacing-05) 0 var(--spacing-03);
  font: 400 12px/1.3333 var(--font-sans); letter-spacing: .32px; color: var(--text-secondary); }
/* Carbon text input: field fill, bottom hairline, sharp corners */
input { width: 100%; height: 40px; padding: 0 var(--spacing-05);
  font: 400 14px/1.2857 var(--font-sans); letter-spacing: .16px; /* body-compact-01 */
  background: var(--field-01); color: var(--text-primary);
  border: 0; border-radius: 0; box-shadow: inset 0 -1px 0 0 var(--border-strong); }
input::placeholder { color: var(--text-placeholder); }
input:focus { outline: 2px solid var(--focus); outline-offset: -2px; }
/* Carbon primary button, 48px, left-aligned label */
button { margin-top: var(--spacing-06); height: 48px; min-width: 120px;
  padding: 0 64px 0 var(--spacing-05); text-align: left;
  display: inline-flex; align-items: center;
  font: 400 14px/1 var(--font-brand); letter-spacing: .16px;
  appearance: none; border: 0; border-radius: 0; cursor: pointer;
  background: var(--button-primary); color: var(--text-on-color);
  transition: background var(--duration-fast-02) var(--ease-productive); }
button:hover { background: var(--button-primary-hover); }
button:active { background: var(--button-primary-active); }
button:focus-visible { outline: 2px solid var(--focus); outline-offset: -2px;
  box-shadow: inset 0 0 0 1px var(--text-on-color); }
#stats { font: 400 14px/1.2857 var(--font-sans); letter-spacing: .16px; /* body-compact-01 */
  color: var(--text-secondary); margin: 0 0 var(--spacing-05); }
#stats .num { color: var(--text-primary); font-weight: 600; }
#stats .err { display: block; margin-top: var(--spacing-02);
  border-left: 3px solid var(--support-warning); padding-left: var(--spacing-03);
  font: 400 12px/1.3333 var(--font-sans); letter-spacing: .32px; color: var(--text-secondary); }
#msg { font: 400 12px/1.3333 var(--font-sans); letter-spacing: .32px; margin: var(--spacing-05) 0 0; min-height: 1.2em; }
#msg.ok { color: var(--support-success); }
#msg.err { color: var(--support-error); }
footer { margin-top: var(--spacing-06); font: 400 12px/1.3333 var(--font-sans); letter-spacing: .32px;
  color: var(--text-helper); text-align: center; }
</style></head><body>
<header>
  <div class="mark">__SYMBOL__</div>
  <h1>Braiins Manager Agent<small>on Umbrel</small></h1>
</header>
<div class="card">
  <span id="pill"><span class="dot"></span><span id="pill-text">Checking&hellip;</span></span>
  <div id="stats"></div>
  <p class="help">Paste the credentials shown by <a href="https://manager.braiins.com" target="_blank" rel="noopener">Braiins Manager</a> when you add a new agent (Devices &rarr; Agents &rarr; Add agent). Saving replaces the current credentials and restarts the agent.</p>
  <form id="form">
    <label for="agent_id">Agent ID</label>
    <input id="agent_id" name="agent_id" placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" value="__AGENT_ID__" required>
    <label for="secret_key">Secret key</label>
    <input id="secret_key" name="secret_key" placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" required>
    <button type="submit">Save &amp; start agent</button>
  </form>
  <p id="msg"></p>
</div>
<footer>__PKG__Agent daemon v__VERSION__ &middot; connects to manager.braiins.com</footer>
<script>
const pill = document.getElementById('pill');
const pillText = document.getElementById('pill-text');
const stats = document.getElementById('stats');
const msg = document.getElementById('msg');

function ago(iso) {
  const s = Math.max(0, (Date.now() - Date.parse(iso)) / 1000);
  if (s < 90) return 'just now';
  if (s < 3600) return Math.round(s / 60) + ' min ago';
  return Math.round(s / 3600) + ' h ago';
}

function renderStats(s) {
  if (!s.configured || !s.running) { stats.innerHTML = ''; return; }
  const parts = [];
  if (s.miners !== null) parts.push(`<span class="num">${s.miners}</span> miner${s.miners === 1 ? '' : 's'} found`);
  if (s.last_sent) parts.push(`telemetry sent <span class="num">${ago(s.last_sent)}</span>`);
  let html = parts.join(' &middot; ');
  // Surface errors only if nothing was successfully sent since
  if (s.last_error && (!s.last_sent || Date.parse(s.last_error.ts) > Date.parse(s.last_sent))) {
    const msg = s.last_error.msg.replace(/&/g, '&amp;').replace(/</g, '&lt;');
    html += `<span class="err">Warning: ${msg}</span>`;
  }
  stats.innerHTML = html;
}

async function refresh() {
  try {
    const s = await (await fetch('status', {cache: 'no-store'})).json();
    if (!s.configured) { pill.className = ''; pillText.textContent = 'Not configured'; }
    else if (s.running) { pill.className = 'running'; pillText.textContent = 'Agent running'; }
    else { pill.className = 'starting'; pillText.textContent = 'Agent starting…'; }
    renderStats(s);
  } catch (e) { pill.className = 'error'; pillText.textContent = 'UI unreachable'; }
}
refresh();
setInterval(refresh, 3000);

document.getElementById('form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  msg.className = ''; msg.textContent = 'Saving…';
  const body = new URLSearchParams(new FormData(ev.target));
  const r = await fetch('', {method: 'POST', body});
  const res = await r.json();
  msg.className = res.ok ? 'ok' : 'err';
  msg.textContent = res.message;
  if (res.ok) { document.getElementById('secret_key').value = ''; refresh(); }
});
</script>
</body></html>"""


def daemon_version():
    # `bma-daemon --version` → "braiins-manager-agent 4.10.0 (<commit>, <date>)"
    try:
        out = subprocess.run(["/usr/bin/bma-daemon", "--version"], capture_output=True, text=True).stdout
        m = re.search(r"\d+\.\d+\.\d+\S*", out)
        return m.group(0) if m else "?"
    except Exception:
        return "?"


VERSION = None
# The Umbrel package (wrapper) version, injected by docker-compose.yml and kept
# in sync with the manifest by bump.py. Distinct from the agent daemon version
# above: a wrapper-only release bumps this without changing the daemon binary.
PACKAGE_VERSION = os.environ.get("APP_VERSION", "").strip()


def current_agent_id():
    try:
        with open(CONFIG) as f:
            for line in f:
                if line.startswith("agent_id:"):
                    val = line.split(":", 1)[1].strip()
                    return val if UUID_RE.match(val) else ""
    except OSError:
        pass
    return ""


def daemon_running():
    return subprocess.run(["pidof", "bma-daemon"], capture_output=True).returncode == 0


LOG = "/var/log/bma.log"
TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")
MINERS_RE = re.compile(r"(\d+) miners polled")
SENT_RE = re.compile(r"batch sent items=(\d+)")
ERR_RE = re.compile(r" (?:WARN|ERROR)\s+(?:[\w:]+: )?(.*)")


def log_stats():
    """Parse the tail of the daemon log for miner count / telemetry activity."""
    stats = {"miners": None, "last_sent": None, "last_error": None}
    try:
        with open(LOG, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 262144))
            tail = f.read().decode(errors="replace")
    except OSError:
        return stats
    for line in tail.splitlines():
        ts = TS_RE.match(line)
        ts = ts.group(1) + "Z" if ts else None
        m = MINERS_RE.search(line)
        if m:
            stats["miners"] = int(m.group(1))
        m = SENT_RE.search(line)
        if m and ts:
            stats["last_sent"] = ts
        m = ERR_RE.search(line)
        if m and ts:
            stats["last_error"] = {"ts": ts, "msg": m.group(1)[:120]}
    return stats


def widget_status():
    """Umbrel home-screen widget (three-stats): agent state, miners, telemetry age."""
    import datetime
    configured, running = bool(current_agent_id()), daemon_running()
    stats = log_stats()
    state = "Running" if configured and running else ("Starting" if configured else "Setup")
    miners = str(stats["miners"]) if stats["miners"] is not None else "—"
    telemetry = "—"
    if stats["last_sent"]:
        age = (datetime.datetime.now(datetime.timezone.utc)
               - datetime.datetime.fromisoformat(stats["last_sent"].replace("Z", "+00:00"))).total_seconds()
        telemetry = "now" if age < 90 else (f"{age / 60:.0f}m ago" if age < 3600 else f"{age / 3600:.0f}h ago")
    # umbrelOS renders three-stats items as {icon, text, subtext}: icon on top,
    # `text` as the emphasized value, `subtext` as the muted caption. There is
    # no `title` field. `icon` is a Tabler icon slug (see DECISIONS.md
    # "Widget icons"): plug-connected = link to Braiins Manager, cpu = miners,
    # cloud-upload = telemetry stream.
    return {
        "type": "three-stats",
        "refresh": "10s",
        "link": "",
        "items": [
            {"icon": "plug-connected", "text": state, "subtext": "Agent"},
            {"icon": "cpu", "text": miners, "subtext": "Miners"},
            {"icon": "cloud-upload", "text": telemetry, "subtext": "Telemetry"},
        ],
    }


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
        if path.endswith("/widgets/status"):
            self._send(json.dumps(widget_status()).encode(), "application/json")
            return
        if path.endswith("/status"):
            state = {"configured": bool(current_agent_id()), "running": daemon_running(), **log_stats()}
            self._send(json.dumps(state).encode(), "application/json")
            return
        global VERSION
        if VERSION is None:
            VERSION = daemon_version()
        pkg = f"Umbrel app {html.escape(PACKAGE_VERSION)} &middot; " if PACKAGE_VERSION else ""
        page = (PAGE
                .replace("__SYMBOL__", BRAIINS_SYMBOL)
                .replace("__AGENT_ID__", html.escape(current_agent_id()))
                .replace("__PKG__", pkg)
                .replace("__VERSION__", html.escape(VERSION)))
        self._send(page.encode(), "text/html; charset=utf-8")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        fields = urllib.parse.parse_qs(self.rfile.read(length).decode())
        agent_id = fields.get("agent_id", [""])[0].strip()
        secret_key = fields.get("secret_key", [""])[0].strip()
        if not UUID_RE.match(agent_id) or not UUID_RE.match(secret_key):
            self._send(json.dumps({"ok": False, "message": "Both values must be UUIDs like 123e4567-e89b-42d3-a456-426614174000."}).encode(), "application/json")
            return
        tmp = CONFIG + ".tmp"
        # 0600: credentials shouldn't be world-readable on the host
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(f"agent_id: {agent_id}\nsecret_key: {secret_key}\n")
        os.replace(tmp, CONFIG)
        self._send(json.dumps({"ok": True, "message": "Saved. The agent is restarting with the new credentials."}).encode(), "application/json")

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
