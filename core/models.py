"""Every model the app can use: what it is for, which files it needs, and how CrispASR runs it.

Files live under the models folder by default (store.MODELS_DIR); each path can still be set by hand in the settings.
"""

# role: "main" models are always needed; "diarize" only with speaker separation; "backup" models are picked by the user,
# per language, as extra transcripts for DeepSeek to vote with.
MODELS = {
    "qwen": {
        "label": "Qwen3-ASR 1.7B", "role": "main",
        "files": {"qwen_model": r"Qwen3-ASR-1.7B\qwen3-asr-1.7b-q8_0.gguf"},
        "desc": "主识别模型，中日混说都能听，带标点。",
    },
    "aligner": {
        "label": "Qwen3 强制对齐", "role": "main",
        "files": {"aligner_model": r"Qwen3-ForcedAligner-0.6B\qwen3-forced-aligner-0.6b-q8_0.gguf"},
        "desc": "给 Qwen 的每个字标上时间，字幕时间轴靠它。",
    },
    "moss": {
        "label": "MOSS 说话人", "role": "diarize",
        "files": {"moss_model": r"MOSS-Transcribe-Diarize\moss-transcribe-diarize-0.9b-q4_k.gguf"},
        "desc": "区分说话人，顺带写出一份文字供校对参考。",
    },
    "mimo": {
        "label": "MiMo-V2.5-ASR", "short": "MiMo", "role": "backup", "langs": ("zh",),
        "files": {"mimo_model": r"MiMo-V2.5-ASR\mimo-asr-q4_k.gguf", "mimo_codec": r"MiMo-V2.5-ASR\mimo-tokenizer-q4_k.gguf"},
        "desc": "中文备用。识别质量不错但很慢，偶尔漏掉一段开头的话。",
    },
    "whisper": {
        "label": "Whisper large-v3", "short": "Whisper", "role": "backup", "langs": ("zh", "ja"),
        "files": {"whisper_model": r"whisper\ggml-large-v3-q5_0.bin"},
        "desc": "中日都能当备用，速度快，错误和 Qwen 不同，适合投票；单独用时会凭空编句子。",
    },
    "parakeet": {
        "label": "Parakeet CTC 1.1B ja", "short": "Parakeet", "role": "backup", "langs": ("ja",),
        "files": {"parakeet_model": r"parakeet-ctc-1.1b-ja\parakeet-ctc-1.1b-ja-q8_0.gguf"},
        "desc": "日语备用，速度快，只懂日语。",
    },
}

BACKUPS = tuple(k for k, m in MODELS.items() if m["role"] == "backup")
DEFAULT_BACKUPS = {"zh": ["mimo"], "ja": ["parakeet"]}


def default_paths(models_dir):
    """Config key -> default file path, for every model file."""
    return {key: str(models_dir / rel) for m in MODELS.values() for key, rel in m["files"].items()}


def installed(cfg, name):
    from pathlib import Path
    return all(Path(cfg[key]).is_file() for key in MODELS[name]["files"])


def args(cfg, name):
    """CrispASR arguments that select this model."""
    if name == "qwen":
        return ["--backend", "qwen3-1.7b", "-m", cfg["qwen_model"]]
    if name == "mimo":
        return ["--backend", "mimo-asr", "-m", cfg["mimo_model"], "--codec-model", cfg["mimo_codec"]]
    if name == "whisper":
        return ["--backend", "whisper", "-m", cfg["whisper_model"]]
    if name == "parakeet":
        return ["--backend", "fastconformer-ctc", "-m", cfg["parakeet_model"]]
    if name == "moss":
        return ["--backend", "moss-diarize", "-m", cfg["moss_model"], "-n", "4096"]
    raise KeyError(name)


def backups_for(cfg, lang):
    """The backup models the user picked for sentences in this language that are installed, in a stable order."""
    picked = (cfg.get("backups") or DEFAULT_BACKUPS).get(lang, [])
    return [k for k in BACKUPS if k in picked and lang in MODELS[k]["langs"] and installed(cfg, k)]
