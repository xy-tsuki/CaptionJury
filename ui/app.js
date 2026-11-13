'use strict';
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (t) => String(t ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const view = $('#view');

/* 图标：16 格、1.4 线宽，颜色跟随文字 */
const svg = (d, cls = '') => `<svg viewBox="0 0 16 16" class="${cls}" aria-hidden="true">${d}</svg>`;
const I = {
  x: svg('<path d="M4 4l8 8M12 4l-8 8"/>'),
  up: svg('<path d="M8 11V2.5M4.5 6L8 2.5 11.5 6M2.5 10.5v3h11v-3"/>', 'ic'),
  prev: svg('<path d="M12 3.5L6 8l6 4.5zM4 3.5v9"/>'),
  next: svg('<path d="M4 3.5L10 8l-6 4.5zM12 3.5v9"/>'),
  play: svg('<path d="M4.5 2.8L13 8l-8.5 5.2z"/>', 'fill'),
  pause: svg('<path d="M4.5 3h2.5v10H4.5zM9 3h2.5v10H9z"/>', 'fill'),
  folder: svg('<path d="M1.8 3.5h4.4l1.4 1.6h6.6v7.4H1.8z"/>'),
  trash: svg('<path d="M2.5 4.5h11M6 4.5V2.8h4v1.7M4 4.5l.7 8.7h6.6l.7-8.7"/>'),
  find: svg('<circle cx="7" cy="7" r="4.2"/><path d="M10.2 10.2L14 14"/>'),
  down: svg('<path d="M8 2.5V11M4.5 7.5L8 11l3.5-3.5M2.5 13.5h11"/>'),
  lock: svg('<rect x="3" y="7" width="10" height="6.5"/><path d="M5.2 7V5a2.8 2.8 0 015.6 0v2"/>'),
};

const S = { cfg: null, terms: '', tf: { topic: '', field: '', people: '', files: true }, files: [], lang: 'mix', mode: 'accurate', opt: true, compare: true, dia: false, formats: ['SRT', 'ASS'], status: null, job: null, sel: 1, flt: 'all', pollT: null, hq: '' };
const api = () => window.pywebview.api;
const FORMATS = ['SRT', 'ASS', 'VTT', 'TXT'];
const LINE_KINDS = { wide: '横屏 · 每条≤18字', tall: '竖屏 · 每条≤12字', full: '不限字数' };
const LANGS = { zh: '中文', ja: '日语', mix: '中文 + 日语' };

function hms(t, ms) {
  // 先换成整数毫秒再拆，避免 5.09 这种浮点数显示成 5,089
  const n = Math.round(Math.max(0, t || 0) * 1000);
  const s = ms ? Math.floor(n / 1000) : Math.floor(Math.max(0, t || 0));
  const p = (x, w = 2) => String(Math.floor(x)).padStart(w, '0');
  const base = `${p(s / 3600)}:${p((s / 60) % 60)}:${p(s % 60)}`;
  return ms ? `${base},${p(n % 1000, 3)}` : base;
}
const bytes = (n) => (n > 1e9 ? (n / 1e9).toFixed(1) + ' GB' : Math.max(1, Math.round(n / 1e6)) + ' MB');
const langAttr = (l) => (l === 'ja' ? 'ja' : 'zh-CN');
const when = (t) => { const d = new Date(t * 1000); const p = (x) => String(x).padStart(2, '0'); return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`; };
const fileName = (p) => String(p).split(/[\\/]/).pop();
const agentMode = () => S.cfg && S.cfg.proof_mode === 'agent';
/* 当前校对用的服务商名称（走 Agent 时是"你的 Agent"） */
function proofName() {
  if (!S.cfg) return '校对 AI';
  if (agentMode()) return '你的 Agent';
  const p = (S.cfg.providers || []).find((x) => x.key === S.cfg.proof_provider);
  return p ? p.label.replace(/（.*）$/, '') : '校对 AI';
}

/* 复制到剪贴板；WebView 不给用剪贴板接口时退回到选中再复制 */
async function copyText(text, area) {
  try { await navigator.clipboard.writeText(text); return true; } catch (e) { /* 退回下面的办法 */ }
  if (area) { area.focus(); area.select(); }
  try { return document.execCommand('copy'); } catch (e) { return false; }
}

let toastT;
/* action 可选：{ label, fn }，在提示里放一个按钮（比如撤销） */
function toast(msg, action) {
  const t = $('#toast');
  t.textContent = msg;
  if (action) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'tact';
    b.textContent = action.label;
    b.onclick = () => { t.hidden = true; action.fn(); };
    t.append(b);
  }
  t.hidden = true;
  void t.offsetWidth;  // 重新触发出现动画
  t.hidden = false;
  clearTimeout(toastT);
  toastT = setTimeout(() => { t.hidden = true; }, action ? 8000 : 4200);
}

/* 解析时间码：00:01:02,345、1:02.3、62.3 都可以；格式不对返回 null */
function parseTc(v) {
  const parts = String(v).trim().replace(',', '.').split(':');
  if (!parts[0] || parts.length > 3) return null;
  const n = parts.map(Number);
  return n.some((x) => !Number.isFinite(x) || x < 0) ? null : n.reduce((acc, x) => acc * 60 + x, 0);
}

/* 导出菜单：选格式，写到源文件旁边。校对页和历史页共用 */
function exportMenu(jid, small = true) {
  const f = S.expFormats || S.formats;
  return `<details class="menu" data-exp="${jid}">
    <summary class="btn ${small ? 'sm' : ''} solid">${I.down}导出字幕</summary>
    <div class="menu-pop">
      <div class="fmts">${FORMATS.map((x) => `<button type="button" class="fmt mono${f.includes(x) ? ' on' : ''}" data-xf="${x}" aria-pressed="${f.includes(x)}">${x}</button>`).join('')}</div>
      <p>写在源文件所在的文件夹（写不进去时存到"文档\字幕对照组"），再次导出会覆盖上次导出的文件。</p>
      <button class="btn sm solid" type="button" data-xgo>导出</button>
      <details class="howto"><summary>怎么导入剪辑软件</summary>
        <p>都用 SRT，菜单名称以你的软件版本为准：</p>
        <ul>
          <li>剪映专业版：在「文本」面板（有的版本在「字幕」或「AI 字幕」）里点「导入本地字幕」，再拖到轨道上</li>
          <li>Premiere：文件 → 导入，再把 SRT 从项目面板拖到时间线上</li>
          <li>达芬奇：剪辑页 文件 → 导入 → 字幕，再从媒体池拖到时间线上</li>
          <li>Final Cut（10.4.1 以上）：先打开项目，文件 → 导入 → 字幕</li>
        </ul>
      </details>
    </div>
  </details>`;
}

function bindExport(root = document) {
  $$('[data-exp]', root).forEach((m) => {
    m.ontoggle = () => { if (m.open) $$('[data-exp]').forEach((o) => { if (o !== m) o.open = false; }); };
    $$('[data-xf]', m).forEach((b) => {
      b.onclick = () => {
        const f = S.expFormats || [...S.formats];
        S.expFormats = f.includes(b.dataset.xf) ? f.filter((x) => x !== b.dataset.xf) : [...f, b.dataset.xf];
        $$('[data-xf]').forEach((o) => { const on = S.expFormats.includes(o.dataset.xf); o.classList.toggle('on', on); o.setAttribute('aria-pressed', on); });
      };
    });
    $('[data-xgo]', m).onclick = async (e) => {
      const f = FORMATS.filter((x) => (S.expFormats || S.formats).includes(x));
      if (!f.length) return toast('请至少选一种格式');
      e.target.classList.add('busy');
      const out = await api().export(m.dataset.exp, f);
      e.target.classList.remove('busy');
      if (out.error) return toast(out.error);
      m.open = false;
      const dir = out[0].replace(/[\\/][^\\/]*$/, '');
      toast(`已导出到 ${dir}：` + out.map(fileName).join('、'), { label: '打开文件夹', fn: () => api().reveal(out[0]) });
    };
  });
}
document.addEventListener('click', (e) => {  // 点菜单外面就收起
  $$('details.menu[open]').forEach((m) => { if (!m.contains(e.target)) m.open = false; });
});

/* 只显示一次的提示：关掉后记在本机，存储用不了时每次都显示 */
const tipSeen = (k) => { try { return localStorage.getItem('tip_' + k) === '1'; } catch { return false; } };
const tipHide = (k) => { try { localStorage.setItem('tip_' + k, '1'); } catch { /* 存不了就下次再显示 */ } };

/* ---------- 新建 ---------- */
/* 三种模式：快速只用主模型；精准多模型对照 + AI 校对；自定义自己选（S.opt / S.compare 是自定义时的勾选） */
const MODES = {
  fast: ['快速', '只用主模型，最快出字幕。'],
  accurate: ['精准', '几个模型对照，AI 逐句校对，拿不准的标出来。'],
  custom: ['自定义', '自己选多模型对照和 AI 校对。'],
};
const modeFlags = (m) => (m === 'fast' ? { opt: false, compare: false } : m === 'accurate' ? { opt: true, compare: true } : { opt: S.opt, compare: S.compare });

/* 按以往的速度估算这次要多久（不含等你粘贴 Agent 回复的时间）；没有记录或没加文件时返回空 */
function estimate(m) {
  const sp = (S.cfg && S.cfg.speed) || {};
  const total = S.files.reduce((a, f) => a + (f.duration || 0), 0);
  if (!total || !sp.qwen) return '';
  const { opt, compare } = modeFlags(m);
  let r = (sp.extract || 0) + (sp.vad || 0) + sp.qwen + (S.dia ? sp.moss || 0 : 0);
  if (compare) {
    const keys = new Set([['zh'], ['ja']].filter(([k]) => S.lang === k || S.lang === 'mix').flatMap(([k]) => ((S.cfg.backups_keys || {})[k] || [])));
    for (const k of keys) r += sp[k] || 0;
  }
  if (opt) r += (sp.qwen2 || 0) + (agentMode() ? 0 : sp.deepseek || 0);
  const sec = total * r;
  return sec < 60 ? '预计不到 1 分钟' : `预计约 ${Math.round(sec / 60)} 分钟`;
}
/* 这次会跑的备用模型：[[语言名, [模型名...]], ...]，按设置里的勾选和已安装的模型 */
function activeBackups() {
  const on = (S.cfg && S.cfg.backups_active) || {};
  return [['zh', '中文'], ['ja', '日语']].filter(([k]) => S.lang === k || S.lang === 'mix').map(([k, l]) => [l, on[k] || []]);
}

function renderNew() {
  const backups = activeBackups();
  const anyBackup = backups.some(([, ms]) => ms.length);
  const compareText = backups.map(([l, ms]) => (ms.length ? `${l}句加跑 ${ms.join('、')}` : `${l}没有可用的备用模型`)).join('，');
  const running = S.status && S.status.running;
  const total = S.files.reduce((a, f) => a + (f.duration || 0), 0);
  const seg = Object.entries(LANGS).map(([id, l]) => `<button type="button" class="${S.lang === id ? 'on' : ''}" aria-pressed="${S.lang === id}" data-lang="${id}">${l}</button>`).join('');
  const fmts = FORMATS.map((f) => `<button type="button" class="fmt mono${S.formats.includes(f) ? ' on' : ''}" aria-pressed="${S.formats.includes(f)}" data-fmt="${f}">${f}</button>`).join('');
  const queue = S.files.map((f, i) => `<div class="q-row"><span class="q-name" title="${esc(f.path)}">${esc(f.name)}</span><span class="q-meta mono">${hms(f.duration)}</span><span class="q-meta mono faint">${bytes(f.size)}</span><button class="x" type="button" aria-label="从队列移除 ${esc(f.name)}" data-rm="${i}">${I.x}</button></div>`).join('');
  // 处理流程：亮的是这次会跑的步骤
  const md = (name, on) => `<span class="md${on ? '' : ' off'}"><i></i>${esc(name)}</span>`;
  const uses = {};
  for (const [l, ms] of backups) for (const m of ms) (uses[m] = uses[m] || []).push(l);
  const flow = modeFlags(S.mode);  // 底部处理流程按当前模式实际会跑的步骤显示
  view.innerHTML = `
  <main class="m-grid">
    <section class="m-left" aria-label="文件">
      <div class="drop${S.files.length ? ' has' : ''}" id="drop">
        ${I.up}
        <div><h1>${S.files.length ? '继续添加文件' : '拖入视频或音频'}</h1><p class="hint mono">MP4 MKV MOV WEBM · WAV MP3 FLAC M4A</p></div>
        <button class="btn" type="button" id="pick">选择文件</button>
      </div>
      ${S.files.length ? `<div class="queue">${queue}</div><div class="q-sum"><span>${S.files.length} 个文件，按顺序转写</span><span class="mono">共 ${hms(total)}</span></div>` : ''}
    </section>
    <section class="m-right" aria-label="转写设置">
      <div class="m-form">
        ${S.firstRun ? `<div class="tip"><span>模型装好了。先拖一个短视频进来，用<b>快速</b>模式试一下；想要更准，在设置里配置校对 AI 后换成<b>精准</b>模式。</span><button class="x" type="button" id="tipnew" aria-label="关掉提示">${I.x}</button></div>` : ''}
        <div class="m-sec"><span class="lbl">识别语言</span><div class="seg" role="group" aria-label="识别语言">${seg}</div></div>
        <div class="m-sec"><span class="lbl">模式</span>
          <div class="modes" role="radiogroup" aria-label="模式">${Object.entries(MODES).map(([id, [name, desc]]) => `<button type="button" class="mode${S.mode === id ? ' on' : ''}" role="radio" aria-checked="${S.mode === id}" data-mode="${id}"><b>${name}</b><span>${desc}</span>${estimate(id) ? `<em class="mono">${estimate(id)}</em>` : ''}</button>`).join('')}</div>
          ${modeWarn()}
          ${S.mode === 'custom' ? `<div>
            <label class="opt check"><input type="checkbox" id="compare" ${S.compare ? 'checked' : ''}><b>多模型对照</b>
              <p>${compareText}。校对页逐句列出各模型的写法，可以一键采用。</p>
              ${S.compare && !anyBackup ? '<span class="warn">没有可用的备用模型，到设置里下载并勾选。</span>' : ''}</label>
            <label class="opt check"><input type="checkbox" id="opt" ${S.opt ? 'checked' : ''}><b>AI 精度优化</b>
              <p>${esc(proofName())} 写全文大纲找出专有名词，Qwen 带着这些词再听一遍，最后对照各份结果逐句校对。只发送文字。${agentMode() ? '跑到这一步时会暂停，在进度页复制提示词给 Agent，再把回复粘贴回来。' : ''}</p>
              ${S.opt && S.cfg && !agentMode() && !S.cfg.has_key ? `<span class="warn">还没有保存 ${esc(proofName())} 的密钥，到设置里填写，或者改用复制给 Agent 的方式。</span>` : ''}</label>
          </div>` : ''}
          <label class="opt check"><input type="checkbox" id="dia" ${S.dia ? 'checked' : ''}><b>区分说话人</b>
            <p>分出是谁在说话，标到每条字幕上。适合访谈、对谈和会议；一个人讲的视频不用开。</p></label>
        </div>
        <div class="m-sec"><span class="lbl">字幕格式</span><div class="fmts" role="group" aria-label="字幕格式">${fmts}</div><span class="faint small">保存在源文件所在的文件夹。</span></div>
        <div class="m-sec"><label class="lbl" for="terms">词库导入</label>
          <textarea class="field terms" id="terms" rows="4" placeholder="每行一个词：人名、团体、作品名、梗……&#10;只对这次转写生效，设置里的术语表照常使用。">${esc(S.terms)}</textarea>
          <div class="t-bar"><button class="btn" type="button" id="tgen">AI 生成词库</button><button class="btn" type="button" id="timp">导入词库</button><button class="btn" type="button" id="tsave">保存词库</button><span class="faint small" id="tcount">${termCount()}</span></div>
        </div>
      </div>
      <div class="m-go">
        ${running
    ? '<a class="btn solid" href="#progress">有任务正在转写，查看进度</a><span class="faint">当前任务结束后才能开始新的转写。</span>'
    : `<button class="btn solid" type="button" id="go" ${S.files.length && S.formats.length ? '' : 'disabled'}>开始转写</button><span class="faint">${!S.files.length ? '先添加要转写的文件。' : !S.formats.length ? '至少选一种字幕格式。' : `${S.files.length} 个文件，共 ${hms(total)}。`}</span>`}
      </div>
    </section>
  </main>
  <section class="flow" aria-label="处理流程">
    <div class="fs"><span class="t">提取音频</span><span class="d">FFmpeg 转成 16 kHz 单声道</span></div>
    <div class="fs wide"><span class="t">识别</span>${md('Qwen3-ASR 主识别，逐字对时、断句', true)}${md('MOSS 区分说话人', S.dia)}</div>
    <div class="fs${flow.compare ? '' : ' off'}"><span class="t">多模型对照</span>${Object.keys(uses).length ? Object.entries(uses).map(([m, ls]) => md(`${m}（${ls.join('、')}）`, flow.compare)).join('') : '<span class="d">没有可用的备用模型</span>'}</div>
    <div class="fs wide${flow.opt ? '' : ' off'}"><span class="t">AI 精度优化</span><span class="d">${flow.opt ? `${esc(proofName())}：大纲 → Qwen 复核 → 逐句校对` : '不运行'}</span></div>
    <div class="fs"><span class="t">生成字幕</span><span class="d mono">${S.formats.join(' ') || '未选择格式'}</span></div>
  </section>`;
  $$('[data-lang]').forEach((b) => { b.onclick = () => { S.lang = b.dataset.lang; renderNew(); }; });
  if ($('#tipnew')) $('#tipnew').onclick = () => { S.firstRun = false; renderNew(); };
  $$('[data-mode]').forEach((b) => { b.onclick = () => { S.mode = b.dataset.mode; renderNew(); }; });
  if ($('#opt')) $('#opt').onchange = (e) => { S.opt = e.target.checked; renderNew(); };
  if ($('#compare')) $('#compare').onchange = (e) => { S.compare = e.target.checked; renderNew(); };
  $('#dia').onchange = (e) => { S.dia = e.target.checked; renderNew(); };
  $$('[data-fmt]').forEach((b) => { b.onclick = () => { const f = b.dataset.fmt; S.formats = S.formats.includes(f) ? S.formats.filter((x) => x !== f) : FORMATS.filter((x) => x === f || S.formats.includes(x)); renderNew(); }; });
  $$('[data-rm]').forEach((b) => { b.onclick = () => { S.files.splice(+b.dataset.rm, 1); renderNew(); }; });
  $('#pick').onclick = async () => { addFiles(await api().pick_files()); };
  if ($('#go')) $('#go').onclick = start;
  bindTerms();
  const drop = $('#drop');
  drop.ondragover = (e) => { e.preventDefault(); drop.classList.add('over'); };
  drop.ondragleave = () => drop.classList.remove('over');
  drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove('over'); };  // 文件路径由 Python 那边收到后推给 poll
}

/* 选了的模式缺东西时，开始转写前就说清楚 */
function modeWarn() {
  const { opt, compare } = modeFlags(S.mode);
  const out = [];
  if (opt && S.cfg && !agentMode() && !S.cfg.has_key) out.push(`AI 校对需要先设置：<a href="#settings">到设置里</a>填 ${esc(proofName())} 的 API 密钥，或者改用“复制给 Agent”（用你订阅的 AI，不需要密钥）。`);
  if (compare && !activeBackups().some(([, ms]) => ms.length)) out.push('没有可用的备用模型，只会用主模型识别。<a href="#settings">到设置里</a>下载并勾选。');
  if (opt && agentMode() && S.mode !== 'custom') out.push('跑到 AI 校对时会暂停，在进度页把提示词复制给你的 Agent，再把回复粘贴回来。');
  return out.map((x) => `<span class="warn">${x}</span>`).join('');
}

function addFiles(list) {
  const have = new Set(S.files.map((f) => f.path));
  S.files.push(...list.filter((f) => !have.has(f.path)));
  if (location.hash === '#new' || !location.hash) renderNew();
}

async function start() {
  if (!S.files.length) return toast('请先添加文件');
  if (!S.formats.length) return toast('请至少选择一种字幕格式');
  const { opt, compare } = modeFlags(S.mode);
  const r = await api().start(S.files.map((f) => f.path), S.lang, opt, S.formats, S.dia, compare, S.terms, S.mode);
  if (r.error) return toast(r.error);
  S.files = [];
  location.hash = '#progress';
}

/* ---------- 词库 ---------- */
const termLines = (t) => String(t || '').split('\n').map((x) => x.trim()).filter(Boolean);
const termCount = () => {
  const n = new Set(termLines(S.terms)).size;
  if (!n) return '可以留空';
  return n > 40 ? `${n} 个词。识别时只把前 40 个告诉 Qwen，全部交给 AI 校对；常用的词放前面` : `${n} 个词`;
};

/* 往词库框后面接上新的词，重复的不再加 */
function addTerms(text) {
  const have = termLines(S.terms);
  const add = termLines(text).filter((t) => !have.includes(t));
  S.terms = [...have, ...add].join('\n');
  return add.length;
}

function bindTerms() {
  const box = $('#terms');
  box.oninput = () => { S.terms = box.value; $('#tcount').textContent = termCount(); };
  $('#tgen').onclick = () => { location.hash = '#terms'; };
  $('#timp').onclick = async () => {
    const r = await api().import_terms();
    if (!r) return;
    if (r.error) return toast(r.error);
    const n = addTerms(r.text);
    renderNew();
    toast(`导入了 ${r.files.length} 个文件，新增 ${n} 个词`);
  };
  $('#tsave').onclick = async () => {
    if (!termLines(S.terms).length) return toast('词库是空的');
    const r = await api().save_terms(S.terms);
    if (r) toast('词库已保存：' + r.path);
  };
}

/* AI 生成词库：二级页面，从新建页进入 */
function termInfo() {
  return { topic: S.tf.topic, field: S.tf.field, people: S.tf.people, lang: S.lang, files: S.tf.files ? S.files.map((f) => f.name) : [] };
}

function renderTerms() {
  const f = S.tf;
  const hasKey = S.cfg && S.cfg.has_key;
  const names = S.files.map((x) => x.name);
  view.innerHTML = `
  <main class="t-page">
    <div class="t-head"><a class="btn ghost" href="#new">← 返回新建</a></div>
    <div class="t-main">
      <div><h1>AI 生成词库</h1><p class="desc">说说视频讲什么、有谁出场，AI 会列出可能说到的人名、团体、作品名和圈内用语，填进新建页的词库。写得越具体，词越准；拿不准的地方留空就行。按新建页选的识别语言（${esc(LANGS[S.lang] || '')}）写。</p></div>
      <label class="lab"><span>视频主题和大致内容</span><textarea class="field" data-tf="topic" rows="4" placeholder="例如：游戏实况录播，边玩新出的恐怖游戏边和观众聊天">${esc(f.topic)}</textarea></label>
      <label class="lab"><span>涉及的领域</span><input class="field" data-tf="field" value="${esc(f.field)}" placeholder="例如：游戏实况、恐怖游戏"></label>
      <label class="lab"><span>涉及的人物和团体</span><textarea class="field" data-tf="people" rows="3" placeholder="例如：主播、嘉宾、提到的人；写上团体名时，AI 会列出全部成员">${esc(f.people)}</textarea></label>
      ${names.length ? `<label class="check"><input type="checkbox" id="tffiles" ${f.files ? 'checked' : ''}><span>附上新建页的文件名（${names.length} 个），文件名里常有主播名和作品名</span></label>` : ''}
      <div class="t-ways">
        <section class="t-way">
          <h3>${agentMode() ? '用服务商接口生成' : `用 ${esc(proofName())} 生成`}</h3>
          <p class="dim">生成完自动回到新建页，词接在词库框后面。只发送上面填的文字和文件名。</p>
          ${hasKey ? '' : '<p class="warn">还没有保存服务商的密钥。到设置里填写，或者用复制给 Agent 的方式。</p>'}
          <div class="ag-bar"><button class="btn solid" type="button" id="tfapi" ${hasKey ? '' : 'disabled'}>生成词库</button><span class="faint small" id="tfst"></span></div>
        </section>
        <section class="t-way">
          <h3>复制给 Agent</h3>
          <p class="dim">把提示词复制给你的 Agent（Claude、ChatGPT、Gemini 等）。它回复词表以后，复制回新建页的词库框，每行一个词。</p>
          <div class="ag-bar"><button class="btn" type="button" id="tfcopy">复制提示词</button><a class="btn ghost" href="#new">回新建页粘贴</a></div>
          <textarea class="field mono ag-prompt" id="tfprompt" readonly rows="6" aria-label="提示词" hidden></textarea>
        </section>
      </div>
    </div>
  </main>`;
  $$('[data-tf]').forEach((el) => { el.oninput = () => { S.tf[el.dataset.tf] = el.value; }; });
  if ($('#tffiles')) $('#tffiles').onchange = (e) => { S.tf.files = e.target.checked; };
  const empty = () => !(f.topic.trim() || f.field.trim() || f.people.trim() || (f.files && names.length));
  $('#tfcopy').onclick = async () => {
    if (empty()) return toast('先填一点视频的信息');
    const text = await api().terms_prompt(termInfo());
    const area = $('#tfprompt');
    area.hidden = false;
    area.value = text;
    toast(await copyText(text, area) ? '已复制提示词' : '复制失败，请在框里全选后手动复制');
  };
  $('#tfapi').onclick = async (e) => {
    if (empty()) return toast('先填一点视频的信息');
    const btn = e.currentTarget;
    btn.classList.add('busy');
    btn.disabled = true;
    $('#tfst').textContent = '正在生成，通常要十几秒…';
    const r = await api().make_terms(termInfo());
    btn.classList.remove('busy');
    btn.disabled = false;
    if (r.error) { $('#tfst').textContent = ''; return toast(r.error); }
    if (!r.terms.length) { $('#tfst').textContent = 'AI 没有给出词，换个说法再试试。'; return; }
    const n = addTerms(r.terms.join('\n'));
    if (location.hash === '#terms') location.hash = '#new';
    toast(`AI 生成了 ${r.terms.length} 个词，新增 ${n} 个，已填进词库`);
  };
}

/* ---------- 进度 ---------- */
const STEPS = { outline: ['第 1 步，共 2 步：写大纲', '它会读完全文，写出场景大纲和专有名词。'], proof: ['第 2 步，共 2 步：逐句校对', '它会对照几份识别结果，逐句改错和调整断句。'] };

function renderProgress() {
  const s = S.status || {};
  const c = s.current;
  if (!c) {
    const done = !s.error && s.finished;
    view.innerHTML = `<div class="empty">
      <h2>${s.error ? '转写出错' : done ? '转写完成' : '现在没有在跑的任务'}</h2>
      <p>${s.error ? esc(s.error) : done ? '字幕已经写到源文件旁边。可以去校对页逐句检查，以后也能在历史里找到它。' : '在新建页添加文件并开始转写，这里会显示每个模型的进度和预计剩余时间。'}</p>
      <div class="acts">${done ? '<a class="btn solid" href="#review">去校对</a>' : ''}<a class="btn" href="#new">新建转写</a><a class="btn ghost" href="#history">查看历史</a></div>
    </div>`;
    return;
  }
  const box = $('#agentbox');
  if (c.agent && box && box.dataset.k === c.agent.k) {  // 正在等粘贴：只刷新左栏和进度条，不动用户正在粘贴的内容
    $('.p-left').innerHTML = progressLeft(s, c);
    $('.lanes').innerHTML = progressLanes(s, c);
    bindProgress(s);
    return;
  }
  const stream = (c.stream || []).map((r) => `<div class="ln2"><span class="tc mono">${hms(r.t, true)}</span><span>${esc(r.text)}</span></div>`).join('');
  view.innerHTML = `
  <main class="p-grid">
    <section class="p-left" aria-label="总进度">${progressLeft(s, c)}</section>
    <section class="p-right" aria-label="各模型进度">
      <div class="lanes">${progressLanes(s, c)}</div>
      ${c.agent ? `<div class="agent" id="agentbox" data-k="${c.agent.k}" data-step="${c.agent.step}">
        <h3>${STEPS[c.agent.step][0]}</h3>
        <p class="dim">把提示词复制给你的 Agent（Claude、ChatGPT、Gemini 等），${STEPS[c.agent.step][1]}等它回复完，把回复整段粘贴到下面，前后多出来的文字没关系。</p>
        <div class="ag-bar"><button class="btn solid" type="button" id="agcopy">复制提示词</button><span class="faint small" id="agsize">正在准备提示词…</span></div>
        <textarea class="field mono ag-prompt" id="agprompt" readonly rows="5" aria-label="提示词"></textarea>
        <label class="lab"><span>Agent 的回复</span><textarea class="field ag-in" id="agentin" rows="7" placeholder="把 Agent 的回复粘贴到这里"></textarea></label>
        <p class="err" id="agerr" hidden></p>
        <div class="ag-bar"><button class="btn solid" type="button" id="agsend">提交结果</button><button class="btn ghost" type="button" id="agskip">${c.agent.step === 'outline' ? '跳过大纲，直接校对' : '跳过校对，保留识别结果'}</button></div>
      </div>` : `<div class="stream" aria-label="最新识别结果">
        ${stream ? `<h3>${esc(c.stream_name)} 最新几条</h3>${stream}` : '<p class="faint small">识别出的文字会在这里滚动显示。</p>'}
      </div>`}
    </section>
  </main>`;
  bindProgress(s);
  if (c.agent) bindAgent(c.agent);
}

function progressLeft(s, c) {
  const elapsed = c.elapsed || 0;
  const remain = c.remaining ?? null;
  const finished = !s.running;
  const on = (x) => (x ? '开启' : '关闭');
  return `
      <div><p class="where">${finished ? '已结束' : c.agent ? '等你粘贴 Agent 的结果' : s.paused ? '已暂停' : `正在转写第 ${(s.index || 0) + 1} 个，共 ${s.total || 1} 个`}</p><h1>${esc(c.name)}</h1></div>
      <div><div class="big">${Math.floor(c.pct)}<small>%</small></div></div>
      <div class="bar" aria-hidden="true"><i style="width:${Math.min(100, c.pct)}%"></i></div>
      <dl class="kv">
        ${finished ? '' : `<dt>已用时</dt><dd class="mono">${hms(elapsed)}</dd><dt>预计剩余</dt><dd class="mono">${c.agent ? '等你粘贴' : remain === null ? '估算中' : hms(remain)}</dd>${s.total > 1 ? `<dt>整个队列</dt><dd class="mono">${Math.floor(s.queue_pct || 0)}%</dd>` : ''}`}
        <dt>时长</dt><dd class="mono">${c.duration ? hms(c.duration) : '-'}</dd>
        <dt>语言</dt><dd>${LANGS[c.lang] || '-'}</dd>
        <dt>多模型对照</dt><dd>${on(c.compare)}</dd>
        <dt>AI 精度优化</dt><dd>${c.opt ? esc(proofName()) : '关闭'}</dd>
        <dt>区分说话人</dt><dd>${on(c.diarize)}</dd>
        ${c.device ? `<dt>运行设备</dt><dd>${esc(c.device)}</dd>` : ''}
        ${s.vram ? `<dt>显存</dt><dd class="mono">${s.vram}</dd>` : ''}
      </dl>
      ${s.error ? `<p class="err">${esc(s.error)}</p>` : ''}
      ${(c.warnings || []).map((w) => `<p class="warn">${esc(w)}</p>`).join('')}
      <div class="p-acts">
        ${finished
    ? (s.finished ? '<a class="btn solid" href="#review">进入校对</a>' : '<a class="btn" href="#new">返回新建</a>')
    : `<button class="btn" type="button" id="pause">${s.paused ? '继续' : '暂停'}</button><button class="btn ghost" type="button" id="cancel">取消</button>`}
      </div>`;
}

function progressLanes(s, c) {
  const N = 64;
  return c.lanes.map((l) => {
    const done = Math.floor(l.pct / 100 * N);
    let cells = '';
    for (let i = 0; i < N; i++) cells += `<span class="cell${i < done ? ' done' : (i === done && l.pct > 0 && l.pct < 100 && !s.paused && !c.agent ? ' now' : '')}"></span>`;
    return `<div class="lane${l.pct === 0 ? ' wait' : l.pct >= 100 ? ' fin' : ''}"><span>${esc(l.name)}</span><div class="cells" aria-hidden="true">${cells}</div><span class="s">${esc(l.state)}</span></div>`;
  }).join('');
}

function bindProgress(s) {
  const p = $('#pause');
  if (p) p.onclick = async () => { await api().pause(!s.paused); };
  const x = $('#cancel');
  if (x) x.onclick = async () => { await api().cancel(); };
}

async function bindAgent(a) {
  const r = await api().agent_prompt();
  const area = $('#agprompt');
  if (!r || !area || r.k !== a.k) return;
  area.value = r.prompt;
  $('#agsize').textContent = `约 ${r.prompt.length.toLocaleString()} 字。太长发不出去时，可以分两次发，或者换支持长文本的 Agent。`;
  $('#agcopy').onclick = async () => { toast(await copyText(r.prompt, area) ? '已复制提示词' : '复制失败，请在框里全选后手动复制'); };
  const err = $('#agerr');
  $('#agsend').onclick = async (e) => {
    const text = $('#agentin').value.trim();
    if (!text) { err.hidden = false; err.textContent = '先把 Agent 的回复粘贴进来'; return; }
    e.target.classList.add('busy');
    const res = await api().agent_submit(a.step, text);
    e.target.classList.remove('busy');
    if (res.error) { err.hidden = false; err.textContent = '没能识别：' + res.error; return; }
    err.hidden = true;
    toast(a.step === 'outline' ? '已收到大纲，Qwen 接着复听' : '已收到校对结果');
  };
  $('#agskip').onclick = async () => { await api().agent_skip(); };
}

/* ---------- 校对 ---------- */
const TAGS = { ok: '一致', keep: '已确认', fix: '已修正', ask: '存疑', raw: '初稿' };
const stc = (st) => 'st-' + (TAGS[st] ? st : 'raw');

function markDiff(a, b) {
  let i = 0;
  while (i < a.length && i < b.length && a[i] === b[i]) i++;
  let j = 0;
  while (j < a.length - i && j < b.length - i && a[a.length - 1 - j] === b[b.length - 1 - j]) j++;
  const mid = a.slice(i, a.length - j);
  return esc(a.slice(0, i)) + (mid ? `<mark>${esc(mid)}</mark>` : '') + esc(a.slice(a.length - j));
}

async function renderReview() {
  const jobs = await api().jobs();
  if (!S.job && jobs.length) await openJob((S.status && S.status.finished) || jobs[0].id);
  if (!S.job) {
    view.innerHTML = `<div class="empty"><h2>还没有转写结果</h2><p>转写完成后，这里左边是预览和各模型写法的对照，右边是字幕列表。改完可以直接导出。</p><div class="acts"><a class="btn solid" href="#new">新建转写</a></div></div>`;
    return;
  }
  const j = S.job;
  const isVideo = /\.(mp4|webm|m4v|mov)$/i.test(j.source);
  const base = `/${j.token}/media/${j.id}`;
  const options = jobs.map((x) => `<option value="${x.id}" ${x.id === j.id ? 'selected' : ''}>${esc(x.name)}</option>`).join('');
  view.innerHTML = `
  <main class="e-grid">
    <section class="e-left${isVideo ? '' : ' audio'}" aria-label="预览与对照">
      <div class="video">
        ${isVideo ? `<video id="media" src="${base}/source" preload="metadata"></video>` : `<audio id="media" src="${base}/audio" preload="metadata"></audio>`}
        <p class="sub" id="sub"></p>
      </div>
      <div class="trans">
        <button class="ico" type="button" aria-label="上一条" title="上一条" id="prev">${I.prev}</button>
        <button class="ico play" type="button" aria-label="播放" id="play">${I.play}</button>
        <button class="ico" type="button" aria-label="下一条" title="下一条" id="next">${I.next}</button>
        <span class="tc mono" id="clock">00:00:00,000</span><span class="of mono">/ ${hms(j.duration)}</span>
      </div>
      <div class="cmp" id="cmp"></div>
    </section>
    <section class="e-right" aria-label="字幕列表">
      <div class="e-bar">
        <select class="pick" id="jobsel" aria-label="切换转写结果">${options}</select>
        <div class="seg small" role="group" aria-label="筛选" id="flt"></div>
        <div class="sp"><select class="pick" id="linemax" aria-label="导出时每条字幕的字数" title="导出时一句太长就拆成几条字幕，在标点或停顿处拆开；列表里的竖线就是拆开的位置">${Object.entries(LINE_KINDS).map(([k, l]) => `<option value="${k}" ${(j.line_max || 'wide') === k ? 'selected' : ''}>${l}</option>`).join('')}</select><label class="check tags" title="导出的字幕去掉全部句号，逗号和日语顿号改成空格"><input type="checkbox" id="nopunct" ${j.no_punct ? 'checked' : ''}><span>去掉标点</span></label>${hasSpeakers() ? `<label class="check tags"><input type="checkbox" id="tags" ${j.speaker_tags ? 'checked' : ''}><span>字幕带说话人</span></label>` : ''}<button class="btn sm" type="button" id="fr" aria-expanded="false">${I.find}查找替换</button>${exportMenu(j.id)}</div>
      </div>
      <div class="frbar" id="frbar" hidden>
        <input class="field" id="f1" placeholder="查找" aria-label="查找"><input class="field" id="f2" placeholder="替换为" aria-label="替换为"><button class="btn sm" type="button" id="frgo">全部替换</button>
      </div>
      ${tipSeen('review') ? '' : `<div class="tip"><span>${Object.entries(TAGS).map(([k, l]) => `<span class="stt ${stc(k)}"><i class="dot"></i>${l}</span>`).join('')}　点一条，左边会列出各模型的写法，可以一键采用；先看<b>存疑</b>的就行。</span><button class="x" type="button" id="tiprev" aria-label="关掉提示">${I.x}</button></div>`}
      <div class="list" id="list"></div>
    </section>
  </main>
  <section class="wave" aria-label="时间轴">
    <div class="ov" id="ov" title="整段录音：彩色是已修正和存疑的位置，点击跳过去"></div>
    <div class="w-t mono" id="wt"></div>
    <canvas id="wc" height="52" aria-hidden="true"></canvas>
    <div class="w-s" id="ws"></div>
  </section>`;
  const m = $('#media');
  $('#prev').onclick = () => step(-1);
  $('#next').onclick = () => step(1);
  $('#play').onclick = () => { S.stopAt = null; m.paused ? m.play() : m.pause(); };
  const icon = () => { const b = $('#play'); b.innerHTML = m.paused ? I.play : I.pause; b.setAttribute('aria-label', m.paused ? '播放' : '暂停'); };
  m.onplay = icon; m.onpause = icon;
  m.ontimeupdate = () => {
    $('#clock').textContent = hms(m.currentTime, true);
    moveHead();
    if (S.stopAt != null && m.currentTime >= S.stopAt) { m.pause(); S.stopAt = null; }  // 播放这一条：到出点停
    const seg = j.segments.find((s) => m.currentTime >= s.a && m.currentTime < s.b);
    if (seg && seg.id !== S.sel && !m.paused) select(seg.id, false);
  };
  $('#jobsel').onchange = async (e) => { await openJob(e.target.value); renderReview(); };
  if ($('#tiprev')) $('#tiprev').onclick = (e) => { tipHide('review'); e.currentTarget.parentElement.remove(); };
  $('#linemax').onchange = async (e) => {
    // 换了每条字数：服务器按新字数重新拆，列表跟着变；选中的条目和播放位置尽量保持
    const m = $('#media'), pos = m ? m.currentTime : 0, at = current() ? current().a : 0;
    await api().set_line_max(j.id, e.target.value);
    await openJob(j.id);
    const hit = S.job.segments.find((s) => s.b > at + 0.01) || S.job.segments[0];
    if (hit) S.sel = hit.id;
    await renderReview();
    if ($('#media')) $('#media').currentTime = pos;
  };
  $('#nopunct').onchange = async (e) => { j.no_punct = e.target.checked; await api().set_no_punct(j.id, e.target.checked); };
  if ($('#tags')) $('#tags').onchange = async (e) => { j.speaker_tags = e.target.checked; await api().set_speaker_tags(j.id, e.target.checked); };
  $('#fr').onclick = () => { const b = $('#frbar'); b.hidden = !b.hidden; $('#fr').setAttribute('aria-expanded', !b.hidden); if (!b.hidden) $('#f1').focus(); };
  $('#frgo').onclick = async () => {
    const f = $('#f1').value;
    if (!f) return;
    const n = await api().replace_all(j.id, f, $('#f2').value);
    await openJob(j.id);
    drawList(); drawCmp(); drawWave();
    toast(n ? `替换了 ${n} 处` : '没有找到');
  };
  $('#ov').onclick = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    const t = (e.clientX - r.left) / r.width * total();
    const near = j.segments.reduce((a, s) => (Math.abs((s.a + s.b) / 2 - t) < Math.abs((a.a + a.b) / 2 - t) ? s : a), j.segments[0]);
    if (near) select(near.id, true);
  };
  bindExport($('.e-bar'));
  drawFilters(); drawList(); drawCmp(); drawWave();
}

async function openJob(id) {
  S.job = await api().job(id);
  if (!S.job.segments.some((s) => s.id === S.sel)) S.sel = S.job.segments[0]?.id ?? 1;
}

const hasSpeakers = () => Object.keys(S.job.speakers || {}).length > 0 || S.job.segments.some((s) => s.speaker);
const speakerName = (s) => (S.job.speakers || {})[s.speaker] || '';
const total = () => S.job.duration || (S.job.segments.length ? S.job.segments[S.job.segments.length - 1].b : 1);

function drawFilters() {
  const segs = S.job.segments;
  const n = (st) => segs.filter((s) => s.status === st).length;
  const items = [['all', '全部', segs.length]];
  if (n('ask')) items.push(['ask', '存疑', n('ask')]);
  if (n('fix')) items.push(['fix', '已修正', n('fix')]);
  if (!items.some(([id]) => id === S.flt)) S.flt = 'all';
  $('#flt').innerHTML = items.map(([id, l, c]) => `<button type="button" class="${S.flt === id ? 'on' : ''}" aria-pressed="${S.flt === id}" data-f="${id}">${l}<span class="n mono">${c}</span></button>`).join('');
  $$('[data-f]').forEach((b) => { b.onclick = () => { S.flt = b.dataset.f; drawFilters(); drawList(); }; });
}

/* 句子文字，导出时拆开的地方插一条竖线 */
function cutText(s) {
  const at = s.cut || [];
  if (!at.length) return esc(s.text);
  let out = '', p = 0;
  at.forEach((x) => { out += esc(s.text.slice(p, x)) + '<i class="cut" title="导出时在这里拆成两条字幕"></i>'; p = x; });
  return out + esc(s.text.slice(p));
}

async function refreshCuts() {
  const m = await api().cuts(S.job.id);
  S.job.segments.forEach((s) => { s.cut = m[s.id] || []; });
  drawList();
}

function drawList() {
  const rows = S.job.segments.filter((s) => S.flt === 'all' || s.status === S.flt);
  $('#list').innerHTML = rows.map((s) => `<button type="button" class="row ${stc(s.status)}${s.id === S.sel ? ' sel' : ''}" data-id="${s.id}">
      <span class="ix mono">${s.id}</span>
      <span class="tc mono">${hms(s.a, true)}<br>${hms(s.b, true)}</span>
      <span class="tx" lang="${langAttr(s.lang)}">${hasSpeakers() ? `<small class="spk">${esc(speakerName(s) || '未标注')}</small>` : ''}${cutText(s)}</span>
      <span class="stt"><i class="dot"></i>${TAGS[s.status] || '初稿'}</span></button>`).join('')
    || '<p class="none-hit">这一类已经清空了。</p>';
  $$('.row').forEach((r) => { r.onclick = () => select(+r.dataset.id, true); });
}

function current() { return S.job.segments.find((s) => s.id === S.sel) || S.job.segments[0]; }

function drawCmp() {
  const s = current();
  if (!s) { $('#cmp').innerHTML = '<p class="faint small">这份结果没有字幕条目。</p>'; return; }
  $('#sub').textContent = s.text;
  $('#sub').lang = langAttr(s.lang);
  const cands = [];
  const missed = !s.qwen;  // Qwen 没识别出、由其他模型补上的一条
  const backups = [['mimo', 'MiMo'], ['whisper', 'Whisper'], ['parakeet', 'Parakeet']].filter(([k]) => s[k]);
  if (s.alt) backups.push(['alt', s.alt_name]);  // 支持多个备用模型之前的旧任务
  const alts = s.alts || [];  // 切块交界处时间对不齐、又听写了一遍的文字，常带着前后句的一部分
  if (backups.length || s.qwen2 || s.moss || missed || alts.length) {
    const base = missed ? s.text : s.qwen;  // 和 Qwen 比；Qwen 没有结果时和默认采用的写法比
    cands.push(['Qwen3-ASR', s.qwen, backups.length ? s[backups[0][0]] : s.qwen2 || s.moss || '']);
    if (s.qwen2) cands.push(['Qwen 复核', s.qwen2, base]);
    for (const [k, name] of backups) cands.push([name, s[k], base]);
    if (s.moss) cands.push(['MOSS', s.moss, base]);
    const names = { qwen2: 'Qwen 复核', moss: 'MOSS', mimo: 'MiMo', whisper: 'Whisper', parakeet: 'Parakeet' };
    for (const a of alts) cands.push([`${names[a.field] || a.field} 另一遍`, a.text, base, true]);
  }
  const rows = cands.map(([who, t, other, alt]) => {
    if (!t) return `<div class="cd"><span class="who">${esc(who)}</span><span class="txt faint">没有结果</span></div>`;
    const ad = t === s.text;
    return `<div class="cd${ad ? ' ad' : ''}${alt ? ' alt' : ''}"><span class="who"${alt ? ' title="切块交界处时间没对齐，这个模型把这一段又听写了一遍，常带着前后句的一部分"' : ''}>${esc(who)}</span><span class="txt" lang="${langAttr(s.lang)}">${markDiff(t, other)}</span><button class="btn sm${ad ? ' ghost' : ''}" type="button" data-adopt="${esc(t)}" ${ad ? 'disabled' : ''}>${ad ? '已采用' : '采用'}</button></div>`;
  }).join('');
  $('#cmp').innerHTML = `
    <div class="cmp-h"><h2>第 ${s.id} 条<span class="stt ${stc(s.status)}"><i class="dot"></i>${TAGS[s.status] || '初稿'}</span></h2>${hasSpeakers() ? speakerBar(s) : ''}</div>
    ${s.why ? `<p class="why${s.status === 'fix' ? ' fix' : ''}">${esc(s.why)}</p>` : ''}
    ${rows ? `<div class="cands">${rows}</div>` : ''}
    <label class="final"><span>最终字幕</span><textarea class="field" id="final" rows="2" lang="${langAttr(s.lang)}">${esc(s.text)}</textarea></label>
    <div class="tim">
      ${[['a', '入点'], ['b', '出点']].map(([k, l]) => `<div class="tim-r"><span>${l}</span><input class="field mono" data-tc="${k}" value="${hms(s[k], true)}" aria-label="${l}"><button class="btn sm" type="button" data-nudge="${k}" data-d="-0.1" aria-label="${l}提前 0.1 秒">−0.1</button><button class="btn sm" type="button" data-nudge="${k}" data-d="0.1" aria-label="${l}推后 0.1 秒">+0.1</button><button class="btn sm ghost" type="button" data-here="${k}" title="设为当前播放位置">设为播放位置</button></div>`).join('')}
    </div>
    <div class="tim-acts"><span class="dur mono">时长 ${(s.b - s.a).toFixed(2)} 秒</span><button class="btn sm" type="button" id="playseg">${I.play}播放这一条</button>${s.status === 'ask' ? '<button class="btn sm" type="button" id="ok" title="文字没问题，不用改">标为已确认</button>' : ''}<button class="btn sm ghost danger" type="button" id="del">${I.trash}删除这条</button></div>`;
  $$('[data-adopt]').forEach((b) => { b.onclick = () => setText(b.dataset.adopt); });
  $('#final').onchange = (e) => setText(e.target.value);
  const m = $('#media');
  $$('[data-tc]').forEach((i) => {
    i.onchange = () => {
      const t = parseTc(i.value);
      if (t == null) { toast('时间格式不对，例如 00:01:02,345'); drawCmp(); return; }
      setTimes(i.dataset.tc, t);
    };
  });
  $$('[data-nudge]').forEach((b) => { b.onclick = () => setTimes(b.dataset.nudge, s[b.dataset.nudge] + Number(b.dataset.d)); });
  $$('[data-here]').forEach((b) => { b.onclick = () => setTimes(b.dataset.here, m.currentTime); });
  $('#playseg').onclick = () => { m.currentTime = s.a; S.stopAt = s.b; m.play(); };
  $('#del').onclick = deleteSeg;
  if ($('#ok')) $('#ok').onclick = confirmSeg;
  if (!hasSpeakers()) return;
  $('#spk').onchange = async (e) => {
    await api().set_speaker(S.job.id, s.id, e.target.value);
    await reloadSpeakers();
  };
  const name = $('#spkname');
  if (name) name.onchange = async (e) => {
    await api().rename_speaker(S.job.id, s.speaker, e.target.value);
    await reloadSpeakers();
  };
}

function speakerBar(s) {
  const opts = Object.entries(S.job.speakers || {}).map(([id, n]) => `<option value="${esc(id)}" ${id === s.speaker ? 'selected' : ''}>${esc(n)}</option>`).join('');
  return `<div class="spkbar"><select class="pick" id="spk" aria-label="这一条的说话人"><option value="" ${s.speaker ? '' : 'selected'}>未标注</option>${opts}<option value="+">新说话人</option></select>${s.speaker ? `<input class="field spkn" id="spkname" value="${esc(speakerName(s))}" aria-label="给这位说话人改名" title="改成已有的名字会把两人合并">` : ''}</div>`;
}

async function reloadSpeakers() {
  await openJob(S.job.id);
  drawList(); drawCmp();
}

/* 改入点或出点；服务器会把时间夹在前后两条之间，以返回的为准 */
async function setTimes(k, t, s = current()) {
  const want = k === 'a' ? [t, s.b] : [s.a, t];
  const got = await api().set_times(S.job.id, s.id, want[0], want[1]);
  if (Math.abs(got[k] - t) > 0.0005) toast(k === 'a' ? '入点不能早于上一条的出点，也不能晚于出点' : '出点不能晚于下一条的入点，也不能早于入点');
  s.a = got.a; s.b = got.b;
  const m = $('#media');
  if (m && k === 'a') m.currentTime = s.a;
  drawList(); drawCmp(); drawWave();
}

async function deleteSeg() {
  const segs = S.job.segments;
  const i = segs.findIndex((x) => x.id === S.sel);
  if (i < 0) return;
  const r = await api().delete_segment(S.job.id, S.sel);
  segs.splice(i, 1);
  S.sel = (segs[i] || segs[i - 1] || {}).id;
  drawFilters(); drawList(); drawCmp(); drawWave();
  toast(`已删除第 ${r.seg.id} 条`, { label: '撤销', fn: async () => {
    await api().restore_segment(S.job.id, r.seg, r.index);
    await openJob(S.job.id);
    S.sel = r.seg.id;
    drawFilters(); drawList(); drawCmp(); drawWave();
  } });
}

async function setText(t) {
  const s = current();
  s.text = t;
  const st = await api().update_text(S.job.id, s.id, t);
  if (st) s.status = st;  // 存疑的条目改过以后变成已确认
  drawFilters(); drawList(); drawCmp(); drawWave();
  refreshCuts();
}

async function confirmSeg() {
  const s = current();
  await api().confirm_segment(S.job.id, s.id);
  s.status = 'keep';
  drawFilters(); drawList(); drawCmp(); drawWave();
}

function select(id, seek) {
  S.sel = id;
  const s = current();
  const m = $('#media');
  if (seek && m) m.currentTime = s.a;
  drawList(); drawCmp(); drawWave();
  const r = $(`.row[data-id="${id}"]`);
  if (r) r.scrollIntoView({ block: 'nearest' });
}

function step(d) {
  const segs = S.job.segments;
  const i = segs.findIndex((s) => s.id === S.sel);
  const n = segs[Math.min(segs.length - 1, Math.max(0, i + d))];
  if (n) select(n.id, true);
}

const SPAN = 32;
function windowStart() {
  const s = current();
  return Math.max(0, (s ? s.a : 0) - 8);
}

function drawWave() {
  const c = $('#wc');
  if (!c) return;
  const w = c.clientWidth || 1000;
  c.width = w;
  const g = c.getContext('2d');
  const cs = getComputedStyle(document.documentElement);
  const t0 = windowStart();
  const s = current();
  const peaks = S.job.peaks || [];
  const H = 52;
  g.clearRect(0, 0, w, H);
  for (let x = 0; x < w; x += 3) {
    const t = t0 + x / w * SPAN;
    const p = peaks[Math.floor(t * 20)] || 0;
    const h = Math.max(1, p / 100 * (H - 4));
    g.fillStyle = s && t >= s.a && t <= s.b ? cs.getPropertyValue('--fg') : cs.getPropertyValue('--line2');
    g.fillRect(x, H / 2 - h / 2, 2, h);
  }
  $('#wt').innerHTML = [0, 8, 16, 24, 32].map((d) => `<span>${hms(t0 + d)}</span>`).join('');
  const vis = S.job.segments.filter((x) => x.b > t0 && x.a < t0 + SPAN);
  $('#ws').innerHTML = vis.map((x) => `<button type="button" class="blk ${stc(x.status)}${x.id === S.sel ? ' sel' : ''}" style="left:${(x.a - t0) / SPAN * 100}%;width:${(x.b - x.a) / SPAN * 100}%" data-b="${x.id}" aria-label="跳到第 ${x.id} 条" title="${esc(x.text)}">${x.id}<i class="eg l" title="拖动改入点"></i><i class="eg r" title="拖动改出点"></i></button>`).join('') + '<span class="head" id="head"></span>';
  $$('[data-b]').forEach((b) => { b.onclick = () => select(+b.dataset.b, true); });
  bindEdges();
  // 总览：整段录音里已修正和存疑的位置，加一个框标出下面波形显示的范围
  const T = total();
  $('#ov').innerHTML = S.job.segments.filter((x) => x.status === 'ask' || x.status === 'fix').map((x) => `<i class="${stc(x.status)}" style="left:${x.a / T * 100}%;width:${(x.b - x.a) / T * 100}%"></i>`).join('')
    + `<span class="vw" style="left:${t0 / T * 100}%;width:${Math.min(100, SPAN / T * 100)}%"></span>`;
  moveHead();
}

/* 拖时间条的左右边缘改入点、出点：拖动时只挪方块，松手再保存 */
function bindEdges() {
  $$('#ws .eg').forEach((h) => {
    h.onclick = (e) => e.stopPropagation();  // 拖完松手不算点击方块
    h.onpointerdown = (e) => {
      e.preventDefault();
      e.stopPropagation();
      const blk = h.parentElement, k = h.classList.contains('l') ? 'a' : 'b';
      const segs = S.job.segments, i = segs.findIndex((x) => x.id === +blk.dataset.b), s = segs[i];
      const prev = segs[i - 1], next = segs[i + 1];
      // 和相邻一条首尾相连时，交界处一起移动
      const mate = k === 'a' ? (prev && s.a - prev.b < 0.02 ? prev : null) : (next && next.a - s.b < 0.02 ? next : null);
      const mk = k === 'a' ? 'b' : 'a';
      const lo = k === 'a' ? (mate ? mate.a + 0.2 : prev ? prev.b : 0) : s.a + 0.2;
      const hi = k === 'a' ? s.b - 0.2 : (mate ? mate.b - 0.2 : next ? next.a : total());
      const mblk = mate && $(`#ws [data-b="${mate.id}"]`);
      const box = $('#ws').getBoundingClientRect(), t0 = windowStart();
      const place = (el, a, b) => { el.style.left = (a - t0) / SPAN * 100 + '%'; el.style.width = (b - a) / SPAN * 100 + '%'; };
      let t = s[k];
      h.setPointerCapture(e.pointerId);
      h.onpointermove = (ev) => {
        t = Math.min(hi, Math.max(lo, t0 + (ev.clientX - box.left) / box.width * SPAN));
        place(blk, k === 'a' ? t : s.a, k === 'b' ? t : s.b);
        if (mblk) place(mblk, mk === 'a' ? t : mate.a, mk === 'b' ? t : mate.b);
      };
      h.onpointerup = h.onpointercancel = async () => {
        h.onpointermove = h.onpointerup = h.onpointercancel = null;
        if (Math.abs(t - s[k]) < 0.005) return;
        t = Math.round(t * 1000) / 1000;
        S.sel = s.id;
        // 先让出位置的那条先改，免得服务器按旧的相邻时间把另一条夹住
        const shrink = (k === 'a') === (t > s.a);
        if (mate && !shrink) await setTimes(mk, t, mate);
        await setTimes(k, t, s);
        if (mate && shrink) await setTimes(mk, t, mate);
      };
    };
  });
}

