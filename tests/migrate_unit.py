"""数据目录改名的迁移：旧目录里的东西搬到新目录，设置里的旧路径改掉，已有的不覆盖。"""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["CAPTIONJURY_HOME"] = tempfile.mkdtemp(prefix="captionjury-test-")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import store  # noqa: E402

root = Path(tempfile.mkdtemp(prefix="captionjury-migrate-"))
old, new = root / "Tingxie", root / "CaptionJury"

# 旧目录：设置（含写死的旧路径）、密钥、任务、运行库
(old / "jobs" / "abc").mkdir(parents=True)
(old / "jobs" / "abc" / "job.json").write_text('{"id": "abc"}', encoding="utf-8")
(old / "runtime" / "crispasr-cpu").mkdir(parents=True)
(old / "deepseek.key").write_bytes(b"\x01\x02")
cfg = {
    "runtime_cpu": str(old / "runtime" / "crispasr-cpu"),
    "qwen06_model": str(old).upper() + "\\models\\q.gguf",  # 大小写不同也要认出来
    "ffmpeg": "ffmpeg",
    "qwen_model": "E:\\AI\\Tingxie-like\\q.gguf",  # 不在旧目录下的不动
    "backups": {"zh": ["whisper"]},
}
(old / "config.json").write_text(json.dumps(cfg), encoding="utf-8")

left = store.migrate(old, new)
assert left == [], left
assert not old.exists(), "旧目录搬空后应删掉"
assert (new / "jobs" / "abc" / "job.json").exists()
assert (new / "runtime" / "crispasr-cpu").is_dir()
assert (new / "deepseek.key").read_bytes() == b"\x01\x02"
got = json.loads((new / "config.json").read_text(encoding="utf-8"))
assert got["runtime_cpu"] == str(new / "runtime" / "crispasr-cpu"), got
assert got["qwen06_model"] == str(new) + "\\models\\q.gguf", got
assert got["qwen_model"] == cfg["qwen_model"] and got["ffmpeg"] == "ffmpeg"
assert got["backups"] == {"zh": ["whisper"]}

# 新目录里已有同名项：不覆盖，留在旧目录，旧目录不删
(old / "jobs").mkdir(parents=True)
(old / "jobs" / "x.txt").write_text("old", encoding="utf-8")
(old / "extra.txt").write_text("e", encoding="utf-8")
left = store.migrate(old, new)
assert left == ["jobs"], left
assert (new / "extra.txt").exists() and (old / "jobs" / "x.txt").exists()
assert (new / "jobs" / "abc" / "job.json").exists()

# 没有旧目录：什么都不做
assert store.migrate(root / "nothing", new) == []

# 设了 CAPTIONJURY_HOME 时不碰真实的旧目录
assert store.HOME == Path(os.environ["CAPTIONJURY_HOME"])
store.load_config()
print("ok")
