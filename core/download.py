"""模型和运行库下载：断点续传、SHA-256 校验、失败时自动换下一个来源。

下载先写到目标文件旁边的 .part 文件，校验通过后才改名成正式文件，所以半截文件永远不会被当成已安装。
GitHub 备份里超过 2 GB 的文件分成几卷，按顺序接在同一个 .part 文件后面。
"""
import hashlib
import shutil
import threading
import zipfile
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
    client = client or httpx.Client(follow_redirects=True, timeout=httpx.Timeout(30, read=60), headers={"User-Agent": "captionjury-downloader"})
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


def label(name):
    """下载项的显示名：模型名或运行库名。"""
    if name.startswith("runtime_"):
        return models.RUNTIMES[name.removeprefix("runtime_")]["label"]
    return models.MODELS[name]["label"]


def total(name):
    if name.startswith("runtime_"):
        return models.RUNTIMES[name.removeprefix("runtime_")]["file"]["size"]
    return models.size(name)


class Downloader:
    """后台按顺序下载用户点过的模型和运行库。同一时间只下一个文件，状态给界面轮询。

    暂停：打断正在下的文件（.part 留着），把它放回队首，整个队列停下；继续时用 Range 从断点接着下。
    取消：可以只取消一项（正在下的或排队的），也可以全部取消；取消的 .part 也留着，再点下载会接着下。
    """

    def __init__(self, load_config, save_config):
        self.load_config, self.save_config = load_config, save_config
        self.queue, self.lock = [], threading.Lock()
        self.state = {"current": None, "queue": [], "done": [], "failed": {}, "paused": False}
        self._stop = None  # 打断正在下的文件的原因："cancel" 或 "pause"
        self._thread = None
        self._sources = {}

    def add(self, name, source):
        """排队下载一个模型的全部缺失文件；先用 source，失败就依次换其他来源。"""
        with self.lock:
            if name in self.queue or (self.state["current"] or {}).get("name") == name:
                return self.status()
            self.queue.append(name)
            self.state["failed"].pop(name, None)
            pool = models.RUNTIME_SOURCES if name.startswith("runtime_") else models.SOURCES
            source = source if source in pool else next(iter(pool))
            self._sources[name] = [source] + [s for s in pool if s != source]
            if not self.state["paused"]:
                self._kick()
        return self.status()

    def _kick(self):
        """队列里有东西、又没有线程在下时，开一个下载线程（调用方持有锁）。"""
        if self.queue and (not self._thread or not self._thread.is_alive()):
            self._stop = None
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def cancel(self, name=None):
        """name 为空时全部取消；否则只取消这一项（正在下的或排队的）。"""
        with self.lock:
            cur = (self.state["current"] or {}).get("name")
            if name is None:
                for n in self.queue:
                    self.state["failed"][n] = "已取消，再点下载会接着下"
                self.queue.clear()
                self.state["paused"] = False
                if cur:
                    self._stop = "cancel"
            elif name == cur:
                self._stop = "cancel"
            elif name in self.queue:
                self.queue.remove(name)
                self.state["failed"][name] = "已取消，再点下载会接着下"
            if self.state["paused"] and not self.queue:  # 暂停着的那项被取消了，就没有可继续的了
                self.state["paused"] = False
                self.state["current"] = None

    def pause(self):
        with self.lock:
            if self.state["current"] or self.queue:
                self.state["paused"] = True
                if self._thread and self._thread.is_alive():
                    self._stop = "pause"

    def resume(self):
        with self.lock:
            self.state["paused"] = False
            self._kick()

    def status(self):
        s = dict(self.state)
        s["queue"] = list(self.queue)
        s["labels"] = {n: label(n) for n in s["queue"] + s["done"] + list(s["failed"])}
        s["sizes"] = {n: total(n) for n in s["queue"]}
        return s

    def _run(self):
        while True:
            with self.lock:
                if not self.queue or self.state["paused"]:
                    if not self.state["paused"]:
                        self.state["current"] = None
                    return
                name = self.queue.pop(0)
            try:
                self._one(name)
                self.state["done"].append(name)
                self.state["failed"].pop(name, None)
            except Cancelled:
                with self.lock:
                    if self._stop == "pause":  # 放回队首，继续时从断点接着下
                        self.queue.insert(0, name)
                        if self.state["current"]:
                            self.state["current"]["speed"] = 0
                        self._stop = None
                        if self.state["paused"]:
                            return
                        continue  # 线程退出前用户已经点了继续
                    self._stop = None
                    self.state["failed"][name] = "已取消，再点下载会接着下"
            except Exception as e:
                self.state["failed"][name] = str(e)[:300]

    def _one(self, name):
        if name.startswith("runtime_"):
            return self._runtime(name)
        cfg = self.load_config()
        files = [(key, f) for key, f in models.MODELS[name]["files"].items() if not Path(cfg[key]).is_file()]
        self._files(name, models.MODELS[name]["label"], [(f, cfg[key]) for key, f in files])

    def _runtime(self, name):
        """下载 CrispASR 运行库 zip，校验后解压到设置里的运行库目录。"""
        r = models.RUNTIMES[name.removeprefix("runtime_")]
        target = Path(self.load_config()[name])
        zpath = target.parent / r["file"]["path"]
        if not zpath.is_file():
            self._files(name, r["label"], [(r["file"], zpath)])
        self.state["current"]["note"] = "正在解压"
        tmp = target.with_name(target.name + ".unzip")
        shutil.rmtree(tmp, ignore_errors=True)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(tmp)
        exe = next(tmp.rglob("crispasr.exe"), None)
        if not exe:
            shutil.rmtree(tmp, ignore_errors=True)
            raise IOError("压缩包里没有 crispasr.exe")
        shutil.rmtree(target, ignore_errors=True)
        exe.parent.replace(target)
        shutil.rmtree(tmp, ignore_errors=True)
        zpath.unlink()

    def _files(self, name, label, files):
        """按顺序下载 (文件信息, 目标路径) 列表，每个文件失败时换下一个来源。"""
        total = sum(f["size"] for f, _ in files)
        cur = self.state["current"] = {"name": name, "label": label, "done": 0, "total": total,
                                       "speed": 0, "source": "", "note": ""}
        base = 0
        for f, dest in files:
            errors = []
            for source in self._sources[name]:
                label = models.SOURCES.get(source) or models.RUNTIME_SOURCES[source]
                cur["source"] = label
                if errors:
                    cur["note"] = "；".join(errors) + f"。已改用{label}"
                got, t0 = [0], time.time()

                def on_bytes(n):
                    got[0] += n
                    cur["done"] = base + got[0]
                    cur["speed"] = got[0] / max(0.5, time.time() - t0)

                try:
                    fetch_file(f, dest, source, on_bytes, lambda: self._stop is not None)
                    break
                except Cancelled:
                    raise
                except Exception as e:
                    errors.append(f"{label}失败：{str(e)[:160]}")
            else:
                raise IOError("所有来源都下载失败。" + "；".join(errors))
            base += f["size"]
            cur["note"] = ""