function moveHead() {
  const h = $('#head'), m = $('#media');
  if (!h || !m) return;
  const p = (m.currentTime - windowStart()) / SPAN * 100;
  h.style.left = p + '%';
  h.style.display = p < 0 || p > 100 ? 'none' : '';
}

/* ---------- 历史 ---------- */
async function renderHistory() {
  const jobs = S.history = await api().jobs();
  if (!jobs.length) {
    view.innerHTML = `<div class="empty"><h2>还没有识别记录</h2><p>每次转写的结果都会留在这里，可以随时重新打开校对，或者换一种格式再导出。</p><div class="acts"><a class="btn solid" href="#new">新建转写</a></div></div>`;
    return;
  }
  view.innerHTML = `
  <header class="pagehead"><h1>历史记录</h1><span class="faint small" id="hn"></span></header>
  <div class="h-tools">
    <input class="field" id="hq" type="search" placeholder="按文件名查找" aria-label="按文件名查找" value="${esc(S.hq)}">
    <span class="faint small">删除记录只删程序里保存的识别结果，不动源文件和已经导出的字幕。</span>
  </div>
  <div class="h-list" id="hlist"></div>`;
  $('#hq').oninput = (e) => { S.hq = e.target.value; drawHistory(); };
  drawHistory();
}

