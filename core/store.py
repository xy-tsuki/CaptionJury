"""本机设置、经 DPAPI 加密的 API 密钥（每个校对接口一份）和任务文件。"""
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
KEYFILE = HOME / "deepseek.key"  # DeepSeek 的密钥沿用原来的文件名，其他接口的放在 keys 目录
MODELS_DIR = HOME / "models"
RUNTIME_DIR = HOME / "runtime"
# 安装版自带的 ffmpeg，放在 exe 旁边的 ffmpeg 文件夹里；源码运行时用 PATH 里的 ffmpeg
BUNDLED_FFMPEG = Path(sys.executable).parent / "ffmpeg" / "ffmpeg.exe"

DEFAULTS = {
    # auto 按显卡自动挑（core/runtime.py），也可以是 models.RUNTIMES 里的某一个；gpu_device 是 auto 或显卡编号
    "gpu_backend": "auto",
    "gpu_device": "auto",
    **{"runtime_" + k: str(RUNTIME_DIR / r["dir"]) for k, r in models.RUNTIMES.items()},
    "ffmpeg": "ffmpeg",
    **models.default_paths(MODELS_DIR),
    "backups": models.DEFAULT_BACKUPS,
    # 校对：api 走服务商接口（OpenAI 兼容格式）；agent 把提示词交给用户，复制给订阅制 Agent 再粘贴回来
    "proof_mode": "api",
    "proof_provider": "deepseek",
    "proof_url": "https://api.deepseek.com",
    "proof_model": "deepseek-flash",
    "glossary": "",
    "lang": "mix",
    # 新建页的模式：fast 只用主模型；accurate 多模型对照 + AI 校对；custom 自己选 opt 和 compare
    "mode": "accurate",
    "opt": True,
    "compare": True,
    "diarize": False,
    "formats": ["SRT", "ASS"],
    "line_max": "wide",  # 导出时每条字幕的字数：wide 横屏、tall 竖屏、full 不限（subtitles.LINE_MAX）
    "speed": {},  # 各步骤的速度统计，旧版只有一份（都在 GPU 上跑）
    "speed_rt": {},
    "bench": {},  # 本机测速结果（core/bench.py），自动模式把实测最快的运行库排第一  # 按运行库分开的速度统计：运行库 -> 同 speed
}


def _ensure():
    JOBS.mkdir(parents=True, exist_ok=True)


def load_config():
    _ensure()
    cfg = dict(DEFAULTS)
    if CONFIG.exists():
        saved = json.loads(CONFIG.read_text(encoding="utf-8"))
        # 只支持 DeepSeek 时的旧设置名
        for old, new in (("deepseek_url", "proof_url"), ("deepseek_model", "proof_model")):
            if old in saved and new not in saved:
                saved[new] = saved[old]
        if "mode" not in saved and ("opt" in saved or "compare" in saved):  # 有模式之前的设置：按两个开关推出来
            opt, compare = saved.get("opt", True), saved.get("compare", saved.get("opt", True))
            saved["mode"] = "accurate" if opt and compare else "fast" if not opt and not compare else "custom"
        # 有「自动」之前的设置：运行库是引导页按驱动版本替用户选的，改成自动；已有的运行库路径照旧保留
        if "gpu_device" not in saved:
            saved["gpu_backend"] = "auto"
        cfg.update(saved)
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


def key_file(provider=None):
    """这个校对接口的密钥文件；不写接口时用设置里当前选的那个。"""
    provider = provider or load_config()["proof_provider"]
    if not provider.isalnum():
        raise ValueError("接口名不对")
    return KEYFILE if provider == "deepseek" else HOME / "keys" / f"{provider}.key"


def set_key(key, provider=None):
    """用 Windows DPAPI 加密：只有当前 Windows 账户能解开这个文件。"""
    _ensure()
    f = key_file(provider)
    f.parent.mkdir(parents=True, exist_ok=True)
    blob = win32crypt.CryptProtectData(key.strip().encode("utf-8"), "Tingxie API key", None, None, None, 0)
    f.write_bytes(blob)


def get_key(provider=None):
    f = key_file(provider)
    if not f.exists():
        return None
    return win32crypt.CryptUnprotectData(f.read_bytes(), None, None, None, 0)[1].decode("utf-8")


def has_key(provider=None):
    return key_file(provider).exists()


def delete_key(provider=None):
    key_file(provider).unlink(missing_ok=True)


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
            # 没做完的任务：running（跑着或程序被关掉）、stopped（用户取消）、failed（出错）；有 params 的才能接着跑
            "state": j.get("state", "done" if "written" in j else "failed"), "error": j.get("error", ""),
            "params": j.get("params"), "diarize": bool(j.get("speakers")) or bool((j.get("params") or {}).get("diarize")),
        })
    return sorted(out, key=lambda x: x["created"], reverse=True)


def delete_job(jid):
    """删掉一个任务的目录（任务文件、音频和各模型的原始输出），不动源文件和导出的字幕。"""
    if not jid or not all(c in "0123456789abcdef" for c in jid):
        raise ValueError("任务编号不对")
    d = JOBS / jid
    if d.is_dir():
        shutil.rmtree(d)
