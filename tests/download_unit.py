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
SLOW = [False]  # 为 True 时分小块慢慢发，用来测暂停和取消
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
        if not SLOW[0]:
            self.wfile.write(body[start:])
            return
        try:
            for i in range(start, len(body), 16384):
                self.wfile.write(body[i:i + 16384])
                time.sleep(0.01)
        except (ConnectionError, OSError):  # 客户端暂停或取消时断开
            pass


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

# 7. 运行库：下载 zip、校验、解压到设置里的目录（zip 里多一层文件夹），删掉 zip
import io, zipfile
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    z.writestr("crispasr-windows-x86_64-vulkan/crispasr.exe", b"exe")
    z.writestr("crispasr-windows-x86_64-vulkan/ggml.dll", b"dll")
ZIP = buf.getvalue()
rt = models.RUNTIMES["vulkan"]["file"]
real_rt = dict(rt)
rt.update(size=len(ZIP), sha256=hashlib.sha256(ZIP).hexdigest(), url=base + "/rt/vulkan.zip")
FILES["/rt/vulkan.zip"] = ZIP
cfg["runtime_vulkan"] = str(tmp / "runtime" / "crispasr-vulkan")
(tmp / "runtime" / "crispasr-vulkan").mkdir(parents=True)
(tmp / "runtime" / "crispasr-vulkan" / "old.dll").write_bytes(b"old")  # 旧版本留下的文件要清掉
dl.add("runtime_vulkan", "hf")
for _ in range(100):
    if not dl._thread.is_alive():
        break
    time.sleep(0.05)
st = dl.status()
assert "runtime_vulkan" in st["done"], st
d = Path(cfg["runtime_vulkan"])
assert sorted(p.name for p in d.iterdir()) == ["crispasr.exe", "ggml.dll"], list(d.iterdir())
assert not any(p.suffix in (".zip", ".part") or p.name.endswith(".unzip") for p in d.parent.iterdir()), list(d.parent.iterdir())
rt.clear(); rt.update(real_rt)

# 8. 运行库的加速镜像：地址是镜像前缀加官方地址；选了镜像先试它，其余来源依次排在后面
f = models.RUNTIMES["cuda12"]["file"]
assert models.urls(f, "official") == [f["url"]]
assert models.urls(f, "ghfast") == ["https://ghfast.top/" + f["url"]]
import threading
hold = threading.Event()
dl._thread = threading.Thread(target=hold.wait, daemon=True)  # 占着下载线程，add 只排队、不真的下载
dl._thread.start()
dl.add("runtime_cuda12", "ghproxy")
assert dl._sources["runtime_cuda12"][0] == "ghproxy" and set(dl._sources["runtime_cuda12"]) == set(models.RUNTIME_SOURCES)
dl.add("runtime_cpu", "hf")  # 模型的来源名对运行库无效：从官方开始
assert dl._sources["runtime_cpu"][0] == "official"
dl.cancel()
hold.set()

# 9. 暂停、继续、单项取消：暂停时 .part 留着、放回队首；继续时用 Range 接着下
def wait(cond, secs=10):
    for _ in range(int(secs / 0.02)):
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError("等待超时")


BIG = os.urandom(2_000_000)
FILES["/hf/x/y/resolve/main/big.gguf"] = BIG
models.MODELS[name]["files"]["whisper_model"] = {"path": r"whisper\big.gguf", "repo": "x/y", "size": len(BIG), "sha256": hashlib.sha256(BIG).hexdigest()}
download.CHUNK = 16384  # 每读 16 KB 检查一次是否要停
SLOW[0] = True
cfg = {"whisper_model": str(tmp / "p" / "big.gguf")}
dl = download.Downloader(lambda: cfg, lambda c: None)
dl.add(name, "hf")
wait(lambda: (dl.state["current"] or {}).get("done", 0) > 100_000)
dl.pause()
wait(lambda: not dl._thread.is_alive())
st = dl.status()
part = tmp / "p" / "big.gguf.part"
assert st["paused"] and st["queue"] == [name] and not st["done"] and not st["failed"], st
assert 0 < part.stat().st_size < len(BIG) and not Path(cfg["whisper_model"]).exists()
assert st["labels"][name] == models.MODELS[name]["label"] and st["sizes"][name] == len(BIG)
have = part.stat().st_size
SEEN.clear()
dl.resume()
wait(lambda: name in dl.state["done"])
assert Path(cfg["whisper_model"]).read_bytes() == BIG and not dl.status()["paused"]
assert SEEN[0] == ("/hf/x/y/resolve/main/big.gguf", f"bytes={have}-"), SEEN

cfg["whisper_model"] = str(tmp / "q" / "big.gguf")  # 只取消正在下的这一项
dl.add(name, "hf")
wait(lambda: (dl.state["current"] or {}).get("done", 0) > 100_000)
dl.cancel(name)
wait(lambda: not dl._thread.is_alive())
assert "已取消" in dl.status()["failed"][name] and not dl.queue
assert not (tmp / "q" / "big.gguf.part").exists()  # 取消就删掉下了一半的文件

cfg["whisper_model"] = str(tmp / "r" / "big.gguf")  # 暂停后再取消：排队里的那项直接删
dl.add(name, "hf")
wait(lambda: (dl.state["current"] or {}).get("done", 0) > 100_000)
dl.pause()
wait(lambda: not dl._thread.is_alive())
assert (tmp / "r" / "big.gguf.part").exists()
dl.cancel()
st = dl.status()
assert not st["paused"] and not st["queue"] and st["current"] is None and "已取消" in st["failed"][name], st
assert not (tmp / "r" / "big.gguf.part").exists()
dl.pause()  # 没东西可暂停时什么也不做
assert not dl.status()["paused"]

cfg["whisper_model"] = str(tmp / "s" / "big.gguf")  # 暂停后只取消这一项（PR #5 review）：删掉，继续也不再下
dl.add(name, "hf")
wait(lambda: (dl.state["current"] or {}).get("done", 0) > 100_000)
dl.pause()
wait(lambda: not dl._thread.is_alive())
assert (dl.state["current"] or {}).get("name") == name and (tmp / "s" / "big.gguf.part").exists()
dl.cancel(name)
st = dl.status()
assert not st["queue"] and st["current"] is None and not st["paused"] and "已取消" in st["failed"][name], st
assert not (tmp / "s" / "big.gguf.part").exists()
dl.resume()
assert not dl.queue and not (dl._thread and dl._thread.is_alive()) and not Path(cfg["whisper_model"]).exists()
SLOW[0] = False
models.MODELS[name]["files"]["whisper_model"] = real
print("all passed")