function drawHistory() {
  const q = S.hq.trim().toLowerCase();
  const rows = S.history.filter((j) => !q || j.name.toLowerCase().includes(q));
  $('#hn').textContent = q ? `${rows.length} / ${S.history.length} 个` : `${S.history.length} 个`;
  const live = S.status && S.status.running && S.status.current ? S.status.current.id : null;
  const chips = (j) => [LANGS[j.lang], j.compare && '对照', j.opt && 'AI 优化', j.speakers && '说话人'].filter(Boolean).map((t) => `<span class="chip">${t}</span>`).join('');
  const acts = (j) => {
    if (j.id === live) return '<span class="undone">正在转写</span>';
    const usable = j.count > 0;
    return `${usable ? `<button class="btn sm" type="button" data-open="${j.id}">打开校对</button>${exportMenu(j.id)}` : ''}
      <button class="btn sm ghost" type="button" data-reveal="${esc(j.source)}" title="在资源管理器里显示源文件" aria-label="打开源文件所在文件夹">${I.folder}</button>
      <button class="btn sm ghost danger" type="button" data-del="${j.id}" title="删除这条记录" aria-label="删除这条记录">${I.trash}</button>`;
  };
  $('#hlist').innerHTML = `<div class="h-row h-head"><span>文件</span><span>识别时间</span><span>时长</span><span>处理</span><span>条目</span><span></span></div>`
    + rows.map((j) => `<div class="h-row${S.job && S.job.id === j.id ? ' cur' : ''}">
      <div class="h-name"><b title="${esc(j.name)}">${esc(j.name)}</b><span title="${esc(j.source)}">${esc(j.source)}</span></div>
      <span class="h-when mono small dim">${when(j.created)}</span>
      <span class="h-dur mono small dim">${hms(j.duration)}</span>
      <div class="h-chips">${chips(j)}</div>
      <div class="h-count">${j.done || j.id === live ? `<span>${j.count} 条</span>` : '<span class="undone">没有做完</span>'}${j.ask ? `<span class="a st-ask"><i class="dot"></i>存疑 ${j.ask}</span>` : ''}${j.fix ? `<span class="f st-fix"><i class="dot"></i>已修正 ${j.fix}</span>` : ''}</div>
      <div class="h-acts">${acts(j)}</div>
    </div>`).join('')
    + (rows.length ? '' : '<p class="none-hit" style="padding:28px 32px">没有文件名包含这个词的记录。</p>');
  $$('[data-open]').forEach((b) => { b.onclick = async () => { await openJob(b.dataset.open); S.flt = 'all'; location.hash = '#review'; }; });
  $$('[data-reveal]').forEach((b) => { b.onclick = async () => { const r = await api().reveal(b.dataset.reveal); if (r && r.error) toast(r.error); }; });
  // 删除要点两次：第一次按钮变红提示，4 秒内再点才删
  $$('[data-del]').forEach((b) => {
    b.onclick = async () => {
      if (!b.classList.contains('armed')) {
        b.classList.add('armed');
        b.innerHTML = '确认删除';
        setTimeout(() => { if (b.isConnected) { b.classList.remove('armed'); b.innerHTML = I.trash; } }, 4000);
        return;
      }
      const id = b.dataset.del;
      const r = await api().delete_job(id);
      if (r.error) return toast(r.error);
      if (S.job && S.job.id === id) S.job = null;
      S.history = S.history.filter((j) => j.id !== id);
      toast('已删除这条记录');
      S.history.length ? drawHistory() : renderHistory();
    };
  });
  bindExport($('#hlist'));
}

