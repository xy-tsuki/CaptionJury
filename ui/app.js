'use strict';
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (t) => String(t ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const view = $('#view');
const X = '<svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.2" aria-hidden="true"><path d="M2 2l8 8M10 2l-8 8"></path></svg>';

const S = { cfg: null, files: [], lang: 'mix', opt: true, dia: false, formats: ['SRT', 'ASS'], status: null, job: null, sel: 1, flt: 'all', pollT: null, seenFinished: null };
const api = () => window.pywebview.api;

function hms(t, ms) {
  t = Math.max(0, t || 0);
  const p = (n, w = 2) => String(Math.floor(n)).padStart(w, '0');
  const base = `${p(t / 3600)}:${p((t / 60) % 60)}:${p(t % 60)}`;
  return ms ? `${base},${p((t % 1) * 1000, 3)}` : base;
}
const bytes = (n) => (n > 1e9 ? (n / 1e9).toFixed(1) + ' GB' : Math.round(n / 1e6) + ' MB');
const langAttr = (l) => (l === 'ja' ? 'ja' : 'zh-CN');

let toastT;
function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastT);
  toastT = setTimeout(() => { t.hidden = true; }, 4200);
}

/* ---------- new job ---------- */
function help() {
  if (!S.opt) return '只运行 Qwen3-ASR，不做 DeepSeek 校对。';
  if (S.lang === 'zh') return '加跑 MiMo-V2.5-ASR 作为备用，DeepSeek 同时参考两份结果完成校对。';
  if (S.lang === 'ja') return '加跑 parakeet-ctc-1.1b-ja 作为备用，DeepSeek 同时参考两份结果完成校对。';
  return '按语言分流：中文句加跑 MiMo，日语句加跑 Parakeet，DeepSeek 逐句对照校对。';
}

