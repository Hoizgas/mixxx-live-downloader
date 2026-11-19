from __future__ import annotations

import ipaddress
import json
import os
import queue
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from tkinter import BooleanVar, StringVar, Tk, filedialog, messagebox, ttk
from urllib.parse import urlparse

from flask import Flask, jsonify, redirect, render_template_string, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.serving import make_server


APP_NAME = "Mixxx Live Downloader"
CONFIG_DIR = Path.home() / "Library" / "Application Support" / "MixxxLiveDownloader"
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULT_FOLDER = Path.home() / "Music" / "Mixxx_Downloads"
CERT_FILE = CONFIG_DIR / "server.crt"
KEY_FILE = CONFIG_DIR / "server.key"
os.environ["PATH"] = os.pathsep.join(
    ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", os.environ.get("PATH", "")]
)


def find_brew() -> str | None:
    for candidate in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew", shutil.which("brew")):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def missing_audio_tools() -> list[str]:
    return [tool for tool in ("yt-dlp", "ffmpeg", "ffprobe") if not shutil.which(tool)]


def missing_brew_formulas(tools: list[str]) -> list[str]:
    formulas = []
    if "yt-dlp" in tools:
        formulas.append("yt-dlp")
    if "ffmpeg" in tools or "ffprobe" in tools:
        formulas.append("ffmpeg")
    return formulas


def load_config() -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        value = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            value = {}
    except (OSError, json.JSONDecodeError):
        value = {}
    value.setdefault("download_folder", str(DEFAULT_FOLDER))
    value.setdefault("port", 5000)
    value.setdefault("lan_enabled", False)
    value.setdefault("admin_username", "")
    value.setdefault("admin_password_hash", "")
    value.setdefault("secret_key", secrets.token_hex(32))
    return value


config_lock = threading.RLock()
config = load_config()
app = Flask(__name__)
app.secret_key = config["secret_key"]
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    SESSION_COOKIE_SECURE=False,
    MAX_CONTENT_LENGTH=1024 * 1024,
)
download_folder = Path(config["download_folder"]).expanduser()
jobs: list[dict] = []
jobs_lock = threading.Lock()
download_queue: queue.Queue[str] = queue.Queue()


def save_config() -> None:
    with config_lock:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")
        try:
            CONFIG_FILE.chmod(0o600)
        except OSError:
            pass


def valid_youtube_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        return parsed.scheme == "https" and (
            host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com")
        )
    except ValueError:
        return False


def new_csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


LOGIN_PAGE = r"""
<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Anmelden · Mixxx Live Downloader</title><style>
:root{color-scheme:dark;--bg:#101218;--panel:#191d26;--line:#2b3140;--text:#eef1f7;--muted:#a3abc0;--accent:#8b7cff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;min-height:100vh;display:grid;place-items:center}
main{width:min(440px,calc(100% - 32px));background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:28px}h1{margin:0 0 6px}p{color:var(--muted)}label{display:block;margin-top:16px;color:var(--muted)}input{width:100%;margin-top:6px;background:#10131a;color:var(--text);border:1px solid var(--line);border-radius:9px;padding:12px}
button{width:100%;margin-top:20px;color:white;background:var(--accent);border:0;border-radius:9px;padding:12px;cursor:pointer;font-weight:700}.error{color:#ff9c9c}
</style></head><body><main><h1>Mixxx Live Downloader</h1><p>Mit Admin-Zugangsdaten anmelden.</p>
{% if error %}<p class="error">{{ error }}</p>{% endif %}
<form method="post" action="/login"><input type="hidden" name="csrf_token" value="{{ csrf_token }}">
<label for="username">Nutzername</label><input id="username" name="username" autocomplete="username" required autofocus>
<label for="password">Passwort</label><input id="password" name="password" type="password" autocomplete="current-password" required>
<button>Anmelden</button></form></main></body></html>
"""

