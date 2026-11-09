"""模型下载：断点续传、SHA-256 校验、失败时自动换下一个来源。

下载先写到目标文件旁边的 .part 文件，校验通过后才改名成正式文件，所以半截文件永远不会被当成已安装。
GitHub 备份里超过 2 GB 的文件分成几卷，按顺序接在同一个 .part 文件后面。
"""
import hashlib
import threading
import time
from pathlib import Path

import httpx

from . import models

CHUNK = 1 << 20  # 每次读写 1 MB


class Cancelled(Exception):
    pass


def _fetch(client, url, out, have, part_size, on_bytes, cancelled):
    """把一个 URL 从第 have 个字节起追加写进 out；返回这一卷写完后的字节数。"""
    headers = {"Range": f"bytes={have}-"} if have else {}
    with client.stream("GET", url, headers=headers) as r:
        if r.status_code == 404:
            raise FileNotFoundError(f"来源里没有这个文件：{url}")
        r.raise_for_status()
        if have and r.status_code != 206:  # 服务器不支持续传，只能从头下这一卷
            out.seek(out.tell() - have)
            out.truncate()
            on_bytes(-have)
            have = 0
        for block in r.iter_bytes(CHUNK):
            if cancelled():
                raise Cancelled()
            out.write(block)
            have += len(block)
            on_bytes(len(block))
    if part_size is not None and have != part_size:
        raise IOError(f"下载不完整：{have} / {part_size} 字节")
    return have


def sha256(path, cancelled=lambda: False):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(CHUNK * 8):
            if cancelled():
                raise Cancelled()
            h.update(block)
    return h.hexdigest()


def fetch_file(f, dest, source, on_bytes=lambda n: None, cancelled=lambda: False, client=None):
    """从一个来源下载一个模型文件到 dest，校验 SHA-256。已经下了一半的会接着下。"""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    urls = models.urls(f, source)
    sizes = [min(models.PART, f["size"] - i * models.PART) for i in range(len(urls))] if len(urls) > 1 else [None]
    done = part.stat().st_size if part.exists() else 0
    if done > f["size"]:
        part.unlink()
        done = 0
    on_bytes(done)
    own = client is None
    client = client or httpx.Client(follow_redirects=True, timeout=httpx.Timeout(30, read=60), headers={"User-Agent": "tingxie-downloader"})
    try:
        with open(part, "ab") as out:
            start = 0
            for url, size in zip(urls, sizes):
                end = start + (size if size is not None else f["size"])
                if done < end:
                    got = _fetch(client, url, out, done - start, size, on_bytes, cancelled)
                    done = start + got
                start = end
    finally:
        if own:
            client.close()
    if part.stat().st_size != f["size"]:
        raise IOError(f"文件大小不对：{part.stat().st_size} / {f['size']} 字节")
    if sha256(part, cancelled) != f["sha256"]:
        part.unlink()  # 内容不对，留着只会让下次续传接着错
        raise IOError("SHA-256 校验失败，文件可能损坏或被篡改，已删除，请重新下载")
    part.replace(dest)


class Downloader:
    """后台按顺序下载用户点过的模型。同一时间只下一个文件，状态给界面轮询。"""

    def __init__(self, load_config, save_config):
        self.load_config, self.save_config = load_config, save_config
        self.queue, self.lock = [], threading.Lock()
        self.state = {"current": None, "queue": [], "done": [], "failed": {}}
        self._cancel = False
        self._thread = None
        self._sources = {}

    def add(self, name, source):
        """排队下载一个模型的全部缺失文件；先用 source，失败就依次换其他来源。"""
        with self.lock:
            if name in self.queue or (self.state["current"] or {}).get("name") == name:
                return self.status()
            self.queue.append(name)
            self.state["failed"].pop(name, None)
            self._sources[name] = [source] + [s for s in models.SOURCES if s != source]
            self._cancel = False
            if not self._thread or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()
        return self.status()

    def cancel(self):
        with self.lock:
            self.queue.clear()
            self._cancel = True

    def status(self):
        s = dict(self.state)
        s["queue"] = list(self.queue)
        return s

    def _run(self):
        while True:
            with self.lock:
                if not self.queue:
                    self.state["current"] = None
                    return
                name = self.queue.pop(0)
            try:
                self._one(name)
                self.state["done"].append(name)
            except Cancelled:
                self.state["failed"][name] = "已取消，再点下载会接着下"
            except Exception as e:
                self.state["failed"][name] = str(e)[:300]

    def _one(self, name):
        cfg = self.load_config()
        files = [(key, f) for key, f in models.MODELS[name]["files"].items() if not Path(cfg[key]).is_file()]
        total = sum(f["size"] for _, f in files)
        cur = self.state["current"] = {"name": name, "label": models.MODELS[name]["label"], "done": 0, "total": total,
                                       "speed": 0, "source": "", "note": ""}
        base = 0
        for key, f in files:
            errors = []
            for source in self._sources[name]:
                cur["source"] = models.SOURCES[source]
                got, t0 = [0], time.time()

                def on_bytes(n):
                    got[0] += n
                    cur["done"] = base + got[0]
                    cur["speed"] = got[0] / max(0.5, time.time() - t0)

                try:
                    fetch_file(f, cfg[key], source, on_bytes, lambda: self._cancel)
                    break
                except Cancelled:
                    raise
                except Exception as e:
                    errors.append(f"{models.SOURCES[source]}：{str(e)[:160]}")
                    cur["note"] = "，".join(errors) + "；正在换下一个来源"
            else:
                raise IOError("所有来源都下载失败。" + "；".join(errors))
            base += f["size"]
            cur["note"] = ""
