"""Built-in Web Dashboard & Multi-Host QR Code Hub for Adlos MCP."""

import io
import json
import socket
import logging
from aiohttp import web
import qrcode

from .config import STATE_DIR, config, derive_host_bot_id, load_paired_users

_LOGGER = logging.getLogger(__name__)
KNOWN_HOSTS_FILE = STATE_DIR / "known_hosts.json"


def _get_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def load_known_hosts() -> list[dict]:
    """Loads list of registered/known hosts for the Multi-Host QR switcher."""
    default_host = {
        "hostname": config.hostname,
        "bot_id": config.bot_id,
        "bot_name": config.bot_name,
        "server_url": config.server_url,
        "is_local": True,
    }
    hosts = [default_host]
    if KNOWN_HOSTS_FILE.exists():
        try:
            saved = json.loads(KNOWN_HOSTS_FILE.read_text(encoding="utf-8"))
            if isinstance(saved, list):
                for h in saved:
                    if isinstance(h, dict) and h.get("hostname") and h.get("hostname") != config.hostname:
                        hosts.append(h)
        except Exception:
            pass
    return hosts


def save_known_host(hostname: str, bot_name: str = "", server_url: str = "") -> dict:
    clean_host = hostname.strip()
    bot_id = derive_host_bot_id(clean_host)
    final_name = bot_name.strip() or f"Ubuntu ({clean_host})"
    final_server = server_url.strip() or config.server_url

    hosts = [h for h in load_known_hosts() if not h.get("is_local")]
    hosts = [h for h in hosts if h.get("hostname", "").lower() != clean_host.lower()]
    new_entry = {
        "hostname": clean_host,
        "bot_id": bot_id,
        "bot_name": final_name,
        "server_url": final_server,
        "is_local": False,
    }
    hosts.append(new_entry)
    KNOWN_HOSTS_FILE.write_text(json.dumps(hosts, indent=2, ensure_ascii=False), encoding="utf-8")
    return new_entry


def delete_known_host(hostname: str) -> None:
    hosts = [h for h in load_known_hosts() if not h.get("is_local") and h.get("hostname", "").lower() != hostname.strip().lower()]
    KNOWN_HOSTS_FILE.write_text(json.dumps(hosts, indent=2, ensure_ascii=False), encoding="utf-8")