PAGE = r"""
<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mixxx Downloader</title><style>
:root{color-scheme:dark;--bg:#101218;--panel:#191d26;--line:#2b3140;--text:#eef1f7;--muted:#a3abc0;--accent:#8b7cff;--green:#64d8a4}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:850px;margin:40px auto;padding:0 20px}h1{margin:0 0 6px;font-size:28px}p{color:var(--muted)}
.top{display:flex;justify-content:space-between;align-items:center;gap:12px}.panel{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:20px;margin:18px 0}
.row{display:flex;gap:10px;flex-wrap:wrap}input{flex:1;min-width:230px;background:#10131a;color:var(--text);border:1px solid var(--line);border-radius:9px;padding:12px}
button{color:white;background:var(--accent);border:0;border-radius:9px;padding:11px 16px;cursor:pointer;font-weight:650}button.secondary{background:#303646}
.item{border-top:1px solid var(--line);padding:13px 0}.item:first-child{border-top:0}.title{font-weight:650;overflow-wrap:anywhere}.meta{color:var(--muted);font-size:13px;overflow-wrap:anywhere}
.notice{font-size:13px;background:#242131;padding:12px;border-radius:9px}.link{color:var(--green)}
</style></head><body><main>
<div class="top"><div><h1>Mixxx Downloader</h1><p>Angemeldet als {{ username }}</p></div><form method="post" action="/logout"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><button class="secondary">Abmelden</button></form></div>
<div class="panel"><strong>Zielordner</strong><p id="folder"></p><button class="secondary" onclick="chooseFolder()">Ordner wählen</button></div>
<div class="panel"><form id="form" class="row"><input id="url" type="url" placeholder="https://www.youtube.com/watch?v=…" required><button>Zur Warteschlange</button></form>
<p class="notice">Bitte nur Inhalte herunterladen, für die du die nötigen Rechte hast. Eine MP3-Konvertierung erhöht nicht die ursprüngliche Audioqualität.</p></div>
<div class="panel"><strong>Downloads</strong><div id="jobs"><p>Noch keine Downloads.</p></div></div></main>
<script>
const csrf={{ csrf_token|tojson }};
async function refresh(){const r=await fetch('/api/status');if(r.status===401){location.href='/login';return}const d=await r.json();document.getElementById('folder').textContent=d.folder;const root=document.getElementById('jobs');root.replaceChildren();if(!d.jobs.length){root.innerHTML='<p>Noch keine Downloads.</p>';return}for(const j of d.jobs){const box=document.createElement('div');box.className='item';const title=document.createElement('div');title.className='title';title.textContent=j.url;const meta=document.createElement('div');meta.className='meta';meta.textContent=j.status+(j.message?' · '+j.message:'');box.append(title,meta);root.append(box)}}
const headers={'Content-Type':'application/json','X-CSRF-Token':csrf};
document.getElementById('form').addEventListener('submit',async e=>{e.preventDefault();const url=document.getElementById('url').value.trim();const r=await fetch('/api/queue',{method:'POST',headers,body:JSON.stringify({url})});const d=await r.json();if(!r.ok)alert(d.error||'Link konnte nicht hinzugefügt werden.');else document.getElementById('url').value='';refresh()});
async function chooseFolder(){const r=await fetch('/api/choose-folder',{method:'POST',headers});const d=await r.json();if(!r.ok)alert(d.error||'Ordner konnte nicht gewählt werden.');refresh()}
refresh();setInterval(refresh,2000);
</script></body></html>
"""


@app.before_request
def require_admin():
    if request.endpoint in ("login", "login_post", "static"):
        return None
    if not config.get("admin_password_hash"):
        return "Adminzugangsdaten zuerst in der App einrichten.", 503
    if not session.get("authenticated"):
        if request.path.startswith("/api/"):
            return jsonify(error="Bitte anmelden."), 401
        return redirect(url_for("login"))
    if request.method == "POST":
        token = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
        if not secrets.compare_digest(str(token), str(session.get("csrf_token", ""))):
            return "Ungültiges CSRF-Token.", 403
    return None


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.is_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response


@app.get("/login")
def login():
    if session.get("authenticated"):
        return redirect("/")
    return render_template_string(LOGIN_PAGE, csrf_token=new_csrf_token(), error="")


@app.post("/login")
def login_post():
    token = request.form.get("csrf_token", "")
    if not secrets.compare_digest(str(token), str(session.get("csrf_token", ""))):
        return render_template_string(LOGIN_PAGE, csrf_token=new_csrf_token(), error="Sitzung abgelaufen. Bitte erneut versuchen."), 403
    username = request.form.get("username", "")
    password = request.form.get("password", "")
    expected = str(config.get("admin_username", ""))
    password_hash = str(config.get("admin_password_hash", ""))
    if not secrets.compare_digest(username, expected) or not password_hash or not check_password_hash(password_hash, password):
        time.sleep(0.4)
        return render_template_string(LOGIN_PAGE, csrf_token=new_csrf_token(), error="Nutzername oder Passwort ist falsch."), 401
    session.clear()
    session["authenticated"] = True
    new_csrf_token()
    return redirect("/")


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
def index():
    return render_template_string(PAGE, csrf_token=new_csrf_token(), username=config.get("admin_username", "Admin"))


