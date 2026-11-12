"""Progress for one file: time-based weights learned from past runs, plus smooth creep between real updates."""
import math

# Seconds of fixed start-up per step, then seconds per second of audio. Rates are refined after every run.
STARTUP = {"extract": 0.3, "vad": 1.0, "qwen": 6.0, "qwen2": 6.0, "mimo": 12.0, "whisper": 3.0, "deepseek": 1.0, "moss": 5.0}
RATES = {"extract": 0.005, "vad": 0.004, "qwen": 0.22, "qwen2": 0.22, "mimo": 0.75, "whisper": 0.12, "deepseek": 0.15, "moss": 0.12}
LEARN_MIN_AUDIO = 30.0  # short files are dominated by start-up, so they teach nothing about rates
SPEECH_GUESS = 0.8  # share of a file assumed to be speech until detection has run


class Tracker:
    def __init__(self, keys, duration, cur, rates, clock):
        self.keys, self.cur, self.rates, self.clock = keys, cur, dict(RATES, **rates), clock
        self.t0 = clock()
        self.started = {}
        self.sim = None
        guess = duration * SPEECH_GUESS
        self.audio = {
            "extract": duration, "vad": duration, "qwen": guess, "qwen2": guess, "deepseek": guess, "moss": guess * 2,
            "mimo": guess, "whisper": guess,
        }
        cur["lanes"] = [{"key": k, "name": k, "pct": 0, "state": "等待"} for k in keys]

    def predicted(self, key):
        return STARTUP[key] + self.rates[key] * self.audio[key]

    def set_audio(self, key, seconds):
        self.audio[key] = seconds

    def _lane(self, key):
        return next(x for x in self.cur["lanes"] if x["key"] == key)

    def set(self, key, pct, label=None):
        self.started.setdefault(key, self.clock())
        lane = self._lane(key)
        lane["pct"] = max(lane["pct"], pct)
        lane["state"] = label or f"{int(lane['pct'])}%"
        self._refresh()

    def creep(self, key, target, seconds, label=None):
        """Eases towards target over about `seconds`, never reaching it, until a real update arrives."""
        self.sim = {"key": key, "base": self._lane(key)["pct"], "target": target, "seconds": max(0.2, seconds), "t": self.clock(), "label": label}

    def tick(self):
        s = self.sim
        if s:
            done = 1 - math.exp(-2 * (self.clock() - s["t"]) / s["seconds"])
            self.set(s["key"], s["base"] + (s["target"] - s["base"]) * done, s["label"])
        self._refresh()

    def finish(self, key, label="完成", learn=True):
        self.sim = None
        lane = self._lane(key)
        lane["pct"], lane["state"] = 100, label
        taken = self.clock() - self.started.get(key, self.clock())
        if learn and self.audio[key] >= LEARN_MIN_AUDIO:
            observed = max(0.0, taken - STARTUP[key]) / self.audio[key]
            self.rates[key] = self.rates[key] * 0.5 + observed * 0.5
        self._refresh()

    def _refresh(self):
        lanes = self.cur["lanes"]
        total = sum(self.predicted(x["key"]) for x in lanes) or 1
        done = sum(self.predicted(x["key"]) * x["pct"] / 100 for x in lanes)
        elapsed = self.clock() - self.t0
        # Weights shift as real audio amounts become known; the bar itself must never move backwards.
        self.cur["pct"] = max(self.cur.get("pct", 0), min(100.0, done / total * 100))
        self.cur["elapsed"] = elapsed
        pace = min(2.5, max(0.5, elapsed / done)) if done > 3 else 1.0
        self.cur["remaining"] = (total - done) * pace if elapsed > 2 else None
