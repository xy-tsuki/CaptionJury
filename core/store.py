"""Local settings, DPAPI-encrypted API key, and job files."""
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

import win32crypt

from . import models

HOME = Path(os.environ.get("TINGXIE_HOME") or Path(os.environ["LOCALAPPDATA"]) / "Tingxie")
JOBS = HOME / "jobs"
CONFIG = HOME / "config.json"
KEYFILE = HOME / "deepseek.key"
MODELS_DIR = HOME / "models"
RUNTIME_DIR = HOME / "runtime"
# 安装版自带的 ffmpeg，放在 exe 旁边的 ffmpeg 文件夹里；源码运行时用 PATH 里的 ffmpeg
BUNDLED_FFMPEG = Path(sys.executable).parent / "ffmpeg" / "ffmpeg.exe"

DEFAULTS = {
    "gpu_backend": "cuda",
    "runtime_cuda": str(RUNTIME_DIR / "crispasr-cuda13"),
    "runtime_vulkan": str(RUNTIME_DIR / "crispasr-vulkan"),
    "ffmpeg": "ffmpeg",
    **models.default_paths(MODELS_DIR),
    "backups": models.DEFAULT_BACKUPS,
    "deepseek_url": "https://api.deepseek.com",
    "deepseek_model": "deepseek-flash",
    "glossary": "",
    "lang": "mix",
    "opt": True,
    "compare": True,
    "diarize": False,
    "formats": ["SRT", "ASS"],
    "speed": {},
}


def _ensure():
    JOBS.mkdir(parents=True, exist_ok=True)


def load_config():
    _ensure()
    cfg = dict(DEFAULTS)
    if CONFIG.exists():
        cfg.update(json.loads(CONFIG.read_text(encoding="utf-8")))
    # 去掉已经不支持的备用模型（Parakeet 在 2026-11-12 去掉了）；某种语言只勾了它的，改回默认
    backups = {}
    for lang, names in (cfg.get("backups") or {}).items():
        kept = [k for k in names if k in models.BACKUPS]
        backups[lang] = kept if kept or not names else list(models.DEFAULT_BACKUPS.get(lang, []))
    cfg["backups"] = backups or models.DEFAULT_BACKUPS
    if cfg["ffmpeg"] == "ffmpeg" and getattr(sys, "frozen", False) and BUNDLED_FFMPEG.is_file():
        cfg["ffmpeg"] = str(BUNDLED_FFMPEG)
    return cfg


def save_config(changes):
    cfg = load_config()
    cfg.update({k: v for k, v in changes.items() if k in DEFAULTS})
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    return cfg


def set_key(key):
    """Encrypts with Windows DPAPI: only this Windows account can decrypt the file."""
    _ensure()
    blob = win32crypt.CryptProtectData(key.strip().encode("utf-8"), "Tingxie DeepSeek key", None, None, None, 0)
    KEYFILE.write_bytes(blob)


def get_key():
    if not KEYFILE.exists():
        return None
    return win32crypt.CryptUnprotectData(KEYFILE.read_bytes(), None, None, None, 0)[1].decode("utf-8")


def has_key():
    return KEYFILE.exists()


def delete_key():
    KEYFILE.unlink(missing_ok=True)


def new_job_dir():
    _ensure()
    jid = uuid.uuid4().hex[:12]
    d = JOBS / jid
    d.mkdir()
    return jid, d


def save_job(job):
    path = JOBS / job["id"] / "job.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def load_job(jid):
    return json.loads((JOBS / jid / "job.json").read_text(encoding="utf-8"))


def list_jobs():
    """历史记录：每个任务的概要，新的在前。"""
    _ensure()
    out = []
    for d in JOBS.iterdir():
        f = d / "job.json"
        if not f.exists():
            continue
        try:
            j = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):  # 正在写或已损坏的任务文件跳过
            continue
        segs = j.get("segments", [])
        out.append({
            "id": j["id"], "name": j["name"], "source": j.get("source", ""), "created": j["created"],
            "duration": j.get("duration", 0), "lang": j.get("lang", ""), "opt": bool(j.get("opt")),
            "compare": bool(j.get("compare", j.get("opt"))), "speakers": len(j.get("speakers") or {}),
            "count": len(segs), "fix": sum(s.get("status") == "fix" for s in segs),
            "ask": sum(s.get("status") == "ask" for s in segs),
            "done": "written" in j, "written": j.get("written", []),
        })
    return sorted(out, key=lambda x: x["created"], reverse=True)


def delete_job(jid):
    """删掉一个任务的目录（任务文件、音频和各模型的原始输出），不动源文件和导出的字幕。"""
    if not jid or not all(c in "0123456789abcdef" for c in jid):
        raise ValueError("任务编号不对")
    d = JOBS / jid
    if d.is_dir():
        shutil.rmtree(d)
