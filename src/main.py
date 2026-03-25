import os
import sys
import subprocess
import socket
import time
import atexit
import signal
import threading
import ctypes
import asyncio
from urllib.parse import quote

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.pop("SSLKEYLOGFILE", None)

API_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Python_API")
if API_DIR not in sys.path:
    sys.path.insert(0, API_DIR)

import webview
from link_handler import ensure_env_file

ensure_env_file()

HTML_LOADER_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<title>Music Together</title>
<style>
body {
    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    background: #121212;
    color: white;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    height: 100vh;
    margin: 0;
}
h1 { font-size: 42px; margin-bottom: 10px; }
.subtitle { color: #aaaaaa; margin-bottom: 40px; }
.btn {
    background: #bb86fc;
    color: #000;
    padding: 15px 30px;
    border-radius: 8px;
    border: none;
    font-size: 18px;
    font-weight: bold;
    cursor: pointer;
    margin: 10px;
    width: 300px;
    transition: 0.2s;
}
.btn:hover { background: #9c6aea; transform: scale(1.02); }
input {
    padding: 15px;
    font-size: 16px;
    width: 270px;
    margin-bottom: 15px;
    border-radius: 8px;
    border: 1px solid #333;
    background: #1e1e1e;
    color: white;
    outline: none;
}
input:focus { border-color: #bb86fc; }
.loading { display: none; margin-top: 20px; color: #bb86fc; font-weight: bold; }
</style>
</head>
<body>
    <h1>Music Together</h1>
    <p class="subtitle">Host your own lobby or join a friend's offline session!</p>

    <div style="display:flex; flex-direction:column; align-items:center;">
        <button class="btn" onclick="hostSession()">Host a Session</button>
        <div id="loading" class="loading">Booting Local Server...</div>

        <p style="margin: 30px 0 15px; color:#aaa; font-size:14px;">OR CONNECT TO SESSION:</p>

        <input type="text" id="username" placeholder="Username" value="__LAST_USERNAME__" />
        <input type="text" id="ip" placeholder="http://192.168.1.X:54321" value="__LAST_IP__" />
        <button class="btn" onclick="connectSession()" style="background:#03dac6;">Connect</button>
    </div>

    <script>
        let launcherSettingsLoaded = false;

        function applyLauncherSettings(settings) {
            if (!settings) return;
            document.getElementById('username').value = settings.last_username || '';
            document.getElementById('ip').value = settings.last_ip || '';
        }

        async function loadLauncherSettings() {
            if (launcherSettingsLoaded) return;
            launcherSettingsLoaded = true;
            try {
                applyLauncherSettings({
                    last_username: localStorage.getItem('mt_launcher_username') || '',
                    last_ip: localStorage.getItem('mt_launcher_ip') || ''
                });
            } catch (e) {
                console.log('Could not load local launcher settings', e);
            }

            try {
                const settings = await pywebview.api.get_launcher_settings();
                applyLauncherSettings(settings);
                localStorage.setItem('mt_launcher_username', settings.last_username || '');
                localStorage.setItem('mt_launcher_ip', settings.last_ip || '');
            } catch (e) {
                console.log('Could not load saved launcher settings', e);
            }
        }

        function tryLoadLauncherSettings() {
            if (window.pywebview && pywebview.api && typeof pywebview.api.get_launcher_settings === 'function') {
                loadLauncherSettings();
            } else {
                setTimeout(tryLoadLauncherSettings, 100);
            }
        }

        async function saveLauncherSettings(username, ip = null) {
            try {
                localStorage.setItem('mt_launcher_username', username || '');
                if (ip !== null) {
                    localStorage.setItem('mt_launcher_ip', ip || '');
                }
                await pywebview.api.save_launcher_settings(username, ip);
            } catch (e) {
                console.log('Could not save launcher settings', e);
            }
        }

        function buildSessionUrl(baseUrl, username) {
            const normalized = baseUrl.endsWith('/web/index.html')
                ? baseUrl
                : (baseUrl.endsWith('/') ? baseUrl.slice(0, -1) : baseUrl) + '/web/index.html';
            if (!username) return normalized;
            const separator = normalized.includes('?') ? '&' : '?';
            return normalized + separator + 'username=' + encodeURIComponent(username);
        }

        async function hostSession() {
            const username = document.getElementById('username').value.trim();
            if (!username) {
                alert('Please enter a username!');
                return;
            }
            document.getElementById('loading').style.display = 'block';
            const targetUrl = await pywebview.api.host_server(username);
            await saveLauncherSettings(username);
            window.location.href = targetUrl || buildSessionUrl('http://127.0.0.1:54321/', username);
        }

        async function connectSession() {
            const username = document.getElementById('username').value.trim();
            if (!username) {
                alert('Please enter a username!');
                return;
            }
            let ip = document.getElementById('ip').value.trim();
            if (!ip) {
                alert('Please enter an IP address!');
                return;
            }
            if (!ip.startsWith('http')) ip = 'http://' + ip;
            if (ip.endsWith('/')) ip = ip.slice(0, -1);
            const targetUrl = await pywebview.api.prepare_connection(username, ip);
            await saveLauncherSettings(username, ip);
            window.location.href = targetUrl || buildSessionUrl(ip, username);
        }

        document.addEventListener('DOMContentLoaded', tryLoadLauncherSettings);
        document.addEventListener('pywebviewready', loadLauncherSettings);
    </script>
</body>
</html>
"""


def run_server():
    """Start the FastAPI server used by the desktop launcher."""
    import uvicorn
    from Python_API.Music_Together_API import app, print_network_interfaces
    from Python_API.json_loader import migrate_legacy_json_to_sqlite

    migrate_legacy_json_to_sqlite()
    port = 54321
    print_network_interfaces(port)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info", access_log=False)


class Api:
    """pywebview bridge exposed to the launcher window."""

    def __init__(self):
        """Initialize the launcher API bridge."""
        self.server_process = None
        self.server_thread = None

    def shutdown_server(self):
        """Terminate the child backend process if it is still running."""
        proc = self.server_process
        if proc is None:
            return True
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self.server_process = None
        return True

    def _server_command(self):
        """Build the command used to launch the backend server."""
        if getattr(sys, "frozen", False):
            return [sys.executable, "--server"]
        return [sys.executable, os.path.abspath(__file__), "--server"]

    def _wait_for_port(self, host, port, timeout=30):
        """Wait until a TCP port is accepting connections."""
        deadline = time.time() + timeout
        last_error = None
        while time.time() < deadline:
            if self.server_process is not None and self.server_process.poll() is not None:
                if self._server_is_ready(host, port):
                    return True
                raise RuntimeError("Backend process exited before opening the port")
            try:
                with socket.create_connection((host, port), timeout=1):
                    return True
            except OSError as exc:
                last_error = exc
                time.sleep(0.25)
        if last_error is not None:
            raise RuntimeError(f"Backend did not start on {host}:{port}") from last_error
        raise RuntimeError(f"Backend did not start on {host}:{port}")

    def _port_is_open(self, host, port):
        """Return True when a TCP port is already reachable."""
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            return False

    def _server_is_ready(self, host, port):
        """Return True only when the backend answers an HTTP request."""
        import urllib.request

        try:
            with urllib.request.urlopen(f"http://{host}:{port}/db", timeout=1) as response:
                return response.status == 200
        except Exception:
            return False

    def _terminate_stale_listener(self, port):
        """Terminate an old Music Together listener holding the server port."""
        try:
            import psutil
        except Exception:
            return False

        current_pid = os.getpid()
        terminated = False
        for conn in psutil.net_connections(kind="inet"):
            if not conn.laddr or conn.laddr.port != port or not conn.pid or conn.pid == current_pid:
                continue
            try:
                proc = psutil.Process(conn.pid)
                cmdline = " ".join(proc.cmdline())
                name = proc.name().lower()
                if "music_together" in cmdline.lower() or "main.py" in cmdline.lower() or "python" in name:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except Exception:
                        proc.kill()
                    terminated = True
            except Exception:
                continue
        return terminated

    def host_server(self, username=""):
        """Start the backend server once, persist the launcher state, and return the host URL."""
        if self._server_is_ready("127.0.0.1", 54321):
            self.save_launcher_settings(username, None)
            return self._build_session_url("http://127.0.0.1:54321/", username)
        if not getattr(sys, "frozen", False):
            if self.server_thread is None or not self.server_thread.is_alive():
                self.server_thread = threading.Thread(target=run_server, daemon=True)
                self.server_thread.start()
            self._wait_for_port("127.0.0.1", 54321, timeout=30)
            self.save_launcher_settings(username, None)
            return self._build_session_url("http://127.0.0.1:54321/", username)

        if self.server_process is None or self.server_process.poll() is not None:
            self._terminate_stale_listener(54321)
            env = os.environ.copy()
            path_entries = []
            for entry in sys.path:
                if entry and os.path.isdir(entry) and entry not in path_entries:
                    path_entries.append(entry)
            if env.get("PYTHONPATH"):
                path_entries.append(env["PYTHONPATH"])
            env["PYTHONPATH"] = os.pathsep.join(path_entries)
            self.server_process = subprocess.Popen(
                self._server_command(),
                cwd=os.getcwd(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
        self._wait_for_port("127.0.0.1", 54321, timeout=30)
        self.save_launcher_settings(username, None)
        return self._build_session_url("http://127.0.0.1:54321/", username)

    def prepare_connection(self, username, ip):
        """Persist connection info and return the final frontend URL."""
        normalized_ip = (ip or "").strip()
        if normalized_ip and not normalized_ip.startswith("http"):
            normalized_ip = "http://" + normalized_ip
        normalized_ip = normalized_ip.rstrip("/")
        self.save_launcher_settings(username, normalized_ip)
        return self._build_session_url(normalized_ip, username)

    def get_launcher_settings(self):
        """Return the persisted launcher username and IP."""
        from Python_API.json_loader import load_db

        db = load_db()
        launcher = db.get("settings", {}).get("launcher", {})
        return {
            "last_username": launcher.get("last_username", ""),
            "last_ip": launcher.get("last_ip", ""),
        }

    def save_launcher_settings(self, username, ip=None):
        """Persist launcher username and optional IP to the DB."""
        from Python_API.json_loader import load_db, save_db

        db = load_db()
        db.setdefault("settings", {}).setdefault("launcher", {})
        db["settings"]["launcher"]["last_username"] = username or ""
        if ip is not None:
            db["settings"]["launcher"]["last_ip"] = ip or ""
        save_db(db)
        return True

    def _build_session_url(self, base_url, username):
        """Return the frontend URL with the username query string."""
        normalized = base_url.rstrip("/")
        if not normalized.endswith("/web/index.html"):
            normalized = normalized + "/web/index.html"
        if not username:
            return normalized
        separator = "&" if "?" in normalized else "?"
        return normalized + separator + f"username={quote(username)}"


def _read_launcher_settings():
    """Load launcher username and IP from the DB for initial HTML rendering."""
    from Python_API.json_loader import load_db

    db = load_db()
    launcher = db.get("settings", {}).get("launcher", {})
    return {
        "last_username": launcher.get("last_username", ""),
        "last_ip": launcher.get("last_ip", ""),
    }


def build_launcher_html():
    """Render the launcher HTML with the persisted username and IP prefilled."""
    settings = _read_launcher_settings()
    username = (settings.get("last_username", "") or "").replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
    ip = (settings.get("last_ip", "") or "").replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
    return (
        HTML_LOADER_TEMPLATE
        .replace("__LAST_USERNAME__", username)
        .replace("__LAST_IP__", ip)
    )


def get_asset_path(relative_path):
    """Resolve an asset path both in source runs and bundled builds."""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)


def apply_windows_branding():
    """Set a stable Windows app identity so taskbar/system surfaces use Music Together branding."""
    if os.name != "nt":
        return

    try:
        app_id = "MusicTogether.Desktop"
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass


def configure_windows_asyncio():
    """Use the selector loop on Windows to avoid noisy Proactor shutdown traces."""
    if os.name != "nt":
        return
    policy_factory = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if policy_factory is None:
        return
    try:
        asyncio.set_event_loop_policy(policy_factory())
    except Exception:
        pass

    try:
        ctypes.windll.kernel32.SetConsoleTitleW("Music Together")
    except Exception:
        pass


if __name__ == "__main__":
    if "--server" in sys.argv:
        configure_windows_asyncio()
        run_server()
        raise SystemExit(0)

    from Python_API.json_loader import migrate_legacy_json_to_sqlite

    migrate_legacy_json_to_sqlite()
    configure_windows_asyncio()
    apply_windows_branding()
    api = Api()
    window = webview.create_window(
        "Music Together",
        html=build_launcher_html(),
        js_api=api,
        width=1280,
        height=800,
        min_size=(1000, 600),
        background_color="#121212",
    )

    if window is not None:
        window.events.closed += api.shutdown_server

    def _cleanup(*_args):
        api.shutdown_server()

    atexit.register(_cleanup)

    for sig_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                signal.signal(sig, lambda *_args: _cleanup())
            except Exception:
                pass

    icon_path = get_asset_path(os.path.join("Asset", "logo.ico"))
    try:
        if os.path.exists(icon_path):
            webview.start(icon=icon_path)
        else:
            webview.start()
    finally:
        _cleanup()