/* ---------- 设置 ---------- */
async function renderSettings() {
  const c = S.cfg = await api().config();
  const ok = await api().check_models();
  const ms = await api().models();
  const srcs = await api().sources();
  const fetchRow = (m) => m.installed ? '' : `<div class="mget" data-get="${m.name}"><select class="field" data-src="${m.name}" aria-label="${esc(m.label)} 下载来源">${srcs.map((s) => `<option value="${s.key}" ${s.key === (S.src || 'hf') ? 'selected' : ''}>${esc(s.label)}</option>`).join('')}</select><button class="btn" type="button" data-dl="${m.name}" data-label="下载（${bytes(m.size)}）">下载（${bytes(m.size)}）</button><span class="dlst" data-dlst="${m.name}"></span></div>`;
  const LANG = { zh: '中文', ja: '日语' };
  const files = (m) => m.files.map((f) => `<div class="mrow"><span>${m.files.length > 1 ? esc(f.key.replace(/^\w+_/, '')) : '文件'}</span><input class="field mono" data-cfg="${f.key}" value="${esc(f.path)}" aria-label="${esc(m.label)} 路径"><span class="ok${f.ok ? '' : ' bad'}">${f.ok ? '就绪' : '找不到文件'}</span></div>`).join('');
  const card = (m) => `<div class="mcard"><div class="mhead"><b>${esc(m.label)}</b><span class="state${m.installed ? '' : ' no'}"><i></i>${m.installed ? '已安装' : '未安装'}</span></div><p class="desc">${esc(m.desc)}</p>${fetchRow(m)}${m.role === 'backup' ? `<div class="muse">${m.langs.map((l) => `<label class="check"><input type="checkbox" data-bk="${m.name}" data-lang="${l}" ${m.use.includes(l) ? 'checked' : ''}><span>用于${LANG[l]}</span></label>`).join('')}${m.installed ? '' : '<span class="faint">未安装时不会使用</span>'}</div>` : ''}${files(m)}</div>`;
  const group = (role) => ms.filter((m) => m.role === role).map(card).join('');
  view.innerHTML = `
  <main class="s-grid">
    <nav class="s-nav" aria-label="设置分类"><a href="#settings" data-j="api">校对 AI</a><a href="#settings" data-j="models">本地模型</a><a href="#settings" data-j="runtime">运行环境</a><a href="#settings" data-j="terms">校对术语</a></nav>
    <div class="s-main" id="smain">
      <section class="sec" id="api">
        <div><h2>校对 AI</h2><p class="desc">开启 AI 精度优化时用来写大纲和逐句校对。只发送识别出的文字，音视频不会上传。</p></div>
        <div class="stack">
          <div class="lab"><span>校对方式</span><div class="seg" role="group" aria-label="校对方式">
            <button type="button" class="${agentMode() ? '' : 'on'}" data-pm="api">服务商接口（API 密钥）</button><button type="button" class="${agentMode() ? 'on' : ''}" data-pm="agent">复制给 Agent（订阅制）</button>
          </div></div>
          ${agentMode() ? `<p class="desc">适合已经订阅了 Claude、ChatGPT、Gemini 等、但没有 API 密钥的情况。跑到 AI 精度优化时转写会暂停，进度页给出提示词：复制给 Agent，等它回复完，把回复整段粘贴回来，程序会自动识别。每个文件要复制粘贴两次（先写大纲，再逐句校对），也可以跳过其中一步。</p>` : `
          <div class="two">
            <label class="lab"><span>服务商</span><select class="field" id="prov">${c.providers.map((p) => `<option value="${p.key}" ${p.key === c.proof_provider ? 'selected' : ''}>${esc(p.label)}${p.has_key ? '（已保存密钥）' : ''}</option>`).join('')}</select></label>
            <label class="lab"><span>接口地址（OpenAI 兼容格式）</span><input class="field mono" data-cfg="proof_url" value="${esc(c.proof_url)}" placeholder="https://…/v1"></label>
          </div>
          <div class="lab"><span>API 密钥</span>
            <div class="key">
              <input class="field mono" id="key" type="password" autocomplete="off" style="flex:1;min-width:220px" placeholder="${c.has_key ? '已保存，粘贴新密钥可替换' : '粘贴 API 密钥'}" aria-label="API 密钥">
              <button class="btn" type="button" id="ksave">保存</button>
              ${c.has_key ? '<button class="btn ghost danger" type="button" id="kdel">删除</button>' : ''}
            </div>
          </div>
          <p class="lock">${I.lock}<span>${c.has_key ? '密钥已用 Windows DPAPI 加密保存，只有当前 Windows 账户能解开。每个服务商的密钥分开保存。' : '保存后用 Windows DPAPI 加密，只有当前 Windows 账户能解开。'}</span></p>
          <div class="lab"><span>模型</span>
            <div class="key"><select class="field mono" id="pmodel" style="flex:1;min-width:220px" ${c.has_key ? '' : 'disabled'}><option>${c.has_key ? '正在获取模型列表…' : '保存密钥后从接口获取'}</option></select><button class="btn" type="button" id="mrefresh" ${c.has_key ? '' : 'disabled'}>刷新列表</button></div>
            <span class="faint small" id="mstat"></span>
          </div>`}
        </div>
      </section>
      <section class="sec" id="models">
        <div><h2>本地模型</h2><p class="desc">Qwen 是主识别模型。开启多模型对照时，勾选的备用模型各识别一遍，校对页逐句列出；同时开启 AI 精度优化，校对 AI 会按多数一致来校对。备用模型越多越准，也越慢。改了路径后重新打开这一页检查。</p></div>
        <div class="stack">
          <h3>必装</h3>${group('main')}
          <h3>区分说话人</h3>${group('diarize')}
          <h3>备用模型</h3>${group('backup')}
        </div>
      </section>
      <section class="sec" id="runtime">
        <div><h2>运行环境</h2><p class="desc">「自动」按显卡在已安装的运行库里挑：NVIDIA 显卡先试 CUDA 13、CUDA 12.6，其次 Vulkan；AMD、Intel 显卡用 Vulkan；都用不了时用 CPU。用不上显卡、显存不足时自动换下一个。手动指定的运行库用不了时，任务会停下并说明原因。</p></div>
        <div class="stack">
          <div class="lab"><span>运行库</span><div class="seg" role="group" aria-label="运行库" style="align-self:flex-start;flex-wrap:wrap">
            ${['auto', ...RT_KEYS].map((k) => `<button type="button" class="${c.gpu_backend === k ? 'on' : ''}" data-back="${k}">${k === 'auto' ? '自动' : RT_SHORT[k]}</button>`).join('')}
          </div></div>
          ${(c.gpu_backend === 'auto' ? RT_KEYS.filter((k) => ok.runtimes[k]) : [c.gpu_backend]).map((k) => `<label class="lab"><span>${RT_SHORT[k]} 运行库目录${ok.runtimes[k] ? '' : '（找不到 crispasr.exe）'}</span><input class="field mono" data-cfg="runtime_${k}" value="${esc(c['runtime_' + k])}"></label>`).join('')}
          ${c.gpu_backend === 'auto' && !ok.runtime ? '<p class="err">一个运行库都没有安装。</p>' : ''}
          <label class="lab"><span>显卡编号（有多块显卡时）</span><input class="field mono" data-cfg="gpu_device" value="${esc(String(c.gpu_device ?? 'auto'))}" style="width:120px" title="auto 表示选显存最大的一块；也可以填检测结果里的编号"></label>
          <div><button class="btn sm" type="button" id="rtcheck">检测运行环境</button></div>
          <div id="rtres" class="small"></div>
          <label class="lab"><span>FFmpeg</span><input class="field mono" data-cfg="ffmpeg" value="${esc(c.ffmpeg)}"></label>
          <div><a class="btn sm" href="#setup">打开安装引导，下载运行库</a></div>
          <p class="faint small">字幕对照组 ${esc(c.version)}</p>
        </div>
      </section>
      <section class="sec" id="terms">
        <div><h2>校对术语</h2><p class="desc">人名、地名等固定写法，每行一条。Qwen 识别时作为提示，AI 校对时也会参考。</p></div>
        <label class="lab"><span>术语表</span><textarea class="field" data-cfg="glossary" rows="6" placeholder="例如：&#10;星月&#10;麦当劳">${esc(c.glossary)}</textarea></label>
      </section>
    </div>
  </main>`;
  $$('[data-j]').forEach((a) => { a.onclick = (e) => { e.preventDefault(); $('#' + a.dataset.j).scrollIntoView({ behavior: 'smooth' }); }; });
  // 左边分类跟着滚动位置高亮
  const spy = () => {
    const top = $('#smain').getBoundingClientRect().top + 40;
    let cur = 'api';
    for (const sec of $$('.sec')) if (sec.getBoundingClientRect().top <= top) cur = sec.id;
    $$('[data-j]').forEach((a) => a.classList.toggle('cur', a.dataset.j === cur));
  };
  $('#smain').onscroll = spy; spy();
  $$('[data-cfg]').forEach((i) => { i.onchange = async () => { await api().save_config({ [i.dataset.cfg]: i.value }); toast('已保存'); }; });
  $$('[data-src]').forEach((s) => { s.onchange = () => { S.src = s.value; }; });
  watchDownloads();
  $$('[data-bk]').forEach((i) => { i.onchange = async () => { await api().set_backup(i.dataset.bk, i.dataset.lang, i.checked); S.cfg = await api().config(); }; });
  $('#rtcheck').onclick = async () => {
    const box = $('#rtres');
    box.textContent = '正在检测，每个运行库要几秒……';
    const r = await api().runtime_check();
    box.innerHTML = r.rows.map((x) => `<div><b>${esc(x.label)}</b>：${x.ok ? esc(x.state) : `<span class="faint">${esc(x.state)}</span>`}${x.devices && x.devices.length ? `<br><span class="faint mono">${x.devices.map(esc).join('<br>')}</span>` : ''}</div>`).join('')
      + `<p style="margin-top:8px">按现在的设置会用：<b>${esc(r.chosen)}</b></p>`;
  };
  $$('[data-back]').forEach((b) => { b.onclick = async () => { await api().save_config({ gpu_backend: b.dataset.back }); showSys(b.dataset.back); renderSettings(); }; });
  $$('[data-pm]').forEach((b) => { b.onclick = async () => { await api().save_config({ proof_mode: b.dataset.pm }); renderSettings(); }; });
  if (agentMode()) return;
  $('#prov').onchange = async (e) => { await api().set_provider(e.target.value); renderSettings(); };
  $('#ksave').onclick = async () => {
    const v = $('#key').value.trim();
    if (!v) return toast('请先粘贴密钥');
    await api().set_key(v);
    toast('密钥已加密保存');
    renderSettings();
  };
  const del = $('#kdel');
  if (del) del.onclick = async () => { await api().delete_key(); toast('密钥已删除'); renderSettings(); };
  if (c.has_key) loadModels(c);
  $('#mrefresh').onclick = () => loadModels(c);
}