def make_qr_png_bytes(bot_id: str, bot_name: str, server_url: str) -> bytes:
    payload = json.dumps({
        "action": "adlos_contact",
        "id": bot_id,
        "name": bot_name,
        "home_server": server_url,
    })
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Adlos MCP – Multi-Rechner QR & Setup Hub</title>
  <style>
    :root {
      --bg: #0f172a;
      --card: #1e293b;
      --border: #334155;
      --accent: #00b4d8;
      --accent2: #10b981;
      --text: #f8fafc;
      --muted: #94a3b8;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: system-ui, -apple-system, sans-serif;
      background: var(--bg);
      color: var(--text);
      padding: 24px;
      line-height: 1.5;
    }
    .container { max-width: 1040px; margin: 0 auto; }
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 12px;
      margin-bottom: 24px;
      padding-bottom: 16px;
      border-bottom: 1px solid var(--border);
    }
    h1 { font-size: 1.6rem; display: flex; align-items: center; gap: 10px; }
    .badge {
      background: rgba(16, 185, 129, 0.18);
      color: #34d399;
      border: 1px solid rgba(16, 185, 129, 0.4);
      padding: 4px 12px;
      border-radius: 999px;
      font-size: 0.85rem;
      font-weight: 600;
    }
    .grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 24px;
    }
    @media (max-width: 800px) { .grid { grid-template-columns: 1fr; } }
    .card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 24px;
      box-shadow: 0 10px 25px rgba(0,0,0,0.25);
    }
    .card h2 { font-size: 1.2rem; margin-bottom: 14px; color: #38bdf8; }
    .host-tabs {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 18px;
    }
    .host-tab {
      background: #0f172a;
      color: var(--text);
      border: 1px solid var(--border);
      padding: 8px 14px;
      border-radius: 10px;
      cursor: pointer;
      font-size: 0.9rem;
      display: flex;
      align-items: center;
      gap: 8px;
      transition: all 0.15s;
    }
    .host-tab.active {
      background: linear-gradient(135deg, #00b4d8, #10b981);
      color: #fff;
      border-color: transparent;
      font-weight: 600;
    }
    .qr-box {
      background: #fff;
      padding: 16px;
      border-radius: 16px;
      display: inline-block;
      margin: 12px 0;
    }
    .qr-box img { width: 240px; height: 240px; display: block; }
    .code-pill {
      background: #0f172a;
      border: 1px solid var(--border);
      padding: 8px 14px;
      border-radius: 8px;
      font-family: monospace;
      font-size: 1.05rem;
      letter-spacing: 1px;
      color: #38bdf8;
      display: inline-block;
      margin-top: 6px;
    }
    label { display: block; font-size: 0.85rem; color: var(--muted); margin-top: 12px; margin-bottom: 4px; }
    input, select {
      width: 100%;
      padding: 10px 12px;
      border-radius: 8px;
      border: 1px solid var(--border);
      background: #0f172a;
      color: var(--text);
      font-size: 0.95rem;
    }
    button.btn {
      margin-top: 14px;
      width: 100%;
      padding: 11px 16px;
      border: none;
      border-radius: 10px;
      background: linear-gradient(135deg, #00b4d8, #10b981);
      color: white;
      font-weight: 600;
      font-size: 0.95rem;
      cursor: pointer;
    }
    button.btn:hover { opacity: 0.92; }
    .info-list { list-style: none; margin-top: 12px; font-size: 0.92rem; }
    .info-list li { padding: 6px 0; border-bottom: 1px solid rgba(255,255,255,0.06); }
    pre {
      background: #0f172a;
      padding: 12px;
      border-radius: 8px;
      border: 1px solid var(--border);
      overflow-x: auto;
      font-size: 0.82rem;
      color: #cbd5e1;
      margin-top: 10px;
    }
    .toast {
      margin-top: 10px;
      padding: 8px 12px;
      border-radius: 8px;
      background: rgba(16, 185, 129, 0.2);
      color: #34d399;
      display: none;
      font-size: 0.9rem;
    }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>🖥️ Adlos MCP – Multi-Rechner Hub</h1>
      <span class="badge">🟢 Adlos E2EE Dienst aktiv</span>
    </header>

    <div class="grid">
      <!-- LEFT CARD: QR CODE SWITCHER FOR MULTIPLE MACHINES -->
      <div class="card" style="text-align: center;">
        <h2>📱 Adlos Kopplungs-QR-Code</h2>
        <p style="color: var(--muted); font-size: 0.9rem; margin-bottom: 12px;">
          Wähle einen deiner Rechner aus und scanne den QR-Code in der <b>Adlos-App</b> unter <i>Kontakt hinzufügen → QR-Code scannen</i>:
        </p>

        <div class="host-tabs" id="hostTabs"></div>

        <div class="qr-box">
          <img id="qrImage" src="/qr.png" alt="Adlos QR Code">
        </div>

        <div style="margin-top: 8px;">
          <div style="font-weight: 700; font-size: 1.15rem;" id="selectedHostName">__BOT_NAME__</div>
          <div style="color: var(--muted); font-size: 0.82rem; margin-top: 4px;">15-stelliger Kontakt-Code (für manuelle Eingabe):</div>
          <div class="code-pill" id="selectedHostId">__BOT_ID__</div>
        </div>

        <div id="envSnippetBox" style="text-align: left; margin-top: 18px;">
          <div style="font-size: 0.82rem; color: var(--muted);">Konfiguration auf diesem Ziel-Rechner (automatisch über Hostname <code id="selectedHostname">__HOSTNAME__</code>):</div>
          <pre id="selectedEnvSnippet">ADLOS_BOT_ID=__BOT_ID__
ADLOS_BOT_NAME="__BOT_NAME__"
GEMINI_MODEL=gemini-3.5-flash-lite</pre>
        </div>
      </div>

      <!-- RIGHT COLUMN: ADD NEW MACHINE + AI SETTINGS -->
      <div style="display: flex; flex-direction: column; gap: 24px;">
        <div class="card">
          <h2>➕ Weiteren Rechner zum QR-Hub hinzufügen</h2>
          <p style="color: var(--muted); font-size: 0.88rem;">
            Gib einfach den Hostnamen (Ausgabe von <code>hostname</code> im Terminal) oder Namen eines weiteren Rechners ein. Jeder Rechner bekommt automatisch eine eigene 15-stellige Adlos-ID!
          </p>
          <form id="addHostForm">
            <label>Linux-Hostname des Rechners (z.B. <code>proxmox</code>, <code>raspi</code>, <code>workstation</code>)</label>
            <input type="text" id="newHostname" placeholder="z.B. mediaserver" required>
            <label>Anzeigename in der Adlos-App (optional)</label>
            <input type="text" id="newBotName" placeholder="z.B. Ubuntu (mediaserver)">
            <button type="submit" class="btn">QR-Code für diesen Rechner erzeugen</button>
          </form>
        </div>

        <div class="card">
          <h2>🧠 KI & Host-Einstellungen (__HOSTNAME__)</h2>
          <form id="configForm">
            <label>KI-Modell (Gemini Standard: <code>gemini-3.5-flash-lite</code>)</label>
            <select id="geminiModel">
              <option value="gemini-3.5-flash-lite" __SEL_35__>gemini-3.5-flash-lite (Standard & Schnell)</option>
              <option value="gemini-2.0-flash" __SEL_20__>gemini-2.0-flash</option>
              <option value="gemini-1.5-flash" __SEL_15__>gemini-1.5-flash</option>
            </select>

            <label>Gemini API-Key (__KEY_STATUS__)</label>
            <input type="password" id="geminiKey" placeholder="Leer lassen, um aktuellen Key zu behalten">

            <label>Ubuntu Passwort für GNOME-Keyring & sudo (__PASS_STATUS__)</label>
            <input type="password" id="hostPassword" placeholder="Entsperrt Chrome/Antigravity & apt Updates">

            <button type="submit" class="btn">Einstellungen speichern</button>
            <div class="toast" id="saveToast">✅ Einstellungen erfolgreich gespeichert!</div>
          </form>

          <ul class="info-list" style="margin-top: 16px;">
            <li><b>Aktiver Rechner:</b> <code>__HOSTNAME__</code> (IP: <code>__LOCAL_IP__</code>)</li>
            <li><b>Gekoppelte Adlos-Geräte:</b> __PAIRED_COUNT__</li>
            <li><b>Web-Hub Adresse:</b> <code>http://__LOCAL_IP__:__WEB_PORT__</code></li>
          </ul>
        </div>
      </div>
    </div>
  </div>

  <script>
    let hosts = __HOSTS_JSON__;
    let currentIndex = 0;

    function renderTabs() {
      const container = document.getElementById('hostTabs');
      container.innerHTML = '';
      hosts.forEach((h, idx) => {
        const btn = document.createElement('button');
        btn.className = 'host-tab' + (idx === currentIndex ? ' active' : '');
        btn.innerHTML = `<span>🖥️ ${h.bot_name}</span>`;
        btn.onclick = () => selectHost(idx);
        container.appendChild(btn);
      });
    }

    function selectHost(idx) {
      currentIndex = idx;
      const h = hosts[idx];
      document.getElementById('qrImage').src = `/qr.png?bot_id=${encodeURIComponent(h.bot_id)}&bot_name=${encodeURIComponent(h.bot_name)}`;
      document.getElementById('selectedHostName').textContent = h.bot_name;
      document.getElementById('selectedHostId').textContent = h.bot_id;
      document.getElementById('selectedHostname').textContent = h.hostname;
      document.getElementById('selectedEnvSnippet').textContent =
`# Auf Rechner '${h.hostname}' in .env:
ADLOS_BOT_ID=${h.bot_id}
ADLOS_BOT_NAME=${h.bot_name}
GEMINI_MODEL=gemini-3.5-flash-lite`;
      renderTabs();
    }

    document.getElementById('addHostForm').onsubmit = async (e) => {
      e.preventDefault();
      const hostname = document.getElementById('newHostname').value.trim();
      const bot_name = document.getElementById('newBotName').value.trim();
      if (!hostname) return;
      const res = await fetch('/api/hosts', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({hostname, bot_name})
      });
      if (res.ok) {
        hosts = await res.json();
        document.getElementById('newHostname').value = '';
        document.getElementById('newBotName').value = '';
        selectHost(hosts.length - 1);
      }
    };

    document.getElementById('configForm').onsubmit = async (e) => {
      e.preventDefault();
      const gemini_model = document.getElementById('geminiModel').value;
      const gemini_key = document.getElementById('geminiKey').value.trim();
      const host_password = document.getElementById('hostPassword').value.trim();
      const res = await fetch('/api/config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({gemini_model, gemini_key, host_password})
      });
      if (res.ok) {
        const toast = document.getElementById('saveToast');
        toast.style.display = 'block';
        setTimeout(() => location.reload(), 1000);
      }
    };

    renderTabs();
  </script>
</body>
</html>
"""


class WebDashboard:
    """Serves the local & LAN QR Code Hub and Multi-Host Manager."""

    def __init__(self) -> None:
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/qr.png", self._handle_qr_png)
        app.router.add_post("/api/hosts", self._handle_add_host)
        app.router.add_post("/api/config", self._handle_save_config)

        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        for candidate_port in (config.web_port, 8787, 8788, 8789, 8099):
            try:
                site = web.TCPSite(self._runner, "0.0.0.0", candidate_port)
                await site.start()
                config.set_runtime("ADLOS_WEB_PORT", str(candidate_port))
                _LOGGER.info(
                    "QR & Multi-Host Web Hub available at http://localhost:%s (LAN: http://%s:%s)",
                    candidate_port,
                    _get_local_ip(),
                    candidate_port,
                )
                return
            except OSError:
                continue
        _LOGGER.warning("Could not bind WebDashboard to any candidate port.")

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    async def _handle_index(self, request: web.Request) -> web.Response:
        hosts = [
            {k: v for k, v in h.items() if k != "server_url"}
            for h in load_known_hosts()
        ]
        paired = load_paired_users()
        cur_model = config.gemini_model

        html = (
            HTML_TEMPLATE
            .replace("__BOT_NAME__", config.bot_name)
            .replace("__BOT_ID__", config.bot_id)
            .replace("__HOSTNAME__", config.hostname)
            .replace("__LOCAL_IP__", _get_local_ip())
            .replace("__WEB_PORT__", str(config.web_port))
            .replace("__PAIRED_COUNT__", str(len(paired)))
            .replace("__KEY_STATUS__", "✅ gesetzt" if config.gemini_api_key else "❌ noch nicht gesetzt")
            .replace("__PASS_STATUS__", "✅ gesetzt" if config.host_user_password else "optional")
            .replace("__SEL_35__", "selected" if cur_model == "gemini-3.5-flash-lite" else "")
            .replace("__SEL_20__", "selected" if cur_model == "gemini-2.0-flash" else "")
            .replace("__SEL_15__", "selected" if cur_model == "gemini-1.5-flash" else "")
            .replace("__HOSTS_JSON__", json.dumps(hosts, ensure_ascii=False))
        )
        return web.Response(text=html, content_type="text/html")

    async def _handle_qr_png(self, request: web.Request) -> web.Response:
        bot_id = request.query.get("bot_id", config.bot_id)
        bot_name = request.query.get("bot_name", config.bot_name)
        png_bytes = make_qr_png_bytes(bot_id, bot_name, config.server_url)
        return web.Response(body=png_bytes, content_type="image/png")

    async def _handle_add_host(self, request: web.Request) -> web.Response:
        data = await request.json()
        hostname = str(data.get("hostname") or "").strip()
        bot_name = str(data.get("bot_name") or "").strip()
        if hostname:
            save_known_host(hostname, bot_name)
        hosts = [
            {k: v for k, v in h.items() if k != "server_url"}
            for h in load_known_hosts()
        ]
        return web.json_response(hosts)

    async def _handle_save_config(self, request: web.Request) -> web.Response:
        data = await request.json()
        model = str(data.get("gemini_model") or "").strip()
        key = str(data.get("gemini_key") or "").strip()
        pwd = str(data.get("host_password") or "").strip()

        if model:
            config.set_runtime("GEMINI_MODEL", model)
        if key:
            config.set_runtime("GEMINI_API_KEY", key)
        if pwd:
            config.set_runtime("HOST_USER_PASSWORD", pwd)
        return web.json_response({"ok": True})
