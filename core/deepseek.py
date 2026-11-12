"""AI 校对：先写全文大纲，再对照各份识别结果逐句校对文字和断句。

模块名是只支持 DeepSeek 时留下的。现在可以接任何 OpenAI 兼容格式的接口（Chat），
也可以把提示词交给用户复制给订阅制 Agent、再把回复粘贴回来（流水线里的 _Agent），两种方式共用同一套提示词和校验。
"""
import difflib
import json
import re
from concurrent.futures import ThreadPoolExecutor

import httpx

from . import models, sentences

MAX_OUTPUT_TOKENS = 32768
MAX_TERMS = 40
BATCH = 30  # 走接口时每次请求校对多少句；交给 Agent 时全文一次给完
CONTEXT = 4  # sentences shown either side of a batch, for reading only
WORKERS = 4  # requests in flight at once
NOVEL_MAX = 0.2  # share of new characters no transcript has before an edit is marked as doubtful
SIMILAR_MIN = 0.5  # an edit this unlike every transcript is marked as doubtful

OUTLINE_SYSTEM = """你是字幕校对的助手。输入是一段中日双语音视频的完整语音识别稿。语音识别只听声音，常把词写成读音相近的别字，所以稿中的专有名词和不通顺的地方都不可靠。
title 是视频或音频的文件名（可能为空），常含主播名、作品名和话题，是专有名词写法最可靠的依据；里面的编号、日期、平台后缀不用管。
transcript 按顺序每项一句：是字符串时，就是主识别模型 Qwen3-ASR 的结果（其他模型和它一致或没有结果）；是对象时，primary 是 Qwen3-ASR 的结果，others 是其他模型听到的不同写法，键是模型名（MiMo 中文较准，日语里的汉字常写成简体字形，Whisper 中日都能听但偶尔会写出没说过的句子，MOSS 整体较弱但能听到轻声）。几份写法对照着看，更容易认出专有名词的正确写法。
通读全文，先判断场景：在哪里、在做什么、有哪些人、在聊什么。然后整理后续校对要用的大纲：
1. 场景和主题，各部分内容。
2. 人名、地名、地标、机构、品牌、型号、作品名、专业术语的正确写法。结合场景和读音推断，例如在某地旅行时，读音相近的词很可能是当地的知名地点。
3. 疑似听错的词：列出“原词 → 最可能的词（依据）”，依据写读音和场景。候选的读音必须和原词相近；除了专有名词，也要想到感叹语、俗语、玩笑话。拿不准就注明。
术语表中的写法优先，其次是 title 里的写法：稿中读音和 title 里的名字相近的词（例如“かぐらめ”和 title 里的“神楽めあ”），按 title 的写法。
另外单独列出 terms：稿中很可能出现的专有名词的正确写法，每项只写词本身，最多 40 项。只放有把握的词，它们会作为提示交给语音识别重听一遍，写错会把识别带偏。
只输出 JSON：{"outline":"大纲正文","terms":["词1","词2"]}"""

