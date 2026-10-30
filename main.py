#!/usr/bin/env python3
"""Main entrypoint for Adlos MCP Host Server (Adlos E2EE Bot + Multi-Host QR Hub + MCP Server)."""

import sys
import signal
import logging
import asyncio
import argparse

from src.config import config, derive_host_bot_id, load_paired_users
from src.adlos_bridge import AdlosBridge, generate_pairing_qr
from src.web_ui import WebDashboard, save_known_host


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


async def run_adlos_daemon() -> None:
    qr_path, ascii_qr = generate_pairing_qr()
    paired = load_paired_users()

    print("=" * 72)
    print(f"🚀 Adlos MCP Host-Server ('{config.bot_name}') gestartet!")
    print(f"• Hostname         : {config.hostname}")
    print(f"• Kontakt-ID (15c) : {config.bot_id}")
    print(f"• KI-Provider      : {config.ai_provider} / {config.gemini_model} (Key: {'✅' if config.gemini_api_key else '❌ noch nicht gesetzt'})")
    print(f"• Gekoppelte User  : {len(paired)} ({', '.join(u.get('name', k) for k, u in paired.items()) or 'noch keine'})")
    print(f"• QR-Web-Dashboard : http://localhost:{config.web_port}")
    print(f"• QR-Code Bild     : {qr_path}")
    print("=" * 72)
    print("\n📱 Scanne diesen QR-Code in der Adlos-App oder öffne http://localhost:" + str(config.web_port) + ":\n")
    print(ascii_qr)
    print("=" * 72)

    dashboard = WebDashboard()
    bridge = AdlosBridge()
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    await dashboard.start()
    await bridge.start()
    await stop_event.wait()
    await bridge.stop()
    await dashboard.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Adlos MCP Ubuntu Host Server")
    parser.add_argument(
        "--mcp",
        action="store_true",
        help="Startet im reinen MCP stdio-Modus (für Antigravity / Claude Desktop Integration)",
    )
    parser.add_argument(
        "--qr",
        nargs="?",
        const="",
        default=None,
        help="Zeigt den Kopplungs-QR-Code für diesen (oder einen angegebenen) Rechner im Terminal an",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Gibt direkt den aktuellen Systemstatus auf der Konsole aus",
    )
    args = parser.parse_args()

    if args.mcp:
        from src.mcp_server import run_mcp_stdio
        run_mcp_stdio()
        return

    setup_logging()

    if args.qr is not None:
        target_host = args.qr.strip()
        if target_host:
            entry = save_known_host(target_host)
            import io, json, qrcode
            payload = json.dumps({
                "action": "adlos_contact",
                "id": entry["bot_id"],
                "name": entry["bot_name"],
                "home_server": entry["server_url"],
            })
            qr = qrcode.QRCode(border=1)
            qr.add_data(payload)
            qr.make(fit=True)
            buf = io.StringIO()
            qr.print_ascii(out=buf, invert=True)
            print(f"🖥️  Rechner     : {entry['bot_name']} (Hostname: {target_host})")
            print(f"🔑 15-Char ID  : {entry['bot_id']}")
            print(f"🌐 Web-Hub     : http://localhost:{config.web_port}\n")
            print(buf.getvalue())
        else:
            qr_path, ascii_qr = generate_pairing_qr()
            print(f"🖥️  Rechner     : {config.bot_name} (Hostname: {config.hostname})")
            print(f"🔑 15-Char ID  : {config.bot_id}")
            print(f"🌐 Web-Hub     : http://localhost:{config.web_port}")
            print(f"🖼️  QR-Bild     : {qr_path}\n")
            print(ascii_qr)
        return

    if args.status:
        from src import tools
        print(tools.system_status())
        return

    try:
        asyncio.run(run_adlos_daemon())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
