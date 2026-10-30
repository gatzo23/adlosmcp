"""Host system control tools for VirtualBox, Docker, Ubuntu Updates, Desktop Apps, and Keyring."""

import os
import re
import time
import shutil
import socket
import logging
import datetime
import subprocess
from pathlib import Path
import psutil

from .config import config, SCREENSHOTS_DIR

_LOGGER = logging.getLogger(__name__)


def _get_desktop_env() -> dict[str, str]:
    """Returns environment variables required to interact with User kai's active Ubuntu GUI session."""
    uid = os.getuid()
    runtime_dir = f"/run/user/{uid}"
    env = os.environ.copy()
    env.setdefault("DISPLAY", ":0")
    env.setdefault("WAYLAND_DISPLAY", "wayland-0")
    env.setdefault("XDG_RUNTIME_DIR", runtime_dir)
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime_dir}/bus")
    env.setdefault("XDG_SESSION_TYPE", "wayland")
    return env


def _run_cmd(
    cmd: list[str],
    timeout: int = 60,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Helper to run a subprocess safely and return (returncode, stdout, stderr)."""
    try:
        proc = subprocess.run(
            cmd,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", f"Zeitüberschreitung nach {timeout} Sekunden bei Befehl: {' '.join(cmd)}"
    except Exception as err:
        return 1, "", f"Fehler beim Ausführen von {' '.join(cmd)}: {err}"


def _run_sudo_cmd(
    cmd: list[str],
    sudo_password: str = "",
    timeout: int = 300,
    extra_env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Runs a command via sudo (first tries passwordless sudo -n, then falls back to sudo -S with password)."""
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)

    # 1. Try passwordless sudo -n
    env_flags = [f"{k}={v}" for k, v in (extra_env or {}).items()]
    code, out, err = _run_cmd(["sudo", "-n"] + env_flags + cmd, timeout=timeout, env=env)
    if code == 0:
        return code, out, err

    # 2. Fallback to password via sudo -S
    pwd = (sudo_password or config.host_user_password or "").strip()
    if not pwd:
        return (
            code,
            out,
            "Für diesen Befehl werden sudo-Rechte benötigt. Bitte entweder `setup_service.sh --sudoers` "
            "einmalig ausführen oder `HOST_USER_PASSWORD` in der `.env` (bzw. im Chat) angeben.\n"
            f"Details: {err}",
        )

    return _run_cmd(
        ["sudo", "-S", "-p", ""] + env_flags + cmd,
        timeout=timeout,
        input_text=pwd + "\n",
        env=env,
    )


# =====================================================================
# 1. SYSTEM STATUS & HEALTH
# =====================================================================

