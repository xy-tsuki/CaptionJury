"""The transcription queue: audio extraction, segmentation, ASR, proofreading, export."""
import array
import ctypes
import difflib
import hashlib
import os
import json
import queue
import re
import subprocess
import threading
import time
import uuid
import wave
from pathlib import Path

from . import chunks, deepseek, lines, models, progress, runtime, sentences, speakers, store, subtitles

NO_WINDOW = 0x08000000
PUNCT = re.compile(r"[\s\W_]+", re.UNICODE)
SLICES_DONE = re.compile(r"\((\d+)/\d+ slices\)")
OUT_TIME = re.compile(r"out_time_(?:us|ms)=(\d+)")
LOAD_SHARE = 8  # part of a model's bar reserved for loading it
HINT_MAX = 40  # 交给 Qwen 的提示最多带几个词
# 各模型在日志里说明用上了显卡的写法；Whisper 写的是 "using CUDA0 backend"
ON_GPU = re.compile(r"preferred GPU backend|backend=(CUDA|Vulkan)|on gpu|using (CUDA|Vulkan)\d* backend")
NAMES = {
    "extract": "提取音频", "vad": "语音检测", "qwen": "Qwen3-ASR", "qwen2": "Qwen3-ASR 复核", "mimo": "MiMo-V2.5-ASR",
    "whisper": "Whisper large-v3", "deepseek": "AI 校对", "moss": "MOSS 说话人",
}


class Cancelled(Exception):
    pass


def _fingerprint(*parts):
    """断点续跑用的指纹：输入一样（命令、切块、模型文件大小……）才能沿用上次的结果。"""
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:24]


def _reusable(d, out, key):
    """out 是上次跑完的、并且输入和这次一样。key 文件只在这一步成功后才写，跑到一半的不会被沿用。"""
    k = d / (out + ".key")
    return (d / out).is_file() and k.is_file() and k.read_text(encoding="utf-8") == key


def _mark(d, out, key):
    (d / (out + ".key")).write_text(key, encoding="utf-8")


def _unmark(d, out):
    (d / (out + ".key")).unlink(missing_ok=True)


def _file_sizes(cmd):
    """命令里引用的模型文件的大小：同一路径换了文件也能看出来。"""
    return [os.path.getsize(a) for a in cmd if isinstance(a, str) and a.lower().endswith((".gguf", ".bin")) and os.path.isfile(a)]


class ProcFailed(RuntimeError):
    """子进程非零退出；带上退出码和 stderr 最后几十行，用来判断是不是运行库或显卡的问题。"""

    def __init__(self, msg, code, tail):
        super().__init__(msg)
        self.code, self.tail = code, tail


class Skipped(Exception):
    """用户在 Agent 那一步点了跳过。"""


class _Agent:
    """订阅制 Agent：把提示词交给用户复制过去，等用户把回复粘贴回来。和 deepseek.Chat 用法一样。

    全文一次给完（不分批），结构不对的条目不再追问，直接丢掉，免得用户来回复制好几次。
    """
    batch, attempts, name = None, 1, "Agent"

    def __init__(self, owner, step):
        self.owner, self.step = owner, step

    def ask(self, system, payload, history=()):
        return self.owner._wait_agent(self.step, deepseek.prompt_text(system, payload))


def _norm(t):
    return PUNCT.sub("", t)


MOSS_TOKENS_PER_SEC = 16  # MOSS 每秒音频最多输出这么多 token（含说话人标记），正常说话远用不到


def _moss_tokens(raw):
    """MOSS 这次运行的输出上限：按最长的窗口算，至少 512，最多 4096。"""
    longest = max((s["t1_cs"] - s["t0_cs"]) / 100 for s in raw)
    return int(min(4096, max(512, longest * MOSS_TOKENS_PER_SEC)))


def _hint(terms):
    """交给 Qwen 的上下文提示。写成一句话：光列词表时 Qwen 有时会把它当成识别结果读出来。

    最多 HINT_MAX 个词：词库可能有几百个，全塞给 Qwen 并没有让它写对（2026-11-12 试过近 300 个），
    反而会把读音相近的话带偏；完整的词库照样交给 AI 大纲和校对。
    """
    terms = list(dict.fromkeys(terms))[:HINT_MAX]
    return f"这段录音里可能出现这些词：{'、'.join(terms)}。" if terms else ""


def _echoes(text, context):
    """True when a chunk's text is mostly the hint itself."""
    t, c = _norm(text), _norm(context)
    if not t:
        return False
    run = difflib.SequenceMatcher(None, t, c, autojunk=False).find_longest_match(0, len(t), 0, len(c)).size
    return run >= 6 and run >= len(t) * 0.5


def _group_slices(groups, total):
    """The audio of each sentence group, padded a little but never into the next group."""
    out = []
    for n, g in enumerate(groups):
        a = max(out[-1][1] if out else 0.0, g[0]["a"] - 0.3)
        nxt = groups[n + 1][0]["a"] if n + 1 < len(groups) else total
        b = min(nxt, g[-1]["spoken_end"] + 0.3, total)
        out.append((a, b, {"start": int(a * 16000), "end": int(b * 16000), "t0_cs": int(a * 100), "t1_cs": int(b * 100)}))
    return out


