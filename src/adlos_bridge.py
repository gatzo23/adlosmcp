"""Adlos PocketBase Realtime SSE listener, E2EE handshake, and message bridge."""

import os
import io
import json
import logging
import asyncio
import aiohttp
import qrcode

from .config import BASE_DIR, config, load_paired_users, save_paired_user
from .crypto import (
    all_candidate_rooms,
    derive_room_id,
    derive_room_id_alt,
    decrypt_text,
    encrypt_text,
    encrypt_bytes,
)
from .ai_agent import ai_agent
from . import tools

_LOGGER = logging.getLogger(__name__)


def generate_pairing_qr() -> tuple[str, str]:
    """Generates pairing QR image (pairing_qr.png) and returns (qr_file_path, ascii_qr)."""
    payload = json.dumps({
        "action": "adlos_contact",
        "id": config.bot_id,
        "name": config.bot_name,
        "home_server": config.server_url,
    })

    qr_path = BASE_DIR / "pairing_qr.png"
    img = qrcode.make(payload)
    img.save(qr_path, format="PNG")

    qr = qrcode.QRCode(border=1)
    qr.add_data(payload)
    qr.make(fit=True)
    buf = io.StringIO()
    qr.print_ascii(out=buf, invert=True)
    return str(qr_path), buf.getvalue()


