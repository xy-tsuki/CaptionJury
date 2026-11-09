"""下载器：断点续传、分卷、SHA-256 校验和换源。用本机的小 HTTP 服务模拟各个来源，不连外网。"""
import hashlib
import http.server
import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
from core import download, models

DATA = os.urandom(250_000)
FILES = {}  # URL 路径 -> 内容
SEEN = []  # (路径, Range 头)


class Server(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        SEEN.append((self.path, self.headers.get("Range")))
        body = FILES.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        m = re.match(r"bytes=(\d+)-", self.headers.get("Range") or "")
        start = int(m.group(1)) if m else 0
        self.send_response(206 if m else 200)
        self.send_header("Content-Length", str(len(body) - start))
        self.end_headers()
        self.wfile.write(body[start:])


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Server)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_port}"

# 把三个来源都指到本机服务
models_urls = models.urls


def local_urls(f, source):
    return [u.replace("https://huggingface.co", base + "/hf").replace("https://hf-mirror.com", base + "/mirror")
             .replace(models.GITHUB_BACKUP, base + "/gh/") for u in models_urls(f, source)]


models.urls = local_urls
models.PART = 100_000  # 让测试文件也分成三卷
spec = {"path": r"t\model.gguf", "repo": "x/y", "size": len(DATA), "sha256": hashlib.sha256(DATA).hexdigest()}
tmp = Path(tempfile.mkdtemp())

# 1. 正常下载，校验通过后才出现正式文件
FILES["/hf/x/y/resolve/main/model.gguf"] = DATA
got = []
download.fetch_file(spec, tmp / "a.gguf", "hf", got.append)
assert (tmp / "a.gguf").read_bytes() == DATA and not (tmp / "a.gguf.part").exists() and sum(got) == len(DATA)

# 2. 断点续传：已有的 .part 只请求剩下的部分
(tmp / "b.gguf.part").write_bytes(DATA[:123_456])
SEEN.clear()
download.fetch_file(spec, tmp / "b.gguf", "hf")
assert (tmp / "b.gguf").read_bytes() == DATA and SEEN == [("/hf/x/y/resolve/main/model.gguf", "bytes=123456-")], SEEN

# 3. GitHub 备份的分卷按顺序接起来，续传时跳过已经下完的卷
for i in range(3):
    FILES[f"/gh/model.gguf.{i + 1:03d}"] = DATA[i * 100_000:(i + 1) * 100_000]
(tmp / "c.gguf.part").write_bytes(DATA[:150_000])
SEEN.clear()
download.fetch_file(spec, tmp / "c.gguf", "github")
assert (tmp / "c.gguf").read_bytes() == DATA
assert SEEN == [("/gh/model.gguf.002", "bytes=50000-"), ("/gh/model.gguf.003", None)], SEEN

# 4. 内容不对：删掉 .part，不留正式文件
FILES["/mirror/x/y/resolve/main/model.gguf"] = DATA[:-1] + b"!"
try:
    download.fetch_file(spec, tmp / "d.gguf", "mirror")
    raise AssertionError("应当校验失败")
except IOError as e:
    assert "SHA-256" in str(e)
assert not (tmp / "d.gguf").exists() and not (tmp / "d.gguf.part").exists()

# 5. 下载器：选的来源没有这个文件时自动换下一个来源，并记下原因
name = "whisper"
real = models.MODELS[name]["files"]["whisper_model"]
models.MODELS[name]["files"]["whisper_model"] = dict(spec, path=r"whisper\model.gguf")
cfg = {"whisper_model": str(tmp / "w" / "model.gguf")}
dl = download.Downloader(lambda: cfg, lambda c: None)
del FILES["/gh/model.gguf.001"]  # GitHub 备份缺一卷
FILES["/mirror/x/y/resolve/main/model.gguf"] = DATA
dl.add(name, "github")
for _ in range(100):
    if dl._thread and not dl._thread.is_alive():
        break
    time.sleep(0.05)
st = dl.status()
assert st["done"] == [name] and not st["failed"], st
assert Path(cfg["whisper_model"]).read_bytes() == DATA

# 6. 所有来源都失败时报告每个来源的原因
cfg["whisper_model"] = str(tmp / "w2" / "model.gguf")
FILES.clear()
dl.add(name, "hf")
for _ in range(100):
    if not dl._thread.is_alive():
        break
    time.sleep(0.05)
err = dl.status()["failed"][name]
assert "所有来源都下载失败" in err and "Hugging Face" in err and "GitHub" in err, err
models.MODELS[name]["files"]["whisper_model"] = real
print("all passed")