/* 从接口拉模型列表填进下拉框；拉不到时改成手动填写 */
async function loadModels(c) {
  const sel = $('#pmodel'), st = $('#mstat');
  if (!sel) return;
  st.textContent = '正在连接…';
  const r = await api().list_models();
  if (!$('#pmodel')) return;
  if (r.error) {
    st.textContent = '获取模型列表失败：' + r.error + '。可以检查密钥和接口地址后点刷新，或者直接手动填写模型名。';
    const input = document.createElement('input');
    input.className = 'field mono'; input.id = 'pmodel'; input.style.cssText = 'flex:1;min-width:220px';
    input.value = c.proof_model; input.setAttribute('aria-label', '模型名');
    input.onchange = async () => { await api().save_config({ proof_model: input.value.trim() }); toast('已保存'); };
    sel.replaceWith(input);
    return;
  }
  const list = r.models.includes(c.proof_model) || !c.proof_model ? r.models : [c.proof_model, ...r.models];
  sel.innerHTML = list.map((m) => `<option value="${esc(m)}" ${m === c.proof_model ? 'selected' : ''}>${esc(m)}${r.models.includes(m) ? '' : '（当前设置，接口列表里没有）'}</option>`).join('');
  sel.disabled = false;
  st.textContent = `连接正常，共 ${r.models.length} 个模型。`;
  if (!c.proof_model || !r.models.includes(c.proof_model)) st.textContent += '当前选的模型不在列表里，请重新选一个。';
  sel.onchange = async () => { await api().save_config({ proof_model: sel.value }); c.proof_model = sel.value; S.cfg.proof_model = sel.value; toast('已保存'); };
}