def system_status() -> str:
    """Gibt einen umfassenden Überblick über den Ubuntu-Rechner zurück (CPU, RAM, Speicherplatz, Uptime, VirtualBox-VMs, Docker-Container und Neustart-Status)."""
    hostname = socket.gethostname()
    boot_dt = datetime.datetime.fromtimestamp(psutil.boot_time())
    uptime_delta = datetime.datetime.now() - boot_dt
    days = uptime_delta.days
    hours, rem = divmod(uptime_delta.seconds, 3600)
    minutes, _ = divmod(rem, 60)
    uptime_str = f"{days}d {hours}h {minutes}m" if days > 0 else f"{hours}h {minutes}m"

    cpu_pct = psutil.cpu_percent(interval=0.4)
    load1, load5, load15 = os.getloadavg()
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    # Check temperatures if available
    temp_str = "N/A"
    try:
        temps = psutil.sensors_temperatures()
        for sensor_name in ("coretemp", "k10temp", "acpitz", "nvme"):
            if sensor_name in temps and temps[sensor_name]:
                temp_str = f"{temps[sensor_name][0].current:.1f}°C"
                break
    except Exception:
        pass

    # Check VirtualBox running VMs
    _, running_vms_out, _ = _run_cmd(["VBoxManage", "list", "runningvms"], timeout=10)
    running_vms = [line.split('"')[1] for line in running_vms_out.splitlines() if '"' in line]
    vbox_summary = ", ".join(running_vms) if running_vms else "Keine VM aktiv ⚠️"

    # Check Docker containers
    _, docker_out, _ = _run_cmd(["docker", "ps", "-a", "--format", "{{.Names}}|{{.State}}"], timeout=10)
    total_containers = 0
    running_containers = 0
    stopped_names = []
    for line in docker_out.splitlines():
        if "|" in line:
            total_containers += 1
            cname, cstate = line.split("|", 1)
            if cstate.strip() == "running":
                running_containers += 1
            else:
                stopped_names.append(cname)

    # Check desktop apps
    _, agy_out, _ = _run_cmd(["pgrep", "-f", "/opt/antigravity/antigravity|/usr/local/bin/antigravity"])
    _, chrome_out, _ = _run_cmd(["pgrep", "-f", "google-chrome"])
    agy_running = "✅ läuft" if agy_out else "❌ aus"
    chrome_running = "✅ läuft" if chrome_out else "❌ aus"

    reboot_req = os.path.exists("/var/run/reboot-required")
    reboot_str = "⚠️ Ja (Neustart empfohlen)" if reboot_req else "✅ Nein"

    lines = [
        f"🖥️ **Systemstatus: {hostname}**",
        f"• **Uptime:** {uptime_str} (Start: {boot_dt.strftime('%d.%m.%Y %H:%M')})",
        f"• **CPU:** {cpu_pct:.1f}% (Load: {load1:.2f}, {load5:.2f}, {load15:.2f}) | **Temp:** {temp_str}",
        f"• **RAM:** {mem.used / (1024**3):.1f} GB / {mem.total / (1024**3):.1f} GB ({mem.percent}%)",
        f"• **Festplatte (/):** {disk.used / (1024**3):.1f} GB / {disk.total / (1024**3):.1f} GB ({disk.percent}% belegt, {disk.free / (1024**3):.1f} GB frei)",
        f"• **VirtualBox aktive VMs:** {vbox_summary}",
        f"• **Docker Container:** {running_containers}/{total_containers} aktiv",
        f"• **Desktop-Apps:** Antigravity: {agy_running} | Chrome: {chrome_running}",
        f"• **Neustart erforderlich:** {reboot_str}",
    ]
    if stopped_names:
        lines.append(f"• **Gestoppte Container ({len(stopped_names)}):** {', '.join(stopped_names[:8])}")
    return "\n".join(lines)


# =====================================================================
# 2. VIRTUALBOX VM MANAGEMENT (e.g. HomeAssistant)
# =====================================================================

def _parse_vbox_vms() -> list[tuple[str, str]]:
    """Returns list of (vm_name, vm_uuid) from VBoxManage list vms."""
    code, out, _ = _run_cmd(["VBoxManage", "list", "vms"], timeout=15)
    if code != 0 or not out:
        return []
    vms = []
    for line in out.splitlines():
        m = re.match(r'^"([^"]+)"\s+\{([^}]+)\}', line.strip())
        if m:
            vms.append((m.group(1), m.group(2)))
    return vms


def _resolve_vm_name(query: str) -> str | None:
    """Fuzzy matches a VM name like 'homeassistant' or 'home assistant' to actual VBox VM name 'HomeAssistant'."""
    vms = _parse_vbox_vms()
    if not vms:
        return None
    q_clean = re.sub(r"[^a-z0-9]", "", query.lower())
    for name, uuid in vms:
        if query.strip() == name or query.strip() == uuid:
            return name
        n_clean = re.sub(r"[^a-z0-9]", "", name.lower())
        if q_clean and (q_clean == n_clean or q_clean in n_clean or n_clean in q_clean):
            return name
    if q_clean in ("ha", "hass", "homeassistant"):
        for name, _ in vms:
            if "home" in name.lower() or "assist" in name.lower():
                return name
    return None


def vbox_list_vms() -> str:
    """Listet alle VirtualBox Virtuellen Maschinen (VMs) und ihren aktuellen Status (running / stopped) auf."""
    vms = _parse_vbox_vms()
    if not vms:
        return "Keine VirtualBox-VMs gefunden (oder VBoxManage nicht verfügbar)."

    _, running_out, _ = _run_cmd(["VBoxManage", "list", "runningvms"], timeout=15)
    running_names = {
        m.group(1)
        for line in running_out.splitlines()
        if (m := re.match(r'^"([^"]+)"', line.strip()))
    }

    lines = ["📦 **VirtualBox Virtuelle Maschinen:**"]
    for name, uuid in vms:
        is_run = name in running_names
        status_icon = "🟢 running" if is_run else "🔴 stopped"
        lines.append(f"• **{name}** (`{uuid}`) — {status_icon}")
    return "\n".join(lines)


