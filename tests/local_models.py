"""For scripts that run the real models in a throwaway data folder: copies the model and runtime paths from this
computer's own settings, so the throwaway folder finds the same files."""
import json
import os
from pathlib import Path

from core import models, store

KEYS = ["runtime_cuda", "runtime_vulkan", "gpu_backend", "ffmpeg", "backups"] + [k for m in models.MODELS.values() for k in m["files"]]


def use():
    real = Path(os.environ["LOCALAPPDATA"]) / "Tingxie" / "config.json"
    if real.exists() and real.parent != store.HOME:
        cfg = json.loads(real.read_text(encoding="utf-8"))
        store.save_config({k: cfg[k] for k in KEYS if k in cfg})
