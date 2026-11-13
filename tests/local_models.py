"""For scripts that run the real models in a throwaway data folder: copies the model and runtime paths from this
computer's own settings, so the throwaway folder finds the same files."""
import json
import os
from pathlib import Path

from core import models, store

KEYS = ["runtime_" + k for k in models.RUNTIMES] + ["gpu_backend", "gpu_device", "ffmpeg", "backups"] + [k for m in models.MODELS.values() for k in m["files"]]


def use():
    real = Path(os.environ["LOCALAPPDATA"]) / "Tingxie" / "config.json"
    # 测试脚本接下来会改设置、写测试密钥。如果 core.store 在设置 TINGXIE_HOME 之前就被导入了，
    # store.HOME 还是真实数据目录，会把用户的设置和密钥覆盖掉（2026-11-10 出过一次），所以直接停下。
    if store.HOME.resolve() == real.parent.resolve() or str(store.HOME) != os.environ.get("TINGXIE_HOME"):
        raise SystemExit(f"测试的数据目录不是临时目录（{store.HOME}），已停止，避免改动本机真实设置")
    if real.exists():
        cfg = json.loads(real.read_text(encoding="utf-8"))
        store.save_config({k: cfg[k] for k in KEYS if k in cfg})