@app.get("/api/status")
def status():
    with jobs_lock:
        snapshot = [
            {"url": item["url"], "status": item["status"], "message": item.get("message", "")}
            for item in reversed(jobs[-100:])
        ]
    return jsonify(folder=str(download_folder), jobs=snapshot)


@app.post("/api/queue")
def enqueue():
    data = request.get_json(silent=True) or {}
    url = str(data.get("url", "")).strip()
    if not valid_youtube_url(url):
        return jsonify(error="Bitte einen gültigen HTTPS-YouTube-Link eingeben."), 400
    job = {"id": uuid.uuid4().hex, "url": url, "status": "Wartet"}
    with jobs_lock:
        jobs.append(job)
    download_queue.put(job["id"])
    return jsonify(ok=True), 202


@app.post("/api/choose-folder")
def choose_folder():
    global download_folder
    script = 'POSIX path of (choose folder with prompt "Mixxx-Zielordner auswählen")'
    try:
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return jsonify(error=f"Ordnerdialog fehlgeschlagen: {exc}"), 500
    if result.returncode:
        return jsonify(error="Ordnerauswahl abgebrochen oder fehlgeschlagen."), 400
    selected = Path(result.stdout.strip()).expanduser().resolve()
    if not selected.is_dir():
        return jsonify(error="Der ausgewählte Pfad ist kein Ordner."), 400
    download_folder = selected
    with config_lock:
        config["download_folder"] = str(selected)
        save_config()
    return jsonify(ok=True, folder=str(download_folder))


def download_worker() -> None:
    global download_folder
    while True:
        job_id = download_queue.get()
        try:
            with jobs_lock:
                job = next((item for item in jobs if item["id"] == job_id), None)
                if job is None:
                    continue
                job["status"] = "Lädt"
                target = download_folder
            target.mkdir(parents=True, exist_ok=True)
            executable = shutil.which("yt-dlp")
            if not executable:
                raise RuntimeError("yt-dlp fehlt. Installiere es mit Homebrew: brew install yt-dlp")
            command = [
                executable, "--no-playlist", "--extract-audio", "--audio-format", "mp3",
                "--audio-quality", "0", "--embed-metadata", "--embed-thumbnail",
                "--windows-filenames", "--restrict-filenames",
                "--output", str(target / "%(title).180B [%(id)s].%(ext)s"), job["url"],
            ]
            if shutil.which("nice"):
                command = ["nice", "-n", "10", *command]
            result = subprocess.run(command, capture_output=True, text=True, timeout=3600, check=False)
            if result.returncode:
                detail = (result.stderr or result.stdout or "Unbekannter yt-dlp-Fehler").strip()
                raise RuntimeError(detail[-1200:])
            with jobs_lock:
                job["status"] = "Fertig"
                job["message"] = "Download und MP3-Konvertierung abgeschlossen."
        except subprocess.TimeoutExpired:
            with jobs_lock:
                job["status"] = "Fehler"
                job["message"] = "Zeitlimit von 60 Minuten überschritten."
        except Exception as exc:
            with jobs_lock:
                job["status"] = "Fehler"
                job["message"] = str(exc)
        finally:
            download_queue.task_done()