/* ---------- 首次运行引导：选运行库，下载运行库和必装模型 ---------- */
async function renderSetup() {
  const info = S.setup = await api().setup_info();
  const srcs = await api().sources();
  const g = info.gpu;
  if (!S.pick) S.pick = (info.runtimes.find((r) => r.installed && r.key === info.recommend) || info.runtimes.find((r) => r.installed) || { key: info.recommend }).key;
  const gpuText = (g
    ? `检测到 ${esc(g.name)}，驱动版本 ${esc(g.driver)}。` + (g.cuda13 ? '推荐 CUDA 13。' : g.cuda12 ? '驱动低于 580，用不了 CUDA 13，推荐 CUDA 12.6。' : '驱动太老，CUDA 用不了：推荐 Vulkan，或者先到 NVIDIA 官网更新驱动。')
    : '没有检测到 NVIDIA 显卡。AMD、Intel 显卡和核显用 Vulkan，没有显卡的电脑用 CPU' + (info.avx2 ? '' : '（这台电脑不支持 AVX2，用 CPU Legacy）') + '。')
    + '运行时会自动检查选中的运行库能不能用上显卡，用不了时自动换另一个已安装的运行库。';
  const rt = info.runtimes.map((r) => `<label class="rt check mcard"><input type="radio" name="rt" value="${r.key}" ${S.pick === r.key ? 'checked' : ''}><b>${esc(r.label)}${r.key === info.recommend ? '（推荐）' : ''}　<span class="faint mono small">${r.installed ? '已安装' : bytes(r.size)}</span></b><p>${esc(r.desc)}</p></label>`).join('');
  const mods = info.models.map((m) => `<div class="mrow" style="grid-template-columns:minmax(0,1fr) 90px"><span>${esc(m.label)}</span><span class="faint mono small" style="text-align:right">${m.installed ? '已安装' : bytes(m.size)}</span></div>`).join('');
  view.innerHTML = `
  <header class="pagehead"><h1>第一次使用</h1><span class="faint small">识别在本机显卡上运行，需要先下载语音识别运行库和两个必装模型，文件放在 %LOCALAPPDATA%\\Tingxie。</span></header>
  <div class="s-main" style="flex:1">
    <section class="sec">
      <div><h2>1. 运行库</h2><p class="desc">${gpuText}</p></div>
      <div class="stack">${rt}</div>
    </section>
    <section class="sec">
      <div><h2>2. 必装模型</h2><p class="desc">备用模型和说话人模型以后可以在设置里再下。</p></div>
      <div class="stack"><div>${mods}</div>
        <label class="lab"><span>模型下载来源（失败时自动换下一个）</span><select class="field" id="msrc" style="width:auto;min-width:260px">${srcs.map((s) => `<option value="${s.key}" ${s.key === (S.src || 'hf') ? 'selected' : ''}>${esc(s.label)}</option>`).join('')}</select></label>
      </div>
    </section>
    ${info.ffmpeg ? '' : '<section class="sec"><div><h2>FFmpeg</h2></div><p class="err">找不到 FFmpeg。请安装 FFmpeg，或在设置里填写 ffmpeg.exe 的路径。</p></section>'}
    <section class="sec">
      <div><h2>3. 下载</h2><p class="desc">中途关掉程序也没关系，下次再点下载会接着下。</p></div>
      <div class="stack">
        <div id="setst" class="setst"></div>
        <div style="display:flex;gap:10px;flex-wrap:wrap"><button class="btn solid" type="button" id="setgo">开始下载</button><a class="btn ghost" href="#settings">跳过，在设置里自己填路径</a></div>
      </div>
    </section>
  </div>`;
  $$('[name="rt"]').forEach((r) => { r.onchange = () => { S.pick = r.value; renderSetup(); }; });
  $('#msrc').onchange = (e) => { S.src = e.target.value; };
  $('#setgo').onclick = async () => {
    if (setupReady()) { S.firstRun = true; S.mode = 'fast'; location.hash = '#new'; return; }
    await api().save_config({ gpu_backend: 'auto' });  // 下载哪个由用户选，跑的时候自动在已安装的里挑
    showSys('auto');
    if (!pickedRuntime().installed) await api().download_model('runtime_' + S.pick, 'official');
    for (const m of S.setup.models) if (!m.installed) await api().download_model(m.name, $('#msrc').value);
    watchSetup();
  };
  watchSetup();
}

