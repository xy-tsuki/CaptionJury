"""程序能用的所有模型：用途、需要哪些文件、去哪里下载、CrispASR 怎么调用。

文件默认放在模型目录（store.MODELS_DIR）下，每个路径都可以在设置里手动改。
每个文件都记了 Hugging Face 仓库、大小和 SHA-256，从任何来源下载都能校验。
"""
import math
from pathlib import Path

GITHUB_BACKUP = "https://github.com/xy-tsuki/tingxie/releases/download/models-v1/"
PART = 1_900_000_000  # GitHub Release 附件单个最大 2 GB，更大的文件在备份里分成编号的几卷


def _f(path, repo, size, sha256):
    return {"path": path, "repo": repo, "size": size, "sha256": sha256}


# role："main" 必装；"diarize" 只在区分说话人时需要；"backup" 是用户按语言勾选的备用模型，
# 多出一份识别结果给 DeepSeek 投票。
MODELS = {
    "qwen": {
        "label": "Qwen3-ASR 1.7B", "role": "main",
        "files": {"qwen_model": _f(r"Qwen3-ASR-1.7B\qwen3-asr-1.7b-q8_0.gguf", "cstr/qwen3-asr-1.7b-GGUF", 2506723200,
                                 "9851ab996591a2d0cb0efb216002764b509c86bd40c95e613d7b65b8e69c8a6e")},
        "desc": "主识别模型，中日混说都能听，带标点。",
    },
    "aligner": {
        "label": "Qwen3 强制对齐", "role": "main",
        "files": {"aligner_model": _f(r"Qwen3-ForcedAligner-0.6B\qwen3-forced-aligner-0.6b-q8_0.gguf", "cstr/qwen3-forced-aligner-0.6b-GGUF", 985594624,
                                    "539df5dd0fe1721e378ac13bfac9a26b1260dafb62d892c518c1f21244762636")},
        "desc": "给 Qwen 的每个字标上时间，字幕时间轴靠它。",
    },
    "moss": {
        "label": "MOSS 说话人", "role": "diarize",
        "files": {"moss_model": _f(r"MOSS-Transcribe-Diarize\moss-transcribe-diarize-0.9b-q4_k.gguf", "cstr/MOSS-Transcribe-Diarize-GGUF", 1190110816,
                                 "1801a463daffd3c4f40d7178c75425fc75260362b6f2653815f0a16a508c932e")},
        "desc": "区分说话人，顺带写出一份文字供校对参考。",
    },
    "mimo": {
        "label": "MiMo-V2.5-ASR", "short": "MiMo", "role": "backup", "langs": ("zh",),
        "files": {"mimo_model": _f(r"MiMo-V2.5-ASR\mimo-asr-q4_k.gguf", "cstr/mimo-asr-GGUF", 4517988832,
                                 "12dbc7cc7a20c7add6ff00bf8b12bca1c46304e0100a5c5a6e74bdecfc57a306"),
                  "mimo_codec": _f(r"MiMo-V2.5-ASR\mimo-tokenizer-q4_k.gguf", "cstr/mimo-tokenizer-GGUF", 395594656,
                                 "3f3a903b10294ead4ef6a4afec035639fd2113b1d307d42f649a97cc85670e3f")},
        "desc": "中文备用。识别质量不错但很慢，偶尔漏掉一段开头的话。",
    },
    "whisper": {
        "label": "Whisper large-v3", "short": "Whisper", "role": "backup", "langs": ("zh", "ja"),
        "files": {"whisper_model": _f(r"whisper\ggml-large-v3-q5_0.bin", "ggerganov/whisper.cpp", 1081140203,
                                    "d75795ecff3f83b5faa89d1900604ad8c780abd5739fae406de19f23ecd98ad1")},
        "desc": "中日都能当备用，速度快，错误和 Qwen 不同，适合投票；单独用时会凭空编句子。",
    },
    "parakeet": {
        "label": "Parakeet CTC 1.1B ja", "short": "Parakeet", "role": "backup", "langs": ("ja",),
        "files": {"parakeet_model": _f(r"parakeet-ctc-1.1b-ja\parakeet-ctc-1.1b-ja-q8_0.gguf", "cstr/parakeet-ctc-1.1b-ja-GGUF", 1137330464,
                                     "4fca7a83833012d515b4a71ab3df9a0abcf3362107369850d89dda962dfd34b4")},
        "desc": "日语备用，速度快，只懂日语。",
    },
}

