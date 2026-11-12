"""把打包进程序的 Python 和各个依赖包的许可证复制到 licenses 目录，并写一份清单。build.ps1 调用：

    python packaging/collect_licenses.py <输出目录>
"""
import shutil
import sys
from importlib import metadata
from pathlib import Path

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
SKIP = {"pip", "setuptools", "wheel"}
rows = []

py_lic = Path(sys.base_prefix) / "LICENSE.txt"
if py_lic.is_file():
    shutil.copy(py_lic, out / "Python-LICENSE.txt")
rows.append(f"Python {sys.version.split()[0]}  PSF License  Python-LICENSE.txt")

for dist in sorted(metadata.distributions(), key=lambda d: d.metadata["Name"].lower()):
    name = dist.metadata["Name"]
    if name.lower() in SKIP:
        continue
    lic = dist.metadata.get("License-Expression") or dist.metadata.get("License") or ""
    if not lic or len(lic) > 60:  # 有的包把整篇许可证塞进 License 字段，改用分类标签
        lic = next((c.split(" :: ")[-1] for c in dist.metadata.get_all("Classifier") or [] if c.startswith("License ::")), "见许可证文件")
    # 只取 dist-info 目录里的许可证；有的包（pywin32）在每个子包里都放了一份
    files = [f for f in dist.files or [] if ".dist-info" in str(f) and f.name.upper().startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE"))]
    saved = []
    for f in files:
        rel = str(f).replace("\\", "/").split(".dist-info/", 1)[1].removeprefix("licenses/")
        dest = out / f"{name}-{rel.replace('/', '_')}"
        if not dest.suffix:
            dest = dest.with_suffix(".txt")
        shutil.copy(dist.locate_file(f), dest)
        saved.append(dest.name)
    rows.append(f"{name} {dist.version}  {lic}  {', '.join(saved) or '（包里没有许可证文件）'}")

rows += [
    "FFmpeg（GyanD essentials 构建）  GPL-3.0  FFmpeg-LICENSE.txt；源码见 FFmpeg-README.txt 里的链接和 https://ffmpeg.org",
    "",
    "以下组件不在安装包里，由程序在第一次使用时从官方来源下载，许可证随下载文件一起提供：",
    "CrispASR 运行库  MIT（内含 NVIDIA CUDA 运行库，按 NVIDIA 的再分发条款随 CrispASR 发布）",
    "语音识别模型  Apache-2.0 / MIT，详见 https://github.com/xy-tsuki/tingxie/releases/tag/models-v1",
]
(out / "THIRD_PARTY.txt").write_text("听写使用的第三方组件\n\n" + "\n".join(rows) + "\n", encoding="utf-8-sig")
print(f"{len(rows)} 项 -> {out}")