class AdlosBridge:
    """Connects the Ubuntu Host MCP Agent to Adlos via PocketBase SSE & E2EE."""

    def __init__(self) -> None:
        self._running = False
        self._session: aiohttp.ClientSession | None = None
        self._sse_task: asyncio.Task | None = None
        self._poll_task: asyncio.Task | None = None
        self._processed_ids: set[str] = set()

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if config.adlos_token:
            headers["Authorization"] = f"Bearer {config.adlos_token}"
        return headers

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._session = aiohttp.ClientSession()
        self._sse_task = asyncio.create_task(self._sse_loop(), name="adlosmcp_sse")
        self._poll_task = asyncio.create_task(self._poll_loop(), name="adlosmcp_poll")
        _LOGGER.info(
            "AdlosBridge started for '%s' (ID: %s) on %s",
            config.bot_name,
            config.bot_id,
            config.server_url,
        )

        # Send boot notification if enabled and users are already paired
        if config.notify_on_boot:
            asyncio.create_task(self._send_boot_notification())

    async def stop(self) -> None:
        self._running = False
        if self._sse_task and not self._sse_task.done():
            self._sse_task.cancel()
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
        if self._session and not self._session.closed:
            await self._session.close()
        _LOGGER.info("AdlosBridge stopped.")

    async def _send_boot_notification(self) -> None:
        await asyncio.sleep(3)
        users = load_paired_users()
        if not users:
            return
        status_summary = await asyncio.to_thread(tools.system_status)
        boot_msg = f"⚡ **Ubuntu Server ist aktiv!**\n\n{status_summary}"
        for uid, uinfo in users.items():
            room_id = uinfo.get("room_id") or derive_room_id(uid, config.bot_id)
            room_key = uinfo.get("room_key")
            await self.send_text(room_id, boot_msg, room_key)

    async def _sse_loop(self) -> None:
        backoff = 2
        while self._running:
            realtime_url = f"{config.server_url}/api/realtime"
            try:
                assert self._session is not None
                headers = {}
                if config.adlos_token:
                    headers["Authorization"] = f"Bearer {config.adlos_token}"

                async with self._session.get(
                    realtime_url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=None, sock_read=65),
                ) as resp:
                    if resp.status != 200:
                        _LOGGER.warning("SSE connection failed (HTTP %s)", resp.status)
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, 30)
                        continue

                    backoff = 2
                    event_type = "message"
                    while self._running:
                        line_bytes = await resp.content.readline()
                        if not line_bytes:
                            break
                        line = line_bytes.decode("utf-8", errors="replace").strip()
                        if not line:
                            continue

                        if line.startswith("event:"):
                            event_type = line[6:].strip()
                        elif line.startswith("data:"):
                            data_str = line[5:].strip()
                            try:
                                data = json.loads(data_str)
                            except Exception:
                                continue

                            if event_type == "PB_CONNECT":
                                client_id = data.get("clientId")
                                if client_id:
                                    await self._subscribe_messages(client_id)
                            else:
                                action = data.get("action")
                                record = data.get("record")
                                if action in ("create", "update") and isinstance(record, dict):
                                    await self._process_record(record)
            except asyncio.CancelledError:
                break
            except Exception as err:
                _LOGGER.debug("SSE reconnect after error: %s", err)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def _subscribe_messages(self, client_id: str) -> None:
        assert self._session is not None
        url = f"{config.server_url}/api/realtime"
        payload = {"clientId": client_id, "subscriptions": ["messages"]}
        try:
            async with self._session.post(url, json=payload, headers=self._headers(), timeout=10) as resp:
                if resp.status in (200, 204):
                    _LOGGER.info("SSE subscribed to 'messages' (clientId=%s)", client_id)
        except Exception as err:
            _LOGGER.warning("Failed to subscribe SSE: %s", err)

    async def _poll_loop(self) -> None:
        await asyncio.sleep(4)
        while self._running:
            try:
                await self._poll_records()
            except asyncio.CancelledError:
                break
            except Exception as err:
                _LOGGER.debug("Poll error: %s", err)
            await asyncio.sleep(10)

    async def _poll_records(self) -> None:
        assert self._session is not None
        url = f"{config.server_url}/api/collections/messages/records?perPage=30&sort=-created"
        async with self._session.get(url, headers=self._headers(), timeout=10) as resp:
            if resp.status != 200:
                return
            data = await resp.json()
            for record in data.get("items", []):
                await self._process_record(record)

    async def _process_record(self, record: dict) -> None:
        record_id = str(record.get("id") or "").strip()
        if not record_id or record_id in self._processed_ids:
            return

        sender = str(record.get("sender") or "").strip()
        msg_type = str(record.get("type") or "").strip()
        room = str(record.get("room") or "").strip()
        raw_text = str(record.get("text") or "").strip()

        # Ignore our own outgoing messages
        if sender == config.bot_name or sender == config.bot_id:
            return

        # 1. Handshake processing
        if msg_type == "handshake":
            handled = await self._handle_handshake(record_id, room, raw_text)
            if handled:
                self._remember_id(record_id)
            return

        # 2. Regular chat message from a paired user
        paired_users = load_paired_users()
        matched_user = None
        for uid, uinfo in paired_users.items():
            uname = uinfo.get("name", "")
            candidate_rooms = all_candidate_rooms(uid, config.bot_id, uname)
            if uinfo.get("room_id"):
                candidate_rooms.add(uinfo["room_id"])
            if room in candidate_rooms:
                matched_user = (uid, uinfo)
                break

        if matched_user:
            self._remember_id(record_id)
            await self._handle_user_message(record_id, matched_user, room, sender, raw_text)

    def _remember_id(self, record_id: str) -> None:
        self._processed_ids.add(record_id)
        if len(self._processed_ids) > 1000:
            self._processed_ids.clear()

    async def _handle_handshake(self, record_id: str, room: str, raw_text: str) -> bool:
        try:
            payload = json.loads(raw_text) if raw_text.startswith("{") else {}
        except Exception:
            return False

        if not isinstance(payload, dict):
            return False

        sender_id = str(payload.get("sender_id") or "").strip()
        sender_name = str(payload.get("sender_name") or "").strip() or sender_id
        room_key = str(payload.get("room_key") or "").strip()

        if not sender_id:
            return False

        candidate_rooms = all_candidate_rooms(sender_id, config.bot_id, sender_name)
        if room not in candidate_rooms:
            return False

        # Primary room ID used by Adlos chat list (single '_')
        primary_room = derive_room_id(sender_id, config.bot_id)
        _LOGGER.info(
            "Received valid Adlos handshake from '%s' (%s) in room %s (primary: %s)",
            sender_name,
            sender_id,
            room,
            primary_room,
        )

        # Delete handshake record from PocketBase (Ephemeral Relay)
        await self._delete_record(record_id)

        # Save user pairing only once (since Adlos sends both roomId and roomIdAlt handshakes)
        existing = load_paired_users().get(sender_id)
        already_paired_same_key = existing and existing.get("room_key") == room_key
        save_paired_user(sender_id, sender_name, room_key, primary_room)

        if not already_paired_same_key:
            key_status = (
                "✅ Gemini KI ist aktiv!"
                if config.gemini_api_key
                else "ℹ️ Tipp: Sende `/setkey DEIN_GEMINI_API_KEY`, um die Gemini-KI zu aktivieren (oder `/help` für alle Direktbefehle)."
            )
            welcome = (
                f"🖥️ **Hallo {sender_name}!**\n"
                f"Dein **Ubuntu Server (`adlosmcp`)** ist jetzt Ende-zu-Ende-verschlüsselt mit deiner Adlos-App gekoppelt! 🔒🚀\n\n"
                f"Du kannst mir ab sofort von überall schreiben:\n"
                f"• *„Starte meine VM HomeAssistant“*\n"
                f"• *„Welche Docker-Container laufen gerade?“*\n"
                f"• *„Prüfe und installiere Sicherheitsupdates“*\n"
                f"• *„Starte Antigravity auf dem Desktop“*\n"
                f"• *„Schick mir einen Screenshot“*\n\n"
                f"{key_status}"
            )
            await self.send_text(primary_room, welcome, room_key)

        return True

    async def _handle_user_message(
        self,
        record_id: str,
        matched_user: tuple[str, dict],
        room: str,
        sender: str,
        raw_text: str,
    ) -> None:
        user_id, uinfo = matched_user
        user_name = uinfo.get("name") or sender or user_id
        room_key = uinfo.get("room_key")

        decrypted = decrypt_text(raw_text, room_key) if room_key else raw_text
        _LOGGER.info("Incoming Adlos message from %s in %s: %s", user_name, room, decrypted[:80])

        # Ephemeral relay: delete message record immediately
        await self._delete_record(record_id)

        # Update user's active room_id so replies always go to the exact room the user is writing in
        if uinfo.get("room_id") != room:
            save_paired_user(user_id, user_name, room_key or "", room)

        # Process via AI Agent & MCP Tools
        reply_text, attachment_path = await ai_agent.process_message(room, user_name, decrypted)

        if attachment_path and os.path.exists(attachment_path):
            await self.send_file(room, reply_text, attachment_path, room_key)
        elif reply_text:
            await self.send_text(room, reply_text, room_key)

    async def send_text(self, target_room: str, text: str, room_key: str | None) -> bool:
        if not self._session:
            return False
        url = f"{config.server_url}/api/collections/messages/records"
        encrypted = encrypt_text(text, room_key) if room_key else text
        payload = {
            "room": target_room,
            "sender": config.bot_name,
            "text": encrypted,
            "type": "text",
        }
        try:
            async with self._session.post(url, json=payload, headers=self._headers(), timeout=15) as resp:
                if resp.status in (200, 201, 204):
                    return True
                body = await resp.text()
                _LOGGER.error("Failed to send Adlos message (HTTP %s): %s", resp.status, body)
        except Exception as err:
            _LOGGER.error("Exception sending Adlos message: %s", err)
        return False

    async def send_file(self, target_room: str, text: str, file_path: str, room_key: str | None) -> bool:
        if not self._session:
            return False
        url = f"{config.server_url}/api/collections/messages/records"
        headers = {}
        if config.adlos_token:
            headers["Authorization"] = f"Bearer {config.adlos_token}"

        try:
            with open(file_path, "rb") as f:
                raw_bytes = f.read()
        except Exception as err:
            _LOGGER.error("Could not read attachment %s: %s", file_path, err)
            return await self.send_text(target_room, text, room_key)

        enc_bytes = encrypt_bytes(raw_bytes, room_key) if room_key else raw_bytes
        enc_text = encrypt_text(text, room_key) if (text and room_key) else (text or "")

        form = aiohttp.FormData()
        form.add_field("text", enc_text)
        form.add_field("sender", config.bot_name)
        form.add_field("room", target_room)
        form.add_field("type", "image")
        form.add_field(
            "file",
            enc_bytes,
            filename=os.path.basename(file_path),
            content_type="application/octet-stream" if room_key else "image/png",
        )

        try:
            async with self._session.post(url, data=form, headers=headers, timeout=25) as resp:
                if resp.status in (200, 201, 204):
                    return True
                body = await resp.text()
                _LOGGER.error("Failed to send Adlos file (HTTP %s): %s", resp.status, body)
        except Exception as err:
            _LOGGER.error("Exception sending Adlos file: %s", err)
        return False

    async def _delete_record(self, record_id: str) -> None:
        if not self._session:
            return
        url = f"{config.server_url}/api/collections/messages/records/{record_id}"
        try:
            async with self._session.delete(url, headers=self._headers(), timeout=10) as resp:
                if resp.status in (200, 204):
                    _LOGGER.debug("Ephemeral relay deleted record %s", record_id)
        except Exception:
            pass