function pickedRuntime() { return S.setup.runtimes.find((r) => r.key === S.pick); }
function setupReady() { return pickedRuntime().installed && S.setup.models.every((m) => m.installed); }

/* 引导页的下载进度，每秒刷新；全部装好后按钮变成“开始使用” */
async function watchSetup() {
  clearTimeout(S.setT);
  if (location.hash !== '#setup' || !$('#setst')) return;
  const st = await api().download_status();
  const rt = pickedRuntime();
  const items = [{ name: 'runtime_' + S.pick, label: rt.label, installed: rt.installed }, ...S.setup.models];
  if (st.done.some((n) => items.find((i) => i.name === n && !i.installed))) return renderSetup();  // 有东西刚装好：重新检查
  const cur = st.current;
  const line = (i) => {
    let t;
    if (i.installed) t = '已就绪';
    else if (cur && cur.name === i.name) t = `${cur.total ? Math.floor(cur.done / cur.total * 100) : 0}%　${(cur.speed / 1e6).toFixed(1)} MB/s　${esc(cur.source)}${cur.note ? '。' + esc(cur.note) : ''}`;
    else if (st.queue.includes(i.name)) t = '排队中';
    else if (st.failed[i.name]) t = '<span class="err">' + esc(st.failed[i.name]) + '</span>';
    else t = '未下载';
    return `<div><span>${esc(i.label)}</span><span class="mono small">${t}</span></div>`;
  };
  $('#setst').innerHTML = items.map(line).join('');
  const busy = cur || st.queue.length;
  const go = $('#setgo');
  go.disabled = !!busy;
  go.textContent = setupReady() ? '开始使用' : busy ? '正在下载…' : Object.keys(st.failed).length ? '重试下载' : '开始下载';
  if (busy) S.setT = setTimeout(watchSetup, 1000);
}

