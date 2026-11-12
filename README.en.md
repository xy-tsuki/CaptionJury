# Tingxie

[中文](README.md)

A Windows desktop tool that turns video or audio with mixed Chinese and Japanese speech into timed subtitles (SRT / ASS / VTT / TXT). All speech recognition runs on your own GPU, so audio and video never leave your computer. Optionally, an AI proofreads the text by comparing several transcripts: through the API of DeepSeek or another provider, or by copying a prompt to the Claude, ChatGPT or similar subscription you already have.

> The name is provisional. This is a 0.x release; the interface and features will still change. The app's interface is in Chinese.

## Features

- **Mixed Chinese and Japanese**: the main model, Qwen3-ASR 1.7B, handles sentences that switch between the two languages. A forced-alignment model times every character, and sentences are split at punctuation and pauses.
- **Multi-model comparison** (optional): backup models run per language (MiMo and Whisper for Chinese; Whisper and MiMo for Japanese; choose them in Settings). The review page lists every model's version of each line so you can adopt one with a click.
- **AI accuracy mode** (optional): the AI writes an outline of the whole recording to find proper nouns, Qwen listens again with those terms as a hint, and the AI then proofreads sentence by sentence, following what most transcripts agree on. Edits the transcripts do not support are not applied silently; they are marked "uncertain" for you to decide. It works best together with multi-model comparison, since there are more transcripts to compare.
- **Speaker labels** (optional): MOSS-Transcribe-Diarize tells speakers apart and labels each subtitle.
- **Nothing Qwen missed is dropped**: passages that other models heard but Qwen did not are listed as uncertain subtitles, which you can delete if they are wrong.
- **Three modes**: Fast (main model only), Accurate (multi-model comparison + AI proofreading, the default) and Custom (choose yourself). The New page estimates how long each takes from past runs and shows which steps will run.
- **Subtitles ready for editing**: pick the line length for your video (landscape ≤18 characters, portrait ≤12, or unlimited); long sentences are split at punctuation, pauses and likely word boundaries using per-character timing. Punctuation can be removed. The SRT imports directly into CapCut (剪映), Premiere, DaVinci Resolve and Final Cut.
- **Review page**: lines are listed exactly as they will be exported; compare each model's version line by line and adopt one with a click; edit text, drag the edges of a block on the timeline or type a timecode to change in and out points, play a single line, delete (with undo), find and replace, then export. An overview strip above the waveform marks where corrected and uncertain lines are in the whole recording.
- **History**: every past result is kept; reopen it for review or export it again in another format.

## Requirements

- Windows 10 / 11, 64-bit.
- A dedicated GPU: NVIDIA cards use the CUDA runtime (driver 580 or newer); AMD, Intel and older NVIDIA cards use the Vulkan runtime.
- Disk: about 4 GB for the required parts; backup and speaker models are extra (MiMo 4.9 GB; Whisper and MOSS about 1.1 GB each).
- Tested on an RTX 3060 Laptop GPU (12 GB VRAM); it uses about 6 GB of VRAM while running.

## Install

1. Download `Tingxie-Setup-<version>.exe` from [Releases](https://github.com/xy-tsuki/tingxie/releases) and run it. You can install for yourself only (no administrator rights needed) or for all users.
2. The installer is not code-signed, so Windows may warn about an "unknown publisher" or SmartScreen may block it. Click "More info" → "Run anyway".
3. On first launch a setup page detects your GPU, recommends the CUDA or Vulkan runtime, and downloads the runtime and the two required models. Models have three download sources (Hugging Face, the hf-mirror mirror, and this project's GitHub backup); it switches source on failure and resumes interrupted downloads.

## Usage

1. **New** (新建): drop or pick files, choose the language (Chinese / Japanese / Chinese + Japanese), choose a mode (Fast 快速 / Accurate 精准 / Custom 自定义; in Custom, tick multi-model comparison 多模型对照 and AI accuracy mode AI 精度优化 yourself), tick speaker labels if wanted, choose subtitle formats, and start. Subtitles are written next to the source file.
2. **Progress** (进度): one bar per model with an estimate of the time left; you can pause or cancel.
3. **Review** (校对): preview and the comparison of each model's version on the left, the subtitle list on the right, with filters for "uncertain" and "corrected" lines. An uncertain line becomes "confirmed" once you edit it or click "mark as confirmed" (标为已确认). Choose the line length at the top (changing it re-splits the list), then click "Export subtitles" (导出字幕) and pick the formats; the export menu says where to import the file in each editor.
4. **History** (历史): past results, which you can reopen for review, export, show in Explorer, or delete (only the saved result is deleted; the source file and exported subtitles stay).

For AI accuracy mode, pick one of two ways in Settings → Proofreading AI (设置 → 校对 AI):

- **Provider API** (服务商接口): choose a provider (DeepSeek, OpenAI, Qwen / DashScope, Kimi, Zhipu GLM, SiliconFlow, OpenRouter, Gemini, Claude, or a custom OpenAI-compatible address), enter its API key, and pick a model from the list the API returns. Keys are encrypted with Windows DPAPI, so only your Windows account can decrypt them, and each provider's key is stored separately.
- **Copy to an Agent** (复制给 Agent): for when you have a Claude, ChatGPT, Gemini or similar subscription but no API key. The job pauses at this step and the Progress page shows a prompt; copy it to your Agent, paste its whole reply back, and the program picks out the result. Each file takes two round trips (outline, then proofreading), and either can be skipped.

Either way, only the recognised text is sent, never the audio or video.

## Where data is kept

The program is installed in `%LOCALAPPDATA%\Programs\Tingxie` (for yourself) or Program Files. Runtimes, models, settings and jobs live in `%LOCALAPPDATA%\Tingxie`; upgrades leave them alone, and the uninstaller asks whether to delete them.

## Known limitations

- Windows only.
- Overlapping speech and quiet speech are often missed.
- Qwen sometimes misses a passage; subtitle times there are estimated from character counts. The app warns you, and you should check them on the review page.
- The runtime can only be downloaded from CrispASR's official GitHub, which may be slow from mainland China.

## Run from source

Requires Python 3.11.

```powershell
python -m venv $env:LOCALAPPDATA\Tingxie\venv
& "$env:LOCALAPPDATA\Tingxie\venv\Scripts\python.exe" -m pip install -r requirements.txt
& "$env:LOCALAPPDATA\Tingxie\venv\Scripts\python.exe" app.py
```

Build the installer with `powershell -ExecutionPolicy Bypass -File packaging\build.ps1` (needs Inno Setup 6.5 or newer; add `-SkipInstaller` to build only the exe). Developer notes are in [AGENTS.md](AGENTS.md) and the change history in the [devlog](docs/devlog.md) (both in Chinese).

## Credits and licenses

- Speech recognition runtime: [CrispASR](https://github.com/CrispStrobe/CrispASR).
- Models: Qwen3-ASR, Qwen3-ForcedAligner, MiMo-V2.5-ASR, Whisper large-v3 and MOSS-Transcribe-Diarize; sources and licenses are listed in [docs/model-licenses.md](docs/model-licenses.md).
- The installer bundles FFmpeg (GPL); third-party licenses ship with it in the `licenses` folder.
