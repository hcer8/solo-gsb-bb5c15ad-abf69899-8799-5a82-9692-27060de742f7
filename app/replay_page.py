"""离线复盘回放页：生成单个自包含 replay.html，数据内嵌、无网络依赖。

bundle 由 app/export.py 汇总（已脱敏）。页面用原生 JS 把事件流按
「轮次 + 虚拟时间」重放：拖动滑块即还原任意时刻的房间编排、已揭示
卡片、回执、答复版本与时钟状态；同一虚拟时刻的事件严格按事件 ID
升序生效（数据本身即 ID 序，前端不做重排）。
"""

from __future__ import annotations

import json


def _safe_json(obj) -> str:
    """嵌入 <script> 的 JSON：转义标签起始符与行分隔符，杜绝注入与解析歧义。"""
    text = json.dumps(obj, ensure_ascii=False)
    text = text.replace("<", "\\u003c").replace(">", "\\u003e")
    text = text.replace(chr(0x2028), "\\u2028").replace(chr(0x2029), "\\u2029")
    return text


def build_replay_html(bundle: dict) -> str:
    data = _safe_json(bundle)
    return _TEMPLATE.replace("/*__DATA__*/null", f"window.__DEBRIEF__ = {data};")


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>演练复盘 · 情景注入台</title>
<style>
  :root {
    --bg:#0f1420; --panel:#1a2233; --panel-2:#222c42; --text:#e6ebf5;
    --muted:#8b96ad; --accent:#4da3ff; --green:#3ecf8e; --amber:#f5b942;
    --red:#ef5b6b; --border:#2c3852;
  }
  * { box-sizing: border-box; }
  body { margin:0; font-family:"PingFang SC","Microsoft YaHei",system-ui,sans-serif;
         background:var(--bg); color:var(--text); min-height:100vh; }
  .topbar { display:flex; align-items:center; justify-content:space-between;
            padding:14px 24px; border-bottom:1px solid var(--border); background:var(--panel); }
  .topbar h1 { font-size:18px; margin:0; letter-spacing:2px; }
  .wm { font-size:12px; color:var(--muted); font-family:ui-monospace,monospace; }
  main { max-width:1100px; margin:20px auto; padding:0 16px; }
  .panel { background:var(--panel); border:1px solid var(--border); border-radius:10px;
           padding:16px 18px; margin-bottom:14px; }
  h2 { font-size:15px; margin:0 0 10px; }
  .muted { color:var(--muted); font-size:13px; }
  /* 页签 */
  .tabs { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:12px; }
  .tab { background:var(--panel-2); border:1px solid var(--border); color:var(--text);
         border-radius:8px 8px 0 0; padding:8px 14px; font-size:13px; font-weight:600; cursor:pointer; }
  .tab.active { border-color:var(--accent); color:var(--accent); background:var(--panel); }
  /* 播放器 */
  .player { display:flex; align-items:center; gap:14px; flex-wrap:wrap; }
  .clock { font-family:ui-monospace,monospace; font-size:38px; font-weight:700;
           font-variant-numeric:tabular-nums; line-height:1; min-width:190px; }
  .phase { font-size:12px; padding:3px 10px; border-radius:999px; }
  .phase-prep    { background:rgba(139,150,173,.15); color:var(--muted); }
  .phase-running { background:rgba(62,207,142,.15); color:var(--green); }
  .phase-paused  { background:rgba(245,185,66,.15); color:var(--amber); }
  .phase-reset   { background:rgba(239,91,107,.15); color:var(--red); }
  .transport { display:flex; align-items:center; gap:10px; flex:1; min-width:300px; }
  button { border:none; border-radius:6px; padding:8px 16px; font-size:13px;
           cursor:pointer; color:#0b1020; font-weight:600; background:var(--accent); }
  button:disabled { opacity:.35; cursor:not-allowed; }
  input[type=range] { flex:1; accent-color:var(--accent); }
  select { background:var(--panel-2); border:1px solid var(--border); color:var(--text);
           border-radius:6px; padding:7px 8px; font-size:13px; }
  .ticks { display:flex; justify-content:space-between; font-size:11px;
           color:var(--muted); font-family:ui-monospace,monospace; margin-top:2px; }
  .cols { display:grid; grid-template-columns:1fr 320px; gap:14px; }
  @media (max-width:900px) { .cols { grid-template-columns:1fr; } }
  /* 卡片 */
  .card { background:var(--panel-2); border:1px solid var(--border);
          border-left:4px solid var(--muted); border-radius:8px; padding:11px 13px; margin-bottom:10px; }
  .card.revealed { border-left-color:var(--green); }
  .card.pending  { opacity:.7; }
  .card-meta { display:flex; justify-content:space-between; gap:8px; font-size:12px;
               color:var(--muted); margin-bottom:3px; flex-wrap:wrap; }
  .trig { font-family:ui-monospace,monospace; color:var(--accent); }
  .badge { padding:2px 8px; border-radius:999px; font-size:11px; white-space:nowrap; }
  .b-all { background:rgba(77,163,255,.15); color:var(--accent); }
  .b-tgt { background:rgba(245,185,66,.15); color:var(--amber); }
  .b-rev { background:rgba(62,207,142,.15); color:var(--green); }
  .b-pend { background:rgba(139,150,173,.15); color:var(--muted); }
  .b-timeout { background:rgba(239,91,107,.15); color:var(--red); }
  .card-title { font-weight:700; margin:3px 0; }
  .card-content { font-size:13px; white-space:pre-wrap; color:var(--text); margin:4px 0; }
  .sub { margin-top:7px; padding-top:7px; border-top:1px dashed var(--border);
         font-size:12px; display:flex; gap:6px; flex-wrap:wrap; }
  .ok { background:rgba(62,207,142,.15); color:var(--green); }
  .wait { background:rgba(139,150,173,.15); color:var(--muted); }
  .gate { margin-top:6px; font-size:12px; }
  .gate-waiting { color:var(--amber); } .gate-unlocked { color:var(--green); }
  .gate-timeout { color:var(--red); }
  .ans { margin-top:7px; background:var(--panel); border:1px solid var(--border);
         border-radius:6px; padding:7px 9px; }
  .ans-meta { color:var(--accent); font-size:11px; margin-bottom:3px; }
  .ans-body { white-space:pre-wrap; font-size:13px; }
  details { margin-top:6px; }
  summary { cursor:pointer; color:var(--muted); font-size:12px; }
  .trail-item { font-size:12px; margin-top:5px; padding:5px 8px; border-radius:5px;
                background:var(--panel); border:1px solid var(--border); }
  .trail-item.latest { border-color:var(--green); }
  /* 侧栏 */
  .log { list-style:none; margin:0; padding:0; max-height:520px; overflow-y:auto;
         font-size:12px; font-family:ui-monospace,monospace; }
  .log li { padding:4px 6px; border-bottom:1px solid var(--border); display:flex; gap:8px; }
  .log li.applied { color:var(--text); }
  .log li.future { color:#55607a; }
  .log .t { color:var(--accent); min-width:86px; }
  .log li.future .t { color:#4a5369; }
  .clog { list-style:none; margin:6px 0 0; padding:0; font-size:12px;
          font-family:ui-monospace,monospace; color:var(--muted); }
  .clog li { padding:2px 0; }
  .clog li.hot { color:var(--accent); }
  .counts { display:flex; gap:8px; flex-wrap:wrap; margin-top:8px; }
  .counts span { background:var(--panel-2); border:1px solid var(--border);
                 border-radius:999px; padding:2px 10px; font-size:11px; color:var(--muted); }
  .empty { text-align:center; color:var(--muted); padding:40px 10px; font-size:14px; }
</style>
</head>
<body>
<header class="topbar">
  <h1>演练复盘 · 情景注入台</h1>
  <span id="wm" class="wm"></span>
</header>
<main>
  <section class="panel">
    <h2 id="room-name"></h2>
    <div id="meta" class="muted"></div>
    <div id="counts" class="counts"></div>
  </section>

  <nav id="tabs" class="tabs"></nav>

  <section class="panel">
    <div class="player">
      <div>
        <div id="clock" class="clock">00:00:00</div>
        <div style="margin-top:6px"><span id="phase" class="phase phase-prep"></span></div>
      </div>
      <div class="transport">
        <button id="btn-play">▶ 播放</button>
        <select id="speed" title="回放速度">
          <option value="1">1×</option><option value="2">2×</option>
          <option value="4" selected>4×</option><option value="8">8×</option>
          <option value="30">30×</option>
        </select>
        <div style="flex:1">
          <input id="slider" type="range" min="0" max="0" step="100" value="0" disabled>
          <div class="ticks"><span>T+00:00:00</span><span id="tick-max">T+00:00:00</span></div>
        </div>
      </div>
    </div>
  </section>

  <div class="cols">
    <section class="panel">
      <h2 id="cards-title">当时的卡片状态</h2>
      <div id="cards"></div>
    </section>
    <section class="panel">
      <h2>事件流（同刻按 ID 排序）</h2>
      <ul id="log" class="log"></ul>
      <h2 style="margin-top:14px">时钟动作</h2>
      <ul id="clog" class="clog"></ul>
    </section>
  </div>
</main>

<script>
'use strict';
/*__DATA__*/null

(function () {
  const B = window.__DEBRIEF__;
  const $ = (s) => document.querySelector(s);

  /* ------------------------------------------------ 工具 */
  function fmt(ms) {
    const s = Math.max(0, Math.floor((ms || 0) / 1000));
    const p = (n) => String(n).padStart(2, '0');
    return `${p(Math.floor(s/3600))}:${p(Math.floor((s%3600)/60))}:${p(s%60)}`;
  }
  function escRound(meta) {
    if (!meta) return '';
    const r = meta.round;
    return `R${r} T+${fmt(meta.vtime_ms)}`;
  }
  const TYPE_TEXT = {
    group_added: '参与组创建', group_renamed: '参与组改名', group_removed: '参与组删除',
    card_added: '卡片编排', card_removed: '卡片删除', card_revealed: '卡片揭示',
    receipt_updated: '回执变化', answer_updated: '答复更新',
    answer_lock_updated: '答复锁定变化', gate_updated: '门槛状态变化',
    clock: '时钟动作', reset: '重置演练',
  };

  /* ------------------------------------------------ 页签构造
   * prep   : 首次开始前的编排（有轮次时才出现）
   * round N: 第 N 轮（开始 → 重置/至今），可拖虚拟时间
   * tail   : 最后一次重置之后、尚未再次开始的编排 */
  const rounds = B.rounds || [];
  const tabs = [];
  if (rounds.length) {
    const pre = B.events.filter((e) => e.id < rounds[0].start_event_id);
    if (pre.length) tabs.push({ kind: 'prep', label: '开始前编排', pass: pre, prefix: [] });
  } else {
    tabs.push({ kind: 'prep', label: '编排阶段', pass: B.events, prefix: [] });
  }
  rounds.forEach((r, i) => {
    const nxt = rounds[i + 1];
    const endId = r.reset_event_id != null ? r.reset_event_id
                : (nxt ? nxt.start_event_id : Infinity);
    const run = B.events.filter((e) => e.id >= r.start_event_id && e.id < endId);
    tabs.push({ kind: 'round', label: `第 ${r.index} 轮演练`, round: r, run,
                prefixIds: r.start_event_id });
    if (r.reset_event_id != null && !nxt) {
      const tail = B.events.filter((e) => e.id > r.reset_event_id);
      if (tail.length) {
        tabs.push({ kind: 'tail', label: '重置后编排', pass: tail,
                    prefixIds: r.reset_event_id });
      }
    }
  });
  if (!tabs.length) tabs.push({ kind: 'prep', label: '编排阶段', pass: [], prefix: [] });

  /* ------------------------------------------------ 事件重放状态机 */
  function newState() {
    return { groups: new Map(), cards: new Map(), phase: 'prep', clockMs: 0 };
  }
  function applyEvent(st, e) {
    const p = e.payload || {};
    switch (e.type) {
      case 'group_added':   st.groups.set(p.id, { id: p.id, name: p.name }); break;
      case 'group_renamed': { const g = st.groups.get(p.id); if (g) g.name = p.name; break; }
      case 'group_removed':
        st.groups.delete(p.id);
        st.cards.forEach((c) => { c.receipts.delete(p.id); c.answers.delete(p.id); });
        break;
      case 'card_added':
        st.cards.set(p.id, { ...p, receipts: new Set(), answers: new Map(), gate: null });
        break;
      case 'card_removed': st.cards.delete(p.id); break;
      case 'card_revealed': {
        let c = st.cards.get(p.id);
        if (!c) { c = { ...p, receipts: new Set(), answers: new Map(), gate: null };
                  st.cards.set(p.id, c); }
        Object.assign(c, p);
        break;
      }
      case 'receipt_updated': {
        const c = st.cards.get(p.card_id);
        if (!c) break;
        if (p.acked) c.receipts.add(p.group_id); else c.receipts.delete(p.group_id);
        break;
      }
      case 'answer_updated': {
        const c = st.cards.get(p.card_id);
        if (!c) break;
        c.answers.set(p.group_id, { body: p.body, revision: p.revision });
        break;
      }
      case 'answer_lock_updated': {
        const c = st.cards.get(p.card_id);
        if (c) c.answers_locked = p.answers_locked;
        break;
      }
      case 'gate_updated': {
        const c = st.cards.get(p.card_id);
        if (c) c.gate = p.gate;
        break;
      }
      case 'clock': {
        st.lastClock = e;
        if (p.action === 'started') { st.phase = 'running'; st.clockMs = 0; }
        else if (p.action === 'paused') { st.phase = 'paused'; st.clockMs = p.elapsed_ms || 0; }
        else if (p.action === 'resumed') { st.phase = 'running'; st.clockMs = p.elapsed_ms || 0; }
        break;
      }
      case 'reset':
        st.phase = 'reset'; st.clockMs = 0;
        st.cards.forEach((c) => {
          c.revealed = false; c.revealed_at_ms = null; c.timed_out = false;
          c.answers_locked = false; c.receipts.clear(); c.answers.clear();
        });
        break;
    }
  }

  /* 计算某页签在虚拟时间 T 的状态。同刻事件天然按 ID 序（数组即 ID 序）。 */
  function stateAt(tab, T) {
    const st = newState();
    if (tab.kind === 'round') {
      for (const e of B.events) if (e.id < tab.prefixIds) applyEvent(st, e);
      for (const e of tab.run) if (e.vtime_ms <= T) applyEvent(st, e);
      st.clockMs = (st.phase === 'paused') ? st.clockMs : T;
      return st;
    }
    if (tab.kind === 'tail') {
      for (const e of B.events) if (e.id <= tab.prefixIds) applyEvent(st, e);
      tab.pass.forEach((e) => applyEvent(st, e));
      st.phase = 'prep'; st.clockMs = 0;
      return st;
    }
    tab.pass.forEach((e) => applyEvent(st, e));
    st.phase = 'prep'; st.clockMs = 0;
    return st;
  }

  /* ------------------------------------------------ 渲染 */
  const gname = (st, id) => { const g = st.groups.get(id); return g ? g.name : `#${id}`; };
  const sortedCards = (st) => [...st.cards.values()]
    .sort((a, b) => a.trigger_seconds - b.trigger_seconds || a.seq - b.seq);

  function gateHtml(card, st) {
    const g = card.gate;
    if (!g) return '';
    let txt;
    if (g.state === 'waiting') {
      const pend = (g.pending_groups || []).map((id) => gname(st, id)).join('、');
      txt = `⏳ 等待前置 #${g.depends_on_seq} 集齐回执` + (pend ? `（未回执：${pend}）` : '');
    } else if (g.state === 'unlocked') {
      txt = `🔓 门槛已解锁（前置 #${g.depends_on_seq}）`;
    } else {
      txt = `⏰ 超时揭示（前置 #${g.depends_on_seq} 回执未集齐）`;
    }
    return `<div class="gate gate-${g.state}">${txt}</div>`;
  }

  function trailHtml(card) {
    const trails = (B.answers || []).filter((a) => a.card_id === card.id);
    if (!trails.length) return '';
    let body = '';
    for (const a of trails) {
      const latestId = a.current ? a.trajectory.length - 1 : -1;
      a.trajectory.forEach((t, i) => {
        const cur = a.current && i === latestId;
        body += `<div class="trail-item${cur ? ' latest' : ''}">组 #${a.group_id} · v${t.revision}`
          + ` · 第 ${t.round} 轮 T+${fmt(t.vtime_ms)}${cur ? ' · 当前版本' : ''}`
          + `<div class="ans-body">${escapeHtml(t.body || '（空）')}</div></div>`;
      });
    }
    return `<details><summary>答复版本轨迹（${trails.reduce((n, a) => n + a.trajectory.length, 0)} 条）</summary>${body}</details>`;
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"]/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  function render(st, tab, T) {
    const box = $('#cards');
    const cards = sortedCards(st);
    if (!cards.length) {
      box.innerHTML = '<div class="empty">此时刻没有任何卡片。<br>空演练也可以正常生成与打开复盘包。</div>';
    } else {
      box.innerHTML = cards.map((c) => {
        const expected = c.group_id == null ? [...st.groups.keys()] : [c.group_id];
        const target = c.group_id == null
          ? '<span class="badge b-all">全员</span>'
          : `<span class="badge b-tgt">仅 ${escapeHtml(gname(st, c.group_id))}</span>`;
        const status = c.revealed
          ? (c.timed_out
              ? `<span class="badge b-timeout">超时揭示 T+${fmt(c.revealed_at_ms)}</span>`
              : `<span class="badge b-rev">已揭示 T+${fmt(c.revealed_at_ms)}</span>`)
          : '<span class="badge b-pend">待触发</span>';
        const receipts = c.revealed && expected.length ? `<div class="sub">
            <span class="muted">回执 ${expected.filter((id) => c.receipts.has(id)).length}/${expected.length}</span>`
          + expected.map((id) => `<span class="badge ${c.receipts.has(id) ? 'ok' : 'wait'}">`
            + `${c.receipts.has(id) ? '✓ ' : '未回执 · '}${escapeHtml(gname(st, id))}</span>`).join('')
          + '</div>' : '';
        const answers = c.revealed && expected.length ? expected.map((id) => {
          const a = c.answers.get(id);
          if (!a) return '';
          return `<div class="ans"><div class="ans-meta">${escapeHtml(gname(st, id))} · 版本 ${a.revision}`
            + `${c.answers_locked ? ' · 🔒 已锁定' : ''}</div>`
            + `<div class="ans-body">${escapeHtml(a.body || '（空）')}</div></div>`;
        }).join('') : '';
        return `<div class="card ${c.revealed ? 'revealed' : 'pending'}">
          <div class="card-meta">
            <span><span class="trig">T+${fmt(c.trigger_seconds * 1000)}</span> · #${c.seq} ${target}</span>
            <span>${status}${c.answers_locked ? ' 🔒' : ''}</span>
          </div>
          <div class="card-title">${escapeHtml(c.title)}</div>
          ${c.content ? `<div class="card-content">${escapeHtml(c.content)}</div>` : ''}
          ${gateHtml(c, st)}${receipts}${answers}${trailHtml(c)}
        </div>`;
      }).join('');
    }

    // 事件日志：页签涉及的事件按 ID 序列出，已生效/未生效着色
    let list;
    if (tab.kind === 'round') {
      list = tab.run;
    } else {
      list = tab.pass || [];
    }
    $('#log').innerHTML = list.map((e) => {
      const on = tab.kind !== 'round' || e.vtime_ms <= T;
      return `<li class="${on ? 'applied' : 'future'}"><span class="t">#${e.id} ${
        tab.kind === 'round' ? 'T+' + fmt(e.vtime_ms) : ''}</span><span>${
        TYPE_TEXT[e.type] || e.type} ${escapeHtml(eventBrief(e, st))}</span></li>`;
    }).join('') || '<li class="muted">（无事件）</li>';

    // 时钟动作（全轮次），高亮当前轮
    $('#clog').innerHTML = (B.clock_actions || []).map((a) => {
      const hotCls = tab.kind === 'round' && tab.round.index === a.round ? 'hot' : '';
      const txt = { started: '▶ 开始', paused: '⏸ 暂停', resumed: '⏵ 继续', reset: '⟲ 重置' }[a.action] || a.action;
      return `<li class="${hotCls}">第 ${a.round} 轮 · ${a.kind === 'reset' ? '⟲ 重置' : txt} · T+${fmt(a.vtime_ms)}</li>`;
    }).join('') || '<li>（无时钟动作）</li>';

    // 时钟与阶段
    $('#clock').textContent = fmt(st.clockMs);
    const ph = $('#phase');
    const phMap = {
      prep: ['phase-prep', '编排准备（虚拟时钟冻结）'],
      running: ['phase-running', '进行中'],
      paused: ['phase-paused', '已暂停（虚拟时钟冻结）'],
      reset: ['phase-reset', '已重置'],
    };
    const [cls, label] = phMap[st.phase] || phMap.prep;
    ph.className = 'phase ' + cls;
    ph.textContent = label;
  }

  function eventBrief(e, st) {
    const p = e.payload || {};
    switch (e.type) {
      case 'group_added': return `「${p.name}」`;
      case 'group_renamed': return `#${p.id} →「${p.name}」`;
      case 'group_removed': return `#${p.id}`;
      case 'card_added': return `#${p.seq}「${p.title}」T+${fmt(p.trigger_seconds * 1000)}`;
      case 'card_removed': return `#${p.id}`;
      case 'card_revealed': return `#${p.seq}「${p.title}」T+${fmt(p.revealed_at_ms)}${p.timed_out ? '（超时）' : ''}`;
      case 'receipt_updated': return `卡片 #${p.card_id} / 组 #${p.group_id}：${p.acked ? '已回执' : '撤销'}`;
      case 'answer_updated': return `卡片 #${p.card_id} / 组 #${p.group_id} → v${p.revision}`;
      case 'answer_lock_updated': return `卡片 #${p.card_id}：${p.answers_locked ? '锁定' : '开放'}`;
      case 'gate_updated': return `卡片 #${p.card_id}：${p.gate ? p.gate.state : ''}`;
      case 'clock': return `${p.action} · T+${fmt(p.elapsed_ms)}`;
      case 'reset': return '时钟归零、收回卡片';
      default: return '';
    }
  }

  /* ------------------------------------------------ 页签与滑块 */
  let tabIdx = 0, T = 0, playing = false, playTimer = null;

  function buildTabs() {
    const nav = $('#tabs');
    nav.innerHTML = '';
    tabs.forEach((t, i) => {
      const b = document.createElement('button');
      b.className = 'tab' + (i === tabIdx ? ' active' : '');
      b.textContent = t.label;
      b.onclick = () => { stopPlay(); tabIdx = i; T = 0; refresh(); };
      nav.appendChild(b);
    });
  }

  function maxVtime(tab) {
    if (tab.kind === 'round') return tab.round.max_vtime_ms || 0;
    return 0;
  }

  function refresh() {
    buildTabs();
    const tab = tabs[tabIdx];
    const max = maxVtime(tab);
    const slider = $('#slider');
    const scrubbable = tab.kind === 'round' && max > 0;
    slider.disabled = !scrubbable;
    slider.max = max;
    if (!scrubbable) slider.value = 0;
    if (T > max) T = max;
    $('#tick-max').textContent = 'T+' + fmt(max);
    render(stateAt(tab, T), tab, T);
  }

  $('#slider').addEventListener('input', (e) => { stopPlay(); T = Number(e.target.value); refreshPlayback(); });
  function refreshPlayback() {
    const tab = tabs[tabIdx];
    $('#slider').value = T;
    render(stateAt(tab, T), tab, T);
  }

  $('#btn-play').onclick = () => {
    const tab = tabs[tabIdx];
    if (tab.kind !== 'round' || maxVtime(tab) <= 0) return;
    if (playing) { stopPlay(); return; }
    if (T >= maxVtime(tab)) T = 0;
    playing = true;
    $('#btn-play').textContent = '⏸ 暂停';
    const speed = Number($('#speed').value);
    let last = performance.now();
    playTimer = setInterval(() => {
      const now = performance.now();
      T += (now - last) * speed; last = now;
      const max = maxVtime(tab);
      if (T >= max) { T = max; stopPlay(); }
      refreshPlayback();
    }, 80);
  };
  function stopPlay() {
    playing = false;
    $('#btn-play').textContent = '▶ 播放';
    if (playTimer) { clearInterval(playTimer); playTimer = null; }
  }

  /* ------------------------------------------------ 头部元信息 */
  const c = B.counts || {};
  $('#room-name').textContent = (B.room && B.room.name) || '未命名演练';
  $('#wm').textContent = `水位 #${B.watermark} · 导出于 ${B.generated_at}（UTC）· 离线回放`;
  $('#meta').textContent = `导出时状态：${
    { idle: '待机', running: '进行中', paused: '已暂停' }[B.room.status_at_export] || B.room.status_at_export
  } · 虚拟时钟 T+${fmt(B.room.elapsed_ms_at_export)} · 事件 ${c.events || 0} 条`
    + ` · 共 ${c.rounds || 0} 轮`;
  $('#counts').innerHTML = [
    `参与组 ${c.groups || 0}`, `卡片 ${c.cards || 0}`, `已揭示 ${c.revealed_cards || 0}`,
    `现存回执 ${c.receipts || 0}`, `当前答复 ${c.answers_current || 0}`,
    `答复版本 ${c.answer_revisions || 0}`, `时钟动作 ${c.clock_actions || 0}`,
  ].map((x) => `<span>${x}</span>`).join('');

  buildTabs();
  refresh();
})();
</script>
</body>
</html>
"""