class ServerManager:
    def __init__(self):
        self.server = None
        self.thread = None
        self.lock = threading.RLock()
        self.url = ""
        self.error = ""

    @staticmethod
    def lan_address() -> str:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("192.0.2.1", 80))
            value = sock.getsockname()[0]
        finally:
            sock.close()
        ip = ipaddress.ip_address(value)
        if ip.is_loopback or ip.is_link_local:
            raise RuntimeError("Keine erreichbare LAN-IP-Adresse gefunden.")
        return value

    @staticmethod
    def make_tls_context(lan_ip: str) -> ssl.SSLContext:
        openssl = shutil.which("openssl")
        if not openssl:
            raise RuntimeError("OpenSSL wurde nicht gefunden; LAN-HTTPS kann nicht gestartet werden.")
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        san = f"DNS:localhost,IP:127.0.0.1,IP:{lan_ip}"
        command = [
            openssl, "req", "-x509", "-nodes", "-newkey", "rsa:2048", "-sha256",
            "-days", "365", "-keyout", str(KEY_FILE), "-out", str(CERT_FILE),
            "-subj", "/CN=Mixxx Live Downloader", "-addext", f"subjectAltName={san}",
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode:
            raise RuntimeError((result.stderr or "Zertifikat konnte nicht erstellt werden.").strip())
        KEY_FILE.chmod(0o600)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(CERT_FILE), str(KEY_FILE))
        return context

    def start(self, port: int, lan_enabled: bool) -> str:
        with self.lock:
            if self.server is not None:
                return self.url
            try:
                tls = None
                if lan_enabled:
                    host = self.lan_address()
                    tls = self.make_tls_context(host)
                    scheme = "https"
                else:
                    host = "127.0.0.1"
                    scheme = "http"
                server = make_server(host, port, app, threaded=True, ssl_context=tls)
                self.server = server
                self.url = f"{scheme}://{host}:{port}"
                app.config["SESSION_COOKIE_SECURE"] = bool(lan_enabled)
                self.thread = threading.Thread(target=server.serve_forever, daemon=True, name="mixxx-web")
                self.thread.start()
                self.error = ""
                return self.url
            except Exception as exc:
                self.server = None
                self.thread = None
                self.url = ""
                self.error = str(exc)
                raise

    def stop(self) -> None:
        with self.lock:
            server, thread = self.server, self.thread
            self.server = None
            self.thread = None
            self.url = ""
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None and thread.is_alive():
            thread.join(timeout=3)


server_manager = ServerManager()


def create_admin(username: str, password: str) -> None:
    with config_lock:
        config["admin_username"] = username
        config["admin_password_hash"] = generate_password_hash(password, method="scrypt")
        save_config()