def vbox_manage_vm(vm_name: str = "HomeAssistant", action: str = "start") -> str:
    """Steuert eine VirtualBox VM (z.B. 'HomeAssistant').

    Args:
        vm_name: Name der VM (z.B. 'HomeAssistant' oder 'homeassistant').
        action: Gewünschte Aktion:
            - 'start' oder 'headless': Startet die VM im Hintergrund (headless).
            - 'start_gui': Startet die VM mit sichtbarem Fenster auf dem Desktop.
            - 'stop' oder 'acpi': Fährt die VM sauber über ACPI-Powerbutton herunter.
            - 'poweroff': Erzwingt sofortiges Ausschalten (wie Stecker ziehen).
            - 'reset' oder 'restart': Startet die VM neu.
            - 'savestate': Speichert den aktuellen Zustand und pausiert die VM.
            - 'status': Zeigt detaillierte Informationen zur VM.
    """
    resolved = _resolve_vm_name(vm_name)
    if not resolved:
        available = ", ".join(n for n, _ in _parse_vbox_vms()) or "keine"
        return f"❌ VM '{vm_name}' wurde nicht gefunden. Verfügbare VMs: {available}"

    _, running_out, _ = _run_cmd(["VBoxManage", "list", "runningvms"], timeout=15)
    is_running = f'"{resolved}"' in running_out

    act = action.strip().lower()

    if act in ("status", "info"):
        code, info_out, err = _run_cmd(["VBoxManage", "showvminfo", resolved, "--machinereadable"], timeout=15)
        if code != 0:
            return f"❌ Fehler beim Abrufen des Status für '{resolved}': {err}"
        info_map = {}
        for line in info_out.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                info_map[k.strip()] = v.strip().strip('"')
        state = info_map.get("VMState", "unbekannt")
        state_time = info_map.get("VMStateChangeTime", "")
        memory = info_map.get("memory", "?")
        cpus = info_map.get("cpus", "?")
        return (
            f"📦 **VirtualBox VM '{resolved}':**\n"
            f"• **Status:** {'🟢 ' + state if is_running else '🔴 ' + state}\n"
            f"• **RAM / CPUs:** {memory} MB / {cpus} Kerne\n"
            f"• **Letzte Statusänderung:** {state_time}"
        )

    if act in ("start", "headless", "start_gui", "gui"):
        if is_running:
            return f"✅ Die VirtualBox-VM **'{resolved}'** läuft bereits!"
        vm_type = "gui" if "gui" in act else "headless"
        env = _get_desktop_env() if vm_type == "gui" else None
        code, out, err = _run_cmd(
            ["VBoxManage", "startvm", resolved, "--type", vm_type],
            timeout=45,
            env=env,
        )
        if code == 0:
            return f"🚀 VirtualBox-VM **'{resolved}'** wurde erfolgreich gestartet (Modus: `{vm_type}`).\n{out}"
        return f"❌ Fehler beim Starten von **'{resolved}'**: {err or out}"

    if act in ("stop", "acpi", "shutdown"):
        if not is_running:
            return f"ℹ️ Die VM **'{resolved}'** ist bereits ausgeschaltet."
        code, out, err = _run_cmd(
            ["VBoxManage", "controlvm", resolved, "acpipowerbutton"],
            timeout=30,
        )
        if code == 0:
            return f"🛑 Sauberes Herunterfahren (ACPI Powerbutton) an VM **'{resolved}'** gesendet."
        return f"❌ Fehler beim Herunterfahren von **'{resolved}'**: {err or out}"

    if act in ("poweroff", "kill", "force_stop"):
        if not is_running:
            return f"ℹ️ Die VM **'{resolved}'** ist bereits ausgeschaltet."
        code, out, err = _run_cmd(["VBoxManage", "controlvm", resolved, "poweroff"], timeout=30)
        if code == 0:
            return f"⚡ VM **'{resolved}'** wurde hart ausgeschaltet (`poweroff`)."
        return f"❌ Fehler bei poweroff von **'{resolved}'**: {err or out}"

    if act in ("savestate", "save"):
        if not is_running:
            return f"ℹ️ Die VM **'{resolved}'** läuft aktuell nicht."
        code, out, err = _run_cmd(["VBoxManage", "controlvm", resolved, "savestate"], timeout=60)
        if code == 0:
            return f"💾 Zustand der VM **'{resolved}'** wurde gespeichert und die VM beendet."
        return f"❌ Fehler beim Speichern des Zustands von **'{resolved}'**: {err or out}"

    if act in ("reset", "restart", "reboot"):
        if not is_running:
            # Start it if it was stopped
            code, out, err = _run_cmd(["VBoxManage", "startvm", resolved, "--type", "headless"], timeout=45)
            if code == 0:
                return f"🚀 VM **'{resolved}'** war gestoppt und wurde nun im Hintergrund gestartet."
            return f"❌ Fehler beim Starten von **'{resolved}'**: {err or out}"
        code, out, err = _run_cmd(["VBoxManage", "controlvm", resolved, "reset"], timeout=30)
        if code == 0:
            return f"🔄 VM **'{resolved}'** wurde neu gestartet (`reset`)."
        return f"❌ Fehler beim Neustart von **'{resolved}'**: {err or out}"

    return f"❌ Unbekannte Aktion '{action}'. Erlaubt: start, start_gui, stop, poweroff, reset, savestate, status."


