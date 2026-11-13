"""本机测速：用同一段音频、同一个模型和相同的对齐选项，比较能用的运行库，记住最快的那个。

测速音频取自最近一个任务的 audio.wav（中间截 30 秒），没有任务时提示先转写一次。
每个运行库跑 RUNS 次：第一次包含加载模型、编译着色器等一次性开销，后几次取最短的作为常规耗时。
结果存进设置的 bench：{"sig", "model", "results": {运行库: {"first", "warm", "label"} 或 {"error"}}, "fastest"}。
显卡驱动、运行库文件、模型文件任何一样变了，sig 就对不上，结果作废（runtime.pick 不再参考）。
"""
import hashlib
import json
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from . import models, runtime, store

NO_WINDOW = 0x08000000
CLIP = 30  # 测速音频的秒数
RUNS = 3


def signature(cfg, model):
    """决定测速结果是否还有效的指纹：显卡驱动、各运行库 exe、模型和对齐模型文件。"""
    parts = [json.dumps(runtime.nvidia(), sort_keys=True)]
    for k in models.RUNTIMES:
        p = runtime.exe(cfg, k)
        parts.append(f"{k}:{p}:{p.stat().st_mtime if p.is_file() else 0}")
    for key in list(models.MODELS[model]["files"]) + ["aligner_model"]:
        p = Path(cfg[key])
        parts.append(f"{key}:{p}:{p.stat().st_size if p.is_file() else 0}")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


def fastest(cfg, model="qwen"):
    """测速结果仍然有效时返回最快的运行库，否则 None。"""
    b = cfg.get("bench") or {}
    if not b.get("fastest") or b.get("model") != model or b.get("sig") != signature(cfg, model):
        return None
    return b["fastest"]


def sample_audio():
    """最近一个任务的音频；没有就返回 None。"""
    if not store.JOBS.is_dir():
        return None
    wavs = [d / "audio.wav" for d in store.JOBS.iterdir() if (d / "audio.wav").is_file()]
    return max(wavs, key=lambda p: p.stat().st_mtime) if wavs else None


def _clip(cfg, src, dest):
    """从音频中间截 CLIP 秒（开头常是片头音乐或静音）。"""
    import wave
    with wave.open(str(src)) as w:
        total = w.getnframes() / w.getframerate()
    start = max(0.0, min(total - CLIP, total / 2 - CLIP / 2))
    subprocess.run([cfg["ffmpeg"], "-v", "error", "-y", "-ss", f"{start:.2f}", "-t", str(CLIP), "-i", str(src),
                    "-ac", "1", "-ar", "16000", str(dest)], check=True, capture_output=True, creationflags=NO_WINDOW)


class Bench:
    """在后台线程里测速；界面每秒取一次 status。"""

    def __init__(self):
        self.state = {"running": False, "note": "", "results": {}, "fastest": None, "error": None}
        self._thread = None

    def status(self):
        return dict(self.state)

    def start(self, model="qwen"):
        if self.state["running"]:
            return self.status()
        self.state = {"running": True, "note": "准备中", "results": {}, "fastest": None, "error": None}
        self._thread = threading.Thread(target=self._run, args=(model,), daemon=True)
        self._thread.start()
        return self.status()

    def _run(self, model):
        try:
            self._bench(model)
        except Exception as e:  # 显示在设置页
            self.state["error"] = str(e)[:300]
        finally:
            self.state["running"] = False
            self.state["note"] = ""

    def _bench(self, model):
        cfg = store.load_config()
        if not models.installed(cfg, model):
            raise RuntimeError(f"{models.MODELS[model]['label']} 没有安装")
        src = sample_audio()
        if not src:
            raise RuntimeError("还没有转写过任何文件。先转写一次，测速会用那段音频")
        keys = [k for k in models.RUNTIMES if runtime.installed(cfg, k)]
        if len(keys) < 1:
            raise RuntimeError("一个运行库都没有安装")
        tmp = Path(tempfile.mkdtemp(prefix="captionjury-bench-"))
        clip = tmp / "clip.wav"
        _clip(cfg, src, clip)
        results = self.state["results"]
        for k in keys:
            got = runtime.check(dict(cfg, gpu_backend=k), k)
            if not isinstance(got, dict):
                results[k] = {"error": got}
                continue
            times = []
            for i in range(RUNS):
                self.state["note"] = f"{got['label']}：第 {i + 1}/{RUNS} 次"
                t0 = time.time()
                p = subprocess.run(command(cfg, got, model, clip, tmp / f"out_{k}"), capture_output=True, cwd=tmp,
                                   creationflags=NO_WINDOW)
                if p.returncode:
                    tail = p.stderr.decode("utf-8", "replace").splitlines()[-40:]
                    results[k] = {"error": runtime.failure(p.returncode, tail) or f"退出（{p.returncode}）"}
                    break
                times.append(time.time() - t0)
            else:
                results[k] = {"label": got["label"], "first": round(times[0], 2), "warm": round(min(times[1:] or times), 2)}
        ok = {k: r for k, r in results.items() if "warm" in r}
        if not ok:
            raise RuntimeError("没有一个运行库跑通：" + "；".join(f"{runtime.label(k)} {r['error']}" for k, r in results.items()))
        best = min(ok, key=lambda k: ok[k]["warm"])
        self.state["fastest"] = best
        store.save_config({"bench": {"sig": signature(cfg, model), "model": model, "results": results, "fastest": best,
                                     "when": time.time()}})


def command(cfg, choice, model, clip, out):
    """测速用的 crispasr 命令：和正式转写一样带上对齐模型。"""
    cmd = [str(runtime.exe(cfg, choice["key"])), "--gpu-backend", choice["backend"]]
    if choice["device"] is not None:
        cmd += ["-dev", str(choice["device"])]
    if not choice["gpu"]:
        cmd += ["-t", str(runtime.threads())]
    cmd += models.args(cfg, model) + ["-f", str(clip), "-l", "zh", "-ojf", "-of", str(out)]
    if Path(cfg["aligner_model"]).is_file():
        cmd += ["-am", cfg["aligner_model"]]
    return cmd
