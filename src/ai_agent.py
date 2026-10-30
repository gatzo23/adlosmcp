"""Multi-provider AI agent (Gemini, OpenAI/Anthropic-compatible, Ollama) with MCP tool calling."""

import os
import re
import json
import inspect
import logging
import asyncio
from typing import Any

from .config import BASE_DIR, SCREENSHOTS_DIR, config, derive_host_bot_id
from . import tools
from .web_ui import make_qr_png_bytes, save_known_host

_LOGGER = logging.getLogger(__name__)

SYSTEM_INSTRUCTION = """Du bist der persönliche 'Ubuntu Server' Assistent von Kai und läufst als nativer Dienst (adlosmcp) direkt auf seinem Ubuntu-Rechner.
Du kommunizierst mit Kai über seine Ende-zu-Ende-verschlüsselte Adlos-App.

Deine Aufgaben und Werkzeuge:
1. **VirtualBox VMs steuern**: Insbesondere die VM 'HomeAssistant' starten (`vbox_manage_vm(vm_name='HomeAssistant', action='start')`), stoppen oder Status prüfen (`vbox_list_vms`).
2. **Docker & Portainer Container verwalten**: Alle Container prüfen (`docker_list_containers`), gestoppte Container nach einem Stromausfall/Wake-on-LAN starten (`docker_manage_container(container_name='all_stopped', action='start')`) oder einzelne Container neu starten und Logs lesen.
3. **Desktop-Programme & Schlüsselbund (Keyring)**: Programme wie 'antigravity' oder 'chrome' auf dem Ubuntu-Desktop starten (`desktop_manage_app`) und bei Bedarf vorher den GNOME-Schlüsselbund entsperren (`unlock_gnome_keyring`), damit keine Passwortabfrage das Programm blockiert.
4. **System- & Sicherheitsupdates (Zero-Day-Patches)**: Auf neue Ubuntu-Updates prüfen (`apt_check_updates`), Updates installieren (`apt_install_updates`) und prüfen, ob ein Neustart nötig ist (`system_reboot_control`).
5. **Screenshots & Systemstatus**: Den Gesamtzustand des Rechners abfragen (`system_status`) oder ein Live-Bild des Desktops aufnehmen (`take_desktop_screenshot`).

Antworte immer freundlich, präzise und auf Deutsch. Nutze bei Fragen zum Systemzustand oder Aktionen immer direkt deine Tools, statt zu raten!"""


