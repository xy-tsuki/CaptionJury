"""断点续跑的单元测试：指纹、沿用判断、沿用的步骤不记速度。不需要模型。真模型的检查见 e2e.py。"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["CAPTIONJURY_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from core import pipeline, progress  # noqa: E402

d = Path(tempfile.mkdtemp())

# 指纹：输入一样才一样
a = pipeline._fingerprint(["-m", "x.gguf", "-l", "zh"], "seg")
assert a == pipeline._fingerprint(["-m", "x.gguf", "-l", "zh"], "seg")
assert a != pipeline._fingerprint(["-m", "x.gguf", "-l", "ja"], "seg")
assert a != pipeline._fingerprint(["-m", "x.gguf", "-l", "zh"], "seg2")

# 只有跑完（写了 key）而且指纹一样才沿用
(d / "out_qwen.json").write_text("{}", encoding="utf-8")
assert not pipeline._reusable(d, "out_qwen.json", a)  # 跑到一半：有输出没有 key
pipeline._mark(d, "out_qwen.json", a)
assert pipeline._reusable(d, "out_qwen.json", a)
assert not pipeline._reusable(d, "out_qwen.json", "别的指纹")  # 换了模型、语言或提示
pipeline._unmark(d, "out_qwen.json")
assert not pipeline._reusable(d, "out_qwen.json", a)
assert not pipeline._reusable(d, "out_none.json", a)

# 模型文件大小算进指纹：同一路径换了文件也会重跑
m = d / "m.gguf"
m.write_bytes(b"12345")
assert pipeline._file_sizes(["-m", str(m), "-l", "zh", str(d / "missing.gguf")]) == [5]

# 沿用的步骤：显示“沿用上次结果”，不拿来估算速度
cur = {}
t = progress.Tracker(["extract", "qwen"], 100, cur, {}, lambda: 0.0)
t.set_audio("qwen", 100)
before = t.rates["qwen"]
t.reused.add("qwen")
t.finish("qwen")
assert t.rates["qwen"] == before and next(x for x in cur["lanes"] if x["key"] == "qwen")["state"] == "沿用上次结果"
t.finish("extract", "失败", learn=False)
assert next(x for x in cur["lanes"] if x["key"] == "extract")["state"] == "失败"

print("resume_unit: 全部通过")