PROOF_SYSTEM = """你是中日双语字幕校对。输入包含全文大纲、术语表，以及整段音频逐句的几份识别结果。请通读全文后校对文字并修正断句。

每句的识别结果（没有的字段表示那份结果缺失）：
- primary：Qwen3-ASR，主要结果，字幕默认用它。
- primary2：同一个 Qwen3-ASR 换了切分位置再听一遍，还参考了术语提示。它和 primary 是独立的两次识别，但错误倾向相近。
- backups：其他识别模型的结果，键是模型名，可能有好几份：MiMo（中文较准；日语里的汉字常写成简体字形，按读音比较），Whisper（中日都能听，但偶尔会写出没说过的句子）。每份各算一个来源。
  键名带“（另一遍）”的，是某个模型在切块交界处把这一段又听写了一遍，时间和 primary 对不齐，常带着前后句的一部分。只用来对照这一句里的词怎么写，不要据此把前后句的内容补进这一句，也不单独算一个来源。
- moss：说话人分离模型顺带写出的文字。整体不如 primary，但有时能听到别的模型漏掉的轻声和重叠的话。
同一句的几份结果来自同一段声音，可以逐词对照。
title 是视频或音频的文件名（可能为空），常含主播名、作品名和话题；读音和 title 里的名字相近的词，按 title 的写法改，可以不标 uncertain。
全文分批校对。context_before 和 context_after 是本批前后的句子，只用来理解上下文，不要列出它们的 id，也不要改它们。

先看几份结果是否一致：
1. 多数结果一致、primary 不同时，说明 primary 很可能听错了：多数的写法说得通就直接采用，不用标 uncertain。
2. primary 少了半句话，而至少两份其他结果都有，并且读得通，可以补上。只有一份结果有的内容不要补。
3. 只有一份结果的写法时，除非其他写法都说不通，否则不要采用它。
4. 各份结果都说不通时，再按下面的方法纠错。

纠错时同时看三件事，不能只看其中一件：
- 逻辑：这句话说得通吗？放在这个场景里合理吗？
- 上下文：前后句在谈什么？大纲里的场景、人物、地点是什么？
- 读音：把可疑的词还原成读音，想想哪个读音相同或相近的词才说得通。中文注意同音字、声母韵母相近（如 n/l、z/zh、an/ang、前后鼻音）和口音；日语注意同音汉字和假名相近的词。
步骤：
1. 读到不通顺、不合场景或像是生造的词，先当作听错。
2. 按读音列出候选，代入上下文，选逻辑最通顺的词。常见的是专有名词、地名、俗语、感叹语、口头禅，例如“富士三”应为“富士山”，“天气真的号”应为“天气真的好”。
   候选的读音必须和原词基本对得上，字数也相近。话题只用来挑选候选，不能用它替换读音对不上的词。
   不要被上一句的话题带偏：口语里常突然冒出感叹、玩笑、俗语和网络用语。一句话短、被重复、像在喊叫时，优先想这类常用说法，句尾的“了、啦、咯、喽”也可能被听成别的字。
3. 有依据就改，并在 reason 写清依据（哪几份结果一致，或读音和场景）。没有任何一份结果支持、只靠推测改的，uncertain 设为 true。完全无法还原时保留 primary，uncertain 设为 true。

硬性限制：
- 改后的每个词，要么出现在某份识别结果里，要么和某份结果对应位置的读音相近。不能写出所有结果里都没有、读音也对不上的话，哪怕它更通顺。
- 例外：几份结果在同一位置都是不成词的乱字，但读音合起来看明显是一个常见的固定说法（经文、俗语、作品名、人名等），可以写出这个说法，uncertain 设为 true，reason 写出各份结果的读音依据。例如“一語一円”“市子一枝”“いちごいちえ”合起来读作“いちごいちえ”，应为“一期一会”。只有一份结果、或读音只对上一小部分时不要这样做。
- 说得通的词和句子不要改，哪怕换一个词显得更合理；只有不改就讲不通，或多数结果都不同意 primary 时才动手。
- 术语表的写法优先，其次是 title、大纲和全文一致的写法。

文字修改范围：
- 可以改：听错的字词、专有名词、俗语和感叹语、漏掉的半句、错误的标点、句末被截断造成的错误标点。
- 不要改：说话风格、口语、句中的语气词、重复和语序；不要润色、翻译、总结。
- 保持每句原来的语言。中文用简体中文和中文标点，日语保持日语写法和日语标点。
- 几份结果只差“二十年/20年”“M九/M9”这类写法时，按大纲和全文一致的写法，没有就保留 primary。
- 数字和数量不要凭推测改；只有其他结果写的是另一个数，并且 primary 说不通时才改。

断句修改范围，目标是每条字幕是一句完整的话：
- 合并：一句话被切成相邻的几条时，把这些 id 都写进 ids，text 写合并后的整句。
- 拆分：一条里有两句独立的话时，在 text 中用换行符 \\n 分开。单条不超过 28 个字，过长时在逗号等自然停顿处拆开。
- 删除：只由“嗯”“哎”“啊”这类语气词组成的单独一条，text 写空字符串。有内容的话不能删。
- 只能合并相邻的 id，不要调整先后顺序。
- 句子带 speaker 时表示说话人，可以用它判断谁在回应谁。speaker 不同的句子是不同的人说的，一律不能合并，哪怕后一句是在接前一句的话。

只输出 JSON，并且只列出需要修改、修正断句或存疑的句子，其余句子不要列出：
{"items":[{"ids":[涉及的 id],"text":"校对后的完整字幕","uncertain":true或false,"reason":"纠正文字或存疑时用一句中文说明依据；只改断句时留空"}]}
没有需要处理的句子时输出 {"items":[]}。

示例（只示意格式和判断方法）：
输入 segments：
[{"id":7,"primary":"零重力座椅展开或收回时，再不只是调节。","primary2":"零重力座椅展开或收回时暂不支持调节。","moss":"零重力座椅展开或收回时暂不支持调节。"},
 {"id":8,"primary":"这个跟M九一样的","primary2":"这个跟M9是一样的","speaker":"2"},
 {"id":9,"primary":"是吧。","speaker":"2"},
 {"id":10,"primary":"今天去了浅草寺，人好多。それな、めっちゃ混んでた。","speaker":"1"}]
输出：
{"items":[{"ids":[7],"text":"零重力座椅展开或收回时暂不支持调节。","uncertain":false,"reason":"primary2 和 moss 一致，读音也和“再不只是”相近"},
 {"ids":[8,9],"text":"这个跟M9是一样的，是吧。","uncertain":false,"reason":"按 primary2 补上“是”；同一说话人的一句话被切开，合并"},
 {"ids":[10],"text":"今天去了浅草寺，人好多。\\nそれな、めっちゃ混んでた。","uncertain":false,"reason":""}]}"""


