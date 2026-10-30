#!/usr/bin/env bash
set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
SERVICE_DIR="$HOME/.config/systemd/user"
SERVICE_FILE="$SERVICE_DIR/adlosmcp.service"
LOCAL_BIN="$HOME/.local/bin"
APPS_DIR="$HOME/.local/share/applications"
CURRENT_USER="$(whoami)"

cd "$PROJECT_DIR"

echo "🔧 [1/5] Prüfe .env Konfiguration..."
if [ ! -f "$PROJECT_DIR/.env" ]; then
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    chmod 600 "$PROJECT_DIR/.env"
    echo "✅ .env Datei erstellt (chmod 600)."
else
    chmod 600 "$PROJECT_DIR/.env"
    echo "✅ Bestehende .env Datei gefunden."
fi

echo "🐍 [2/5] Prüfe Python Virtual Environment (.venv) & Pakete..."
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements.txt" -q
echo "✅ Abhängigkeiten installiert."

echo "⚙️ [3/5] Richte systemd User-Service (adlosmcp.service) ein..."
mkdir -p "$SERVICE_DIR"

cat <<EOF > "$SERVICE_FILE"
[Unit]
Description=Adlos MCP Host Server & Multi-Host QR Hub
After=network-online.target graphical-session.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$PROJECT_DIR
ExecStart=$VENV_DIR/bin/python $PROJECT_DIR/main.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
Environment=DISPLAY=:0
Environment=WAYLAND_DISPLAY=wayland-0
Environment=XDG_RUNTIME_DIR=/run/user/%U
Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/%U/bus

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable adlosmcp.service
systemctl --user restart adlosmcp.service
echo "✅ systemd-Service 'adlosmcp.service' aktiviert und gestartet!"

echo "🌐 [4/5] Richte 'adlosmcp-qr' Schnellbefehl & Desktop-Starter ein..."
mkdir -p "$LOCAL_BIN" "$APPS_DIR"

cat <<EOF > "$LOCAL_BIN/adlosmcp-qr"
#!/usr/bin/env bash
if [ -n "\$1" ]; then
    "$VENV_DIR/bin/python" "$PROJECT_DIR/main.py" --qr "\$1"
else
    "$VENV_DIR/bin/python" "$PROJECT_DIR/main.py" --qr
    if command -v xdg-open >/dev/null 2>&1 && [ -n "\$DISPLAY\$WAYLAND_DISPLAY" ]; then
        xdg-open "http://localhost:8787" >/dev/null 2>&1 &
    fi
fi
EOF
chmod +x "$LOCAL_BIN/adlosmcp-qr"

cat <<EOF > "$APPS_DIR/adlosmcp-qr.desktop"
[Desktop Entry]
Name=Adlos MCP QR-Code & Rechner-Hub
Comment=Öffnet das Adlos MCP Multi-Rechner QR-Code Dashboard
Exec=xdg-open http://localhost:8787
Icon=network-server
Terminal=false
Type=Application
Categories=Utility;Network;
EOF
echo "✅ Befehl 'adlosmcp-qr' und Desktop-App eingerichtet (Web-Hub: http://localhost:8787)."

if [[ "$1" == "--sudoers" ]]; then
    echo "🛡️ [5/5] Richte passwortlose sudo-Regel für System-Updates (/etc/sudoers.d/adlosmcp) ein..."
    SUDOERS_TMP=$(mktemp)
    cat <<EOF > "$SUDOERS_TMP"
# Erlaubt dem Adlos MCP Server (User $CURRENT_USER) System-Updates und Neustarts ohne Passwortabfrage
$CURRENT_USER ALL=(ALL) NOPASSWD: /usr/bin/apt-get, /usr/bin/apt, /usr/sbin/shutdown, /usr/sbin/reboot
EOF
    chmod 0440 "$SUDOERS_TMP"
    sudo visudo -cf "$SUDOERS_TMP"
    sudo cp "$SUDOERS_TMP" /etc/sudoers.d/adlosmcp
    sudo chmod 0440 /etc/sudoers.d/adlosmcp
    rm -f "$SUDOERS_TMP"
    echo "✅ /etc/sudoers.d/adlosmcp erfolgreich eingerichtet!"
else
    echo "ℹ️ [5/5] Tipp: Für passwortlose apt-Updates im Urlaub kannst du einmalig ausführen:"
    echo "   ./setup_service.sh --sudoers"
fi

echo ""
echo "🎉 Fertig! Öffne den Multi-Rechner QR-Hub jederzeit im Browser unter: http://localhost:8787"
