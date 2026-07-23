"""Minimal config UI for Braiins Manager Agent on Umbrel.

Serves one page on :8080 to enter/replace the Agent ID and Secret key
(generated in Braiins Manager when adding an agent). Writes /data/daemon.yaml;
the entrypoint supervisor (re)starts bma-daemon when the file changes.
Styled per design.braiins.com/braiins. The page polls /status and updates
the state pill live.
"""
import html
import json
import os
import re
import subprocess
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONFIG = "/data/daemon.yaml"
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
<style>
:root {
  --violet-60: #6B50FF;
  --violet-70: #5840D9;
  --gray-100: #161616;
  --gray-90: #212121;
  --gray-80: #2e2e2e;
  --gray-60: #6f6f6f;
  --gray-30: #c6c6c6;
  --gray-10: #f4f4f4;
  --green-50: #13A454;
  --orange-50: #EB6307;
  --red-60: #D9222C;
}
* { box-sizing: border-box; }
body {
  font-family: "Braiins Sans", "IBM Plex Sans", system-ui, sans-serif;
  background: var(--gray-100); color: var(--gray-10);
  max-width: 32rem; margin: 0 auto; padding: 3rem 1.25rem;
}
header { display: flex; align-items: center; gap: .75rem; margin-bottom: 2rem; }
.mark { background: var(--violet-60); border-radius: 8px; width: 44px; height: 44px;
  display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.mark svg { width: 22px; height: 22px; }
h1 { font-size: 1.15rem; font-weight: 600; margin: 0; line-height: 1.3; }
h1 small { display: block; font-size: .8rem; font-weight: 400; color: var(--gray-60); }
.card { background: var(--gray-90); border: 1px solid var(--gray-80); border-radius: 12px; padding: 1.5rem; }
#pill { display: inline-flex; align-items: center; gap: .5rem; font-size: .85rem;
  padding: .35rem .8rem; border-radius: 999px; margin-bottom: 1.25rem;
  background: var(--gray-80); color: var(--gray-30); }
#pill .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--gray-60); }
#pill.running .dot { background: var(--green-50); box-shadow: 0 0 6px var(--green-50); }
#pill.starting .dot { background: var(--orange-50); }
#pill.error .dot { background: var(--red-60); }
p.help { font-size: .875rem; color: var(--gray-30); margin: 0 0 1.25rem; line-height: 1.5; }
p.help a { color: var(--violet-60); text-decoration: none; }
p.help a:hover { text-decoration: underline; }
label { display: block; margin: 1rem 0 .35rem; font-size: .8rem; font-weight: 600;
  letter-spacing: .02em; color: var(--gray-30); }
input { width: 100%; padding: .65rem .75rem; font-size: .9rem;
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  background: var(--gray-100); color: var(--gray-10);
  border: 1px solid var(--gray-80); border-radius: 8px; }
input:focus { outline: none; border-color: var(--violet-60); }
button { margin-top: 1.5rem; width: 100%; padding: .75rem; font-size: .95rem; font-weight: 600;
  font-family: inherit; border: 0; border-radius: 8px;
  background: var(--violet-60); color: #fff; cursor: pointer; }
button:hover { background: var(--violet-70); }
#stats { font-size: .85rem; color: var(--gray-30); margin: -.5rem 0 1rem; line-height: 1.6; }
#stats .num { color: var(--gray-10); font-weight: 600; }
#stats .err { color: var(--orange-50); display: block; font-size: .8rem; }
#msg { font-size: .85rem; margin: 1rem 0 0; min-height: 1.2em; }
#msg.ok { color: var(--green-50); }
#msg.err { color: var(--red-60); }
footer { margin-top: 1.5rem; font-size: .75rem; color: var(--gray-60); text-align: center; }
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
<footer>Agent daemon v__VERSION__ &middot; connects to manager.braiins.com</footer>
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
    html += `<span class="err">⚠ ${msg}</span>`;
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
    state = "Running" if configured and running else ("Starting" if configured else "Setup needed")
    miners = str(stats["miners"]) if stats["miners"] is not None else "—"
    telemetry = "—"
    if stats["last_sent"]:
        age = (datetime.datetime.now(datetime.timezone.utc)
               - datetime.datetime.fromisoformat(stats["last_sent"].replace("Z", "+00:00"))).total_seconds()
        telemetry = "now" if age < 90 else (f"{age / 60:.0f}m ago" if age < 3600 else f"{age / 3600:.0f}h ago")
    return {
        "type": "three-stats",
        "refresh": "10s",
        "link": "",
        "items": [
            {"title": "Agent", "text": state, "subtext": ""},
            {"title": "Miners", "text": miners, "subtext": "found"},
            {"title": "Telemetry", "text": telemetry, "subtext": "sent"},
        ],
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, body, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
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
        page = (PAGE
                .replace("__SYMBOL__", BRAIINS_SYMBOL)
                .replace("__AGENT_ID__", html.escape(current_agent_id()))
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
