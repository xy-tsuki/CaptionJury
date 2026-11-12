"""字幕对照组（CaptionJury）: desktop shell. The UI is served on localhost and shown in a WebView2 window."""
import http.server
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import webview
from webview.dom import DOMEventHandler

from core import deepseek, download, lines, models, pipeline, store, subtitles
from core.version import VERSION

# 打包成 exe 后，界面文件在 PyInstaller 解出的目录（sys._MEIPASS）里
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
UI = ROOT / "ui"
TOKEN = secrets.token_urlsafe(16)
NO_WINDOW = 0x08000000
MIN_LEN = 0.2  # 一条字幕最短 0.2 秒
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
    def start(self, paths, lang, opt, formats, diarize=False, compare=False, terms="", mode="custom"):
        cfg = store.load_config()
        if opt and cfg.get("proof_mode") != "agent" and not store.has_key():
            return {"error": f"AI 精度优化需要 {deepseek.label(cfg)} 的密钥，请先在设置里保存，或者改用复制给 Agent 的方式"}
        if diarize and not Path(store.load_config()["moss_model"]).exists():
            return {"error": "找不到 MOSS 模型，请在设置里检查路径"}
        store.save_config({"lang": lang, "opt": opt, "compare": compare, "formats": formats, "diarize": diarize, "mode": mode})
        try:
            self._queue.start(paths, lang, opt, formats, diarize, compare, deepseek.clean_terms(terms))
        except RuntimeError as e:
            return {"error": str(e)}
        return {}

    # ---------- 词库：导入、保存、AI 生成 ----------
    def import_terms(self):
        """选一个或多个词库文件，按选择顺序首尾相连，返回 {"text", "files"}；取消返回 None。"""
        res = self._window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=True, file_types=("词库文件 (*.txt)", "所有文件 (*.*)"))
        if not res:
            return None
        parts = []
        for p in res:
            raw = Path(p).read_bytes()
            for enc in ("utf-8-sig", "gbk"):  # 记事本旧版存的可能是 GBK
                try:
                    parts.append("\n".join(raw.decode(enc).splitlines()).strip())  # 统一成 \n 换行
                    break
                except UnicodeDecodeError:
                    continue
            else:
                return {"error": f"{Path(p).name} 不是文本文件，或者编码认不出来"}
        return {"text": "\n".join(x for x in parts if x), "files": [Path(p).name for p in res]}

    def save_terms(self, text):
        """把词库存成 txt（UTF-8，每行一个词），返回保存的路径；取消返回 None。"""
        res = self._window.create_file_dialog(webview.SAVE_DIALOG, save_filename="词库.txt", file_types=("词库文件 (*.txt)",))
        if not res:
            return None
        path = res if isinstance(res, str) else res[0]
        if not path.lower().endswith(".txt"):
            path += ".txt"
        Path(path).write_text("\n".join(deepseek.clean_terms(text)) + "\n", encoding="utf-8")
        return {"path": path}

    # ---------- 生成词库 ----------
    def terms_prompt(self, info):
        """复制给订阅制 Agent 的生成词库提示词。"""
        return deepseek.terms_prompt(info, agent=True)

    def make_terms(self, info):
        """用设置里的校对接口生成词库。"""
        cfg = store.load_config()
        key = store.get_key()
        if not key:
            return {"error": f"还没有保存 {deepseek.PROVIDERS.get(cfg.get('proof_provider'), ('校对 AI',))[0]} 的密钥，请先在设置里填写，或者复制提示词给 Agent"}
        try:
            with deepseek.Chat(cfg, key, 180) as chat:
                return {"terms": deepseek.make_terms(chat, info)}
        except Exception as e:
            return {"error": str(e)[:200]}

    # ---------- 订阅制 Agent：复制提示词、粘贴回复 ----------
    def agent_prompt(self):
        """正在等用户粘贴时，交给 Agent 的提示词。"""
        cur = self._queue.state.get("current") or {}
        a = cur.get("agent")
        if not a or not self._queue.agent_prompt:
            return None
        return {"k": a["k"], "step": a["step"], "prompt": self._queue.agent_prompt}

    def agent_submit(self, step, text):
        why = self._queue.agent_reply(step, text)
        return {"error": why} if why else {}

    def agent_skip(self):
        self._queue.agent_skip()

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

    def delete_job(self, jid):
        c = self._queue.state.get("current") or {}
        if self._queue.state["running"] and c.get("id") == jid:
            return {"error": "这个任务正在运行，结束后才能删除"}
        store.delete_job(jid)
        return {}

    def job(self, jid):
        j = store.load_job(jid)
        if lines.ensure(j):  # 旧任务，或者改了每条字数：按现在的字数拆好再给校对页
            store.save_job(j)
        peaks = store.JOBS / jid / "peaks.json"
        j["peaks"] = json.loads(peaks.read_text(encoding="utf-8")) if peaks.exists() else []
        j["token"] = TOKEN
        n = subtitles.line_max(j)
        for s in j["segments"]:  # 早先的任务，理由里还是 primary、moss 这些字段名
            s["why"] = deepseek.readable(s.get("why") or "")
            s["cut"] = subtitles.breaks(s, n)  # 导出时的拆分位置，只给校对页看，不存
        return j

    def cuts(self, jid):
        """每句导出时的拆分位置 {句子编号: [下标, ...]}。改了文字或每条字数以后，校对页用它刷新标记。"""
        j = store.load_job(jid)
        n = subtitles.line_max(j)
        return {s["id"]: subtitles.breaks(s, n) for s in j["segments"]}

    def update_text(self, jid, seg_id, text):
        """用户改了一条字幕的文字（手动填写或采用某个模型的写法）。存疑的条目改过就算用户确认过，返回新的状态。"""
        j = store.load_job(jid)
        status = None
        for s in j["segments"]:
            if s["id"] == seg_id:
                s["text"] = text
                if s.get("status") == "ask":
                    s["status"] = "keep"
                status = s.get("status")
        store.save_job(j)
        return status

    def confirm_segment(self, jid, seg_id):
        """存疑的条目文字不用改，用户直接确认。"""
        j = store.load_job(jid)
        for s in j["segments"]:
            if s["id"] == seg_id and s.get("status") == "ask":
                s["status"] = "keep"
        store.save_job(j)

    def set_times(self, jid, seg_id, a, b):
        """改一条字幕的入点和出点（秒）。夹在前后两条之间、至少 MIN_LEN 秒长，返回实际保存的时间。"""
        j = store.load_job(jid)
        segs = j["segments"]
        i = next(i for i, s in enumerate(segs) if s["id"] == seg_id)
        lo = segs[i - 1]["b"] if i else 0.0
        hi = segs[i + 1]["a"] if i + 1 < len(segs) else (j.get("duration") or 1e9)
        a = round(min(max(float(a), lo), hi - MIN_LEN), 3)
        b = round(min(max(float(b), a + MIN_LEN), hi), 3)
        segs[i]["a"], segs[i]["b"] = a, b
        store.save_job(j)
        return {"a": a, "b": b}

    def delete_segment(self, jid, seg_id):
        """删掉一条字幕，返回它和原来的位置，供撤销。"""
        j = store.load_job(jid)
        i = next(i for i, s in enumerate(j["segments"]) if s["id"] == seg_id)
        seg = j["segments"].pop(i)
        store.save_job(j)
        return {"seg": seg, "index": i}

    def restore_segment(self, jid, seg, index):
        """撤销删除：把字幕放回原来的位置。"""
        j = store.load_job(jid)
        if not any(s["id"] == seg["id"] for s in j["segments"]):
            j["segments"].insert(min(index, len(j["segments"])), seg)
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

    def set_no_punct(self, jid, on):
        j = store.load_job(jid)
        j["no_punct"] = bool(on)
        store.save_job(j)

    def set_line_max(self, jid, kind):
        """每条字幕的字数（wide 横屏 / tall 竖屏 / full 不限）。校对页随后重新打开任务时按新字数重新拆。也记进设置，下次新建的任务沿用。"""
        if kind not in subtitles.LINE_MAX:
            return
        j = store.load_job(jid)
        j["line_max"] = kind
        store.save_job(j)
        store.save_config({"line_max": kind})

    def export(self, jid, formats):
        j = store.load_job(jid)
        try:
            written = subtitles.export(j, formats)
        except OSError as e:  # 源文件旁边和备用文件夹都写不进去
            return {"error": f"字幕写不进去：{e.strerror or e}"}
        store.save_job(j)
        return written

    def reveal(self, path):
        if not Path(path).exists():
            return {"error": "找不到这个文件，可能已经移走或删除"}
        subprocess.Popen(["explorer", "/select,", path])
        return {}

    # ---------- settings ----------
    def config(self):
        cfg = store.load_config()
        cfg["has_key"] = store.has_key()
        cfg["providers"] = [{"key": k, "label": v[0], "url": v[1], "has_key": store.has_key(k)} for k, v in deepseek.PROVIDERS.items()]
        cfg["version"] = VERSION
        cfg["backups_active"] = {lang: [models.MODELS[k]["short"] for k in models.backups_for(cfg, lang)] for lang in ("zh", "ja")}
        cfg["backups_keys"] = {lang: list(models.backups_for(cfg, lang)) for lang in ("zh", "ja")}  # 估算用时按这些键查速度
        return cfg

    def save_config(self, changes):
        store.save_config(changes)

    def set_provider(self, provider):
        """换校对接口：接口地址换成预置的（自定义的保留原来填的），模型等拉到列表后再选。"""
        if provider not in deepseek.PROVIDERS:
            return {"error": "不认识这个接口"}
        url = deepseek.PROVIDERS[provider][1]
        changes = {"proof_provider": provider}
        if url:
            changes["proof_url"] = url
        elif store.load_config()["proof_provider"] != "custom":
            changes["proof_url"] = ""
        store.save_config(changes)
        return {}

    def set_key(self, key):
        store.set_key(key)

    def delete_key(self):
        store.delete_key()

    def list_models(self):
        """当前接口能用的模型，给设置页的下拉框。"""
        key = store.get_key()
        if not key:
            return {"error": "还没有保存密钥"}
        try:
            return {"models": deepseek.test(store.load_config(), key)}
        except Exception as e:
            return {"error": str(e)[:200]}

    test_key = list_models

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

    def setup_info(self):
        """首次运行引导页要的信息：显卡、两种运行库、必装模型是否就绪。"""
        cfg = store.load_config()
        gpu = None
        try:
            r = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
                               capture_output=True, text=True, timeout=10, creationflags=NO_WINDOW)
            if r.returncode == 0 and r.stdout.strip():
                name, driver = [x.strip() for x in r.stdout.strip().splitlines()[0].split(",")[:2]]
                gpu = {"name": name, "driver": driver, "cuda_ok": float(driver.split(".")[0]) >= 580}
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        runtimes = [{"key": k, "label": r["label"], "desc": r["desc"], "size": r["file"]["size"],
                     "installed": (Path(cfg["runtime_" + k]) / "crispasr.exe").is_file()} for k, r in models.RUNTIMES.items()]
        need = [m for m in models.MODELS if models.MODELS[m]["role"] == "main"]
        return {"gpu": gpu, "backend": cfg["gpu_backend"], "runtimes": runtimes,
                "models": [{"name": m, "label": models.MODELS[m]["label"], "size": models.size(m),
                            "installed": models.installed(cfg, m)} for m in need],
                "ffmpeg": bool(shutil.which(cfg["ffmpeg"]) or Path(cfg["ffmpeg"]).is_file())}


def main():
    api = Api()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/{TOKEN}/"
    if "--serve-only" in sys.argv:  # used for browser testing without a window
        print(url, flush=True)
        threading.Event().wait()
    window = webview.create_window("字幕对照组", url, js_api=api, width=1440, height=900, min_size=(1100, 720), background_color="#E8E9EB")
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