# =====================================================================
# 3. DOCKER & PORTAINER CONTAINER MANAGEMENT
# =====================================================================

def docker_list_containers(show_all: bool = True) -> str:
    """Listet alle Docker-Container auf dem Rechner inklusive Status auf."""
    cmd = ["docker", "ps"]
    if show_all:
        cmd.append("-a")
    cmd.extend(["--format", "{{.Names}}\t{{.Status}}\t{{.Image}}"])
    code, out, err = _run_cmd(cmd, timeout=15)
    if code != 0:
        return f"❌ Fehler beim Abrufen der Docker-Container: {err}"
    if not out:
        return "Keine Docker-Container gefunden."

    lines = ["🐳 **Docker-Container Übersicht:**"]
    for row in out.splitlines():
        parts = row.split("\t")
        if len(parts) >= 2:
            name, status = parts[0], parts[1]
            icon = "🟢" if status.lower().startswith("up") else "🔴"
            lines.append(f"• {icon} **{name}** — `{status}`")
    return "\n".join(lines)


def _resolve_container_name(query: str) -> list[str]:
    """Resolves a container name or substring to matching Docker container names."""
    code, out, _ = _run_cmd(["docker", "ps", "-a", "--format", "{{.Names}}"], timeout=10)
    if code != 0 or not out:
        return []
    all_names = [n.strip() for n in out.splitlines() if n.strip()]
    q = query.strip().lower()
    for n in all_names:
        if n.lower() == q:
            return [n]
    return [n for n in all_names if q in n.lower()]


def docker_manage_container(container_name: str, action: str = "restart") -> str:
    """Startet, stoppt oder startet einen Docker-Container neu.

    Args:
        container_name: Name des Containers (z.B. 'portainer', 'frigate', 'pocketbase_app') oder 'all_stopped', um gestoppte Container zu starten.
        action: 'start', 'stop' oder 'restart'.
    """
    act = action.strip().lower()
    if act not in ("start", "stop", "restart"):
        return f"❌ Ungültige Docker-Aktion '{action}'. Erlaubt sind: start, stop, restart."

    if container_name.strip().lower() in ("all_stopped", "stopped"):
        _, out, _ = _run_cmd(
            ["docker", "ps", "-a", "--filter", "status=exited", "--format", "{{.Names}}"],
            timeout=15,
        )
        # Skip one-shot backup/watchtower containers that intentionally exit 0
        skip_oneshot = {"nextcloud-aio-borgbackup", "nextcloud-aio-watchtower"}
        to_start = [n.strip() for n in out.splitlines() if n.strip() and n.strip() not in skip_oneshot]
        if not to_start:
            return "✅ Es gibt aktuell keine unerwartet gestoppten Container."
        code, sout, serr = _run_cmd(["docker", "start"] + to_start, timeout=60)
        if code == 0:
            return f"🚀 Gestoppte Container gestartet: {', '.join(to_start)}"
        return f"⚠️ Fehler beim Starten einiger Container: {serr or sout}"

    matches = _resolve_container_name(container_name)
    if not matches:
        return f"❌ Kein Docker-Container passend zu '{container_name}' gefunden."
    if len(matches) > 1 and matches[0].lower() != container_name.strip().lower():
        return (
            f"ℹ️ Mehrere Container passen auf '{container_name}': {', '.join(matches)}. "
            "Bitte gib den genauen Namen an."
        )

    target = matches[0]
    code, out, err = _run_cmd(["docker", act, target], timeout=60)
    if code == 0:
        return f"✅ Docker-Container **'{target}'** erfolgreich mit Aktion `{act}` ausgeführt."
    return f"❌ Fehler bei `docker {act} {target}`: {err or out}"


