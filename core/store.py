"""Local settings, DPAPI-encrypted API key, and job files."""
import json
import os
import uuid
from pathlib import Path

import win32crypt

HOME = Path(os.environ.get("TINGXIE_HOME") or Path(os.environ["LOCALAPPDATA"]) / "Tingxie")
JOBS = HOME / "jobs"
CONFIG = HOME / "config.json"
KEYFILE = HOME / "deepseek.key"

RUNTIMES = {
    "cuda": r"E:\AI\Tingxu-runtimes\crispasr-cuda13-0.8.50\runtime\crispasr-windows-x86_64-cuda13",
    "vulkan": r"E:\AI\Tingxu-runtimes\crispasr-vulkan-0.8.50\runtime\crispasr-windows-x86_64-vulkan",
}

DEFAULTS = {
    "gpu_backend": "cuda",
    "runtime_cuda": RUNTIMES["cuda"],
    "runtime_vulkan": RUNTIMES["vulkan"],
    "ffmpeg": "ffmpeg",
    "qwen_model": r"E:\AI\Qwen3-ASR-1.7B-OASR\qwen3-asr-1.7b-q8_0.gguf",
    "mimo_model": r"E:\AI\MiMo-V2.5-ASR-GGUF\mimo-asr-q4_k.gguf",
    "mimo_codec": r"E:\AI\MiMo-V2.5-ASR-GGUF\mimo-tokenizer-q4_k.gguf",
    "parakeet_model": r"E:\AI\parakeet-ctc-1.1b-ja-GGUF\parakeet-ctc-1.1b-ja-q8_0.gguf",
    "aligner_model": r"E:\AI\Qwen3-ForcedAligner-0.6B-GGUF\qwen3-forced-aligner-0.6b-q8_0.gguf",
    "moss_model": r"E:\AI\MOSS-Transcribe-Diarize-GGUF\moss-transcribe-diarize-0.9b-q4_k.gguf",
    "deepseek_url": "https://api.deepseek.com",
    "deepseek_model": "deepseek-flash",
    "glossary": "",
    "lang": "mix",
    "opt": True,
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
    _ensure()
    out = []
    for d in JOBS.iterdir():
        f = d / "job.json"
        if f.exists():
            j = json.loads(f.read_text(encoding="utf-8"))
            out.append({"id": j["id"], "name": j["name"], "created": j["created"], "count": len(j.get("segments", []))})
    return sorted(out, key=lambda x: x["created"], reverse=True)