class HostAIAgent:
    """Routes incoming Adlos chat messages to slash commands, Gemini, OpenAI, or Ollama with full tool access."""

    def __init__(self) -> None:
        self._history: dict[str, list[tuple[str, str]]] = {}

    def _append_history(self, room_id: str, role: str, text: str) -> None:
        hist = self._history.setdefault(room_id, [])
        hist.append((role, text))
        if len(hist) > 12:
            self._history[room_id] = hist[-12:]

    async def process_message(self, room_id: str, user_name: str, text: str) -> tuple[str, str | None]:
        """Processes a user message and returns (reply_text, optional_attachment_file_path)."""
        clean_text = text.strip()
        if not clean_text:
            return "Bitte gib eine Nachricht oder einen Befehl ein.", None

        # 1. Check built-in slash commands first
        if clean_text.startswith("/"):
            return await asyncio.to_thread(self._handle_slash_command, room_id, clean_text)

        # 2. Check if AI provider is configured
        provider = config.ai_provider
        if provider == "gemini" and not config.gemini_api_key:
            fallback_reply, screenshot_path = await asyncio.to_thread(
                self._smart_local_fallback, clean_text
            )
            if fallback_reply:
                return fallback_reply, screenshot_path
            return (
                "🤖 **Hinweis:** Es ist noch kein Gemini API-Key hinterlegt.\n\n"
                "Du kannst ihn direkt hier im verschlüsselten Chat setzen mit:\n"
                "`/setkey DEIN_GEMINI_API_KEY`\n\n"
                f"*(Standard-Modell ist `{config.gemini_model}`. Notfall-Befehle wie `status`, `starte homeassistant`, `docker`, `starte antigravity`, `updates prüfen`, `/qr` oder `/help` funktionieren auch ohne KI-Key!)*",
                None,
            )

        # 3. Execute with configured AI provider
        try:
            if provider == "gemini":
                reply = await asyncio.to_thread(self._run_gemini, room_id, clean_text)
            elif provider in ("openai", "ollama"):
                reply = await asyncio.to_thread(self._run_openai_compatible, room_id, clean_text, provider)
            else:
                reply = f"❌ Unbekannter AI_PROVIDER '{provider}'. Nutze `/provider gemini`, `/provider openai` oder `/provider ollama`."
        except Exception as err:
            _LOGGER.exception("AI provider error: %s", err)
            reply = f"⚠️ Fehler bei der KI-Anfrage ({provider}): {err}"

        screenshot_path = self._extract_screenshot_path(reply)
        return reply, screenshot_path

    def _extract_screenshot_path(self, text: str) -> str | None:
        m = re.search(r"(/home/[^\s`'\"]+screenshots/desktop_\d+_\d+\.png)", text)
        if m and os.path.exists(m.group(1)):
            return m.group(1)
        try:
            import time as _time
            files = sorted(SCREENSHOTS_DIR.glob("desktop_*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
            if files and (_time.time() - files[0].stat().st_mtime) < 8.0:
                return str(files[0])
        except Exception:
            pass
        return None

    def _handle_slash_command(self, room_id: str, cmd_text: str) -> tuple[str, str | None]:
        parts = cmd_text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/help", "/hilfe", "/start"):
            return (
                f"🛠️ **{config.bot_name} (Adlos MCP) – Befehle & Hilfe**\n\n"
                "Du kannst mir einfach in natürlicher Sprache schreiben (z.B. *\"Starte meine VM HomeAssistant\"*, *\"Welche Docker-Container laufen nicht?\"*, *\"Installiere die letzten Sicherheitsupdates\"* oder *\"Starte Antigravity\"*).\n\n"
                "**Direkte Schnellbefehle:**\n"
                "• `/status` – Kompletter System-, VM- & Docker-Status\n"
                "• `/vm` – VirtualBox VMs anzeigen\n"
                "• `/docker` – Alle Docker-Container anzeigen\n"
                "• `/updates` – Auf Ubuntu- & Security-Updates prüfen\n"
                "• `/screenshot` – Live-Screenshot vom Desktop senden\n"
                "• `/qr [rechnername]` – Kopplungs-QR-Code für diesen oder einen weiteren Rechner senden\n"
                "• `/setkey <API_KEY>` – Gemini (oder OpenAI) API-Key speichern\n"
                "• `/setpass <PASSWORT>` – Ubuntu-Passwort für Keyring & sudo lokal speichern\n"
                f"• `/provider <gemini|openai|ollama> [modell]` – KI-Anbieter wechseln (aktuell: `{config.ai_provider}` / `{config.gemini_model}`)\n"
                "• `/clear` – Chat-Kontext zurücksetzen",
                None,
            )

        if cmd == "/status":
            return tools.system_status(), None

        if cmd == "/vm":
            return tools.vbox_list_vms(), None

        if cmd == "/docker":
            return tools.docker_list_containers(), None

        if cmd == "/updates":
            return tools.apt_check_updates(), None

        if cmd == "/screenshot":
            res = tools.take_desktop_screenshot()
            spath = self._extract_screenshot_path(res)
            return res, spath

        if cmd == "/qr":
            target_host = arg.strip() if arg else config.hostname
            if arg:
                entry = save_known_host(target_host)
                bot_id = entry["bot_id"]
                bot_name = entry["bot_name"]
            else:
                bot_id = config.bot_id
                bot_name = config.bot_name
            png_bytes = make_qr_png_bytes(bot_id, bot_name, config.server_url)
            qr_file = SCREENSHOTS_DIR / f"qr_{target_host}.png"
            qr_file.write_bytes(png_bytes)
            msg = (
                f"📱 **Kopplungs-QR-Code für `{bot_name}`**\n"
                f"• **Hostname:** `{target_host}`\n"
                f"• **15-stellige Kontakt-ID:** `{bot_id}`\n"
                f"• **Web-Hub:** `http://{config.hostname}:{config.web_port}`"
            )
            return msg, str(qr_file)

        if cmd == "/setkey":
            if not arg:
                return "❌ Bitte gib einen API-Key an: `/setkey DEIN_API_KEY`", None
            if config.ai_provider == "openai":
                config.set_runtime("OPENAI_API_KEY", arg)
                return "✅ **OpenAI API-Key** wurde sicher lokal gespeichert!", None
            config.set_runtime("GEMINI_API_KEY", arg)
            return f"✅ **Gemini API-Key** wurde sicher lokal gespeichert (Modell: `{config.gemini_model}`)! Du kannst mir jetzt frei Fragen und Aufgaben schreiben.", None

        if cmd == "/setpass":
            if not arg:
                return "❌ Bitte gib das Passwort an: `/setpass DEIN_UBUNTU_PASSWORT`", None
            config.set_runtime("HOST_USER_PASSWORD", arg)
            unlock_msg = tools.unlock_gnome_keyring(arg)
            return f"✅ **Ubuntu Host-Passwort** sicher lokal (`chmod 600`) für Keyring & sudo hinterlegt!\n{unlock_msg}", None

        if cmd == "/provider":
            if not arg:
                return (
                    f"🧠 **Aktueller KI-Provider:** `{config.ai_provider}`\n"
                    f"• Gemini Modell: `{config.gemini_model}` (Key gesetzt: {'✅' if config.gemini_api_key else '❌'})\n"
                    f"• OpenAI Modell: `{config.openai_model}` (Key gesetzt: {'✅' if config.openai_api_key else '❌'})\n"
                    f"• Ollama Modell: `{config.ollama_model}` (`{config.ollama_base_url}`)\n\n"
                    "Wechseln mit: `/provider gemini gemini-3.5-flash-lite`, `/provider openai` oder `/provider ollama`",
                    None,
                )
            subparts = arg.split()
            prov = subparts[0].lower()
            if prov not in ("gemini", "openai", "ollama"):
                return "❌ Unterstützte Provider: `gemini`, `openai`, `ollama`", None
            config.set_runtime("AI_PROVIDER", prov)
            if len(subparts) > 1:
                model_name = subparts[1].strip()
                if prov == "gemini":
                    config.set_runtime("GEMINI_MODEL", model_name)
                elif prov == "openai":
                    config.set_runtime("OPENAI_MODEL", model_name)
                elif prov == "ollama":
                    config.set_runtime("OLLAMA_MODEL", model_name)
            return f"✅ KI-Provider auf **`{prov}`** (Modell: `{config.gemini_model if prov == 'gemini' else (config.openai_model if prov == 'openai' else config.ollama_model)}`) umgestellt!", None

        if cmd == "/clear":
            self._history.pop(room_id, None)
            return "🧹 Konversationsgedächtnis für diesen Raum wurde geleert.", None

        return f"❓ Unbekannter Befehl `{cmd}`. Tippe `/help` für eine Übersicht.", None

    def _smart_local_fallback(self, text: str) -> tuple[str | None, str | None]:
        """Allows essential commands to work out-of-the-box even before an AI API key is configured."""
        low = text.lower().strip()
        if low in ("status", "systemstatus", "wie geht es dem rechner", "läuft alles"):
            return tools.system_status(), None
        if low in ("hilfe", "help"):
            return self._handle_slash_command("default", "/help")
        if "screenshot" in low or "bildschirmfoto" in low:
            res = tools.take_desktop_screenshot()
            return res, self._extract_screenshot_path(res)
        if ("start" in low or "fahr" in low) and ("homeassistant" in low or "home assistant" in low or "vm" in low):
            return tools.vbox_manage_vm("HomeAssistant", "start"), None
        if ("stop" in low or "beende" in low) and ("homeassistant" in low or "home assistant" in low):
            return tools.vbox_manage_vm("HomeAssistant", "stop"), None
        if "start" in low and "antigravity" in low:
            return tools.desktop_manage_app("antigravity", "start"), None
        if "start" in low and "chrome" in low:
            return tools.desktop_manage_app("chrome", "start"), None
        if "docker" in low and ("liste" in low or "status" in low or "welche" in low or low == "docker"):
            return tools.docker_list_containers(), None
        if "update" in low and ("install" in low or "mach" in low):
            return tools.apt_install_updates(), None
        if "update" in low:
            return tools.apt_check_updates(), None
        return None, None

    def _run_gemini(self, room_id: str, user_text: str) -> str:
        """Executes the prompt using Google GenAI SDK with automatic tool calling and model fallback."""
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=config.gemini_api_key)
        hist = self._history.get(room_id, [])

        if hist:
            context_lines = ["Bisheriger Gesprächsverlauf:"]
            for role, msg in hist[-6:]:
                context_lines.append(f"{role}: {msg}")
            context_lines.append(f"\nAktuelle Nachricht von Kai:\n{user_text}")
            full_prompt = "\n".join(context_lines)
        else:
            full_prompt = user_text

        # Try configured model (default: gemini-3.5-flash-lite), with fallback chain if model ID changes on Google's side
        candidate_models = [config.gemini_model]
        for fb in ("gemini-3.5-flash-lite", "gemini-2.0-flash", "gemini-1.5-flash"):
            if fb not in candidate_models:
                candidate_models.append(fb)

        last_err = None
        response = None
        for model_name in candidate_models:
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=full_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_INSTRUCTION,
                        tools=tools.ALL_TOOLS,
                        temperature=0.2,
                    ),
                )
                break
            except Exception as err:
                last_err = err
                _LOGGER.warning("Gemini model '%s' failed (%s), trying next fallback...", model_name, err)

        if response is None:
            raise RuntimeError(f"Alle Gemini-Modelle fehlgeschlagen: {last_err}")

        reply_text = (response.text or "").strip()
        if not reply_text:
            reply_text = "✅ Befehl wurde ausgeführt."

        self._append_history(room_id, "Kai", user_text)
        self._append_history(room_id, "Assistent", reply_text)
        return reply_text

    def _build_openai_tool_schemas(self) -> list[dict[str, Any]]:
        schemas = []
        for fn in tools.ALL_TOOLS:
            sig = inspect.signature(fn)
            doc = (inspect.getdoc(fn) or "").strip()
            props: dict[str, Any] = {}
            required: list[str] = []
            for pname, param in sig.parameters.items():
                ptype = "string"
                if param.annotation is bool:
                    ptype = "boolean"
                elif param.annotation is int:
                    ptype = "integer"
                props[pname] = {"type": ptype, "description": f"Parameter {pname}"}
                if param.default is inspect.Parameter.empty:
                    required.append(pname)
            schemas.append({
                "type": "function",
                "function": {
                    "name": fn.__name__,
                    "description": doc[:500],
                    "parameters": {
                        "type": "object",
                        "properties": props,
                        "required": required,
                    },
                },
            })
        return schemas

    def _run_openai_compatible(self, room_id: str, user_text: str, provider: str) -> str:
        from openai import OpenAI

        if provider == "ollama":
            client = OpenAI(base_url=config.ollama_base_url, api_key="ollama")
            model = config.ollama_model
        else:
            if not config.openai_api_key:
                return "❌ Kein `OPENAI_API_KEY` gesetzt. Nutze `/setkey DEIN_KEY`."
            client = OpenAI(base_url=config.openai_base_url, api_key=config.openai_api_key)
            model = config.openai_model

        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_INSTRUCTION}]
        for role, msg in self._history.get(room_id, [])[-6:]:
            messages.append({"role": "user" if role == "Kai" else "assistant", "content": msg})
        messages.append({"role": "user", "content": user_text})

        tool_schemas = self._build_openai_tool_schemas()

        for _ in range(5):
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tool_schemas,
                tool_choice="auto",
            )
            choice = resp.choices[0].message
            if not choice.tool_calls:
                reply_text = (choice.content or "✅ Ausgeführt.").strip()
                self._append_history(room_id, "Kai", user_text)
                self._append_history(room_id, "Assistent", reply_text)
                return reply_text

            messages.append(choice.model_dump())
            for tc in choice.tool_calls:
                fn_name = tc.function.name
                fn = tools.TOOLS_BY_NAME.get(fn_name)
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except Exception:
                    args = {}
                if fn:
                    try:
                        result_str = str(fn(**args))
                    except Exception as err:
                        result_str = f"Error: {err}"
                else:
                    result_str = f"Unknown tool: {fn_name}"
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str,
                })

        return "✅ Werkzeuge wurden ausgeführt."


ai_agent = HostAIAgent()
