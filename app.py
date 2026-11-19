from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, jsonify, render_template_string, request

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
DEFAULT_FOLDER = Path.home() / "Music" / "Mixxx_Downloads"
os.environ["PATH"] = os.pathsep.join(
    ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", os.environ.get("PATH", "")]
)

app = Flask(__name__)
download_folder = DEFAULT_FOLDER
jobs: list[dict] = []
jobs_lock = threading.Lock()
download_queue: queue.Queue[str] = queue.Queue()


def load_folder() -> Path:
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        folder = Path(data.get("download_folder", str(DEFAULT_FOLDER))).expanduser()
        return folder if folder.is_absolute() else DEFAULT_FOLDER
    except (OSError, json.JSONDecodeError, TypeError, AttributeError):
        return DEFAULT_FOLDER


def save_folder(folder: Path) -> None:
    CONFIG_FILE.write_text(
        json.dumps({"download_folder": str(folder)}, indent=2), encoding="utf-8"
    )


def valid_youtube_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        return parsed.scheme == "https" and (
            host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com")
        )
    except ValueError:
        return False


PAGE = r"""
<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mixxx Downloader</title><style>
:root{color-scheme:dark;--bg:#101218;--panel:#191d26;--line:#2b3140;--text:#eef1f7;--muted:#a3abc0;--accent:#8b7cff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:850px;margin:48px auto;padding:0 20px}h1{margin:0 0 6px;font-size:28px}p{color:var(--muted)}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:20px;margin:18px 0}
.row{display:flex;gap:10px;flex-wrap:wrap}input{flex:1;min-width:230px;background:#10131a;color:var(--text);border:1px solid var(--line);border-radius:9px;padding:12px}
button{color:white;background:var(--accent);border:0;border-radius:9px;padding:11px 16px;cursor:pointer;font-weight:650}
button.secondary{background:#303646}.item{border-top:1px solid var(--line);padding:13px 0}.item:first-child{border-top:0}
.title{font-weight:650;overflow-wrap:anywhere}.meta{color:var(--muted);font-size:13px;overflow-wrap:anywhere}
.notice{font-size:13px;background:#242131;padding:12px;border-radius:9px}
</style></head><body><main>
<h1>Mixxx Downloader</h1><p>Lokale Warteschlange für rechtmäßig herunterladbare YouTube-Inhalte.</p>
<div class="panel"><strong>Zielordner</strong><p id="folder"></p><button class="secondary" onclick="chooseFolder()">Ordner wählen</button></div>
<div class="panel"><form id="form" class="row"><input id="url" type="url" placeholder="https://www.youtube.com/watch?v=…" required><button>Zur Warteschlange</button></form>
<p class="notice">Bitte nur Inhalte herunterladen, für die du die nötigen Rechte hast. Eine MP3-Konvertierung erhöht nicht die ursprüngliche Audioqualität.</p></div>
<div class="panel"><strong>Downloads</strong><div id="jobs"><p>Noch keine Downloads.</p></div></div></main>
<script>
async function refresh(){const r=await fetch('/api/status');const d=await r.json();document.getElementById('folder').textContent=d.folder;const root=document.getElementById('jobs');root.replaceChildren();if(!d.jobs.length){root.innerHTML='<p>Noch keine Downloads.</p>';return}for(const j of d.jobs){const box=document.createElement('div');box.className='item';const title=document.createElement('div');title.className='title';title.textContent=j.url;const meta=document.createElement('div');meta.className='meta';meta.textContent=j.status+(j.message?' · '+j.message:'');box.append(title,meta);root.append(box)}}
document.getElementById('form').addEventListener('submit',async e=>{e.preventDefault();const url=document.getElementById('url').value.trim();const r=await fetch('/api/queue',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})});const d=await r.json();if(!r.ok)alert(d.error||'Link konnte nicht hinzugefügt werden.');else document.getElementById('url').value='';refresh()});
async function chooseFolder(){const r=await fetch('/api/choose-folder',{method:'POST'});const d=await r.json();if(!r.ok)alert(d.error||'Ordner konnte nicht gewählt werden.');refresh()}
refresh();setInterval(refresh,2000);
</script></body></html>
"""


def worker() -> None:
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
                raise RuntimeError("yt-dlp nicht gefunden. Installiere es in der aktiven Python-Umgebung.")
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
                job["finished_at"] = time.time()
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


@app.get("/")
def index():
    return render_template_string(PAGE)


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
    if not shutil.which("osascript"):
        return jsonify(error="osascript ist auf diesem Mac nicht verfügbar."), 500
    result = subprocess.run(
        ["osascript", "-e", 'POSIX path of (choose folder with prompt "Mixxx-Zielordner auswählen")'],
        capture_output=True, text=True, timeout=120, check=False,
    )
    if result.returncode:
        return jsonify(error="Ordnerauswahl abgebrochen oder fehlgeschlagen."), 400
    selected = Path(result.stdout.strip()).expanduser().resolve()
    if not selected.is_dir():
        return jsonify(error="Der ausgewählte Pfad ist kein Ordner."), 400
    download_folder = selected
    save_folder(download_folder)
    return jsonify(ok=True, folder=str(download_folder))


if __name__ == "__main__":
    download_folder = load_folder()
    download_folder.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=worker, daemon=True).start()
    print("Mixxx Downloader: http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
