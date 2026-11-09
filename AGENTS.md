# 听写（暂定名，仓库名 tingxie）

## 项目是什么
Windows 桌面工具：把中日混说的视频或音频转成带时间轴的字幕（SRT / ASS / VTT / TXT）。在本机 GPU 上跑语音识别，再由 DeepSeek 对照几份识别结果校对。用户自己用，暂未发布。

## 技术栈
- Python 3.11，界面用 pywebview（WebView2 窗口）加一个本机 HTTP 服务；前端是原生 HTML / CSS / JS，没有构建步骤。
- 语音识别通过 CrispASR 0.8.50（ggml，CUDA 13 / Vulkan 两个版本）的命令行调用：
  - 主模型 Qwen3-ASR 1.7B q8_0，配 Qwen3-ForcedAligner 0.6B 出字级时间戳；
  - 备用模型（用户按语言勾选，可多选）：MiMo-V2.5-ASR q4_k（中文）、Whisper large-v3 q5_0（中日）、Parakeet CTC 1.1B ja（日语）；
  - 说话人区分：MOSS-Transcribe-Diarize 0.9B q4_k。
- 校对：DeepSeek API（默认 flash 模型），用 httpx 直接调用。
- 音频处理：ffmpeg。
- 密钥用 Windows DPAPI（pywin32 的 win32crypt）加密保存。

## 目录结构
- `app.py`：程序入口。启动本机 HTTP 服务和 WebView2 窗口，`Api` 类是前端可调用的全部接口。
- `run.bat`：首次运行时在 `%LOCALAPPDATA%\Tingxie\venv` 建虚拟环境并装依赖，然后用 pythonw 启动 app.py。
- `core/pipeline.py`：任务队列和整条流水线（抽音频 → 检测人声 → 切块 → MOSS → Qwen → 断句 → 大纲 → Qwen 复核 → 备用模型 → DeepSeek 校对 → 写字幕），也负责调用 crispasr.exe。
- `core/models.py`：模型清单。每个模型的用途、所需文件、默认路径、CrispASR 参数，以及每种语言用哪些备用模型。加新模型从这里开始。
- `core/chunks.py`：把音频切成约 30 秒、长度均匀的块，前后重叠，每块只"拥有"切点之后的部分。
- `core/sentences.py`：按字级时间戳把各块的结果拼成全文，再按标点和停顿全局断句；把其他模型的文字按句对齐挂上去。
- `core/speakers.py`：把 MOSS 的说话人编号按文字对齐到 Qwen 的每个字上。
- `core/deepseek.py`：大纲和术语提取、分批并发校对、对 DeepSeek 输出的结构校验和内容校验。
- `core/progress.py`：按历史运行时间估算进度和剩余时间。
- `core/subtitles.py`：把句子排成字幕条并写出各种格式。
- `core/store.py`：配置、加密密钥和任务文件，全部在 `%LOCALAPPDATA%\Tingxie`（可用环境变量 `TINGXIE_HOME` 改位置）。
- `ui/`：前端页面（上传、进度、审阅和逐句对照各模型结果、设置）。
- `tests/`：单元测试和各种手动检查脚本，见下面"常用命令"。
- `参考稿_*.txt`：测试视频的人工校对稿，用来算字错率。内容是私人录音，只放在本机，不进仓库（已写进 .gitignore）。

## 常用命令
命令里的 `PY` 指 `%LOCALAPPDATA%\Tingxie\venv\Scripts\python.exe`（run.bat 建的虚拟环境）。
- 开发运行：双击 `run.bat`，或 `PY app.py`。需要图形界面和本机的模型文件。
- 构建：无，直接运行源码。
- 语法检查：`PY -m compileall -q app.py core tests`
- 单元测试（不需要模型和网络）：`PY tests/speakers_unit.py`、`PY tests/deepseek_unit.py`、`PY tests/backups_unit.py`
- 字错率：`PY tests/cer.py 参考稿_VID_1001.txt 结果.srt [job.json ...] [-v]`
- 需要本机模型的检查（不进 CI）：
  - `PY tests/e2e.py 音频 zh 1`：真模型加本地假 DeepSeek 跑完整流程（`DIARIZE=1` 开启说话人区分，`BACKUPS='{"zh": ["mimo", "whisper"]}'` 指定备用模型）；
  - 这些脚本用临时数据目录，通过 `tests/local_models.py` 沿用本机设置里的模型路径；
  - `PY tests/speaker_run.py`、`tests/pause_check.py`、`tests/window_check.py`、`tests/ui_server.py`。
- 会把数据发给 DeepSeek 的脚本（每次先问用户）：`tests/real_deepseek.py`、`tests/proof_only.py`。
- lint：目前没有配置。

## 架构要点
- **切块**：Qwen 常漏掉块开头的话，所以每块从切点前 3 秒开始，但只保留切点之后的字（按字级时间戳判断归属），全文拼好以后再统一断句。切点放在目标长度附近最安静的 300 毫秒。
- **多份结果**：开启精度优化时，每句有好几份文字：`qwen`（主结果）、`qwen2`（换了切点再识别一遍，带术语提示）、`moss`，以及每个启用的备用模型各一个字段（`mimo` / `whisper` / `parakeet`）。DeepSeek 按多数一致来纠错。审阅页会把这几份结果都列出来给用户对照。
- **DeepSeek 的输出不直接信任**：结构不合法的条目（编号不相邻、跨说话人合并、删掉有内容的句子）会打回重写一次，还不行就丢掉；内容和各份识别结果对不上的条目不丢，而是标成存疑并写明原因，交给用户选。
- **Qwen 的上下文提示**要写成一句话。写成词表时，Qwen 会把它原样读出来；近乎静音的块还会把提示读出来，所以有 `_echoes` 过滤。
- **任务数据**：每个任务一个目录 `%LOCALAPPDATA%\Tingxie\jobs\<id>\`，里面有 job.json、音频和各模型的原始输出。

## 已知问题
- 模型和 CrispASR 默认放在 `%LOCALAPPDATA%\Tingxie\models` 和 `runtime`，但还没有下载器，要用户自己放文件或在设置里改路径；见 GitHub Issue #1。
- 备用模型的文字按句切分靠文字对齐，没有标点的模型（Whisper）在句子边界偶尔会错位。
- 只支持 Windows（DPAPI、WebView2、crispasr.exe）。
- 重叠说话、小声说话识别不出来，是目前字错率的主要来源。
- 只有一段视频有参考稿，评测样本太少，相差一两个百分点不能说明问题。
- `core/pipeline.py` 已有 500 多行，以后继续加功能要考虑拆分。

## 项目约定
- 代码注释和文档字符串用英文；界面文字和给用户看的提示用中文。
- 把识别结果发给 DeepSeek 前要先问用户；下载模型前要说明名称、来源和大小并征得同意。
- 测试输出不要写到原视频旁边，用临时目录或 `TINGXIE_HOME` 指向的目录。
- API 密钥只能经 DPAPI 加密保存，不能打印，也不能写进日志。