TERMS_SYSTEM = """你帮用户为一段中日双语音视频准备转写用的词库。用户会描述视频的主题、大致内容、涉及的领域、人物和团体，files 是要转写的文件名（文件名里常有主播名、作品名）。
词库有两个用途：作为提示交给语音识别模型，让它把专有名词写对；以及交给校对 AI，作为专有名词正确写法的依据。语音识别常把不认识的专有名词写成读音相近的别字，词库就是用来避免这一点的。

请列出视频里很可能被说出来的词，按这个优先级：
1. 人名和昵称：用户提到的人物，以及和他们常一起出现的人（同团体成员、搭档、常合作的人）。日本人名写日文正式写法，常被叫的昵称、爱称也单独列出。
2. 团体、公司、节目、作品、游戏、歌曲等名称，用原文写法。
3. 这个领域和圈子里的常用术语、梗、俗语和固定说法，以及这类视频里常出现的说法。
4. 地名、店名、品牌等。
只列专有名词和容易写错的词，不要列日常常用词。中文的词用简体中文，日语的词用日语写法；同一个对象在两种语言里写法不同时，两种都列。
只放有把握的词：写错的词会把识别带偏，宁可少列。最多 40 个。"""
TERMS_API = '\n只输出 JSON：{"terms":["词1","词2"]}'
TERMS_AGENT = "\n只输出词表本身：每行一个词，不要编号、解释、分类标题和代码块，方便直接复制。"
TERM_INFO = (("topic", "视频主题和大致内容"), ("field", "涉及的领域"), ("people", "涉及的人物和团体"))