/* ---------- 外壳 ---------- */
function route() {
  const v = (location.hash || '#new').slice(1);
  const nv = v === 'terms' ? 'new' : v;  // 生成词库页属于新建
  $$('.nav a').forEach((a) => { const on = a.dataset.v === nv; a.classList.toggle('cur', on); if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
  if (v === 'progress') renderProgress();
  else if (v === 'review') renderReview();
  else if (v === 'history') renderHistory();
  else if (v === 'settings') renderSettings();
  else if (v === 'setup') renderSetup();
  else if (v === 'terms') renderTerms();
  else renderNew();
}

/* 下载进度：设置页打开、并且有下载在进行时每秒刷新一次 */
async function watchDownloads() {
  clearTimeout(S.dlT);
  if (!$('[data-get]')) return;
  const st = await api().download_status();
  const cur = st.current;
  $$('[data-dlst]').forEach((el) => {
    const name = el.dataset.dlst;
    const btn = $(`[data-dl="${name}"]`);
    btn.textContent = btn.dataset.label;
    btn.onclick = async () => { await api().download_model(name, $(`[data-src="${name}"]`).value); watchDownloads(); };
    if (cur && cur.name === name) {
      const pct = cur.total ? Math.floor(cur.done / cur.total * 100) : 0;
      el.textContent = `${pct}%  ${(cur.speed / 1e6).toFixed(1)} MB/s  ${cur.source}${cur.note ? '。' + cur.note : ''}`;
      btn.textContent = '取消';
      btn.onclick = async () => { await api().cancel_download(); watchDownloads(); };
    } else if (st.queue.includes(name)) {
      el.textContent = '排队中';
    } else if (st.failed[name]) {
      el.textContent = st.failed[name];
    } else {
      el.textContent = '';
    }
  });
  if (st.done.some((n) => $(`[data-get="${n}"]`))) return renderSettings();  // 下好了：重新检查文件
  if (cur || st.queue.length) S.dlT = setTimeout(watchDownloads, 1000);
}

const RT_KEYS = ['cuda', 'cuda12', 'vulkan', 'cpu', 'cpu_legacy'];
const RT_SHORT = { cuda: 'CUDA 13', cuda12: 'CUDA 12.6', vulkan: 'Vulkan', cpu: 'CPU', cpu_legacy: 'CPU Legacy' };
const RT_NAMES = { auto: '自动选择', cuda: 'GPU · CUDA 13', cuda12: 'GPU · CUDA 12.6', vulkan: 'GPU · Vulkan', cpu: 'CPU', cpu_legacy: 'CPU Legacy' };
function showSys(backend) {
  $('#sys').innerHTML = `运行库：${esc(RT_NAMES[backend] || backend)}<br>字幕对照组 ${esc(S.cfg.version)}`;
}

async function poll() {
  try {
    const s = await api().status();
    const wasRunning = S.status && S.status.running;
    S.status = s;
    if (s.dropped.length) addFiles(s.dropped);
    const badge = $('#navpct');  // 不在进度页时，导航上也能看到进度
    badge.hidden = !(s.running && s.current);
    if (s.running && s.current) badge.textContent = s.current.agent ? '待粘贴' : Math.floor(s.current.pct) + '%';
    const waiting = s.current && s.current.agent ? s.current.agent.k : null;  // 不在进度页时提醒一次
    if (waiting && waiting !== S.agentSeen && location.hash !== '#progress') toast('需要把提示词复制给 Agent', { label: '去进度页', fn: () => { location.hash = '#progress'; } });
    S.agentSeen = waiting;
    if (location.hash === '#progress' && (s.running || wasRunning)) renderProgress();
    if (wasRunning && !s.running) {
      if (s.finished) S.job = null;
      if (location.hash === '#new' || location.hash === '') renderNew();
      if (location.hash === '#history') renderHistory();
    }
  } finally {
    S.pollT = setTimeout(poll, 400);
  }
}

async function boot() {
  S.cfg = await api().config();
  S.lang = S.cfg.lang; S.mode = S.cfg.mode || 'accurate'; S.opt = S.cfg.opt; S.compare = S.cfg.compare; S.dia = S.cfg.diarize; S.formats = S.cfg.formats;
  showSys(S.cfg.gpu_backend);
  window.addEventListener('hashchange', route);
  const si = await api().setup_info();  // 运行库或必装模型缺了，先进引导页
  const rtOk = si.backend === 'auto' ? si.runtimes.some((r) => r.installed) : (si.runtimes.find((r) => r.key === si.backend) || {}).installed;
  if (!rtOk || !si.models.every((m) => m.installed)) location.hash = '#setup';
  window.addEventListener('resize', () => { if (location.hash === '#review' && S.job) drawWave(); });
  route();
  poll();
}
let booted = false;
function tryBoot() {
  if (booted || !window.pywebview || !window.pywebview.api || !window.pywebview.api.config) return;
  booted = true;
  boot();
}
window.addEventListener('pywebviewready', tryBoot);
tryBoot();