function renderNew() {
  const zh = S.lang !== 'ja', ja = S.lang !== 'zh';
  const model = (name, role, on) => `<div class="md ${on ? 'on' : 'off'}"><span class="led"></span><span>${name}</span><span class="r">${on ? role : '不运行'}</span></div>`;
  const seg = [['zh', '中文'], ['ja', '日语'], ['mix', '中文 + 日语']].map(([id, l]) => `<button type="button" class="${S.lang === id ? 'on' : ''}" aria-pressed="${S.lang === id}" data-lang="${id}">${l}</button>`).join('');
  const fmts = ['SRT', 'ASS', 'VTT', 'TXT'].map((f) => `<label class="check mono" style="font-size:13px"><input type="checkbox" data-fmt="${f}" ${S.formats.includes(f) ? 'checked' : ''}><span>${f}</span></label>`).join('');
  const queue = S.files.map((f, i) => `<div class="q-row"><span class="mono cap ix">${String(i + 1).padStart(2, '0')}</span><span class="q-name">${esc(f.name)}</span><span class="mono dim sz" style="font-size:12px;text-align:right">${hms(f.duration)}</span><span class="mono faint" style="font-size:12px;text-align:right">${bytes(f.size)}</span><button class="x" type="button" aria-label="从队列移除" data-rm="${i}">${X}</button></div>`).join('');
  view.innerHTML = `
  <main class="m-grid">
    <section class="m-left" aria-label="文件">
      <div class="drop" id="drop">
        <h1>拖入视频或音频</h1>
        <div class="ln"><span class="mono cap">MP4 MKV MOV WEBM / WAV MP3 FLAC M4A</span><button class="btn" type="button" id="pick">选择文件</button></div>
      </div>
      <div class="queue"><div class="cap" style="padding-bottom:10px">队列</div>${queue || '<p class="faint" style="font-size:13px">还没有文件</p>'}</div>
    </section>
    <section class="m-right" aria-label="转写设置">
      <div class="m-sec">
        <span class="cap">识别语言</span>
        <div style="display:flex;align-items:center;gap:20px;flex-wrap:wrap">
          <div class="seg" role="group" aria-label="识别语言">${seg}</div>
          <label class="check"><input type="checkbox" id="opt" ${S.opt ? 'checked' : ''}><span>精度优化</span></label>
          <label class="check"><input type="checkbox" id="dia" ${S.dia ? 'checked' : ''}><span>区分说话人</span></label>
        </div>
        <p class="m-help">${help()}${S.dia ? ' 先用 MOSS 分出说话人，再标到字幕上。' : ''}</p>
      </div>
      <div class="m-sec"><span class="cap">字幕格式</span><div style="display:flex;gap:24px;flex-wrap:wrap">${fmts}</div></div>
      <div class="m-sec"><span class="cap">保存位置</span><span>与源文件同目录</span></div>
      <button class="btn solid" type="button" id="go" style="margin-top:auto;height:56px;font-size:15px">开始转写</button>
    </section>
  </main>
  <section class="chain" aria-label="处理链路">
    <div class="st"><span class="n mono">01</span><span class="t">提取音频</span><span class="dim" style="font-size:13px">FFmpeg</span></div>
    <div class="st"><span class="n mono">02</span><span class="t">断句对时</span><span class="dim" style="font-size:13px">${'按句子切分，逐字对时'}</span></div>
    <div class="st"><span class="n mono">03</span><span class="t">识别</span>
      ${model('Qwen3-ASR 1.7B', '主识别', true)}${model('MiMo-V2.5-ASR', '中文备用', S.opt && zh)}${model('parakeet-ctc-1.1b-ja', '日语备用', S.opt && ja)}${model('MOSS', '说话人', S.dia)}
    </div>
    <div class="st ${S.opt ? '' : 'off'}"><span class="n mono">04</span><span class="t">校对</span><span class="dim" style="font-size:13px">DeepSeek</span><span class="faint" style="font-size:13px">${S.opt ? '对照两份结果' : '不运行'}</span></div>
    <div class="st"><span class="n mono">05</span><span class="t">生成字幕</span><span class="dim mono" style="font-size:13px">${S.formats.join(' ') || '未选择格式'}</span></div>
  </section>`;
  $$('[data-lang]').forEach((b) => { b.onclick = () => { S.lang = b.dataset.lang; renderNew(); }; });
  $('#opt').onchange = (e) => { S.opt = e.target.checked; renderNew(); };
  $('#dia').onchange = (e) => { S.dia = e.target.checked; renderNew(); };
  $$('[data-fmt]').forEach((c) => { c.onchange = () => { const f = c.dataset.fmt; S.formats = c.checked ? [...S.formats, f] : S.formats.filter((x) => x !== f); renderNew(); }; });
  $$('[data-rm]').forEach((b) => { b.onclick = () => { S.files.splice(+b.dataset.rm, 1); renderNew(); }; });
  $('#pick').onclick = async () => { addFiles(await api().pick_files()); };
  $('#go').onclick = start;
  const drop = $('#drop');
  drop.ondragover = (e) => { e.preventDefault(); drop.classList.add('over'); };
  drop.ondragleave = () => drop.classList.remove('over');
  drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove('over'); };
}

function addFiles(list) {
  const have = new Set(S.files.map((f) => f.path));
  S.files.push(...list.filter((f) => !have.has(f.path)));
  if (location.hash === '#new' || !location.hash) renderNew();
}

async function start() {
  if (!S.files.length) return toast('请先添加文件');
  if (!S.formats.length) return toast('请至少选择一种字幕格式');
  const r = await api().start(S.files.map((f) => f.path), S.lang, S.opt, S.formats, S.dia);
  if (r.error) return toast(r.error);
  S.files = [];
  location.hash = '#progress';
}