def docker_container_logs(container_name: str, tail_lines: int = 40) -> str:
    """Ruft die letzten Log-Zeilen eines Docker-Containers ab."""
    matches = _resolve_container_name(container_name)
    if not matches:
        return f"❌ Kein Container passend zu '{container_name}' gefunden."
    target = matches[0]
    tail = max(5, min(int(tail_lines), 200))
    code, out, err = _run_cmd(["docker", "logs", "--tail", str(tail), target], timeout=15)
    combined = (out + "\n" + err).strip()
    if not combined:
        return f"ℹ️ Keine Log-Ausgaben für Container '{target}'."
    if len(combined) > 3500:
        combined = combined[-3500:]
    return f"📜 **Logs für `{target}` (letzte {tail} Zeilen):**\n```\n{combined}\n```"


# =====================================================================
# 4. GNOME KEYRING & DESKTOP APPLICATIONS (Antigravity, Chrome, etc.)
# =====================================================================

def unlock_gnome_keyring(password: str = "") -> str:
    """Entsperrt den Ubuntu GNOME-Schlüsselbund (login.keyring), damit Programme wie Chrome und Antigravity nach einem Auto-Login nicht nach dem Anmeldekennwort fragen.

    Args:
        password: Optionales Benutzerpasswort. Falls leer, wird HOST_USER_PASSWORD aus der .env verwendet.
    """
    pwd = (password or config.host_user_password or "").strip()
    if not pwd:
        return (
            "⚠️ Kein Passwort zum Entsperren des GNOME-Schlüsselbunds angegeben. "
            "Du kannst es entweder in der `.env` als `HOST_USER_PASSWORD` hinterlegen "
            "oder beim Aufruf mitgeben (oder wir starten Chrome/Antigravity direkt mit `--password-store=basic`)."
        )

    env = _get_desktop_env()
    try:
        proc = subprocess.run(
            ["gnome-keyring-daemon", "--unlock"],
            input=pwd,
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
        if proc.returncode == 0:
            # Export returned environment variables if any
            for line in proc.stdout.splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip()
            return "🔓 **GNOME-Schlüsselbund (`login.keyring`) erfolgreich entsperrt!** Chrome und Antigravity starten nun ohne Passwortabfrage."
        return f"⚠️ Schlüsselbund-Rückmeldung (Code {proc.returncode}): {proc.stderr or proc.stdout}"
    except Exception as err:
        return f"❌ Fehler beim Entsperren des GNOME-Schlüsselbunds: {err}"


def desktop_manage_app(
    app_name: str,
    action: str = "start",
    extra_args: str = "",
    bypass_keyring_prompt: bool = True,
) -> str:
    """Startet, stoppt oder prüft ein grafisches Desktop-Programm (wie 'antigravity', 'chrome', 'virtualbox') auf dem Ubuntu-Desktop.

    Args:
        app_name: Name der Anwendung ('antigravity', 'chrome', 'virtualbox' oder Befehl).
        action: 'start', 'stop' oder 'status'.
        extra_args: Optionale Startparameter (z.B. ein Projektpfad oder eine URL).
        bypass_keyring_prompt: Wenn True, wird zuerst versucht den GNOME-Keyring zu entsperren bzw. '--password-store=basic' genutzt, damit kein Passwort-Popup den Start blockiert.
    """
    app_key = app_name.strip().lower()
    act = action.strip().lower()

    # Map known desktop apps to binaries and pgrep patterns
    if app_key in ("antigravity", "agy"):
        binary = "/usr/local/bin/antigravity"
        pgrep_pattern = "/opt/antigravity/antigravity|/usr/local/bin/antigravity"
        is_chromium = True
        display_name = "Antigravity IDE"
    elif app_key in ("chrome", "google-chrome", "browser"):
        binary = "/usr/bin/google-chrome"
        pgrep_pattern = "/opt/google/chrome/chrome|/usr/bin/google-chrome"
        is_chromium = True
        display_name = "Google Chrome"
    elif app_key in ("virtualbox", "vbox"):
        binary = "/usr/bin/virtualbox"
        pgrep_pattern = "VirtualBox"
        is_chromium = False
        display_name = "VirtualBox GUI"
    else:
        found = shutil.which(app_name.strip())
        if not found:
            return f"❌ Anwendung '{app_name}' wurde auf dem System nicht gefunden."
        binary = found
        pgrep_pattern = os.path.basename(found)
        is_chromium = False
        display_name = app_name.strip()

    _, pgrep_out, _ = _run_cmd(["pgrep", "-af", pgrep_pattern], timeout=5)
    # Filter out our own python process
    my_pid = str(os.getpid())
    running_lines = [
        line for line in pgrep_out.splitlines()
        if line.strip() and not line.startswith(my_pid + " ") and "adlosmcp" not in line
    ]
    is_running = len(running_lines) > 0

    if act == "status":
        if is_running:
            return f"✅ **{display_name}** läuft aktuell ({len(running_lines)} Prozess(e))."
        return f"🔴 **{display_name}** läuft aktuell nicht."

    if act == "stop":
        if not is_running:
            return f"ℹ️ **{display_name}** läuft aktuell nicht."
        _run_cmd(["pkill", "-f", pgrep_pattern], timeout=10)
        return f"🛑 **{display_name}** wurde beendet."

    if act == "start":
        if is_running and app_key in ("antigravity", "agy"):
            return f"✅ **{display_name}** läuft bereits auf dem Desktop!"

        notes = []
        # Handle GNOME keyring unlocking so no password modal blocks the app
        if bypass_keyring_prompt:
            if config.host_user_password:
                unlock_res = unlock_gnome_keyring(config.host_user_password)
                notes.append(unlock_res)

        cmd = [binary]
        if is_chromium and bypass_keyring_prompt and not config.host_user_password:
            # Prevents the modal keyring popup if no keyring password was configured
            cmd.append("--password-store=basic")
            notes.append("ℹ️ Mit `--password-store=basic` gestartet (verhindert Schlüsselbund-Passwortabfrage).")

        if extra_args.strip():
            cmd.extend(extra_args.strip().split())

        env = _get_desktop_env()
        try:
            subprocess.Popen(
                cmd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            time.sleep(1.5)
            extra_note = ("\n" + "\n".join(notes)) if notes else ""
            return f"🚀 **{display_name}** wurde auf deinem Desktop gestartet!{extra_note}"
        except Exception as err:
            return f"❌ Fehler beim Starten von **{display_name}**: {err}"

    return f"❌ Unbekannte Aktion '{action}'. Erlaubt: start, stop, status."


def take_desktop_screenshot() -> str:
    """Erstellt einen Screenshot des aktuellen Ubuntu-Desktops und gibt den Dateipfad zurück (im Adlos-Chat wird das Bild automatisch verschlüsselt mitgesendet)."""
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = SCREENSHOTS_DIR / f"desktop_{ts}.png"
    env = _get_desktop_env()

    # 1. Try gnome-screenshot if installed
    if shutil.which("gnome-screenshot"):
        code, _, _ = _run_cmd(["gnome-screenshot", "-f", str(out_path)], timeout=10, env=env)
        if code == 0 and out_path.exists():
            return f"📸 Screenshot erfolgreich erstellt: `{out_path}`"

    # 2. Try GNOME Shell D-Bus Screenshot API (works on Ubuntu Wayland + X11)
    gdbus_cmd = [
        "gdbus", "call", "--session",
        "--dest", "org.gnome.Shell.Screenshot",
        "--object-path", "/org/gnome/Shell/Screenshot",
        "--method", "org.gnome.Shell.Screenshot.Screenshot",
        "false", "false", str(out_path),
    ]
    code, _, _ = _run_cmd(gdbus_cmd, timeout=10, env=env)
    if code == 0 and out_path.exists():
        return f"📸 Screenshot erfolgreich erstellt: `{out_path}`"

    # 3. Try ImageMagick import on X11/XWayland
    if shutil.which("import"):
        code, _, _ = _run_cmd(["import", "-window", "root", str(out_path)], timeout=10, env=env)
        if code == 0 and out_path.exists():
            return f"📸 Screenshot erfolgreich erstellt: `{out_path}`"

    return "❌ Screenshot konnte nicht erstellt werden (weder GNOME-Shell-Screenshot noch `gnome-screenshot` verfügbar)."


# =====================================================================
# 5. UBUNTU UPDATES & ZERO-DAY SECURITY PATCHES
# =====================================================================

def apt_check_updates(refresh_cache: bool = True, sudo_password: str = "") -> str:
    """Prüft auf verfügbare Ubuntu-Paketupdates und hebt Sicherheitsupdates (Zero-Day / Security-Patches) hervor."""
    notes = []
    if refresh_cache:
        code, _, err = _run_sudo_cmd(["apt-get", "update", "-q"], sudo_password=sudo_password, timeout=120)
        if code != 0:
            notes.append(f"ℹ️ Hinweis: Paketlisten wurden aus lokalem Cache gelesen (`sudo apt-get update` nicht passwortlos aktiv).")

    code, out, err = _run_cmd(["apt", "list", "--upgradable"], timeout=30)
    if code != 0:
        return f"❌ Fehler beim Prüfen der Updates: {err}"

    lines = [
        line.strip()
        for line in out.splitlines()
        if line.strip() and not line.startswith("Listing...") and not line.startswith("Auflistung...")
    ]

    if not lines:
        reboot_note = "\n⚠️ Hinweis: Ein System-Neustart steht noch aus (`/var/run/reboot-required`)." if os.path.exists("/var/run/reboot-required") else ""
        return "✅ **Dein Ubuntu-System ist auf dem neuesten Stand!** Keine offenen Paket-Updates." + reboot_note

    security_pkgs = [l for l in lines if "security" in l.lower()]
    other_pkgs = [l for l in lines if "security" not in l.lower()]

    res = [f"📦 **{len(lines)} Update(s) verfügbar** (davon 🛡️ **{len(security_pkgs)} Sicherheits-Updates**):"]
    if notes:
        res.extend(notes)

    if security_pkgs:
        res.append("\n🛡️ **Sicherheits-Updates (Security):**")
        for pkg in security_pkgs[:25]:
            res.append(f"• `{pkg.split('/')[0]}` ({pkg})")
        if len(security_pkgs) > 25:
            res.append(f"• ... und {len(security_pkgs) - 25} weitere Security-Pakete")

    if other_pkgs:
        res.append("\n🔧 **Reguläre Paket-Updates:**")
        for pkg in other_pkgs[:20]:
            res.append(f"• `{pkg.split('/')[0]}`")
        if len(other_pkgs) > 20:
            res.append(f"• ... und {len(other_pkgs) - 20} weitere Pakete")

    return "\n".join(res)


def apt_install_updates(sudo_password: str = "") -> str:
    """Installiert alle verfügbaren Ubuntu-System- und Sicherheitsupdates vollautomatisch ohne interaktive Rückfragen."""
    extra_env = {"DEBIAN_FRONTEND": "noninteractive"}

    # 1. apt-get update
    code_u, out_u, err_u = _run_sudo_cmd(
        ["apt-get", "update", "-q"],
        sudo_password=sudo_password,
        timeout=180,
        extra_env=extra_env,
    )
    if code_u != 0:
        return f"❌ Fehler bei `sudo apt-get update`:\n{err_u or out_u}"

    # 2. apt-get upgrade -y
    upgrade_cmd = [
        "apt-get",
        "upgrade",
        "-y",
        "-q",
        "-o", "Dpkg::Options::=--force-confdef",
        "-o", "Dpkg::Options::=--force-confold",
    ]
    code_g, out_g, err_g = _run_sudo_cmd(
        upgrade_cmd,
        sudo_password=sudo_password,
        timeout=900,
        extra_env=extra_env,
    )
    if code_g != 0:
        return f"❌ Fehler bei `sudo apt-get upgrade`:\n{err_g or out_g}"

    # Summarize output
    summary_lines = [
        l for l in out_g.splitlines()
        if any(k in l.lower() for k in ("upgraded", "aktualisiert", "newly installed", "setting up", "trigger"))
    ]
    summary = "\n".join(summary_lines[-15:]) if summary_lines else out_g[-1000:]
    reboot_req = os.path.exists("/var/run/reboot-required")
    reboot_msg = (
        "\n\n⚠️ **Wichtig:** Für einige Updates (z.B. Kernel/Bibliotheken) ist ein **System-Neustart** erforderlich."
        if reboot_req
        else "\n\n✅ Kein Neustart erforderlich."
    )
    return f"🛡️ **System-Updates erfolgreich installiert!**\n```\n{summary}\n```{reboot_msg}"


def system_reboot_control(action: str = "status", sudo_password: str = "") -> str:
    """Prüft, ob ein Neustart des Ubuntu-Rechners nötig ist ('status') oder führt einen sauberen System-Neustart durch ('reboot')."""
    act = action.strip().lower()
    if act == "status":
        if os.path.exists("/var/run/reboot-required"):
            pkgs = ""
            if os.path.exists("/var/run/reboot-required.pkgs"):
                try:
                    pkgs_list = Path("/var/run/reboot-required.pkgs").read_text(encoding="utf-8").splitlines()
                    pkgs = f" (Auslösende Pakete: {', '.join(set(pkgs_list[:10]))})"
                except Exception:
                    pass
            return f"⚠️ **Neustart erforderlich:** Ja{pkgs}."
        return "✅ **Neustart erforderlich:** Nein. Das System muss aktuell nicht neu gestartet werden."

    if act in ("reboot", "restart"):
        # Schedule reboot in 1 minute so our response message still reaches Adlos cleanly
        code, out, err = _run_sudo_cmd(["shutdown", "-r", "+1", "Adlos MCP Remote Reboot"], sudo_password=sudo_password, timeout=15)
        if code == 0:
            return "🔄 **System-Neustart eingeleitet!** Der Rechner startet in 60 Sekunden neu (und meldet sich nach dem Hochfahren wieder im Chat)."
        return f"❌ Fehler beim Einleiten des Neustarts: {err or out}"

    if act == "cancel":
        code, out, err = _run_sudo_cmd(["shutdown", "-c"], sudo_password=sudo_password, timeout=15)
        if code == 0:
            return "✅ Geplanter System-Neustart wurde abgebrochen."
        return f"❌ Fehler beim Abbrechen: {err or out}"

    return f"❌ Unbekannte Aktion '{action}'. Erlaubt: status, reboot, cancel."


# =====================================================================
# 6. SYSTEMD SERVICES & GENERAL HOST COMMANDS
# =====================================================================

def systemd_manage_service(service_name: str, action: str = "status", user_service: bool = False, sudo_password: str = "") -> str:
    """Prüft oder steuert einen Linux systemd-Dienst (z.B. 'docker', 'ssh', 'adlosmcp')."""
    act = action.strip().lower()
    if act not in ("status", "start", "stop", "restart"):
        return f"❌ Ungültige Aktion '{action}'. Erlaubt: status, start, stop, restart."

    svc = service_name.strip()
    if user_service:
        cmd = ["systemctl", "--user", act, svc, "--no-pager"]
        code, out, err = _run_cmd(cmd, timeout=30, env=_get_desktop_env())
    elif act == "status":
        cmd = ["systemctl", "status", svc, "--no-pager", "-l"]
        code, out, err = _run_cmd(cmd, timeout=30)
    else:
        cmd = ["systemctl", act, svc]
        code, out, err = _run_sudo_cmd(cmd, sudo_password=sudo_password, timeout=60)

    combined = (out or err).strip()
    if len(combined) > 2500:
        combined = combined[:2500] + "\n..."
    icon = "✅" if code == 0 else "⚠️"
    return f"{icon} **systemd `{svc}` ({act}):**\n```\n{combined or 'Erfolgreich ausgeführt.'}\n```"


ALL_TOOLS = [
    system_status,
    vbox_list_vms,
    vbox_manage_vm,
    docker_list_containers,
    docker_manage_container,
    docker_container_logs,
    unlock_gnome_keyring,
    desktop_manage_app,
    take_desktop_screenshot,
    apt_check_updates,
    apt_install_updates,
    system_reboot_control,
    systemd_manage_service,
]

TOOLS_BY_NAME = {fn.__name__: fn for fn in ALL_TOOLS}