BACKUPS = tuple(k for k, m in MODELS.items() if m["role"] == "backup")
DEFAULT_BACKUPS = {"zh": ["mimo"], "ja": ["parakeet"]}


SOURCES = {
    "hf": "Hugging Face（原始项目）",
    "mirror": "hf-mirror（中国大陆镜像）",
    "github": "本项目 GitHub 备份",
}


# CrispASR 运行库：从官方 GitHub Release 下载 zip，解压到数据目录。key 和设置里的 gpu_backend 一致。
CRISPASR_RELEASE = "https://github.com/CrispStrobe/CrispASR/releases/download/v0.8.50/"
RUNTIMES = {
    "cuda": {"label": "CrispASR 0.8.50 · CUDA 13", "dir": "crispasr-cuda13",
             "desc": "NVIDIA 显卡用，速度最快。需要 580 以上版本的显卡驱动。",
             "file": {"path": "crispasr-windows-x86_64-cuda13.zip", "size": 569609379,
                      "sha256": "f26ded177372f584f87f2644340d94ece7346ce7b7b8bc4c068a09333bd6b120"}},
    "vulkan": {"label": "CrispASR 0.8.50 · Vulkan", "dir": "crispasr-vulkan",
               "desc": "AMD、Intel 和较老的 NVIDIA 显卡用，比 CUDA 慢一些。",
               "file": {"path": "crispasr-windows-x86_64-vulkan.zip", "size": 37063304,
                        "sha256": "ac6fbfd527d782d7b999a135721dd131d76b18016283256c842b8f12411b1978"}},
}
RUNTIME_SOURCE = "CrispASR 官方 GitHub"
for _r in RUNTIMES.values():
    _r["file"]["url"] = CRISPASR_RELEASE + _r["file"]["path"]


def default_paths(models_dir):
    """每个模型文件的配置键 -> 默认路径。"""
    return {key: str(models_dir / f["path"]) for m in MODELS.values() for key, f in m["files"].items()}


def installed(cfg, name):
    return all(Path(cfg[key]).is_file() for key in MODELS[name]["files"])


def urls(f, source):
    """从某个来源下载一个文件要用的 URL，按顺序排列（只有 GitHub 分卷时才有多个）。"""
    if "url" in f:  # 运行库只有官方一个来源
        return [f["url"]]
    name = Path(f["path"]).name
    if source == "hf":
        return [f"https://huggingface.co/{f['repo']}/resolve/main/{name}"]
    if source == "mirror":
        return [f"https://hf-mirror.com/{f['repo']}/resolve/main/{name}"]
    if source == "github":
        n = math.ceil(f["size"] / PART)
        return [GITHUB_BACKUP + name] if n == 1 else [f"{GITHUB_BACKUP}{name}.{i:03d}" for i in range(1, n + 1)]
    raise KeyError(source)


def size(name):
    return sum(f["size"] for f in MODELS[name]["files"].values())


def args(cfg, name):
    """让 CrispASR 使用这个模型的命令行参数。"""
    if name == "qwen":
        return ["--backend", "qwen3-1.7b", "-m", cfg["qwen_model"]]
    if name == "mimo":
        return ["--backend", "mimo-asr", "-m", cfg["mimo_model"], "--codec-model", cfg["mimo_codec"]]
    if name == "whisper":
        return ["--backend", "whisper", "-m", cfg["whisper_model"]]
    if name == "parakeet":
        return ["--backend", "fastconformer-ctc", "-m", cfg["parakeet_model"]]
    if name == "moss":
        return ["--backend", "moss-diarize", "-m", cfg["moss_model"]]  # -n（最多输出多少 token）按窗口长度在流水线里给
    raise KeyError(name)


def backups_for(cfg, lang):
    """用户为这种语言勾选、并且已经装好的备用模型，按固定顺序排列。"""
    picked = (cfg.get("backups") or DEFAULT_BACKUPS).get(lang, [])
    return [k for k in BACKUPS if k in picked and lang in MODELS[k]["langs"] and installed(cfg, k)]