/* ---------- progress ---------- */
function renderProgress() {
  const s = S.status || {};
  const c = s.current;
  if (!c) {
    view.innerHTML = `<div class="empty"><p>${s.error ? esc(s.error) : '当前没有任务'}</p><a class="btn" href="#new">新建转写</a></div>`;
    return;
  }
  const elapsed = c.elapsed || 0;
  const remain = c.remaining ?? null;
  const langName = { zh: '中文', ja: '日语', mix: '中文 + 日语' }[c.lang];
  const N = 64;
  const lanes = c.lanes.map((l) => {
    const done = Math.floor(l.pct / 100 * N);
    let cells = '';
    for (let i = 0; i < N; i++) cells += `<span class="cell${i < done ? ' done' : (i === done && l.pct > 0 && l.pct < 100 && !s.paused ? ' now' : '')}"></span>`;
    return `<div class="lane${l.pct === 0 ? ' wait' : ''}"><span>${l.name}</span><div class="cells" aria-hidden="true">${cells}</div><span class="s">${esc(l.state)}</span></div>`;
  }).join('');
  const stream = (c.stream || []).map((r) => `<div class="ln2"><span class="tc mono">${hms(r.t, true)}</span><span>${esc(r.text)}</span></div>`).join('');
  const finished = !s.running;
  view.innerHTML = `
  <main class="p-grid">
    <section class="p-left" aria-label="总进度">
      <div style="display:flex;flex-direction:column;gap:6px">
        <span class="cap">${finished ? '已结束' : `正在处理 ${(s.index || 0) + 1} / ${s.total || 1}`}</span>
        <h1 style="font-size:17px;font-weight:500;word-break:break-all">${esc(c.name)}</h1>
      </div>
      <div class="big">${Math.floor(c.pct)}<small>%</small></div>
      <dl class="kv" style="margin:0">
        <dt>语言</dt><dd>${langName}</dd>
        <dt>精度优化</dt><dd>${c.opt ? '开启' : '关闭'}</dd>
        <dt>区分说话人</dt><dd>${c.diarize ? '开启' : '关闭'}</dd>
        <dt>时长</dt><dd class="mono">${c.duration ? hms(c.duration) : '-'}</dd>
        ${finished ? '' : `${s.total > 1 ? `<dt>整个队列</dt><dd class="mono">${Math.floor(s.queue_pct || 0)}%</dd>` : ''}<dt>已用时</dt><dd class="mono">${hms(elapsed)}</dd><dt>预计剩余</dt><dd class="mono">${remain === null ? '估算中' : hms(remain)}</dd>`}
        ${s.vram ? `<dt>显存</dt><dd class="mono">${s.vram}</dd>` : ''}
      </dl>
      ${s.error ? `<p class="err">${esc(s.error)}</p>` : ''}
      ${(c.warnings || []).map((w) => `<p class="err">${esc(w)}</p>`).join('')}
      <div style="margin-top:auto;display:flex;gap:8px">
        ${finished
    ? (s.finished ? `<a class="btn solid" style="flex:1" href="#review">进入校对</a>` : `<a class="btn" style="flex:1" href="#new">返回新建</a>`)
    : `<button class="btn" type="button" style="flex:1" id="pause">${s.paused ? '继续' : '暂停'}</button><button class="btn ghost" type="button" id="cancel">取消</button>`}
      </div>
    </section>
    <section class="p-right" aria-label="各模型进度">
      <div class="ruler"><span class="cap">本地模型依次加载</span><div class="tk mono"></div><span></span></div>
      ${lanes}
      <div class="stream" aria-label="已完成的识别结果">
        ${stream ? `<div class="cap" style="padding-bottom:8px">${esc(c.stream_name)} 最后几条结果</div>${stream}` : ''}
      </div>
    </section>
  </main>`;
  const p = $('#pause');
  if (p) p.onclick = async () => { await api().pause(!s.paused); };
  const x = $('#cancel');
  if (x) x.onclick = async () => { await api().cancel(); };
}

