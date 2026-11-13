# 模型许可证

2026-11-09 从 Hugging Face 接口（`/api/models/<仓库>` 的 `cardData.license`）和模型说明页查到的结果。GitHub 备份（Release `models-v1`）转存的就是下表"下载来源"一列里的文件。

| 模型 | 下载来源（GGUF 转换） | 许可证 | 上游原始模型 | 上游许可证 |
|---|---|---|---|---|
| Qwen3-ASR 1.7B | cstr/qwen3-asr-1.7b-GGUF | Apache-2.0 | Qwen/Qwen3-ASR-1.7B | Apache-2.0 |
| Qwen3-ASR 0.6B（轻量模式，q4_k） | cstr/qwen3-asr-0.6b-GGUF | Apache-2.0 | Qwen/Qwen3-ASR-0.6B | Apache-2.0 |
| Qwen3-ForcedAligner 0.6B | cstr/qwen3-forced-aligner-0.6b-GGUF | Apache-2.0 | Qwen/Qwen3-ForcedAligner-0.6B | Apache-2.0 |
| MOSS-Transcribe-Diarize 0.9B | cstr/MOSS-Transcribe-Diarize-GGUF | Apache-2.0 | OpenMOSS-Team/MOSS-Transcribe-Diarize | Apache-2.0 |
| MiMo-V2.5-ASR | cstr/mimo-asr-GGUF | MIT | XiaomiMiMo/MiMo-V2.5-ASR | MIT |
| MiMo 音频编码器 | cstr/mimo-tokenizer-GGUF | MIT | XiaomiMiMo/MiMo-Audio-Tokenizer | MIT |
| Whisper large-v3 | ggerganov/whisper.cpp | MIT | openai/whisper-large-v3 | Apache-2.0（Hugging Face 页）；OpenAI 代码仓库为 MIT |
| Parakeet CTC 1.1B ja（2026-11-12 起程序不再使用，备份里仍保留） | cstr/parakeet-ctc-1.1b-ja-GGUF | Apache-2.0 | grider-transwithai/parakeet-ctc-1.1b-ja | Apache-2.0 |

所有仓库都不是 gated（不需要登录同意条款就能下载）。

## 结论

全部是宽松许可证，允许转存和再分发。GitHub 备份已于 2026-11-10 上传，附件全部从上表来源原样复制（Qwen 从 HF 重新下载，没有用本机那份不同版本的文件），附上了 Apache-2.0 和 MIT 全文。条件：

- **Apache-2.0**：随文件附上许可证全文，并说明文件经过改动（这些都是量化后的 GGUF，不是原始权重）。
- **MIT**：保留版权声明和许可证全文。
- 两者都不允许暗示原作者为本项目背书。

所以 Release `models-v1` 的说明里要写清每个文件的来源仓库、上游模型、许可证和"已量化为 GGUF"，并附上 Apache-2.0 和 MIT 的许可证全文。

## 需要留意

- 上游仓库都没有单独的 LICENSE 文件，许可证只写在模型说明的元数据里。
- Parakeet 日语模型没有写明是从哪个模型微调的。如果是从 nvidia/parakeet-ctc-1.1b（CC-BY-4.0）微调，再分发还要注明 NVIDIA 的署名。CC-BY-4.0 同样允许转存，所以不影响上传，Release 说明里一并注明即可。
- 说明页的性能表用的是 ReazonSpeech 等日语数据集。训练数据的许可证管的是数据本身，不改变权重声明的 Apache-2.0。
