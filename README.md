# 听写

[English](README.en.md)

把中日混说的视频或音频转成带时间轴的字幕（SRT / ASS / VTT / TXT）的 Windows 桌面工具。语音识别全部在本机显卡上跑，音视频不出电脑；可选让 DeepSeek 对照几份识别结果校对文字。

> 名称暂定，目前是 0.x 版本，界面和功能还会变。

## 能做什么

- **中日混说**：主模型 Qwen3-ASR 1.7B 一句话里夹着中文和日语也能听，配强制对齐模型给每个字标时间，按标点和停顿断句。
- **多模型对照**（可选）：按语言加跑备用模型（中文 MiMo、Whisper，日语 Whisper，可在设置里勾选），校对页逐句列出各模型的写法，一键采用。
- **AI 精度优化**（可选）：DeepSeek 先写全文大纲找出专有名词，Qwen 带着这些词再听一遍，最后 DeepSeek 按多数一致逐句校对。DeepSeek 的修改和识别结果对不上时不直接采用，标成"存疑"交给你选。和多模型对照一起开时，参考的结果更多，效果最好。
- **区分说话人**（可选）：用 MOSS-Transcribe-Diarize 分出说话人，标到每条字幕上。
- **Qwen 漏掉的话不丢**：其他模型听到、Qwen 没识别出的段落，单独列成存疑字幕，用不上可以删。
- **校对页**：逐条对照各模型的写法，一键采用；改文字、微调入点出点、播放这一条、删除（可撤销）、查找替换，最后导出。底部时间轴上方有一条整段录音的总览，标出已修正和存疑的位置。
- **历史记录**：以前识别过的结果都留着，可以重新打开校对，或者换一种格式再导出。

## 系统要求

- Windows 10 / 11，64 位。
- 独立显卡：NVIDIA 显卡用 CUDA 版运行库（显卡驱动 580 以上），AMD、Intel 或较老的 NVIDIA 显卡用 Vulkan 版。
- 硬盘：必装部分约 4 GB；备用模型和说话人模型另算（MiMo 4.9 GB，Whisper、MOSS 各约 1.1 GB）。
- 作者在 RTX 3060 Laptop（12 GB 显存）上测试，运行时占用约 6 GB 显存。

## 安装

1. 在 [Releases](https://github.com/xy-tsuki/tingxie/releases) 下载 `Tingxie-Setup-<版本>.exe`，双击安装。可以只为自己安装（不需要管理员权限），也可以为所有用户安装。
2. 安装包没有代码签名，Windows 可能提示"未知发布者"或被 SmartScreen（Windows 的下载安全检查）拦截，点"更多信息" → "仍要运行"。
3. 第一次打开会进入引导页：程序检测显卡，推荐 CUDA 或 Vulkan 运行库，然后下载运行库和两个必装模型。模型有三个下载来源（Hugging Face、hf-mirror 镜像、本项目 GitHub 备份），失败会自动换源，中断后可以续传。

## 使用

1. **新建**：拖入或选择文件，选识别语言（中文 / 日语 / 中文 + 日语），按需勾选"多模型对照""AI 精度优化"和"区分说话人"，选字幕格式，开始转写。字幕写在源文件旁边。
2. **进度**：每个模型一条进度条，显示预计剩余时间，可以暂停或取消。
3. **校对**：左边是预览和各模型写法的对照，右边是字幕列表，可以只看"存疑"或"已修正"的条目。存疑的条目改过文字或点"标为已确认"后变成"已确认"。改完点"导出字幕"，选好格式导出。
4. **历史**：列出以前的识别结果，可以打开校对、导出、打开源文件所在文件夹，或者删除记录（只删程序里保存的结果，不动源文件和导出的字幕）。

AI 精度优化要用 DeepSeek：在"设置"里填 DeepSeek API 密钥。密钥用 Windows DPAPI 加密保存，只有当前 Windows 账户能解开。发给 DeepSeek 的只有识别出的文字，不上传音视频。

## 数据放在哪里

程序装在 `%LOCALAPPDATA%\Programs\Tingxie`（只为自己安装时）或 Program Files。运行库、模型、设置和转写任务都在 `%LOCALAPPDATA%\Tingxie`，升级不会动它们；卸载时会问要不要一起删掉。

## 已知限制

- 只支持 Windows。
- 重叠说话、小声说话常识别不出来。
- Qwen 偶尔会漏听一段，那里的字幕时间是按字数估算的，程序会给出提示，需要在校对页对一下。
- 运行库只能从 CrispASR 官方 GitHub 下载，中国大陆网络可能较慢。

## 从源码运行

需要 Python 3.11。

```powershell
python -m venv $env:LOCALAPPDATA\Tingxie\venv
& "$env:LOCALAPPDATA\Tingxie\venv\Scripts\python.exe" -m pip install -r requirements.txt
& "$env:LOCALAPPDATA\Tingxie\venv\Scripts\python.exe" app.py
```

构建安装包：`powershell -ExecutionPolicy Bypass -File packaging\build.ps1`（需要 Inno Setup 6.5 以上；加 `-SkipInstaller` 只出 exe）。开发说明见 [AGENTS.md](AGENTS.md)，改动记录见 [开发日志](docs/devlog.md)。

## 致谢与许可证

- 语音识别运行库：[CrispASR](https://github.com/CrispStrobe/CrispASR)。
- 模型：Qwen3-ASR、Qwen3-ForcedAligner、MiMo-V2.5-ASR、Whisper large-v3、MOSS-Transcribe-Diarize，来源和许可证见 [docs/model-licenses.md](docs/model-licenses.md)。
- 安装包自带 FFmpeg（GPL），第三方许可证随安装包放在 `licenses` 目录。
