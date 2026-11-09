"""听写: desktop shell. The UI is served on localhost and shown in a WebView2 window."""
import http.server
import json
import mimetypes
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path

import webview
from webview.dom import DOMEventHandler

from core import deepseek, download, models, pipeline, store, subtitles

ROOT = Path(__file__).parent
UI = ROOT / "ui"
TOKEN = secrets.token_urlsafe(16)
NO_WINDOW = 0x08000000
MEDIA_EXT = "*.mp4;*.mkv;*.mov;*.webm;*.avi;*.ts;*.wav;*.mp3;*.flac;*.m4a;*.aac;*.ogg;*.opus"


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send_file(self, path):
        size = path.stat().st_size
        start, end = 0, size - 1
        m = re.match(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
        if m:
            start = int(m.group(1))
            end = min(int(m.group(2) or end), end)
        self.send_response(206 if m else 200)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if m:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            try:
                while left:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (ConnectionError, OSError):
                self.close_connection = True

    def do_GET(self):
        parts = self.path.split("?")[0].strip("/").split("/")
        path = None
        if parts[0] == TOKEN and len(parts) == 2:
            path = UI / (parts[1] or "index.html")
        elif parts[0] == TOKEN and len(parts) == 1:
            path = UI / "index.html"
        elif parts[0] == TOKEN and len(parts) == 4 and parts[1] == "media" and re.fullmatch(r"[0-9a-f]{12}", parts[2]):
            if parts[3] == "audio":
                path = store.JOBS / parts[2] / "audio.wav"
            elif parts[3] == "source":
                path = Path(store.load_job(parts[2])["source"])
        if path is None or not path.is_file():
            self.send_error(404)
            return
        self._send_file(path)


class Api:
    def __init__(self):
        self._queue = pipeline.Queue()
        self._dropped = []
        self._vram = (0.0, None)
        self._window = None
        self._downloads = download.Downloader(store.load_config, store.save_config)

    # ---------- files ----------
    def pick_files(self):
        res = self._window.create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=True, file_types=(f"音视频文件 ({MEDIA_EXT})", "所有文件 (*.*)")
        )
        return self.probe(list(res or []))

    def probe(self, paths):
        cfg = store.load_config()
        ffprobe = str(Path(cfg["ffmpeg"]).with_name("ffprobe"))
        out = []
        for p in paths:
            r = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p],
                capture_output=True, creationflags=NO_WINDOW,
            )
            try:
                dur = float(r.stdout.decode().strip())
            except ValueError:
                dur = 0
            out.append({"path": p, "name": Path(p).name, "duration": dur, "size": os.path.getsize(p)})
        return out

    # ---------- queue ----------
    def start(self, paths, lang, opt, formats, diarize=False):
        if opt and not store.has_key():
            return {"error": "精度优化需要 DeepSeek，请先在设置里保存密钥"}
        if diarize and not Path(store.load_config()["moss_model"]).exists():
            return {"error": "找不到 MOSS 模型，请在设置里检查路径"}
        store.save_config({"lang": lang, "opt": opt, "formats": formats, "diarize": diarize})
        try:
            self._queue.start(paths, lang, opt, formats, diarize)
        except RuntimeError as e:
            return {"error": str(e)}
        return {}

    def pause(self, paused):
        self._queue.set_paused(paused)

    def cancel(self):
        self._queue.stop()

    def status(self):
        s = dict(self._queue.state)
        s["paused"] = self._queue.paused
        s["vram"] = self._gpu_memory() if s["running"] else None
        s["dropped"], self._dropped = self._dropped, []
        return s

    def _gpu_memory(self):
        t, value = self._vram
        if time.time() - t > 2:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, creationflags=NO_WINDOW,
            )
            try:
                used, total = (int(x) for x in r.stdout.decode().split(","))
                value = f"{used / 1024:.1f} / {total / 1024:.0f} GB"
            except ValueError:
                value = None
            self._vram = (time.time(), value)
        return value

    # ---------- review ----------
    def jobs(self):
        return store.list_jobs()

    def job(self, jid):
        j = store.load_job(jid)
        peaks = store.JOBS / jid / "peaks.json"
        j["peaks"] = json.loads(peaks.read_text(encoding="utf-8")) if peaks.exists() else []
        j["token"] = TOKEN
        return j

    def update_text(self, jid, seg_id, text):
        j = store.load_job(jid)
        for s in j["segments"]:
            if s["id"] == seg_id:
                s["text"] = text
        store.save_job(j)

    def replace_all(self, jid, find, repl):
        j = store.load_job(jid)
        n = 0
        for s in j["segments"]:
            n += s["text"].count(find)
            s["text"] = s["text"].replace(find, repl)
        store.save_job(j)
        return n

    def set_speaker(self, jid, seg_id, speaker):
        """speaker is a known id, "" for nobody, or "+" for someone new."""
        j = store.load_job(jid)
        if speaker == "+":
            speaker = str(max(map(int, j["speakers"]), default=0) + 1)
            j["speakers"][speaker] = f"说话人{speaker}"
        for s in j["segments"]:
            if s["id"] == seg_id:
                s["speaker"] = speaker
        store.save_job(j)

    def rename_speaker(self, jid, speaker, name):
        """Giving someone a name already in use merges the two."""
        name = name.strip()
        j = store.load_job(jid)
        if not name or speaker not in j["speakers"]:
            return
        same = next((k for k, v in j["speakers"].items() if v == name and k != speaker), None)
        if same:
            del j["speakers"][speaker]
            for s in j["segments"]:
                if s.get("speaker") == speaker:
                    s["speaker"] = same
        else:
            j["speakers"][speaker] = name
        store.save_job(j)

    def set_speaker_tags(self, jid, on):
        j = store.load_job(jid)
        j["speaker_tags"] = on
        store.save_job(j)

    def export(self, jid, formats):
        j = store.load_job(jid)
        written = subtitles.export(j, formats)
        store.save_job(j)
        return written

    def reveal(self, path):
        subprocess.Popen(["explorer", "/select,", path])

    # ---------- settings ----------
    def config(self):
        cfg = store.load_config()
        cfg["has_key"] = store.has_key()
        return cfg

    def save_config(self, changes):
        store.save_config(changes)

    def set_key(self, key):
        store.set_key(key)

    def delete_key(self):
        store.delete_key()

    def test_key(self):
        key = store.get_key()
        if not key:
            return {"error": "还没有保存密钥"}
        try:
            return {"models": deepseek.test(store.load_config(), key)}
        except Exception as e:
            return {"error": str(e)[:200]}

    def models(self):
        """Every model, with its files and whether they are found, for the settings page."""
        cfg = store.load_config()
        picked = cfg.get("backups") or models.DEFAULT_BACKUPS
        return [{
            "name": name, "label": m["label"], "role": m["role"], "desc": m["desc"], "langs": list(m.get("langs", ())),
            "files": [{"key": k, "path": cfg[k], "ok": Path(cfg[k]).is_file()} for k in m["files"]],
            "installed": models.installed(cfg, name),
            "size": sum(f["size"] for k, f in m["files"].items() if not Path(cfg[k]).is_file()),
            "use": [lang for lang in m.get("langs", ()) if name in picked.get(lang, [])],
        } for name, m in models.MODELS.items()]

    def sources(self):
        return [{"key": k, "label": v} for k, v in models.SOURCES.items()]

    def download_model(self, name, source):
        """排队下载一个模型缺的文件。"""
        return self._downloads.add(name, source)

    def cancel_download(self):
        self._downloads.cancel()

    def download_status(self):
        return self._downloads.status()

    def set_backup(self, name, lang, on):
        """Turns a backup model on or off for sentences in one language."""
        cfg = store.load_config()
        picked = {k: list(v) for k, v in (cfg.get("backups") or models.DEFAULT_BACKUPS).items()}
        now = [k for k in picked.get(lang, []) if k != name] + ([name] if on else [])
        picked[lang] = [k for k in models.BACKUPS if k in now]
        store.save_config({"backups": picked})
        return picked

    def check_models(self):
        cfg = store.load_config()
        runtime = Path(cfg["runtime_" + cfg["gpu_backend"]]) / "crispasr.exe"
        return {name: models.installed(cfg, name) for name in models.MODELS} | {"runtime": runtime.exists()}


def main():
    api = Api()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/{TOKEN}/"
    if "--serve-only" in sys.argv:  # used for browser testing without a window
        print(url, flush=True)
        threading.Event().wait()
    window = webview.create_window("听写", url, js_api=api, width=1440, height=900, min_size=(1100, 720), background_color="#E8E9EB")
    api._window = window

    def on_drop(e):
        paths = [f.get("pywebviewFullPath") for f in e["dataTransfer"].get("files", [])]
        api._dropped += api.probe([p for p in paths if p])

    def bind(w):
        w.dom.document.events.dragover += DOMEventHandler(lambda e: None, True, True)
        w.dom.document.events.drop += DOMEventHandler(on_drop, True, True)

    webview.start(bind, window)


if __name__ == "__main__":
    main()