def terms_prompt(info, agent=False):
    """生成词库的完整提示词。info 是用户在生成词库页填的内容，files 是新建页队列里的文件名。"""
    payload = {label: str(info.get(k) or "").strip() for k, label in TERM_INFO}
    payload["files"] = [str(f) for f in info.get("files") or []]
    if agent:
        return TERMS_SYSTEM + TERMS_AGENT + "\n\n下面是用户提供的信息（JSON）：\n" + json.dumps(payload, ensure_ascii=False, indent=1)
    return TERMS_SYSTEM + TERMS_API, payload


def make_terms(chat, info):
    """用服务商接口生成词库，返回词的列表（去重，最多 MAX_TERMS 个）。"""
    system, payload = terms_prompt(info)
    reply, _ = chat.ask(system, payload)
    return clean_terms("\n".join(str(t) for t in reply.get("terms") or []))[:MAX_TERMS]


def clean_terms(text):
    """把词表整理成每行一个词：去掉编号、项目符号、引号和重复，跳过像标题的行。"""
    out = []
    for line in str(text or "").splitlines():
        t = re.sub(r"^\s*(?:[-*•·]|\d+[.、)）])\s*", "", line).strip().strip("`\"'“”「」")
        if not t or t.startswith("```") or t.endswith(("：", ":")) or len(t) > 40:
            continue
        if t not in out:
            out.append(t)
    return out


