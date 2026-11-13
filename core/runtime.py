"""挑选 CrispASR 运行库和显卡。

自动模式：按显卡和驱动版本排出候选（驱动版本只用来筛选），再用 `crispasr --diagnostics`
确认这个运行库真的枚举到了 GPU 设备；跑任务时遇到驱动初始化失败、显存不足或程序起不来，
流水线会把它记为失败，再调 pick 换下一个。
手动模式：只用设置里指定的那个，用不了就报出原因和可选的其他运行库。
"""
import ctypes
import os
import re
import subprocess
import threading
from pathlib import Path

from . import models

NO_WINDOW = 0x08000000
MIN_VRAM_MB = 4000  # 模型清单里没写 vram 时的默认值：Qwen3-ASR 1.7B q8_0 加对齐模型大约要 3.5 GB 显存
# --diagnostics 里每个设备一行："[0] gpu    name=CUDA0 desc=NVIDIA GeForce RTX 3060 Laptop GPU mem=11250/12287 MiB id=..."
DEVICE = re.compile(r"^\s*\[\d+\]\s+gpu\s+name=(\S+)\s+desc=(.*?)\s+mem=(\d+)/(\d+)\s*MiB", re.M)
# 跑任务时 stderr 里出现这些，说明是运行库或显卡的问题，自动模式下换下一个运行库重跑
GPU_FAIL = re.compile(
    r"failed to initialize|out of memory|cudaMalloc|ErrorOutOfDeviceMemory|ErrorDeviceLost|ErrorInitializationFailed"
    r"|CUDA error|ggml_vulkan: Device|no CUDA devices|unable to allocate|failed to allocate", re.I)
# 程序本身起不来的退出码：缺 DLL（0xC0000135）、CPU 不支持的指令（0xC000001D）、入口点找不到（0xC0000139）
CRASH_CODES = {0xC0000135: "缺少运行库文件", 0xC000001D: "CPU 不支持这个版本的指令集", 0xC0000139: "运行库文件版本不对"}
PF_AVX2 = 40  # IsProcessorFeaturePresent 的 PF_AVX2_INSTRUCTIONS_AVAILABLE

_lock = threading.Lock()
_diag = {}  # crispasr.exe 路径和修改时间 -> 诊断结果；驱动或运行库变了路径/时间就变，自然重新检测


def exe(cfg, key):
    return Path(cfg["runtime_" + key]) / "crispasr.exe"


def installed(cfg, key):
    return exe(cfg, key).is_file()


def label(key):
    return models.RUNTIMES[key]["short"]


def nvidia():
    """nvidia-smi 查到的第一块 NVIDIA 显卡：{"name", "driver"}；没有 NVIDIA 显卡或查不到时返回 None。"""
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
                           capture_output=True, text=True, timeout=10, creationflags=NO_WINDOW)
        if r.returncode == 0 and r.stdout.strip():
            name, driver = [x.strip() for x in r.stdout.strip().splitlines()[0].split(",")[:2]]
            return {"name": name, "driver": driver}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return None


def driver_major(gpu):
    try:
        return float(gpu["driver"].split(".")[0]) if gpu else 0.0
    except ValueError:
        return 0.0


def has_avx2():
    try:
        return bool(ctypes.windll.kernel32.IsProcessorFeaturePresent(PF_AVX2))
    except (AttributeError, OSError):
        return True  # 查不到时先当作支持，起不来会换 CPU Legacy


def order(gpu, avx2):
    """自动模式下依次尝试的运行库（不管装没装）。最后总有 CPU，CPU Legacy 兜底。"""
    keys = []
    if gpu:
        major = driver_major(gpu)
        keys += [k for k in ("cuda", "cuda12") if major >= models.RUNTIMES[k]["min_driver"]]
    keys.append("vulkan")
    keys += ["cpu", "cpu_legacy"] if avx2 else ["cpu_legacy"]
    return keys


def recommend(gpu, avx2):
    """引导页默认勾选的运行库：自动模式里排第一的。"""
    return order(gpu, avx2)[0]


def diagnose(path):
    """运行 crispasr --diagnostics。

    返回 {"devices": [{"name", "desc", "free", "total"}], "crash": 程序起不来的原因, "gpu_error": 没枚举到 GPU 的原因}。
    CPU 运行库只看 crash；GPU 运行库两样都要没有才算能用。
    """
    path = Path(path)
    try:
        stamp = (str(path), path.stat().st_mtime)
    except OSError:
        return {"devices": [], "crash": "找不到 crispasr.exe", "gpu_error": None}
    with _lock:
        if stamp in _diag:
            return _diag[stamp]
    try:
        r = subprocess.run([str(path), "--diagnostics"], capture_output=True, timeout=60, cwd=path.parent, creationflags=NO_WINDOW)
        text = (r.stdout + r.stderr).decode("utf-8", "replace")
        devices = [{"name": m[0], "desc": m[1].strip(), "free": int(m[2]), "total": int(m[3])} for m in DEVICE.findall(text)]
        crash = (crash_reason(r.returncode) or f"诊断时退出（{r.returncode}）") if r.returncode else None
        gpu_error = None if devices else "初始化失败" if GPU_FAIL.search(text) else "没有找到可用的设备"
        res = {"devices": devices, "crash": crash, "gpu_error": gpu_error}
    except subprocess.TimeoutExpired:
        res = {"devices": [], "crash": "诊断超时", "gpu_error": None}
    except OSError as e:
        res = {"devices": [], "crash": f"无法启动：{e}", "gpu_error": None}
    with _lock:
        _diag[stamp] = res
    return res