def _duration(cfg, path):
    ffprobe = str(Path(cfg["ffmpeg"]).with_name("ffprobe"))
    r = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], capture_output=True, creationflags=NO_WINDOW)
    try:
        return float(r.stdout.decode().strip())
    except ValueError:
        return 0.0


def _suspend(proc, on):
    ntdll = ctypes.WinDLL("ntdll")
    handle = int(proc._handle)
    (ntdll.NtSuspendProcess if on else ntdll.NtResumeProcess)(ctypes.c_void_p(handle))


class Queue:
    def __init__(self):
        self.lock = threading.Lock()
        self.thread = None
        self.proc = None
        self.paused = False
        self.cancel = False
        self.state = {"running": False, "error": None, "finished": None, "current": None, "queue": []}
        self.tracker = None
        self.paused_for = 0.0
        self.paused_at = None
        self.agent_prompt = None  # 正在等用户粘贴时，交给 Agent 的提示词
        self.agent_box = queue.Queue()  # 用户粘贴的回复（已解析的 JSON 和原文），None 表示跳过

    def _clock(self):
        """Seconds of working time: stands still while paused."""
        now = time.time()
        return now - self.paused_for - ((now - self.paused_at) if self.paused_at else 0)

    # ---------- control ----------
    def start(self, files, lang, opt, formats, diarize=False, compare=False, terms=(), resume=None, light=False):
        """开始转写一串文件。resume 是没跑完的任务编号：第一个文件沿用那个任务的目录，跑过的步骤不重跑。"""
        if self.state["running"]:
            raise RuntimeError("已有任务正在运行")
        self.cancel = False
        self.paused = False
        self.paused_for, self.paused_at = 0.0, None
        self.state = {
            "running": True, "error": None, "finished": None, "current": None,
            "queue": [Path(f).name for f in files], "index": 0, "total": len(files), "done": [], "queue_pct": 0,
        }
        self.resume_id = resume
        self.light = light  # 轻量模式：主模型换成 Qwen3-ASR 0.6B，不做对照、AI 校对和说话人区分
        self.thread = threading.Thread(target=self._run, args=(files, lang, opt, formats, diarize, compare, list(terms)), daemon=True)
        self.thread.start()

    def set_paused(self, paused):
        if paused and not self.paused_at:
            self.paused_at = time.time()
        elif not paused and self.paused_at:
            self.paused_for += time.time() - self.paused_at
            self.paused_at = None
        self.paused = paused
        p = self.proc
        if p and p.poll() is None:
            _suspend(p, paused)

    def stop(self):
        self.cancel = True
        self.paused = False
        self.agent_box.put(None)
        p = self.proc
        if p and p.poll() is None:
            _suspend(p, False)
            p.kill()

    def _check(self):
        while self.paused and not self.cancel:
            time.sleep(0.2)
        if self.cancel:
            raise Cancelled()

    # ---------- queue ----------
    def _run(self, files, lang, opt, formats, diarize, compare, terms=()):
        ticker = threading.Thread(target=self._ticker, daemon=True)
        ticker.start()
        try:
            cfg = store.load_config()
            self.durations = [_duration(cfg, f) or 1.0 for f in files]
            for i, f in enumerate(files):
                self.state["index"] = i
                job = self._one(f, lang, opt, formats, self.durations[i], diarize, compare, terms,
                                rest=list(files[i + 1:]), resume=self.resume_id if i == 0 else None, light=self.light)
                self.state["done"].append(job["id"])
            self.state["queue_pct"] = 100
            self.state["finished"] = self.state["done"][-1] if self.state["done"] else None
        except Cancelled:
            self.state["error"] = None
            self._interrupted("stopped", "")
        except Exception as e:  # shown on the progress page
            self.state["error"] = str(e)
            self._interrupted("failed", str(e))
        finally:
            self.proc = None
            self.state["running"] = False

    def _interrupted(self, state, error):
        """任务停下或出错：记进任务文件，历史页可以从这里接着跑。"""
        job = getattr(self, "job", None)
        if not job or job.get("state") != "running":
            return
        try:
            saved = store.load_job(job["id"])  # 存盘的版本，内存里的可能改到一半
        except (OSError, ValueError):
            return
        saved.update(state=state, error=error[:300])
        store.save_job(saved)

    def _ticker(self):
        while self.state["running"]:
            t = self.tracker
            if t and not self.paused:
                t.tick()
                i = self.state["index"]
                done = sum(self.durations[:i]) + self.durations[i] * t.cur["pct"] / 100
                self.state["queue_pct"] = done / sum(self.durations) * 100
            time.sleep(0.25)

    # ---------- one file ----------
    def _one(self, source, lang, opt, formats, duration, diarize, compare=False, terms=(), rest=(), resume=None, light=False):
        cfg = store.load_config()
        if light:
            opt = compare = diarize = False
        self.main = "qwen06" if light else "qwen"
        # 只要纯文本时不用逐字时间；轻量模式下省掉对齐模型（字幕格式要时间轴，照常对齐）
        self.align = not (light and set(formats) <= {"TXT"})
        # 新建页的词库只对这次转写生效：并进设置里的术语表，Qwen 提示和校对都用它
        cfg["glossary"] = "\n".join(dict.fromkeys(deepseek.glossary(cfg) + list(terms)))
        if resume:  # 接着跑：沿用原来的目录、编号和创建时间，目录里已有的结果按指纹沿用
            jid, d, created = resume, store.JOBS / resume, store.load_job(resume)["created"]
        else:
            (jid, d), created = store.new_job_dir(), time.time()
        src = Path(source)
        job = {
            "id": jid, "source": str(src), "name": src.name, "created": created, "state": "running",
            # 接着跑时照原样再来一遍要用的参数；rest 是排在它后面、还没开始的文件
            "params": {"lang": lang, "opt": opt, "formats": list(formats), "diarize": diarize, "compare": compare,
                       "terms": list(terms), "rest": list(rest), "light": light},
            "light": light,
            "lang": lang, "opt": opt, "compare": compare, "terms": list(terms), "duration": 0, "segments": [], "warnings": [], "speakers": {}, "speaker_tags": False, "no_punct": False,
            "line_max": cfg.get("line_max", "wide"),
        }
        self.state["current"] = {"id": jid, "name": src.name, "lang": lang, "opt": opt, "compare": compare, "diarize": diarize, "pct": 0, "elapsed": 0, "remaining": None, "duration": duration, "lanes": [], "stream": [], "stream_name": ""}
        keys = ["extract", "vad"] + (["moss"] if diarize else []) + ["qwen"]
        # Which backup models hear which language's sentences, as the user picked them.
        serves = {}
        for seg_lang in [x for x in ("zh", "ja") if lang in (x, "mix")]:
            for name in models.backups_for(cfg, seg_lang):
                serves.setdefault(name, []).append(seg_lang)
        # 顺序：备用模型（多模型对照）→ AI 大纲 → Qwen 复核（带大纲里的术语）→ AI 校对（AI 精度优化）
        if compare:
            keys += [name for name in models.BACKUPS if name in serves]
        if opt:
            keys.append("qwen2")
            keys.append("deepseek")
        self.job = job
        store.save_job(job)  # 先存一份：程序中途被关掉，历史页也能看到它、接着跑
        self._pick_runtime(cfg, job)
        rates = cfg.get("speed_rt", {}).get(self.rt["key"] + ("_light" if light else "")) or (cfg["speed"] if self.rt["gpu"] and not light else {})
        self.tracker = progress.Tracker(keys, duration, self.state["current"], rates, self._clock)
        self.extras = []  # Qwen 没识别出、其他模型听到的段落；开了 AI 校对时校对前插进去交给 AI，否则最后插成存疑字幕
        for lane in self.state["current"]["lanes"]:
            lane["name"] = NAMES[lane["key"]]
            if light and lane["key"] == "qwen":
                lane["name"] = "Qwen3-ASR 0.6B"

        self._extract(cfg, src, d, job)
        slices = self._segment(cfg, d)
        speech = sum(b - a for a, b, _ in slices)
        self.tracker.set_audio("qwen", speech)
        self.tracker.set_audio("deepseek", speech)

        # "auto" made Qwen translate some Japanese into English; "zh" keeps both languages as spoken.
        asr_lang = {"zh": "zh", "ja": "ja", "mix": "zh"}[lang]
        turns = self._moss(cfg, d, slices, asr_lang, job) if diarize else None
        qwen, words = self._asr(cfg, "qwen", d, slices, asr_lang, context=_hint(deepseek.glossary(cfg)))
        who = speakers.resolve(qwen, *turns) if turns else None
        segs = sentences.build(sentences.stitch(slices, qwen, words, who), lang)
        if not any(words.values()) and self.align:  # 轻量模式只要 TXT 时故意不对齐，不用提醒
            job["warnings"].append("没有拿到逐字时间，字幕时间是按字数估算的")
        else:
            lost = [slices[i][:2] for i in sorted(words) if sentences.lost_alignment(words[i])]
            if lost:
                where = "、".join(f"{int(x // 60)}:{int(x % 60):02d}–{int(y // 60)}:{int(y % 60):02d}" for x, y in lost)
                job["warnings"].append(f"{where} 有一段对不上逐字时间（常见于 Qwen 漏听了一段），那里的字幕时间是按字数估算的，请检查")
        job["segments"] = segs
        if turns:
            self._attach_moss(slices, turns[0], segs)
        store.save_job(job)

        if compare:
            # 每个备用模型按整句分组，只听用户为它勾选的语言的句子
            plans = {}
            for name, langs in serves.items():
                gs = sentences.groups(segs, langs[0] if len(langs) == 1 else None)
                gs = [g for g in gs if any(s["lang"] in langs for s in g)]
                plans[name] = (gs, _group_slices(gs, job["duration"]), langs)
                self.tracker.set_audio(name, sum(b - a for a, b, _ in plans[name][1]))
            for name, (gs, gslices, langs) in plans.items():
                if not gs:
                    self.tracker.finish(name, "无此语言", learn=False)
                    continue
                res, _ = self._asr(cfg, name, d, gslices, langs[0] if len(langs) == 1 else "auto")
                for i, g in enumerate(gs):
                    found = sentences.attach_backup(g, "".join(s["qwen"] for s in g), res.get(i, "").strip(), langs, name)
                    self._keep_extras(segs, found, name)
            store.save_job(job)
        if opt:
            # 大纲参考全部备用结果，找出的专有名词作为提示交给 Qwen 复核
            terms = self._outline(cfg, job)
            self._second_pass(cfg, d, asr_lang, segs, terms)
        store.save_job(job)
        if opt and job.get("outline") is not None:
            # 时间和 Qwen 对不齐、重复了前后句的补插段，并进对应的句子里，校对时作为它的另一份写法
            self.extras = sentences.absorb_repeats(job["segments"], self.extras)
            # 剩下的补插段在校对前插进去，由 AI 决定并进前后句、保留还是删掉
            job["segments"] = sentences.place_extras(job["segments"], self.extras)
            self.extras = []
        if opt:
            self._proofread(cfg, job)
        job["segments"] = sentences.place_extras(job["segments"], self.extras)

        job["segments"] = [s for s in job["segments"] if s["text"]]
        for n, s in enumerate(job["segments"], 1):  # 去掉空句后重新编号，列表里的序号不跳
            s["id"] = n
        job["speakers"] = speakers.number(job["segments"])
        for s in job["segments"]:
            # 只留每个字的开始时间（和 qwen 逐字对应），导出时按它拆长句；用户改了文字也能对回去
            s["ct"] = [round(t[0], 2) if t else None for t in s.pop("times", None) or []]
            s.pop("spoken_end", None)
        lines.ensure(job)  # 校对页按导出的每条字数拆好
        store.save_job(job)  # 先存一份，导出出错也不丢结果
        job["written"] = subtitles.export(job, formats)
        moved = [p for p in job["written"] if Path(p).parent != Path(job["source"]).parent]
        if moved:
            job["warnings"].append(f"源文件所在的文件夹没有写入权限，字幕存到了 {Path(moved[0]).parent}")
        job["state"] = "done"
        store.save_job(job)
        if self.rt["key"] == self.rt_first:  # 中途换过运行库时速度混在一起，不记；轻量模式单独记
            store.save_config({"speed_rt": cfg.get("speed_rt", {}) | {self.rt["key"] + ("_light" if light else ""): self.tracker.rates}})
        return job

    def _extract(self, cfg, src, d, job):
        t = self.tracker
        st = src.stat()
        key = _fingerprint("extract", str(src), st.st_size, st.st_mtime)
        if _reusable(d, "audio.wav", key) and (d / "peaks.json").is_file():
            t.reused.add("extract")
            with wave.open(str(d / "audio.wav")) as w:
                job["duration"] = w.getnframes() / 16000
            store.save_job(job)
            self.state["current"]["duration"] = job["duration"]
            t.finish("extract")
            return
        _unmark(d, "audio.wav")
        t.set("extract", 1)
        total = t.audio["extract"]
        t.creep("extract", 90, t.predicted("extract"))  # covers sources whose length ffprobe cannot read

        def out(line):
            m = OUT_TIME.match(line)
            if m and total > 1:
                t.sim = None
                t.set("extract", min(95, int(m.group(1)) / 1e6 / total * 95))

        self._spawn(
            [cfg["ffmpeg"], "-y", "-loglevel", "error", "-nostats", "-progress", "pipe:1", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(d / "audio.wav")],
            d, on_out=out, what="音频提取失败",
        )
        t.creep("extract", 99.5, 0.2 + total * 0.0005)  # waveform peaks for the review page
        with wave.open(str(d / "audio.wav")) as w:
            n = w.getnframes()
            job["duration"] = n / 16000
            step = 800  # 20 peaks per second
            peaks = []
            while True:
                buf = w.readframes(step * 200)
                if not buf:
                    break
                a = array.array("h", buf)
                for i in range(0, len(a), step):
                    chunk = a[i:i + step]
                    peaks.append(min(100, max(max(chunk), -min(chunk)) * 100 // 16000))
        (d / "peaks.json").write_text(json.dumps(peaks), encoding="utf-8")
        _mark(d, "audio.wav", key)
        store.save_job(job)
        self.state["current"]["duration"] = job["duration"]
        t.finish("extract")

    def _pick_runtime(self, cfg, job, failed=None):
        """选运行库和显卡（runtime.pick），把实际设备写进任务状态和任务文件。"""
        self.rt = runtime.pick(cfg, failed, self.main)
        self.rt_failed = dict(failed or {})
        if failed is None:
            self.rt_first = self.rt["key"]
        job["device"] = self.state["current"]["device"] = runtime.describe(self.rt)
        job["runtime"] = self.rt["key"]

    def _base(self, cfg):
        rt = self.rt
        cmd = [str(runtime.exe(cfg, rt["key"])), "--gpu-backend", rt["backend"]]
        if rt["device"] is not None:
            cmd += ["-dev", str(rt["device"])]
        if not rt["gpu"]:
            cmd += ["-t", str(runtime.threads())]
        return cmd

    def _crispasr(self, cfg, args, cwd, on_err=None):
        """运行一次 crispasr（命令开头的运行库部分由这里加）。

        自动模式下，运行库或显卡出问题（初始化失败、显存不足、程序起不来）时换下一个运行库，从头重跑这一步。
        """
        while True:
            try:
                return self._spawn(self._base(cfg) + args, cwd, on_err)
            except ProcFailed as e:
                why = runtime.failure(e.code, e.tail)
                if not why or cfg.get("gpu_backend", "auto") != "auto":
                    if why:
                        raise RuntimeError(f"{e}。{self.rt['label']} {why}，可以在设置里把运行库改成「自动」") from e
                    raise
                bad = self.rt["key"]
                failed = self.rt_failed | {bad: why}
                try:
                    self._pick_runtime(cfg, self.job, failed)
                except RuntimeError:
                    raise RuntimeError(f"{e}。已经没有可换的运行库（" + "；".join(f"{runtime.label(k)} {v}" for k, v in failed.items()) + "）") from e
                self.state["current"].setdefault("warnings", []).append(f"{runtime.label(bad)} {why}，改用 {self.rt['label']} 重跑")

    def _model_args(self, cfg, key):
        return models.args(cfg, self.main if key in ("qwen", "qwen2") else key)

    def _spawn(self, cmd, cwd, on_err=None, on_out=None, what="语音识别进程退出"):
        self._check()
        p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
        self.proc = p
        tail = []

        def err():
            for raw in iter(p.stderr.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip()
                tail.append(line)
                del tail[:-40]
                if on_err:
                    on_err(line)

        t = threading.Thread(target=err, daemon=True)
        t.start()
        for raw in iter(p.stdout.readline, b""):
            if on_out:
                on_out(raw.decode("utf-8", "replace").strip())
        p.wait()
        t.join()
        self.proc = None
        self._check()
        if p.returncode:
            raise ProcFailed(f"{what}（{p.returncode}）：" + " / ".join(x for x in tail[-3:] if x)[:400], p.returncode, list(tail))

    def _segment(self, cfg, d):
        t = self.tracker
        t.set("vad", 1)
        t.creep("vad", 97, t.predicted("vad"))  # CrispASR reports nothing during detection, so this bar is estimated
        # 人声检测只用 CrispASR 自带的 webrtc，和加载哪个模型无关
        args = self._model_args(cfg, "qwen") + [
            "-f", "audio.wav", "--vad", "-vm", "webrtc", "-vsd", "300", "-vmsd", "28", "--strict-pipeline", "--vad-export", "seg.json",
        ]
        key = _fingerprint(args, (d / "audio.wav.key").read_text(encoding="utf-8"))
        if _reusable(d, "seg.json", key):
            t.reused.add("vad")
        else:
            _unmark(d, "seg.json")
            self._crispasr(cfg, args, d)
            _mark(d, "seg.json", key)
        data = json.loads((d / "seg.json").read_text(encoding="utf-8"))
        v = data["crispasr_vad"]
        self.seg_doc = data
        self.vad_raw = list(v["slices"])
        v["slices"] = chunks.make(d / "audio.wav", self.vad_raw)
        v["num_slices"] = len(v["slices"])
        out = [(s["t0_cs"] / 100, s["t1_cs"] / 100, s) for s in v["slices"]]
        if not out:
            raise RuntimeError("没有检测到语音")
        t.finish("vad")
        return out

    def _transcribe(self, cfg, key, d, raw, args, name=None, before=0.0, overall=None):
        """Runs one model over these stretches of audio, moving its bar by seconds of audio; returns its transcription.

        一个模型分几次跑时：name 区分各次的文件名，before 是前几次已经跑过的音频秒数，overall 是几次合计的秒数，进度条按合计走。
        """
        name = name or key
        doc = json.loads(json.dumps(self.seg_doc))
        doc["crispasr_vad"]["slices"] = raw
        doc["crispasr_vad"]["num_slices"] = len(raw)
        seg_file = d / f"seg_{name}.json"
        seg_file.write_text(json.dumps(doc), encoding="utf-8")
        t = self.tracker
        lengths = [(s["t1_cs"] - s["t0_cs"]) / 100 for s in raw]
        total = overall or sum(lengths) or 1

        def at(n):
            return LOAD_SHARE + (99 - LOAD_SHARE) * (before + sum(lengths[:n])) / total

        if before:
            t.set(key, at(0))
            t.creep(key, at(1), progress.STARTUP[key] + lengths[0] * t.rates[key])
        else:
            t.set(key, 1, "加载中")
            t.creep(key, LOAD_SHARE, progress.STARTUP[key], "加载中")
        on_gpu = [False]
        # 进度条按音频秒数走，长的块走得多
        rate = t.rates[key]

        def line(s):
            on_gpu[0] = on_gpu[0] or bool(ON_GPU.search(s))
            m = SLICES_DONE.search(s)
            if m:
                n = int(m.group(1))
                t.set(key, at(n))
                if n < len(lengths):
                    t.creep(key, at(n + 1), lengths[n] * rate)

        cmd = self._model_args(cfg, key) + ["-f", "audio.wav", "--vad-import", seg_file.name, "-pp", "-ojf", "-of", f"out_{name}"] + args
        out = f"out_{name}.json"
        fp = _fingerprint(cmd, seg_file.read_text(encoding="utf-8"), _file_sizes(cmd))
        if _reusable(d, out, fp):  # 上次已经跑完这一步
            t.reused.add(key)
            t.sim = None
            t.set(key, at(len(lengths)), "沿用上次结果")
            return json.loads((d / out).read_text(encoding="utf-8"))["transcription"]
        _unmark(d, out)
        self._crispasr(cfg, cmd, d, line)
        _mark(d, out, fp)
        if self.rt["gpu"] and not on_gpu[0]:
            self.state["current"].setdefault("warnings", []).append(f"{NAMES[key]} 没有用上 {self.rt['label']}，实际在 CPU 上运行")
        return json.loads((d / f"out_{name}.json").read_text(encoding="utf-8"))["transcription"]

    def _moss(self, cfg, d, slices, lang, job):
        """MOSS 分两次跑短窗口和长窗口，返回各窗口的说话人和文字；失败返回 None，只是字幕不带说话人。"""
        detail = speakers.windows(slices, speakers.DETAIL)
        wide = speakers.windows(slices, speakers.WIDE, speakers.OVERLAP)
        runs = [[speakers.slice_of(slices, g) for g in ws] for ws in (detail, wide)]
        secs = [sum(s["t1_cs"] - s["t0_cs"] for s in r) / 100 for r in runs]
        self.tracker.set_audio("moss", sum(secs))
        try:
            # 短窗口和长窗口分两次跑，各按最长的窗口给输出上限：MOSS 偶尔会卡住重复同一句，
            # 上限给得太宽，一个 30 秒的窗口就要白跑好几分钟（2026-11-11 出现过 5 分钟）
            by_window = {}
            for k, (r, tag) in enumerate(zip(runs, ("moss", "moss_wide"))):
                if not r:
                    continue
                res = self._transcribe(cfg, "moss", d, r, ["-l", lang, "-n", str(_moss_tokens(r))], tag, sum(secs[:k]), sum(secs))
                for w, turns in speakers.turns_by_window(res).items():
                    by_window[w + (len(detail) if k else 0)] = speakers.clean_turns(turns)
        except (RuntimeError, ValueError, KeyError, OSError) as e:
            job["warnings"].append("说话人区分失败，字幕不带说话人：" + str(e)[:200])
            self.tracker.finish("moss", "失败", learn=False)
            return None
        self.tracker.finish("moss")
        n = len(detail)
        return [(g, by_window.get(k, [])) for k, g in enumerate(detail)], [(g, by_window.get(n + k, [])) for k, g in enumerate(wide)]

    def _asr(self, cfg, key, d, slices, lang, only=None, context=""):
        """Runs one model over these chunks; returns text and timed characters, keyed by chunk index."""
        idx = list(range(len(slices))) if only is None else only
        args = ["-l", lang]
        if key in ("qwen", "qwen2") and self.align and Path(cfg["aligner_model"]).exists():
            args += ["-am", cfg["aligner_model"]]
        if key in ("qwen", "qwen2") and context:
            args += ["--context", context]
        res = self._transcribe(cfg, key, d, [slices[i][2] for i in idx], args)
        texts, words = {}, {}
        for r in res:
            if context and _echoes(r["text"], context):
                continue  # on near-silent audio Qwen reads the hint back instead of transcribing
            a, b = r["offsets"]["from"] / 1000, r["offsets"]["to"] / 1000
            best = max(idx, key=lambda i: min(b, slices[i][1]) - max(a, slices[i][0]))
            # Qwen 的文字要和逐字时间一一对上，不能改；其他模型卡住重复的部分只留一遍
            text = r["text"] if key in ("qwen", "qwen2") else sentences.squash_repeats(r["text"])
            texts[best] = texts.get(best, "") + text
            words.setdefault(best, []).extend(r.get("words", []))
        cur = self.state["current"]
        cur["stream_name"] = NAMES[key]
        cur["stream"] = [{"t": slices[i][0], "text": texts[i].strip()} for i in sorted(texts)[-6:]]
        self.tracker.finish(key)
        return texts, words

    def _apply_edit(self, group, item):
        """Returns the sentences that replace group, keeping the originals if the edit cannot be trusted."""
        text = str(item.get("text", "")).strip()
        if not text:
            if all(s.get("extra") or sentences.is_filler(s["qwen"]) or len(_norm(s["qwen"])) <= 2 for s in group):
                return []
            new = None
        else:
            new = sentences.apply_edit(group, text)
        rough = new is None and bool(text)
        if rough:  # 整句改写、和原来的字对不上大半：照用，但标成存疑
            new = sentences.rough_edit(group, text)
        if new is None:
            for s in group:
                s["status"] = "ask"
                s["why"] = "AI 返回的修改异常，暂用 Qwen 的识别。"
            return group
        for s in new:
            agree = sentences.backups_agree(s)
            s["why"] = deepseek.readable(item.get("reason", "") or "")
            if rough:
                s["status"] = "ask"
                s["why"] = "；".join(x for x in (s["why"], sentences.ROUGH_WHY) if x)
            elif s.get("extra"):  # 还是只有其他模型听到的，交给用户确认
                s["status"] = "ask"
                s["why"] = s["why"] or sentences.EXTRA_WHY
            elif item.get("uncertain"):
                s["status"] = "ask"
            elif _norm(s["text"]) != _norm(s["qwen"]):
                s["status"] = "fix"
            else:
                s["status"] = "ok" if agree else "keep"
        return new

    def _chat(self, cfg, step):
        """这一步用的校对方式：Agent（等用户粘贴）或服务商接口。"""
        if cfg.get("proof_mode") == "agent":
            return _Agent(self, step)
        key = store.get_key()
        if not key:
            raise RuntimeError(f"没有保存 {deepseek.label(cfg)} 的密钥，请先在设置里填写")
        return deepseek.Chat(cfg, key)

    def _wait_agent(self, step, prompt):
        """暂停在这一步，等用户把 Agent 的回复粘贴回来（app 里校验过格式）。"""
        while not self.agent_box.empty():  # 丢掉上一步残留的
            self.agent_box.get_nowait()
        self.agent_prompt = prompt
        cur = self.state["current"]
        cur["agent"] = {"k": uuid.uuid4().hex[:8], "step": step}
        self.tracker.sim = None
        self.tracker.set("deepseek", 40 if step == "proof" else 2, "等待粘贴结果")
        try:
            while True:
                self._check()
                try:
                    got = self.agent_box.get(timeout=0.3)
                except queue.Empty:
                    continue
                self._check()
                if got is None:
                    raise Skipped()
                return got
        finally:
            cur.pop("agent", None)
            self.agent_prompt = None

    def agent_reply(self, step, text):
        """app 收到用户粘贴的回复：解析并检查格式，不对就返回原因，对了交给等待中的步骤。"""
        cur = self.state.get("current") or {}
        if not cur.get("agent") or cur["agent"]["step"] != step:
            return "现在没有在等这一步的结果"
        try:
            reply = deepseek.parse_reply(text)
        except ValueError as e:
            return str(e)
        why = deepseek.check_shape(step, reply)
        if why:
            return why
        self.agent_box.put((reply, text))
        return None

    def agent_skip(self):
        self.agent_box.put(None)

    def _outline(self, cfg, job):
        """AI 写全文大纲，存进任务；返回找到的专有名词，作为 Qwen 复核的提示。"""
        segs = [s for s in job["segments"] if s["qwen"]]
        t = self.tracker
        name = deepseek.label(cfg)
        t.set("deepseek", 2, "生成大纲")
        t.creep("deepseek", 40, t.predicted("deepseek") * 0.4, "生成大纲")
        d = store.JOBS / job["id"]
        fp = _fingerprint("outline", segs, Path(job["name"]).stem, deepseek.glossary(cfg))
        try:
            if _reusable(d, "ai_outline.json", fp):  # 上次已经拿到大纲：不再花钱、不用再粘贴
                saved = json.loads((d / "ai_outline.json").read_text(encoding="utf-8"))
                job["outline"], terms = saved["outline"], saved["terms"]
            else:
                _unmark(d, "ai_outline.json")
                chat = self._chat(cfg, "outline")
                if isinstance(chat, _Agent):
                    job["outline"], terms = deepseek.outline(cfg, chat, segs, Path(job["name"]).stem)
                else:
                    with chat:
                        job["outline"], terms = deepseek.outline(cfg, chat, segs, Path(job["name"]).stem)
                (d / "ai_outline.json").write_text(json.dumps({"outline": job["outline"], "terms": terms}, ensure_ascii=False), encoding="utf-8")
                _mark(d, "ai_outline.json", fp)
        except Skipped:  # 跳过大纲：校对照常做，只是没有大纲和术语提示
            job["outline"] = ""
            t.set("deepseek", 40, "等待校对")
            return []
        except Cancelled:
            raise
        except Exception as e:  # 字幕照样能用，校对页当作草稿显示
            self._check()
            job["outline"] = None
            job["warnings"].append(f"{name} 生成大纲失败，没有做校对，已保留 Qwen 的识别结果：" + str(e)[:200])
            t.finish("deepseek", "失败", learn=False)
            return []
        self._check()
        t.sim = None
        t.set("deepseek", 40, "等待校对")
        return terms

    def _second_pass(self, cfg, d, lang, segs, terms):
        """Qwen 把全文再听一遍：切点挪半个块，并带上大纲里的专有名词作为提示。"""
        made = chunks.make(d / "audio.wav", self.vad_raw, chunks.TARGET / 2)
        slices2 = [(c["t0_cs"] / 100, c["t1_cs"] / 100, c) for c in made]
        self.tracker.set_audio("qwen2", sum(b - a for a, b, _ in slices2))
        # 大纲是对着这段的识别稿挑的词，比词库更贴近这一段，放在前面
        context = _hint(terms + deepseek.glossary(cfg))
        texts, words = self._asr(cfg, "qwen2", d, slices2, lang, context=context)
        stream = sentences.stitch(slices2, texts, words)
        gs = sentences.groups(segs)
        for n, g in enumerate(gs):
            t0 = (gs[n - 1][-1]["spoken_end"] + g[0]["a"]) / 2 if n else 0.0
            t1 = (g[-1]["spoken_end"] + gs[n + 1][0]["a"]) / 2 if n + 1 < len(gs) else 1e9
            self._keep_extras(segs, sentences.attach(g, sentences.text_between(stream, t0, t1), "qwen2"), "qwen2")

    def _attach_moss(self, slices, detail, segs):
        """Keeps, per sentence, the words MOSS heard for it, as one more transcript for proofreading."""
        for idx, turns in detail:
            first, last = slices[idx[0]], slices[idx[-1]]
            t0, t1 = first[2].get("own0", first[0]), last[2].get("own1", last[1])
            group = [s for s in segs if t0 <= s["a"] < t1]
            text = "".join(text for _, text in turns)
            if group:
                self._keep_extras(segs, sentences.attach(group, text, "moss"), "moss")
            elif sentences.worth_showing(text):  # 整块都没有 Qwen 的句子：按句拆开，时间按字数分
                for a, b, piece in sentences.spread(text.strip(), t0, t1):
                    self.extras.append({"a": a, "b": b, "lang": "ja" if sentences.KANA.search(piece) else "zh", "field": "moss", "text": piece})

    def _keep_extras(self, segs, found, field):
        """记下对不上 Qwen 任何句子的文字（Qwen 没识别出的部分），给它们估好时间。"""
        for before, after, text in found:
            spot = sentences.extra_spot(segs, before, after, text)
            if spot:
                self.extras.append({"a": spot[0], "b": spot[1], "lang": spot[2], "field": field, "text": text})

    def _proofread(self, cfg, job):
        if job.get("outline") is None:
            return
        segs = [s for s in job["segments"] if s["qwen"] or s.get("extra")]
        t = self.tracker
        name = deepseek.label(cfg)
        cur = self.state["current"]
        cur["stream"], cur["stream_name"] = [], f"{name} 校对"
        t.set("deepseek", 40, "全文校对")
        t.creep("deepseek", 98, t.predicted("deepseek") * 0.6, "全文校对")
        d = store.JOBS / job["id"]
        fp = _fingerprint("proof", segs, job["outline"], Path(job["name"]).stem, deepseek.glossary(cfg))
        try:
            if _reusable(d, "ai_proof.json", fp):
                saved = json.loads((d / "ai_proof.json").read_text(encoding="utf-8"))
                items, dropped = saved["items"], saved["dropped"]
            else:
                _unmark(d, "ai_proof.json")
                chat = self._chat(cfg, "proof")
                if isinstance(chat, _Agent):
                    items, dropped = deepseek.proofread(cfg, chat, segs, job["outline"], Path(job["name"]).stem)
                else:
                    with chat:
                        items, dropped = deepseek.proofread(cfg, chat, segs, job["outline"], Path(job["name"]).stem)
                (d / "ai_proof.json").write_text(json.dumps({"items": items, "dropped": dropped}, ensure_ascii=False), encoding="utf-8")
                _mark(d, "ai_proof.json", fp)
        except Skipped:
            job["warnings"].append("跳过了 AI 校对，保留的是 Qwen 的识别结果")
            t.finish("deepseek", "已跳过", learn=False)
            return
        except Cancelled:
            raise
        except Exception as e:  # 字幕照样能用，校对页当作草稿显示
            self._check()
            job["warnings"].append(f"{name} 校对失败，已保留 Qwen 的识别结果：" + str(e)[:200])
            t.finish("deepseek", "失败", learn=False)
            return
        self._check()
        by_id = {s["id"]: s for s in segs}
        edits, covered, ignored = {}, set(), 0
        for item in items:
            ids = sorted(int(i) for i in (item.get("ids") or []))
            adjacent = bool(ids) and ids == list(range(ids[0], ids[-1] + 1))
            people = {by_id[i].get("speaker") for i in ids if i in by_id} - {"", None}
            if not adjacent or not covered.isdisjoint(ids) or not all(i in by_id for i in ids) or len(people) > 1:
                ignored += 1
                continue
            covered.update(ids)
            edits[ids[0]] = (ids, item)
        final = []
        for s in segs:
            if s["id"] in edits:
                ids, item = edits[s["id"]]
                final += self._apply_edit([by_id[i] for i in ids], item)
            elif s["id"] not in covered:
                if not s.get("extra"):  # AI 没动的补插段保持存疑
                    agree = sentences.backups_agree(s)
                    s["status"] = "ok" if agree else "keep"
                    s["why"] = "" if agree else "几份结果不同，AI 保留了 Qwen 的写法。"
                final.append(s)
        job["segments"] = sentences.finalize(final)
        if ignored + dropped:
            job["warnings"].append(f"{name} 有 {ignored + dropped} 处修改的编号不合理，已忽略")
        t.finish("deepseek", learn=cfg.get("proof_mode") != "agent")  # 等用户粘贴的时间不算进速度