# 预置的校对接口，都按 OpenAI 兼容格式调用（POST /chat/completions，GET /models）
PROVIDERS = {
    "deepseek": ("DeepSeek", "https://api.deepseek.com"),
    "openai": ("OpenAI", "https://api.openai.com/v1"),
    "qwen": ("阿里云百炼（通义千问）", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    "moonshot": ("Kimi（月之暗面）", "https://api.moonshot.cn/v1"),
    "zhipu": ("智谱 GLM", "https://open.bigmodel.cn/api/paas/v4"),
    "siliconflow": ("硅基流动", "https://api.siliconflow.cn/v1"),
    "openrouter": ("OpenRouter", "https://openrouter.ai/api/v1"),
    "gemini": ("Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai"),
    "anthropic": ("Anthropic Claude", "https://api.anthropic.com/v1"),
    "custom": ("自定义（OpenAI 兼容）", ""),
}
TUNABLE = ("response_format", "temperature", "max_tokens")  # 有的接口不认这些参数，报错时去掉再试


def label(cfg):
    """给用户看的校对方式名称。"""
    if cfg.get("proof_mode") == "agent":
        return "Agent"
    return PROVIDERS.get(cfg.get("proof_provider"), ("校对 AI",))[0]


def parse_reply(text):
    """从模型或 Agent 的回复里取出 JSON 对象：允许前后有说明文字、```json 代码块和 <think> 思考过程。"""
    text = re.sub(r"<think>.*?</think>", "", str(text or ""), flags=re.S).strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    try:
        reply = json.loads(text)
    except ValueError:
        a, b = text.find("{"), text.rfind("}")
        if a < 0 or b <= a:
            raise ValueError("回复里没有找到 JSON") from None
        try:
            reply = json.loads(text[a:b + 1])
        except ValueError as e:
            raise ValueError(f"回复里的 JSON 不完整或格式不对：{e}") from None
    if not isinstance(reply, dict):
        raise ValueError("回复的 JSON 应该是一个对象 {…}")
    return reply


class Chat:
    """一个校对接口的连接。同一次任务里记住这个接口不认的参数，后面的请求不再带。"""
    batch, attempts = BATCH, 2

    def __init__(self, cfg, key, timeout=900):
        self.cfg, self.name = cfg, label(cfg)
        url = (cfg.get("proof_url") or PROVIDERS.get(cfg.get("proof_provider"), ("", ""))[1]).rstrip("/")
        if not url:
            raise ValueError("还没有填写接口地址")
        headers = {"Authorization": f"Bearer {key}"}
        if cfg.get("proof_provider") == "anthropic":
            headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
        self.client = httpx.Client(base_url=url, headers=headers, timeout=timeout)
        self.drop, self.max_tokens = set(), MAX_OUTPUT_TOKENS

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.client.close()

    def models(self):
        r = self.client.get("/models")
        if r.status_code >= 400:
            raise ValueError(f"{self.name} 返回 {r.status_code}：{r.text[:200]}")
        return sorted(str(m["id"]) for m in r.json().get("data", []) if m.get("id"))

    def _post(self, messages):
        r = None
        for _ in range(len(TUNABLE) + 2):
            body = {"model": self.cfg["proof_model"], "messages": messages, "stream": False}
            if "response_format" not in self.drop:
                body["response_format"] = {"type": "json_object"}
            if "temperature" not in self.drop:
                body["temperature"] = 0.2
            if "max_tokens" not in self.drop:
                body["max_tokens"] = self.max_tokens
            r = self.client.post("/chat/completions", json=body)
            if r.status_code in (400, 422):
                low = r.text.lower()
                hit = next((p for p in TUNABLE if p in low and p not in self.drop), None)
                if hit == "max_tokens":
                    most = re.search(r"(?:at most|maximum|<=|up to)\D{0,20}(\d{3,6})", low)
                    if most and int(most.group(1)) < self.max_tokens:
                        self.max_tokens = int(most.group(1))
                        continue
                    if self.max_tokens > 8192:
                        self.max_tokens = 8192
                        continue
                if hit:
                    self.drop.add(hit)
                    continue
            if r.status_code >= 400:
                raise ValueError(f"{self.name} 返回 {r.status_code}：{r.text[:300]}")
            return r.json()["choices"][0]
        raise ValueError(f"{self.name} 一直拒绝请求：{r.text[:300]}")

    def ask(self, system, payload, history=()):
        """发一次请求，history 是同一段对话里之前的（回复, 反馈）。返回 (解析出的 JSON, 原文)，出错重试两次。"""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        for reply, feedback in history:
            messages += [{"role": "assistant", "content": reply}, {"role": "user", "content": feedback}]

        def once():
            choice = self._post(messages)
            if choice.get("finish_reason") == "length":
                raise ValueError(f"{self.name} 的输出被截断")
            text = choice["message"]["content"] or ""
            return parse_reply(text), text
        return _retry(once)


def test(cfg, key):
    """返回这个密钥能用的模型。"""
    with Chat(cfg, key, 20) as c:
        return c.models()


def prompt_text(system, payload):
    """交给 Agent 的完整提示词：说明加上输入数据，要求只回复 JSON。"""
    return (system + "\n\n下面是输入数据（JSON）。请按上面的要求处理，只回复一个 JSON 对象，不要写其他文字。\n\n"
            + json.dumps(payload, ensure_ascii=False))


def check_shape(step, reply):
    """粘贴回来的结果是不是这一步要的格式，不是就返回原因。"""
    if step == "outline":
        return None if isinstance(reply.get("outline"), str) else "缺少 outline（大纲正文）。请确认粘贴的是大纲这一步的回复"
    if not isinstance(reply.get("items"), list):
        return "缺少 items 列表。请确认粘贴的是校对这一步的回复"
    return None


def _retry(call):
    for attempt in range(3):
        try:
            return call()
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            if attempt == 2:
                raise


def glossary(cfg):
    return [g.strip() for g in cfg.get("glossary", "").splitlines() if g.strip()]


def outline(cfg, chat, segments, title=""):
    """返回 (大纲, 术语)：校对用的场景说明，以及交给 Qwen 复核的专有名词。chat 是 Chat 或流水线里的 Agent。

    title 是源文件名（不含扩展名），里面常有主播名、作品名，作为专有名词的依据。
    """
    payload = {"title": title, "glossary": glossary(cfg), "transcript": [_outline_row(s) for s in segments]}
    reply, _ = chat.ask(OUTLINE_SYSTEM, payload)
    terms = [str(t).strip() for t in reply.get("terms") or [] if str(t).strip()]
    return str(reply["outline"]), terms[:MAX_TERMS]


def _outline_row(s):
    """大纲输入的一句：只有 Qwen 的文字；其他模型写法不同时，附上这些不同的写法。"""
    others = {}
    for k in models.BACKUPS + ("moss",):
        t = s.get(k)
        if t and _plain(t) != _plain(s["qwen"]) and _plain(t) not in {_plain(v) for v in others.values()}:
            others["MOSS" if k == "moss" else models.MODELS[k]["short"]] = t
    return {"primary": s["qwen"], "others": others} if others else s["qwen"]


SOURCES = (("qwen2", "primary2"), ("moss", "moss"))
NAMES = ((re.compile(r"(?<![A-Za-z])primary2(?![A-Za-z0-9])"), "Qwen 复核"), (re.compile(r"(?<![A-Za-z])primary(?![A-Za-z0-9])"), "Qwen"),
         (re.compile(r"(?<![A-Za-z])moss(?![A-Za-z])"), "MOSS"), (re.compile(r"(?<![A-Za-z])backups(?![A-Za-z])"), "备用模型"))


def readable(reason):
    """DeepSeek 写理由时会用输入里的字段名（primary、moss），换成用户认得的模型名。"""
    for pat, name in NAMES:
        reason = pat.sub(name, reason)
    return reason
TRANSCRIPTS = ("qwen", "qwen2", "moss") + models.BACKUPS  # every field that holds a transcript of the sentence
_KEEP = re.compile(r"[\W_]+")


def _row(s):
    row = {"id": s["id"], "lang": s["lang"], "primary": s["qwen"]}
    for field, name in SOURCES + (("speaker", "speaker"),):
        if s.get(field):
            row[name] = s[field]
    backups = {models.MODELS[k]["short"]: s[k] for k in models.BACKUPS if s.get(k)}
    for alt in s.get("alts") or []:  # 时间错位、重复了这一句的另一遍听写（sentences.absorb_repeats）
        f = alt["field"]
        name = "MOSS" if f == "moss" else "Qwen 复核" if f == "qwen2" else models.MODELS[f]["short"]
        key, n = f"{name}（另一遍）", 2
        while key in backups:
            key, n = f"{name}（另一遍 {n}）", n + 1
        backups[key] = alt["text"]
    if backups:
        row["backups"] = backups
    return row


def _plain(text):
    return _KEEP.sub("", str(text)).lower()


def batches(segments, size=BATCH):
    """Splits the sentences into runs of about `size`, each ending at the longest pause in reach."""
    out, a, n = [], 0, len(segments)
    while n - a > size * 1.3:
        lo, hi = a + int(size * 0.7), min(n - 1, a + int(size * 1.3)) - 1
        cut = max(range(lo, hi + 1), key=lambda i: segments[i + 1]["a"] - segments[i].get("b", segments[i]["a"]))
        out.append((a, cut + 1))
        a = cut + 1
    out.append((a, n))
    return out


def structure_error(item, own, taken):
    """Why an item cannot be applied at all (bad ids, a merge across speakers, a deleted sentence that had words), or None."""
    try:
        ids = sorted(int(i) for i in item.get("ids") or [])
    except (TypeError, ValueError):
        return "ids 必须是整数编号"
    if not ids:
        return "缺少 ids"
    if not all(i in own for i in ids):
        return f"ids {ids} 不全在本批 segments 里（context 里的句子不要列出）"
    if ids != list(range(ids[0], ids[-1] + 1)):
        return f"ids {ids} 不相邻，只能合并相邻的句子"
    if not taken.isdisjoint(ids):
        return f"ids {ids} 和别的条目重复"
    if len({own[i].get("speaker") for i in ids} - {"", None}) > 1:
        return f"ids {ids} 属于不同说话人，不能合并"
    if not str(item.get("text", "")).strip() and not all(sentences.is_filler(own[i]["qwen"]) or len(_plain(own[i]["qwen"])) <= 2 for i in ids):
        return f"ids {ids} 有实际内容，不能删除"
    return None


def content_doubt(item, group, known=""):
    """改后的文字在各份识别结果里找不到依据时返回原因，否则 None。这类修改照样采用，但标成存疑交给用户。

    known 是用户给的可靠写法（文件名、术语表），里面的字算作有依据。
    """
    new = _plain(item.get("text", ""))
    if not new:
        return None
    texts = ["".join(s.get(f) or "" for s in group) for f in TRANSCRIPTS]
    texts += [a["text"] for s in group for a in s.get("alts") or []]
    texts = [_plain(t) for t in texts if t]
    if new in texts:
        return None
    seen = set("".join(texts)) | set(_plain(known))
    novel = "".join(dict.fromkeys(c for c in new if c not in seen))
    best = max(difflib.SequenceMatcher(None, new, t, autojunk=False).ratio() for t in texts)
    if len(novel) > max(2, len(new) * NOVEL_MAX):
        return f"校验：改后的“{novel}”在各份识别结果里都没有，请对照各模型的结果确认"
    if best < (0.3 if len(new) <= 10 else SIMILAR_MIN):
        return f"校验：改后和各份识别结果差别都很大（最多只有 {best:.0%} 相同），请对照确认"
    return None


def _proof_batch(chat, base, segments, a, b):
    """Corrects segments[a:b]. Items that cannot be applied get one chance to be redone; the rest are dropped."""
    own = {s["id"]: s for s in segments[a:b]}
    payload = dict(base, context_before=[_row(s) for s in segments[max(0, a - CONTEXT):a]],
                   segments=[_row(s) for s in segments[a:b]], context_after=[_row(s) for s in segments[b:b + CONTEXT]])
    history, kept, taken, dropped = [], [], set(), 0
    for attempt in range(chat.attempts):
        reply, raw = chat.ask(PROOF_SYSTEM, payload, history)
        bad = []
        for item in reply.get("items") or []:
            if not isinstance(item, dict):
                continue
            why = structure_error(item, own, taken)
            if why:
                bad.append((item, why))
                continue
            ids = sorted(int(i) for i in item["ids"])
            item["ids"] = ids
            taken.update(ids)
            doubt = content_doubt(item, [own[i] for i in ids], base.get("title", "") + "".join(base.get("glossary") or []))
            if doubt:
                item["uncertain"] = True
                item["reason"] = "；".join(x for x in (str(item.get("reason") or "").strip(), doubt) if x)
            kept.append(item)
        if not bad:
            break
        if attempt == chat.attempts - 1:
            dropped += len(bad)
            break
        lines = "\n".join(f"- {json.dumps(it.get('ids'), ensure_ascii=False)}：{why}" for it, why in bad)
        history.append((raw, f"下面这些条目无法套用：\n{lines}\n请只重新输出这些条目（拿不准就保留 primary），其余条目不要重复，格式同前。"))
    return kept, dropped


def proofread(cfg, chat, segments, outline_text, title=""):
    """Returns (items, dropped): the edits and doubts the AI found, batch by batch, and how many it could not get right.

    Edits that the transcripts do not support are not thrown away: they come back marked uncertain, with the reason,
    so the review page can show them next to every transcript.
    """
    base = {"title": title, "outline": outline_text, "glossary": glossary(cfg)}
    parts = batches(segments, chat.batch) if chat.batch else [(0, len(segments))]
    with ThreadPoolExecutor(WORKERS if chat.batch else 1) as pool:
        jobs = [pool.submit(_proof_batch, chat, base, segments, a, b) for a, b in parts]
        items, dropped = [], 0
        for j in jobs:
            got, lost = j.result()
            items += got
            dropped += lost
    return items, dropped