def crash_reason(code):
    return CRASH_CODES.get(code & 0xFFFFFFFF)


def failure(code, tail):
    """跑任务失败时，判断是不是运行库或显卡的问题；是的话返回简短原因，否则 None。"""
    reason = crash_reason(code)
    if reason:
        return reason
    text = "\n".join(tail)
    if re.search(r"out of memory|ErrorOutOfDeviceMemory|cudaMalloc|unable to allocate|failed to allocate", text, re.I):
        return "显存不足"
    if GPU_FAIL.search(text):
        return "初始化失败"
    return None


def _short(desc):
    return re.sub(r"^(NVIDIA|AMD|Intel\(R\))\s+(GeForce\s+)?", "", desc).strip() or desc


def _device(cfg, key, devices, need=MIN_VRAM_MB):
    """在诊断出的 GPU 里选一块：设置里指定了编号就用它，否则选总显存最大的。返回 (编号, 设备) 或 (None, 原因)。"""
    want = str(cfg.get("gpu_device", "auto"))
    if want != "auto":
        i = int(want) if want.isdigit() else -1
        if not 0 <= i < len(devices):
            return None, f"没有编号 {want} 的显卡"
        if devices[i]["total"] < need:
            return None, f"显存不足（{devices[i]['total'] / 1024:.0f} GB）"
        return i, devices[i]
    big = [(i, d) for i, d in enumerate(devices) if d["total"] >= need]
    if not big:
        return None, f"显存不足（{max(d['total'] for d in devices) / 1024:.0f} GB）"
    return max(big, key=lambda x: x[1]["total"])


def check(cfg, key, model="qwen"):
    """看一个运行库能不能用。能用返回 {"key", "backend", "device", "gpu", "label"}，不能用返回原因字符串。"""
    rt = models.RUNTIMES[key]
    if not installed(cfg, key):
        return "没有安装"
    d = diagnose(exe(cfg, key))
    if d["crash"]:
        return d["crash"]
    if not rt["gpu"]:
        return {"key": key, "backend": "cpu", "device": None, "gpu": False, "label": rt["short"]}
    if d["gpu_error"]:
        return d["gpu_error"]
    i, dev = _device(cfg, key, d["devices"], models.MODELS[model].get("vram", MIN_VRAM_MB))
    if i is None:
        return dev
    return {"key": key, "backend": rt["backend"], "device": i, "gpu": True, "label": f"{rt['short']} · {_short(dev['desc'])}"}


def pick(cfg, failed=None, model="qwen"):
    """选这次任务用的运行库。failed 是这次任务里已经失败的 {运行库: 原因}，不再选它们。

    返回的字典比 check 多一个 "notes"：自动模式下跳过的候选和原因，例如 ["CUDA 13 初始化失败"]。
    手动模式用不了、或自动模式一个都用不了时抛 RuntimeError，写明原因和可选项。
    """
    failed = dict(failed or {})
    want = cfg.get("gpu_backend", "auto")
    if want != "auto":
        if want not in models.RUNTIMES:
            raise RuntimeError(f"设置里的运行库 {want} 已经不支持，请在设置里重新选择")
        got = failed.get(want) or check(cfg, want, model)
        if isinstance(got, dict):
            return got | {"notes": []}
        others = [label(k) for k in models.RUNTIMES if k != want and installed(cfg, k)]
        hint = "可以在设置里改成「自动」" + (f"，或者换成已安装的 {'、'.join(others)}" if others else "，或者在安装引导里下载其他运行库")
        raise RuntimeError(f"{label(want)} 用不了：{got}。{hint}")
    notes = [f"{label(k)} {why}" for k, why in failed.items()]
    keys = order(nvidia(), has_avx2())
    from . import bench  # bench 也要用本模块，放这里避免循环导入
    best = bench.fastest(cfg, model)
    if best in keys:  # 本机测过速：实测最快的排第一，其余照旧
        keys = [best] + [k for k in keys if k != best]
    for key in keys:
        if key in failed or not installed(cfg, key):
            continue
        got = check(cfg, key, model)
        if isinstance(got, dict):
            return got | {"notes": notes}
        notes.append(f"{label(key)} {got}")
    tried = "；".join(notes) or "一个运行库都没有安装"
    raise RuntimeError(f"没有能用的语音识别运行库（{tried}）。请在安装引导里下载 CPU 版或其他运行库")


def threads():
    """CPU 运行库用的线程数：留一半给系统和界面，至少 4 个。"""
    return max(4, (os.cpu_count() or 8) // 2)


def describe(choice):
    """任务页显示的设备，例如 "CUDA 12.6 · RTX A6000"，回退时附上原因 "CPU · CUDA 13 初始化失败"。"""
    return choice["label"] + ("（" + "；".join(choice["notes"]) + "）" if choice.get("notes") else "")