/* ---------- review ---------- */
const TAGS = { ok: '一致', keep: '已确认', fix: '已修正', ask: '存疑', raw: '初稿' };

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
    view.innerHTML = '<div class="empty"><p>还没有转写结果</p><a class="btn" href="#new">新建转写</a></div>';
    return;
  }
  const j = S.job;
  const isVideo = /\.(mp4|webm|m4v|mov)$/i.test(j.source);
  const base = `/${j.token}/media/${j.id}`;
  const options = jobs.map((x) => `<option value="${x.id}" ${x.id === j.id ? 'selected' : ''}>${esc(x.name)}</option>`).join('');
  view.innerHTML = `
  <main class="e-grid">
    <section class="e-left" aria-label="预览与对照">
      <div class="video">
        ${isVideo ? `<video id="media" src="${base}/source" preload="metadata"></video>` : `<audio id="media" src="${base}/audio" preload="metadata"></audio><span class="ph mono">音频预览</span>`}
        <p class="sub" id="sub"></p>
      </div>
      <div class="trans">
        <button class="ico" type="button" aria-label="上一条" id="prev"><svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.2" aria-hidden="true"><path d="M12 3L6 8l6 5zM4 3v10"></path></svg></button>
        <button class="ico" type="button" aria-label="播放" id="play"><svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M4 2.5l9 5.5-9 5.5z"></path></svg></button>
        <button class="ico" type="button" aria-label="下一条" id="next"><svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.2" aria-hidden="true"><path d="M4 3l6 5-6 5zM12 3v10"></path></svg></button>
        <span class="tc mono" id="clock">00:00:00,000</span>
      </div>
      <div class="cmp" id="cmp"></div>
    </section>
    <section class="e-right" aria-label="字幕列表">
      <div class="e-bar">
        <select class="pick" id="jobsel" aria-label="选择转写结果">${options}</select>
        <div class="seg small" role="group" aria-label="筛选" id="flt"></div>
        <div class="sp">${hasSpeakers() ? `<label class="check tags"><input type="checkbox" id="tags" ${j.speaker_tags === false ? '' : 'checked'}><span>字幕带说话人</span></label>` : ''}<button class="btn sm" type="button" id="fr">查找替换</button><button class="btn sm solid" type="button" id="exp">导出字幕</button></div>
      </div>
      <div class="frbar" id="frbar" hidden>
        <input class="field" id="f1" placeholder="查找" aria-label="查找"><input class="field" id="f2" placeholder="替换为" aria-label="替换为"><button class="btn sm" type="button" id="frgo">全部替换</button>
      </div>
      <div class="list" id="list"></div>
    </section>
  </main>
  <section class="wave" aria-label="波形时间轴">
    <div class="w-t mono" id="wt"></div>
    <canvas id="wc" height="60" aria-hidden="true"></canvas>
    <div class="w-s" id="ws"></div>
  </section>`;
  const m = $('#media');
  $('#prev').onclick = () => step(-1);
  $('#next').onclick = () => step(1);
  $('#play').onclick = () => (m.paused ? m.play() : m.pause());
  m.ontimeupdate = () => {
    $('#clock').textContent = hms(m.currentTime, true);
    moveHead();
    const seg = j.segments.find((s) => m.currentTime >= s.a && m.currentTime < s.b);
    if (seg && seg.id !== S.sel && !m.paused) select(seg.id, false);
  };
  $('#jobsel').onchange = async (e) => { await openJob(e.target.value); renderReview(); };
  if ($('#tags')) $('#tags').onchange = async (e) => { j.speaker_tags = e.target.checked; await api().set_speaker_tags(j.id, e.target.checked); };
  $('#fr').onclick = () => { const b = $('#frbar'); b.hidden = !b.hidden; };
  $('#frgo').onclick = async () => {
    const f = $('#f1').value;
    if (!f) return;
    const n = await api().replace_all(j.id, f, $('#f2').value);
    await openJob(j.id);
    drawList(); drawCmp();
    toast(`替换了 ${n} 处`);
  };
  $('#exp').onclick = async () => {
    const out = await api().export(j.id, S.formats.length ? S.formats : ['SRT']);
    toast('已导出：' + out.map((p) => p.split('\\').pop()).join('、'));
  };
  drawFilters(); drawList(); drawCmp(); drawWave();
}

async function openJob(id) {
  S.job = await api().job(id);
  if (!S.job.segments.some((s) => s.id === S.sel)) S.sel = S.job.segments[0]?.id ?? 1;
}

const hasSpeakers = () => Object.keys(S.job.speakers || {}).length > 0 || S.job.segments.some((s) => s.speaker);
const speakerName = (s) => (S.job.speakers || {})[s.speaker] || '';

function drawFilters() {
  const segs = S.job.segments;
  const n = (st) => segs.filter((s) => s.status === st).length;
  const items = [['all', `全部 ${segs.length}`]];
  if (S.job.opt) items.push(['fix', `已修正 ${n('fix')}`], ['ask', `存疑 ${n('ask')}`]);
  $('#flt').innerHTML = items.map(([id, l]) => `<button type="button" class="${S.flt === id ? 'on' : ''}" data-f="${id}">${l}</button>`).join('');
  $$('[data-f]').forEach((b) => { b.onclick = () => { S.flt = b.dataset.f; drawFilters(); drawList(); }; });
}

