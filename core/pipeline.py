"""The transcription queue: audio extraction, segmentation, ASR, proofreading, export."""
import array
import ctypes
import difflib
import json
import re
import subprocess
import threading
import time
import wave
from pathlib import Path

from . import chunks, deepseek, models, progress, sentences, speakers, store, subtitles

NO_WINDOW = 0x08000000
PUNCT = re.compile(r"[\s\W_]+", re.UNICODE)
SLICES_DONE = re.compile(r"\((\d+)/\d+ slices\)")
OUT_TIME = re.compile(r"out_time_(?:us|ms)=(\d+)")
LOAD_SHARE = 8  # part of a model's bar reserved for loading it
ON_GPU = re.compile(r"preferred GPU backend|backend=(CUDA|Vulkan)|on gpu")
NAMES = {
    "extract": "提取音频", "vad": "语音检测", "qwen": "Qwen3-ASR", "qwen2": "Qwen3-ASR 复核", "mimo": "MiMo-V2.5-ASR",
    "parakeet": "parakeet-ctc-1.1b-ja", "whisper": "Whisper large-v3", "deepseek": "DeepSeek 校对", "moss": "MOSS 说话人",
}


class Cancelled(Exception):
    pass


def _norm(t):
    return PUNCT.sub("", t)


def _hint(terms):
    """The context Qwen gets. A bare list of words is sometimes read back as the transcript; a sentence is not."""
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

    def _clock(self):
        """Seconds of working time: stands still while paused."""
        now = time.time()
        return now - self.paused_for - ((now - self.paused_at) if self.paused_at else 0)

    # ---------- control ----------
    def start(self, files, lang, opt, formats, diarize=False):
        if self.state["running"]:
            raise RuntimeError("已有任务正在运行")
        self.cancel = False
        self.paused = False
        self.paused_for, self.paused_at = 0.0, None
        self.state = {
            "running": True, "error": None, "finished": None, "current": None,
            "queue": [Path(f).name for f in files], "index": 0, "total": len(files), "done": [], "queue_pct": 0,
        }
        self.thread = threading.Thread(target=self._run, args=(files, lang, opt, formats, diarize), daemon=True)
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
    def _run(self, files, lang, opt, formats, diarize):
        ticker = threading.Thread(target=self._ticker, daemon=True)
        ticker.start()
        try:
            cfg = store.load_config()
            self.durations = [_duration(cfg, f) or 1.0 for f in files]
            for i, f in enumerate(files):
                self.state["index"] = i
                job = self._one(f, lang, opt, formats, self.durations[i], diarize)
                self.state["done"].append(job["id"])
            self.state["queue_pct"] = 100
            self.state["finished"] = self.state["done"][-1] if self.state["done"] else None
        except Cancelled:
            self.state["error"] = None
        except Exception as e:  # shown on the progress page
            self.state["error"] = str(e)
        finally:
            self.proc = None
            self.state["running"] = False

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
    def _one(self, source, lang, opt, formats, duration, diarize):
        cfg = store.load_config()
        jid, d = store.new_job_dir()
        src = Path(source)
        job = {
            "id": jid, "source": str(src), "name": src.name, "created": time.time(),
            "lang": lang, "opt": opt, "duration": 0, "segments": [], "warnings": [], "speakers": {}, "speaker_tags": True,
        }
        self.state["current"] = {"name": src.name, "lang": lang, "opt": opt, "diarize": diarize, "pct": 0, "elapsed": 0, "remaining": None, "duration": duration, "lanes": [], "stream": [], "stream_name": ""}
        keys = ["extract", "vad"] + (["moss"] if diarize else []) + ["qwen"]
        # Which backup models hear which language's sentences, as the user picked them.
        serves = {}
        for seg_lang in [x for x in ("zh", "ja") if lang in (x, "mix")]:
            for name in models.backups_for(cfg, seg_lang):
                serves.setdefault(name, []).append(seg_lang)
        if opt:  # 顺序：备用模型 → DeepSeek 大纲 → Qwen 复核（带大纲里的术语）→ DeepSeek 校对
            keys += [name for name in models.BACKUPS if name in serves]
            keys.append("qwen2")
            keys.append("deepseek")
        self.tracker = progress.Tracker(keys, duration, self.state["current"], cfg["speed"], self._clock)
        self.extras = []  # Qwen 没识别出、其他模型听到的段落，校对完插成存疑字幕
        for lane in self.state["current"]["lanes"]:
            lane["name"] = NAMES[lane["key"]]

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
        if not any(words.values()):
            job["warnings"].append("没有拿到逐字时间，字幕时间是按字数估算的")
        else:
            lost = [slices[i][:2] for i in sorted(words) if sentences._collapsed(words[i])]
            if lost:
                where = "、".join(f"{int(x // 60)}:{int(x % 60):02d}–{int(y // 60)}:{int(y % 60):02d}" for x, y in lost)
                job["warnings"].append(f"{where} 有一段对不上逐字时间（常见于 Qwen 漏听了一段），那里的字幕时间是按字数估算的，请检查")
        job["segments"] = segs
        if turns:
            self._attach_moss(slices, turns[0], segs)
        store.save_job(job)

        if opt:
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
            # 大纲参考全部备用结果，找出的专有名词作为提示交给 Qwen 复核
            terms = self._outline(cfg, job)
            self._second_pass(cfg, d, asr_lang, segs, terms)
        store.save_job(job)
        if opt:
            self._proofread(cfg, job)
        job["segments"] = sentences.place_extras(job["segments"], self.extras)

        job["segments"] = [s for s in job["segments"] if s["text"]]
        job["speakers"] = speakers.number(job["segments"])
        for s in job["segments"]:
            s.pop("times", None)
            s.pop("spoken_end", None)
        job["written"] = subtitles.export(job, formats)
        store.save_job(job)
        store.save_config({"speed": self.tracker.rates})
        return job

    def _extract(self, cfg, src, d, job):
        t = self.tracker
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
        store.save_job(job)
        self.state["current"]["duration"] = job["duration"]
        t.finish("extract")

    def _base(self, cfg):
        runtime = Path(cfg["runtime_" + cfg["gpu_backend"]])
        return [str(runtime / "crispasr.exe"), "--gpu-backend", cfg["gpu_backend"]]

    def _model_args(self, cfg, key):
        return models.args(cfg, "qwen" if key == "qwen2" else key)

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
            raise RuntimeError(f"{what}（{p.returncode}）：" + " / ".join(x for x in tail[-3:] if x)[:400])

    def _segment(self, cfg, d):
        t = self.tracker
        t.set("vad", 1)
        t.creep("vad", 97, t.predicted("vad"))  # CrispASR reports nothing during detection, so this bar is estimated
        key = "parakeet" if Path(cfg["parakeet_model"]).exists() else "qwen"
        cmd = self._base(cfg) + self._model_args(cfg, key) + [
            "-f", "audio.wav", "--vad", "-vm", "webrtc", "-vsd", "300", "-vmsd", "28", "--strict-pipeline", "--vad-export", "seg.json",
        ]
        self._spawn(cmd, d)
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

    def _transcribe(self, cfg, key, d, raw, args):
        """Runs one model over these stretches of audio, moving its bar by seconds of audio; returns its transcription."""
        doc = json.loads(json.dumps(self.seg_doc))
        doc["crispasr_vad"]["slices"] = raw
        doc["crispasr_vad"]["num_slices"] = len(raw)
        seg_file = d / f"seg_{key}.json"
        seg_file.write_text(json.dumps(doc), encoding="utf-8")
        t = self.tracker
        t.set(key, 1, "加载中")
        t.creep(key, LOAD_SHARE, progress.STARTUP[key], "加载中")
        on_gpu = [False]
        # The bar follows seconds of audio, so a long chunk moves it further than a short one.
        lengths = [(s["t1_cs"] - s["t0_cs"]) / 100 for s in raw]
        total = sum(lengths) or 1
        rate = t.rates[key]

        def at(n):
            return LOAD_SHARE + (99 - LOAD_SHARE) * sum(lengths[:n]) / total

        def line(s):
            on_gpu[0] = on_gpu[0] or bool(ON_GPU.search(s))
            m = SLICES_DONE.search(s)
            if m:
                n = int(m.group(1))
                t.set(key, at(n))
                if n < len(lengths):
                    t.creep(key, at(n + 1), lengths[n] * rate)

        cmd = self._base(cfg) + self._model_args(cfg, key) + ["-f", "audio.wav", "--vad-import", seg_file.name, "-pp", "-ojf", "-of", f"out_{key}"] + args
        self._spawn(cmd, d, line)
        if not on_gpu[0]:
            self.state["current"].setdefault("warnings", []).append(f"{NAMES[key]} 没有用上 GPU")
        return json.loads((d / f"out_{key}.json").read_text(encoding="utf-8"))["transcription"]

    def _moss(self, cfg, d, slices, lang, job):
        """One MOSS run over short and long windows; returns their turns, or None so a failure only costs the labels."""
        detail = speakers.windows(slices, speakers.DETAIL)
        wide = speakers.windows(slices, speakers.WIDE, speakers.OVERLAP)
        raw = [speakers.slice_of(slices, g) for g in detail + wide]
        self.tracker.set_audio("moss", sum(s["t1_cs"] - s["t0_cs"] for s in raw) / 100)
        try:
            by_window = speakers.turns_by_window(self._transcribe(cfg, "moss", d, raw, ["-l", lang]))
        except (RuntimeError, ValueError, KeyError, OSError) as e:
            job["warnings"].append("说话人区分失败，字幕不带说话人：" + str(e)[:200])
            self.tracker.finish("moss", "失败", learn=False)
            return None
        self.tracker.finish("moss")
        n = len(detail)
        return [(g, by_window[k]) for k, g in enumerate(detail)], [(g, by_window[n + k]) for k, g in enumerate(wide)]

    def _asr(self, cfg, key, d, slices, lang, only=None, context=""):
        """Runs one model over these chunks; returns text and timed characters, keyed by chunk index."""
        idx = list(range(len(slices))) if only is None else only
        args = ["-l", lang]
        if key in ("qwen", "qwen2") and Path(cfg["aligner_model"]).exists():
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
            texts[best] = texts.get(best, "") + r["text"]
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
            if all(sentences.is_filler(s["qwen"]) or len(_norm(s["qwen"])) <= 2 for s in group):
                return []
            new = None
        else:
            new = sentences.apply_edit(group, text)
        if new is None:
            for s in group:
                s["status"] = "ask"
                s["why"] = "DeepSeek 返回的修改异常，暂用 Qwen 的识别。"
            return group
        for s in new:
            agree = sentences.backups_agree(s)
            s["why"] = item.get("reason", "") or ""
            if item.get("uncertain"):
                s["status"] = "ask"
            elif _norm(s["text"]) != _norm(s["qwen"]):
                s["status"] = "fix"
            else:
                s["status"] = "ok" if agree else "keep"
        return new

    def _key(self):
        key = store.get_key()
        if not key:
            raise RuntimeError("没有保存 DeepSeek 密钥，请先在设置里填写")
        return key

    def _outline(self, cfg, job):
        """DeepSeek 写全文大纲，存进任务；返回找到的专有名词，作为 Qwen 复核的提示。"""
        segs = [s for s in job["segments"] if s["qwen"]]
        t = self.tracker
        key = self._key()
        t.set("deepseek", 2, "生成大纲")
        t.creep("deepseek", 40, t.predicted("deepseek") * 0.4, "生成大纲")
        try:
            job["outline"], terms = deepseek.outline(cfg, key, segs)
        except Exception as e:  # 字幕照样能用，审阅页当作草稿显示
            self._check()
            job["outline"] = None
            job["warnings"].append("DeepSeek 生成大纲失败，没有做校对，已保留 Qwen 的识别结果：" + str(e)[:200])
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
        context = _hint(list(dict.fromkeys(deepseek.glossary(cfg) + terms)))
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
            elif sentences.worth_showing(text):  # 整块都没有 Qwen 的句子
                self.extras.append({"a": t0, "b": t1, "lang": "ja" if sentences.KANA.search(text) else "zh", "field": "moss", "text": text.strip()})

    def _keep_extras(self, segs, found, field):
        """记下对不上 Qwen 任何句子的文字（Qwen 没识别出的部分），给它们估好时间。"""
        for before, after, text in found:
            spot = sentences.extra_spot(segs, before, after, text)
            if spot:
                self.extras.append({"a": spot[0], "b": spot[1], "lang": spot[2], "field": field, "text": text})

    def _proofread(self, cfg, job):
        if job.get("outline") is None:
            return
        segs = [s for s in job["segments"] if s["qwen"]]
        t = self.tracker
        key = self._key()
        cur = self.state["current"]
        cur["stream"], cur["stream_name"] = [], "DeepSeek 校对"
        t.set("deepseek", 40, "全文校对")
        t.creep("deepseek", 98, t.predicted("deepseek") * 0.6, "全文校对")
        try:
            items, dropped = deepseek.proofread(cfg, key, segs, job["outline"])
        except Exception as e:  # subtitles stay usable; the review page shows them as drafts
            self._check()
            job["warnings"].append("DeepSeek 校对失败，已保留 Qwen 的识别结果：" + str(e)[:200])
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
                agree = sentences.backups_agree(s)
                s["status"] = "ok" if agree else "keep"
                s["why"] = "" if agree else "几份结果不同，DeepSeek 保留了 Qwen 的写法。"
                final.append(s)
        job["segments"] = sentences.finalize(final)
        if ignored + dropped:
            job["warnings"].append(f"DeepSeek 有 {ignored + dropped} 处修改的编号不合理，已忽略")
        t.finish("deepseek")
