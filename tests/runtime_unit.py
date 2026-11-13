"""运行库选择的单元测试：候选顺序、诊断输出解析、失败判断、自动和手动模式的回退。不需要模型和显卡。"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["CAPTIONJURY_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import runtime  # noqa: E402

# ---------- 候选顺序：驱动版本只用来筛选 ----------
assert runtime.order({"name": "RTX 3060", "driver": "621.01"}, True) == ["cuda", "cuda12", "vulkan", "cpu", "cpu_legacy"]
# issue #4 的机器：RTX A6000 + 560.94，CUDA 13 不在候选里，先试 CUDA 12.6
assert runtime.order({"name": "RTX A6000", "driver": "560.94"}, True)[0] == "cuda12"
assert runtime.order({"name": "GTX 970", "driver": "472.12"}, True)[0] == "vulkan"
assert runtime.order(None, True) == ["vulkan", "cpu", "cpu_legacy"]
assert runtime.order(None, False) == ["vulkan", "cpu_legacy"]
assert runtime.recommend({"name": "x", "driver": "坏的"}, True) == "vulkan"

# ---------- 诊断输出里的设备行 ----------
DIAG = """=== ggml backends + devices ===
ggml_cuda_init: found 1 CUDA devices (Total VRAM: 12287 MiB):
  registered devices : 2
    [0] gpu    name=CUDA0 desc=NVIDIA GeForce RTX 3060 Laptop GPU mem=11250/12287 MiB id=0000:01:00.0
    [1] cpu    name=CPU desc=AMD Ryzen 7 5800H with Radeon Graphics          mem=2661/32620 MiB id=?
"""
devs = runtime.DEVICE.findall(DIAG)
assert devs == [("CUDA0", "NVIDIA GeForce RTX 3060 Laptop GPU", "11250", "12287")], devs
assert runtime.GPU_FAIL.search("ggml_cuda_init: failed to initialize CUDA: no CUDA-capable device is detected")
assert runtime._short("NVIDIA GeForce RTX 3060 Laptop GPU") == "RTX 3060 Laptop GPU"
assert runtime._short("NVIDIA RTX A6000") == "RTX A6000"

# ---------- 跑任务失败时的原因 ----------
assert runtime.failure(0xC000001D, []) == "CPU 不支持这个版本的指令集"
assert runtime.failure(-1073741515, []) == "缺少运行库文件"  # 0xC0000135 以有符号数出现时
assert runtime.failure(1, ["ggml_backend_cuda_buffer_type_alloc_buffer: allocating 2400 MiB on device 0: cudaMalloc failed: out of memory"]) == "显存不足"
assert runtime.failure(1, ["ggml_cuda_init: failed to initialize CUDA"]) == "初始化失败"
assert runtime.failure(1, ["error: cannot open audio.wav"]) is None  # 和运行库无关的错误不换运行库

# ---------- 选显卡 ----------
big = [{"name": "CUDA0", "desc": "NVIDIA T400", "free": 1800, "total": 2048},
       {"name": "CUDA1", "desc": "NVIDIA RTX A6000", "free": 47000, "total": 49140}]
assert runtime._device({"gpu_device": "auto"}, "cuda", big)[0] == 1
assert runtime._device({"gpu_device": "0"}, "cuda", big) == (None, "显存不足（2 GB）")
assert runtime._device({"gpu_device": "5"}, "cuda", big) == (None, "没有编号 5 的显卡")
assert runtime._device({"gpu_device": "auto"}, "cuda", big[:1])[0] is None
assert runtime._device({"gpu_device": "auto"}, "cuda", big[:1], 1500)[0] == 0  # 轻量模型要的显存少，2 GB 的卡也能用

# ---------- 自动和手动模式 ----------
fake = {
    "cuda": "初始化失败",
    "cuda12": {"key": "cuda12", "backend": "cuda", "device": 0, "gpu": True, "label": "CUDA 12.6 · RTX A6000"},
    "vulkan": {"key": "vulkan", "backend": "vulkan", "device": 0, "gpu": True, "label": "Vulkan · RTX A6000"},
    "cpu": {"key": "cpu", "backend": "cpu", "device": None, "gpu": False, "label": "CPU"},
}
runtime.check = lambda cfg, key, model="qwen": fake.get(key, "没有安装")
runtime.installed = lambda cfg, key: key in fake
runtime.nvidia = lambda: {"name": "NVIDIA RTX A6000", "driver": "600.00"}
runtime.has_avx2 = lambda: True

got = runtime.pick({"gpu_backend": "auto"})
assert got["key"] == "cuda12" and got["notes"] == ["CUDA 13 初始化失败"], got
assert runtime.describe(got) == "CUDA 12.6 · RTX A6000（CUDA 13 初始化失败）"
# 跑的时候 CUDA 12.6 显存不足：换 Vulkan，原因都记着
got = runtime.pick({"gpu_backend": "auto"}, {"cuda12": "显存不足"})
assert got["key"] == "vulkan" and got["notes"] == ["CUDA 12.6 显存不足", "CUDA 13 初始化失败"], got
got = runtime.pick({"gpu_backend": "auto"}, {"cuda12": "显存不足", "vulkan": "初始化失败"})
assert got["key"] == "cpu" and runtime.describe(got).startswith("CPU（")
try:
    runtime.pick({"gpu_backend": "auto"}, {"cuda12": "x", "vulkan": "x", "cpu": "x"})
    raise AssertionError("一个都不能用时应该报错")
except RuntimeError as e:
    assert "没有能用的语音识别运行库" in str(e)
# 手动指定的用不了：不回退，说明原因和可选项
try:
    runtime.pick({"gpu_backend": "cuda"})
    raise AssertionError("手动指定的运行库用不了时应该报错")
except RuntimeError as e:
    assert "CUDA 13 用不了：初始化失败" in str(e) and "CUDA 12.6" in str(e), e
assert runtime.pick({"gpu_backend": "vulkan"})["key"] == "vulkan"

print("runtime_unit: 全部通过")