class ControlPanel:
    def __init__(self):
        self.root = Tk()
        self.root.title(APP_NAME)
        self.root.geometry("540x520")
        self.root.minsize(480, 460)
        self.root.configure(bg="#101218")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.style = ttk.Style(self.root)
        try:
            self.style.theme_use("clam")
        except Exception:
            pass
        self.style.configure("TFrame", background="#101218")
        self.style.configure("Panel.TFrame", background="#191d26")
        self.style.configure("TLabel", background="#101218", foreground="#eef1f7", font=("Helvetica Neue", 12))
        self.style.configure("Muted.TLabel", background="#101218", foreground="#a3abc0", font=("Helvetica Neue", 10))
        self.style.configure("Panel.TLabel", background="#191d26", foreground="#eef1f7", font=("Helvetica Neue", 11))
        self.style.configure("TButton", padding=8, font=("Helvetica Neue", 11))
        self.style.configure("Accent.TButton", padding=9, font=("Helvetica Neue", 11, "bold"))
        self.style.configure("TCheckbutton", background="#191d26", foreground="#eef1f7", font=("Helvetica Neue", 11))
        self.authenticated = False
        self.ui_events: queue.SimpleQueue = queue.SimpleQueue()
        self.installing_dependencies = False
        self.root.after(100, self.show_auth_screen)

    def clear(self):
        for child in self.root.winfo_children():
            child.destroy()

    def heading(self, parent, title, subtitle):
        ttk.Label(parent, text=title, font=("Helvetica Neue", 22, "bold")).pack(anchor="w", pady=(0, 4))
        ttk.Label(parent, text=subtitle, style="Muted.TLabel", wraplength=470).pack(anchor="w", pady=(0, 18))

    def show_auth_screen(self):
        self.clear()
        outer = ttk.Frame(self.root, padding=28)
        outer.pack(fill="both", expand=True)
        is_setup = not bool(config.get("admin_password_hash"))
        self.heading(outer, "Mixxx Live Downloader", "Admin-Konto einrichten" if is_setup else "Admin-Anmeldung")
        ttk.Label(outer, text="Nutzername").pack(anchor="w")
        self.username_var = StringVar(value=config.get("admin_username", ""))
        username = ttk.Entry(outer, textvariable=self.username_var)
        username.pack(fill="x", pady=(5, 14))
        ttk.Label(outer, text="Passwort").pack(anchor="w")
        self.password_var = StringVar()
        password = ttk.Entry(outer, textvariable=self.password_var, show="•")
        password.pack(fill="x", pady=(5, 14))
        if is_setup:
            ttk.Label(outer, text="Passwort wiederholen").pack(anchor="w")
            self.confirm_var = StringVar()
            confirm = ttk.Entry(outer, textvariable=self.confirm_var, show="•")
            confirm.pack(fill="x", pady=(5, 10))
        else:
            confirm = None
        self.auth_message = ttk.Label(outer, text="", style="Muted.TLabel", wraplength=450)
        self.auth_message.pack(anchor="w", pady=(0, 8))
        ttk.Button(
            outer,
            text="Admin-Konto anlegen" if is_setup else "Anmelden",
            style="Accent.TButton",
            command=lambda: self.handle_auth(is_setup),
        ).pack(fill="x", pady=(8, 10))
        if not is_setup:
            password.bind("<Return>", lambda _event: self.handle_auth(False))
        else:
            confirm.bind("<Return>", lambda _event: self.handle_auth(True))
        username.focus_set()

    def handle_auth(self, setup: bool):
        username = self.username_var.get().strip()
        password = self.password_var.get()
        if not username or len(username) > 64:
            self.auth_message.configure(text="Bitte einen Nutzernamen mit höchstens 64 Zeichen eingeben.")
            return
        if setup:
            if len(password) < 12:
                self.auth_message.configure(text="Bitte ein Passwort mit mindestens 12 Zeichen wählen.")
                return
            if password != self.confirm_var.get():
                self.auth_message.configure(text="Die Passwörter stimmen nicht überein.")
                return
            create_admin(username, password)
            self.authenticated = True
            self.show_panel()
            return
        good_name = secrets.compare_digest(username, str(config.get("admin_username", "")))
        good_password = check_password_hash(str(config.get("admin_password_hash", "")), password)
        if not (good_name and good_password):
            self.auth_message.configure(text="Nutzername oder Passwort ist falsch.")
            self.password_var.set("")
            return
        self.authenticated = True
        self.show_panel()

    def show_panel(self):
        self.clear()
        outer = ttk.Frame(self.root, padding=24)
        outer.pack(fill="both", expand=True)
        self.heading(outer, "Kontrollpanel", f"Angemeldet als {config.get('admin_username', 'Admin')}")
        card = ttk.Frame(outer, style="Panel.TFrame", padding=16)
        card.pack(fill="x", pady=(0, 12))
        ttk.Label(card, text="Webserver", style="Panel.TLabel", font=("Helvetica Neue", 13, "bold")).pack(anchor="w")
        line = ttk.Frame(card, style="Panel.TFrame")
        line.pack(fill="x", pady=(12, 10))
        ttk.Label(line, text="Port", style="Panel.TLabel").pack(side="left")
        self.port_var = StringVar(value=str(config.get("port", 5000)))
        self.port_entry = ttk.Entry(line, textvariable=self.port_var, width=9)
        self.port_entry.pack(side="left", padx=(10, 18))
        self.lan_var = BooleanVar(value=bool(config.get("lan_enabled", False)))
        self.lan_check = ttk.Checkbutton(
            card,
            text="Im lokalen Netzwerk erreichbar (HTTPS)",
            variable=self.lan_var,
            command=self.on_lan_toggle,
        )
        self.lan_check.pack(anchor="w", pady=(4, 2))
        ttk.Label(
            card,
            text="Ohne Häkchen lauscht der Server nur auf diesem Mac. Mit Häkchen wird der Server per HTTPS an die aktive LAN-Adresse gebunden.",
            style="Muted.TLabel",
            wraplength=440,
            justify="left",
        ).pack(anchor="w", pady=(4, 8))
        buttons = ttk.Frame(card, style="Panel.TFrame")
        buttons.pack(fill="x", pady=(8, 0))
        self.start_button = ttk.Button(buttons, text="Server starten", style="Accent.TButton", command=self.start_server)
        self.start_button.pack(side="left", padx=(0, 8))
        self.stop_button = ttk.Button(buttons, text="Server stoppen", command=self.stop_server, state="disabled")
        self.stop_button.pack(side="left")
        self.status_var = StringVar(value="Server gestoppt")
        self.status_label = ttk.Label(outer, textvariable=self.status_var, style="Muted.TLabel", wraplength=470)
        self.status_label.pack(anchor="w", pady=(2, 16))
        missing = missing_audio_tools()
        dependency_text = "Audio-Tools bereit" if not missing else "Fehlen: " + ", ".join(missing) + " · werden beim Start geprüft"
        self.dependencies_var = StringVar(value=dependency_text)
        ttk.Label(outer, textvariable=self.dependencies_var, style="Muted.TLabel", wraplength=470).pack(anchor="w", pady=(0, 8))
        self.install_progress = ttk.Progressbar(outer, mode="indeterminate", length=300)
        folder_card = ttk.Frame(outer, style="Panel.TFrame", padding=16)
        folder_card.pack(fill="x", pady=(0, 12))
        ttk.Label(folder_card, text="Download-Ordner", style="Panel.TLabel", font=("Helvetica Neue", 12, "bold")).pack(anchor="w")
        self.folder_var = StringVar(value=str(Path(config["download_folder"]).expanduser()))
        ttk.Label(folder_card, textvariable=self.folder_var, style="Muted.TLabel", wraplength=440).pack(anchor="w", pady=(8, 10))
        ttk.Button(folder_card, text="Ordner wählen…", command=self.select_folder).pack(anchor="w")
        footer = ttk.Frame(outer)
        footer.pack(fill="x", side="bottom")
        self.logout_button = ttk.Button(footer, text="Abmelden", command=self.logout)
        self.logout_button.pack(side="right")
        self.root.after(150, self.refresh_server_state)
        self.root.after(200, self.process_ui_events)

    def save_network_settings(self):
        port_text = self.port_var.get().strip()
        if not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
            raise ValueError("Der Port muss zwischen 1 und 65535 liegen.")
        with config_lock:
            config["port"] = int(port_text)
            config["lan_enabled"] = bool(self.lan_var.get())
            save_config()
        return int(port_text)

    def on_lan_toggle(self):
        try:
            self.save_network_settings()
        except ValueError as exc:
            self.lan_var.set(not self.lan_var.get())
            messagebox.showerror("Port ungültig", str(exc), parent=self.root)
            return
        if server_manager.server is not None:
            self.stop_server()
            self.start_server()

    def start_server(self):
        try:
            port = self.save_network_settings()
        except ValueError as exc:
            messagebox.showerror("Server konnte nicht starten", str(exc), parent=self.root)
            self.status_var.set("Server gestoppt")
            return
        lan_enabled = bool(self.lan_var.get())
        missing = missing_audio_tools()
        if missing:
            brew = find_brew()
            if not brew:
                messagebox.showerror(
                    "Homebrew fehlt",
                    "Für Downloads werden yt-dlp und ffmpeg benötigt. Homebrew wurde nicht gefunden. "
                    "Installiere Homebrew von brew.sh und starte die App danach erneut.",
                    parent=self.root,
                )
                return
            formulas = missing_brew_formulas(missing)
            if not messagebox.askyesno(
                "Fehlende Audio-Tools",
                "Nicht gefunden: " + ", ".join(missing) + ".\n\n"
                "Soll Homebrew jetzt " + ", ".join(formulas) + " installieren? "
                "Homebrew installiert bei Bedarf auch notwendige Abhängigkeiten. "
                "Auf Intel-Macs kann Homebrew die Pakete aus dem Quellcode bauen müssen; das kann deutlich länger dauern.",
                parent=self.root,
            ):
                self.status_var.set("Server gestoppt · benötigte Audio-Tools fehlen.")
                return
            self.installing_dependencies = True
            self.status_var.set("Homebrew installiert die fehlenden Audio-Tools und Abhängigkeiten …")
            self.dependencies_var.set("Installation läuft · dieses Fenster bleibt bedienbar")
            self.start_button.configure(state="disabled")
            self.stop_button.configure(state="disabled")
            self.port_entry.configure(state="disabled")
            self.lan_check.configure(state="disabled")
            self.logout_button.configure(state="disabled")
            self.install_progress.pack(fill="x", pady=(0, 12))
            self.install_progress.start(12)
            threading.Thread(
                target=self.install_dependencies,
                args=(brew, formulas, port, lan_enabled),
                daemon=True,
                name="mixxx-dependency-installer",
            ).start()
            return
        self.launch_server(port, lan_enabled)

    def install_dependencies(self, brew: str, formulas: list[str], port: int, lan_enabled: bool) -> None:
        error = ""
        try:
            env = os.environ.copy()
            env["HOMEBREW_NO_AUTO_UPDATE"] = "1"
            result = subprocess.run(
                [brew, "install", *formulas],
                capture_output=True,
                text=True,
                timeout=10800,
                check=False,
                env=env,
            )
            install_log = result.stderr or result.stdout or ""
            if result.returncode:
                source_result = subprocess.run(
                    [brew, "install", "--build-from-source", *formulas],
                    capture_output=True,
                    text=True,
                    timeout=10800,
                    check=False,
                    env=env,
                )
                install_log += "\n\nHomebrew-Quellcode-Build:\n" + (source_result.stderr or source_result.stdout or "")
                result = source_result
            still_missing = missing_audio_tools()
            if result.returncode or still_missing:
                detail = install_log.strip()
                error = "Homebrew konnte die Audio-Tools nicht vollständig installieren."
                if "no bottle available" in detail.lower() or "tier 3" in detail.lower():
                    error += "\n\nHomebrew hat für diese Intel-Konfiguration kein fertiges Paket gefunden und auch den Quellcode-Build nicht abgeschlossen."
                if still_missing:
                    error += " Noch nicht verfügbar: " + ", ".join(still_missing) + "."
                if detail:
                    error += "\n\n" + detail[-1800:]
        except subprocess.TimeoutExpired:
            error = "Die Homebrew-Installation hat das Zeitlimit von 30 Minuten überschritten."
        except OSError as exc:
            error = f"Homebrew konnte nicht gestartet werden: {exc}"
        self.ui_events.put(("dependencies_done", error, port, lan_enabled))

    def process_ui_events(self):
        while True:
            try:
                event = self.ui_events.get_nowait()
            except queue.Empty:
                break
            if event[0] == "dependencies_done":
                self.finish_dependency_install(event[1], event[2], event[3])
        if self.root.winfo_exists():
            self.root.after(200, self.process_ui_events)

    def finish_dependency_install(self, error: str, port: int, lan_enabled: bool):
        self.installing_dependencies = False
        self.install_progress.stop()
        self.install_progress.pack_forget()
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.port_entry.configure(state="normal")
        self.lan_check.configure(state="normal")
        self.logout_button.configure(state="normal")
        if error:
            missing = missing_audio_tools()
            self.dependencies_var.set("Fehlen weiterhin: " + ", ".join(missing) if missing else "Audio-Tools bereit")
            self.status_var.set("Server gestoppt · Installation fehlgeschlagen.")
            messagebox.showerror("Installation fehlgeschlagen", error, parent=self.root)
            return
        self.dependencies_var.set("Audio-Tools bereit: yt-dlp, ffmpeg und ffprobe")
        self.launch_server(port, lan_enabled)

    def launch_server(self, port: int, lan_enabled: bool):
        try:
            url = server_manager.start(port, lan_enabled)
        except (OSError, RuntimeError) as exc:
            messagebox.showerror("Server konnte nicht starten", str(exc), parent=self.root)
            self.status_var.set("Server gestoppt")
            return
        self.status_var.set(f"Server läuft: {url}\nIm LAN-Modus ist HTTPS aktiv. Das Zertifikat ist selbstsigniert; Browser zeigen vor dem Vertrauen eine Sicherheitswarnung.")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.port_entry.configure(state="disabled")
        webbrowser.open(url)

    def stop_server(self):
        server_manager.stop()
        self.status_var.set("Server gestoppt; keine LAN-Verbindung aktiv.")
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.port_entry.configure(state="normal")
        self.lan_check.configure(state="normal")

    def refresh_server_state(self):
        if server_manager.server is None and hasattr(self, "start_button"):
            self.start_button.configure(state="normal")
            self.stop_button.configure(state="disabled")

    def select_folder(self):
        global download_folder
        selected = filedialog.askdirectory(
            parent=self.root,
            title="Mixxx-Zielordner auswählen",
            initialdir=str(Path(config["download_folder"]).expanduser()),
        )
        if selected:
            download_folder = Path(selected).resolve()
            self.folder_var.set(str(download_folder))
            with config_lock:
                config["download_folder"] = str(download_folder)
                save_config()

    def logout(self):
        if server_manager.server is not None:
            self.stop_server()
        self.authenticated = False
        self.show_auth_screen()

    def close(self):
        server_manager.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    download_folder.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=download_worker, daemon=True, name="mixxx-download-worker").start()
    ControlPanel().run()


if __name__ == "__main__":
    main()