function drawList() {
  const rows = S.job.segments.filter((s) => S.flt === 'all' || s.status === S.flt);
  $('#list').innerHTML = rows.map((s) => {
    const cls = { fix: ' fix', ask: ' ask' }[s.status] || '';
    return `<button type="button" class="row${cls}${s.id === S.sel ? ' sel' : ''}" data-id="${s.id}">
      <span class="ix mono">${String(s.id).padStart(3, '0')}</span>
      <span class="tc mono">${hms(s.a, true)}<br>${hms(s.b, true)}</span>
      <span lang="${langAttr(s.lang)}">${hasSpeakers() ? `<small class="spk">${esc(speakerName(s) || '未标注')}</small>` : ''}${esc(s.text)}</span>
      <span class="stt"><span class="sq"></span>${TAGS[s.status]}</span></button>`;
  }).join('');
  $$('.row').forEach((r) => { r.onclick = () => select(+r.dataset.id, true); });
}

function current() { return S.job.segments.find((s) => s.id === S.sel) || S.job.segments[0]; }

function drawCmp() {
  const s = current();
  if (!s) return;
  $('#sub').textContent = s.text;
  $('#sub').lang = langAttr(s.lang);
  const cands = [];
  if (S.job.opt) {
    const backups = [['mimo', 'MiMo'], ['whisper', 'Whisper'], ['parakeet', 'Parakeet']].filter(([k]) => s[k]);
    if (s.alt) backups.push(['alt', s.alt_name]);  // jobs made before several backups were possible
    cands.push(['Qwen3-ASR', s.qwen, backups.length ? s[backups[0][0]] : s.qwen2]);
    if (s.qwen2) cands.push(['Qwen 复核', s.qwen2, s.qwen]);
    for (const [k, name] of backups) cands.push([name, s[k], s.qwen]);
    if (s.moss) cands.push(['MOSS', s.moss, s.qwen]);
  }
  const rows = cands.map(([who, t, other]) => {
    const ad = t === s.text;
    return `<div class="cd${ad ? ' ad' : ''}"><span class="who mono">${esc(who)}</span><span class="txt" lang="${langAttr(s.lang)}">${markDiff(t, other)}</span><button class="btn sm" type="button" data-adopt="${esc(t)}">${ad ? '已采用' : '采用'}</button></div>`;
  }).join('');
  $('#cmp').innerHTML = `
    <div class="cmp-h"><span class="cap">第 ${s.id} 条${S.job.opt ? ' · 候选对照' : ''}</span>${hasSpeakers() ? speakerBar(s) : ''}</div>
    ${rows}
    <label class="final"><span class="cap">最终字幕</span><textarea class="field" id="final" rows="2" lang="${langAttr(s.lang)}">${esc(s.text)}</textarea></label>
    ${s.why ? `<p class="why">${esc(s.why)}</p>` : ''}`;
  $$('[data-adopt]').forEach((b) => { b.onclick = () => setText(b.dataset.adopt); });
  $('#final').onchange = (e) => setText(e.target.value);
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

async function setText(t) {
  const s = current();
  s.text = t;
  await api().update_text(S.job.id, s.id, t);
  drawList(); drawCmp(); drawWave();
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
  const cs = getComputedStyle(c);
  const t0 = windowStart();
  const s = current();
  const peaks = S.job.peaks;
  g.clearRect(0, 0, w, 60);
  for (let x = 0; x < w; x += 3) {
    const t = t0 + x / w * SPAN;
    const p = peaks[Math.floor(t * 20)] || 0;
    const h = Math.max(1, p / 100 * 54);
    g.fillStyle = s && t >= s.a && t <= s.b ? cs.getPropertyValue('--accline') : cs.getPropertyValue('--line2');
    g.fillRect(x, 30 - h / 2, 2, h);
  }
  const ticks = [0, 8, 16, 24, 32].map((d) => `<span>${hms(t0 + d)}</span>`).join('');
  $('#wt').innerHTML = ticks;
  const vis = S.job.segments.filter((x) => x.b > t0 && x.a < t0 + SPAN);
  $('#ws').innerHTML = vis.map((x) => `<button type="button" class="blk${x.id === S.sel ? ' sel' : ''}" style="left:${(x.a - t0) / SPAN * 100}%;width:${(x.b - x.a) / SPAN * 100}%" data-b="${x.id}" aria-label="跳到第 ${x.id} 条">${x.id}</button>`).join('') + '<span class="head" id="head"></span>';
  $$('[data-b]').forEach((b) => { b.onclick = () => select(+b.dataset.b, true); });
  moveHead();
}

function moveHead() {
  const h = $('#head'), m = $('#media');
  if (!h || !m) return;
  const p = (m.currentTime - windowStart()) / SPAN * 100;
  h.style.left = p + '%';
  h.style.display = p < 0 || p > 100 ? 'none' : '';
}

/* ---------- settings ---------- */
async function renderSettings() {
  const c = S.cfg = await api().config();
  const ok = await api().check_models();
  const ms = await api().models();
  const LANG = { zh: '中文', ja: '日语' };
  const files = (m) => m.files.map((f) => `<div class="mrow"><span>${m.files.length > 1 ? esc(f.key.replace(/^\w+_/, '')) : '文件'}</span><input class="field mono p" data-cfg="${f.key}" value="${esc(f.path)}" aria-label="${esc(m.label)} 路径"><span class="ok">${f.ok ? '就绪' : '找不到文件'}</span></div>`).join('');
  const card = (m) => `<div class="mcard"><div class="mhead"><b>${esc(m.label)}</b><span class="ok">${m.installed ? '已安装' : '未安装'}</span></div><p class="desc">${esc(m.desc)}</p>${m.role === 'backup' ? `<div class="muse">${m.langs.map((l) => `<label class="check"><input type="checkbox" data-bk="${m.name}" data-lang="${l}" ${m.use.includes(l) ? 'checked' : ''}><span>用于${LANG[l]}</span></label>`).join('')}${m.installed ? '' : '<span class="faint">未安装时不会使用</span>'}</div>` : ''}${files(m)}</div>`;
  const group = (role) => ms.filter((m) => m.role === role).map(card).join('');
  view.innerHTML = `
  <main class="s-grid">
    <nav class="s-nav" aria-label="设置分类"><a href="#settings" data-j="api">DeepSeek</a><a href="#settings" data-j="models">本地模型</a><a href="#settings" data-j="runtime">运行环境</a><a href="#settings" data-j="terms">校对术语</a></nav>
    <div class="s-main">
      <section class="sec" id="api">
        <div><h2>DeepSeek</h2><p class="desc">开启精度优化时用于校对。只发送字幕文字，音视频不会上传。</p></div>
        <div class="stack">
          <div class="lab"><span>API 密钥</span>
            <div class="key">
              <input class="field mono" id="key" type="password" autocomplete="off" style="flex:1;min-width:220px" placeholder="${c.has_key ? '已保存，粘贴新密钥可替换' : '粘贴 API 密钥'}" aria-label="API 密钥">
              <button class="btn" type="button" id="ksave">保存</button>
              <button class="btn" type="button" id="ktest">测试连接</button>
              ${c.has_key ? '<button class="btn ghost" type="button" id="kdel">删除</button>' : ''}
            </div>
          </div>
          <p class="lock"><svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.1" aria-hidden="true"><rect x="2.5" y="6" width="9" height="6.5"></rect><path d="M4.5 6V4a2.5 2.5 0 015 0v2"></path></svg><span id="kstat">${c.has_key ? '密钥已用 Windows DPAPI 加密保存，仅当前账户可解密。' : '保存后用 Windows DPAPI 加密，仅当前账户可解密。'}</span></p>
          <div class="two">
            <label class="lab"><span>接口地址</span><input class="field mono" data-cfg="deepseek_url" value="${esc(c.deepseek_url)}"></label>
            <label class="lab"><span>模型</span><input class="field mono" data-cfg="deepseek_model" list="dsmodels" value="${esc(c.deepseek_model)}"><datalist id="dsmodels"></datalist></label>
          </div>
        </div>
      </section>
      <section class="sec" id="models">
        <div><h2>本地模型</h2><p class="desc">Qwen 是主识别模型。开启精度优化时，勾选的备用模型会各识别一遍，交给 DeepSeek 投票；可以同时选好几个，越多越准，也越慢。修改路径后重新打开此页检查。</p></div>
        <div class="stack">
          <h3 class="cap">必装</h3>${group('main')}
          <h3 class="cap">区分说话人时使用</h3>${group('diarize')}
          <h3 class="cap">备用模型</h3>${group('backup')}
        </div>
      </section>
      <section class="sec" id="runtime">
        <div><h2>运行环境</h2><p class="desc">所有模型都在 GPU 上运行。</p></div>
        <div class="stack">
          <div class="lab"><span>推理后端</span><div class="seg" role="group" aria-label="推理后端" style="align-self:flex-start">
            <button type="button" class="${c.gpu_backend === 'cuda' ? 'on' : ''}" data-back="cuda">CUDA 13</button><button type="button" class="${c.gpu_backend === 'vulkan' ? 'on' : ''}" data-back="vulkan">Vulkan</button>
          </div></div>
          <label class="lab"><span>运行时目录${ok.runtime ? '' : '（找不到 crispasr.exe）'}</span><input class="field mono" data-cfg="runtime_${c.gpu_backend}" value="${esc(c['runtime_' + c.gpu_backend])}"></label>
          <label class="lab"><span>FFmpeg</span><input class="field mono" data-cfg="ffmpeg" value="${esc(c.ffmpeg)}"></label>
        </div>
      </section>
      <section class="sec" id="terms">
        <div><h2>校对术语</h2><p class="desc">人名、地名等固定写法，每行一条，校对时交给 DeepSeek 参考。</p></div>
        <label class="lab"><span>术语表</span><textarea class="field" data-cfg="glossary" rows="5">${esc(c.glossary)}</textarea></label>
      </section>
    </div>
  </main>`;
  $$('[data-j]').forEach((a) => { a.onclick = (e) => { e.preventDefault(); $('#' + a.dataset.j).scrollIntoView(); }; });
  $$('[data-cfg]').forEach((i) => { i.onchange = () => api().save_config({ [i.dataset.cfg]: i.value }); });
  $$('[data-bk]').forEach((i) => { i.onchange = () => api().set_backup(i.dataset.bk, i.dataset.lang, i.checked); });
  $$('[data-back]').forEach((b) => { b.onclick = async () => { await api().save_config({ gpu_backend: b.dataset.back }); renderSettings(); }; });
  $('#ksave').onclick = async () => {
    const v = $('#key').value.trim();
    if (!v) return toast('请先粘贴密钥');
    await api().set_key(v);
    toast('密钥已加密保存');
    renderSettings();
  };
  $('#ktest').onclick = async () => {
    $('#kstat').textContent = '正在连接…';
    const r = await api().test_key();
    if (r.error) { $('#kstat').textContent = '连接失败：' + r.error; return; }
    $('#kstat').textContent = '连接正常，可用模型：' + r.models.join('、');
    $('#dsmodels').innerHTML = r.models.map((m) => `<option value="${esc(m)}"></option>`).join('');
  };
  const del = $('#kdel');
  if (del) del.onclick = async () => { await api().delete_key(); toast('密钥已删除'); renderSettings(); };
}

/* ---------- shell ---------- */
function route() {
  const v = (location.hash || '#new').slice(1);
  $$('.nav a').forEach((a) => a.classList.toggle('cur', a.dataset.v === v));
  if (v === 'progress') renderProgress();
  else if (v === 'review') renderReview();
  else if (v === 'settings') renderSettings();
  else renderNew();
}

async function poll() {
  try {
    const s = await api().status();
    const wasRunning = S.status && S.status.running;
    S.status = s;
    if (s.dropped.length) addFiles(s.dropped);
    if (location.hash === '#progress' && (s.running || wasRunning)) renderProgress();
    if (wasRunning && !s.running && s.finished) { S.job = null; }
  } finally {
    S.pollT = setTimeout(poll, 400);
  }
}

async function boot() {
  S.cfg = await api().config();
  S.lang = S.cfg.lang; S.opt = S.cfg.opt; S.dia = S.cfg.diarize; S.formats = S.cfg.formats;
  $('#sys').textContent = S.cfg.gpu_backend === 'cuda' ? 'GPU · CUDA' : 'GPU · Vulkan';
  window.addEventListener('hashchange', route);
  window.addEventListener('resize', () => { if (location.hash === '#review') drawWave(); });
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
