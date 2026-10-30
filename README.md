# Adlos MCP Host-Server (`adlosmcp`) 🖥️🤖🔒

**Version 1.0.0** — Offizielles Server- & Desktop-Add-on für die **Adlos App**.

Mit `adlosmcp` steuerst du deine Ubuntu- und Linux-Rechner (VirtualBox VMs wie **HomeAssistant**, **Docker/Portainer-Container**, **System- & Sicherheits-Updates** sowie Desktop-Programme) direkt von unterwegs über deine **Adlos-App** – vollständig Ende-zu-Ende-verschlüsselt (AES-256-CBC) und ohne Port-Forwarding im Router.

---

## ✨ Features

1. **🖥️ Multi-Rechner-Unterstützung & Web-QR-Hub**
   - Jeder Rechner erhält anhand seines Linux-Hostnamens automatisch eine eindeutige 15-stellige Adlos-ID und erscheint als eigener Kontakt in der Adlos-App (z. B. `Ubuntu (homeserver)`, `Ubuntu (workstation)`).
   - **Integriertes Web-Dashboard (`http://localhost:8787`):** Zeigt den Kopplungs-QR-Code für diesen sowie beliebig viele weitere Rechner im Browser an und erlaubt die direkte Konfiguration von KI-Modellen und API-Keys.
2. **📦 VirtualBox Steuerung (`VBoxManage`)**
   - Prüft und startet virtuelle Maschinen (wie `HomeAssistant`) nach einem Stromausfall oder Wake-on-LAN im Hintergrund (`--type headless`).
   - Unterstützt sauberes Herunterfahren (`acpipowerbutton`), Zustand speichern (`savestate`) und Neustart (`reset`).
3. **🐳 Docker & Portainer Verwaltung (`docker`)**
   - Listet alle Container samt Status auf.
   - Startet einzelne oder alle unerwartet gestoppten Container (`all_stopped`) nach einem Systemausfall neu und ruft Container-Logs ab.
4. **🔐 Desktop-Apps & GNOME-Schlüsselbund (`unlock_gnome_keyring`)**
   - Löst das Ubuntu Auto-Login-Problem (`login.keyring` Passwortabfrage beim Start von Desktop-Apps wie Chrome oder Entwicklungsumgebungen):
     - Entsperrt den GNOME-Schlüsselbund automatisch über `gnome-keyring-daemon --unlock` oder startet Chromium/Electron-Apps mit `--password-store=basic`.
   - Erstellt auf Wunsch Live-Screenshots des Desktops und sendet sie Ende-zu-Ende-verschlüsselt in den Adlos-Chat.
5. **🛡️ Zero-Day- & Sicherheits-Updates (`apt`)**
   - Prüft auf verfügbare Ubuntu-Updates und hebt Sicherheits-Patches (`security`) hervor.
   - Installiert Updates vollautomatisch im Hintergrund (`DEBIAN_FRONTEND=noninteractive`) und prüft `/var/run/reboot-required`.
6. **🧠 Multi-KI-Architektur (Gemini Standard + OpenAI + Ollama)**
   - Nutzt standardmäßig **Google Gemini** (`gemini-3.5-flash-lite`).
   - Jederzeit im Chat oder über das Web-Dashboard umschaltbar auf **OpenAI** oder lokales **Ollama**.
   - **Notfall-Fallback:** Wichtige Kommandos (`status`, `starte homeassistant`, `/docker`, `/updates`, `/screenshot`, `/qr`) funktionieren auch ohne hinterlegten KI-Key sofort!

---

## 🚀 Installation & Schnellstart

### 1. Repository klonen & Dienst einrichten
```bash
git clone https://github.com/gatzo23/adlosmcp.git
cd adlosmcp
chmod +x setup_service.sh
./setup_service.sh
```

### 2. QR-Code aufrufen & mit der Adlos-App koppeln
Öffne im Browser das lokale **Multi-Rechner QR-Dashboard**:
```text
http://localhost:8787
```
*(Oder rufe im Terminal jederzeit `adlosmcp-qr` auf).*

1. Öffne auf deinem Smartphone die **Adlos-App** -> **Kontakt hinzufügen** -> **QR-Code scannen**.
2. Scanne den QR-Code vom Bildschirm.
3. Dein Rechner ist sofort Ende-zu-Ende-verschlüsselt gekoppelt!

### 3. Gemini API-Key setzen (im Web-Dashboard oder per Chat)
Trage deinen API-Key entweder direkt unter `http://localhost:8787` ein oder sende im verschlüsselten Adlos-Chat:
```text
/setkey DEIN_GEMINI_API_KEY
```

### 4. Optional: Passwortlose System-Updates aktivieren
Damit `apt-get update` und `apt-get upgrade` aus der Ferne ohne Passwortabfrage durchlaufen:
```bash
./setup_service.sh --sudoers
```
Alternativ kannst du dein Host-Passwort für den GNOME-Schlüsselbund & `sudo` auch verschlüsselt per `/setpass DEIN_PASSWORT` im Chat hinterlegen.

---

## 💬 Übersicht der Chat-Befehle

Neben freier Konversation in natürlicher Sprache stehen folgende Direktbefehle bereit:
- `/status` – Kompletter System-, VM- & Docker-Status
- `/vm` – VirtualBox VMs anzeigen
- `/docker` – Alle Docker-Container anzeigen
- `/updates` – Auf Ubuntu- & Security-Updates prüfen
- `/screenshot` – Live-Screenshot vom Desktop senden
- `/qr [rechnername]` – Kopplungs-QR-Code für diesen oder einen weiteren Rechner generieren
- `/setkey <API_KEY>` – Gemini / OpenAI API-Key speichern
- `/setpass <PASSWORT>` – Ubuntu-Passwort für Keyring & `sudo` lokal speichern (`chmod 600`)
- `/provider <gemini|openai|ollama> [modell]` – KI-Anbieter oder Modell wechseln
- `/help` – Hilfe anzeigen

---

## 🔌 Nutzung als lokaler MCP-Server (stdio)

`adlosmcp` implementiert das offizielle **Model Context Protocol (MCP)** über `FastMCP`:
```json
{
  "mcpServers": {
    "adlosmcp": {
      "command": "/pfad/zu/adlosmcp/.venv/bin/python",
      "args": ["/pfad/zu/adlosmcp/main.py", "--mcp"]
    }
  }
}
```

## 📄 Lizenz
MIT License
