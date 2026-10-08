/* DarkCNN Studio — painel local. Sem build: só este arquivo, o CSS e a API do pipeline. */
'use strict';

const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
const view = () => $('#view');

const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const fmtClock = (s) => {
  const t = Math.max(0, Math.round(Number(s) || 0));
  const m = Math.floor(t / 60);
  return (m >= 60 ? String(Math.floor(m / 60)) + ':' + String(m % 60).padStart(2, '0') : String(m))
    + ':' + String(t % 60).padStart(2, '0');
};
const fmtAgo = (ts) => {
  if (!ts) return '—';
  const s = (Date.now() / 1000) - ts;
  if (s < 60) return 'agora';
  if (s < 3600) return 'há ' + Math.round(s / 60) + ' min';
  if (s < 86400) return 'há ' + Math.round(s / 3600) + ' h';
  return new Date(ts * 1000).toLocaleDateString('pt-BR');
};

const ICON = {
  warn: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 8v4.5M12 16h.01"/></svg>',
  alert: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" aria-hidden="true"><path d="M12 3 2.5 20h19L12 3z"/><path d="M12 9.5v4.5M12 17h.01"/></svg>',
  play: '<svg width="17" height="17" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5.5 19 12 8 18.5z"/></svg>',
  check: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>',
  x: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>',
  plus: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>',
  stop: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>'
};

/* ---------------------------------------------------------------- API */
async function api(path, opts) {
  const res = await fetch('/api' + path, Object.assign({ headers: { 'Content-Type': 'application/json' } }, opts || {}));
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = { detail: text }; }
  if (!res.ok) throw new Error((data && (data.detail || data.message)) || ('HTTP ' + res.status));
  return data;
}

let toastTimer = null;
function toast(msg, kind) {
  const old = $('.toast');
  if (old) old.remove();
  const el = document.createElement('div');
  el.className = 'toast ' + (kind || '');
  el.setAttribute('role', 'status');
  el.textContent = msg;
  document.body.appendChild(el);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.remove(), kind === 'bad' ? 9000 : 4500);
}

/* ---------------------------------------------------------------- barra lateral */
function initRail() {
  const btn = $('#rail-toggle');
  const apply = (collapsed) => {
    document.body.classList.toggle('rail-collapsed', collapsed);
    btn.setAttribute('aria-expanded', String(!collapsed));
    btn.title = collapsed ? 'Expandir a barra lateral' : 'Retrair a barra lateral';
    btn.setAttribute('aria-label', btn.title);
  };
  apply(localStorage.getItem('dc.rail') === 'collapsed');
  btn.addEventListener('click', () => {
    const next = !document.body.classList.contains('rail-collapsed');
    localStorage.setItem('dc.rail', next ? 'collapsed' : 'open');
    apply(next);
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'b' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); btn.click(); }
  });
}

/* ---------------------------------------------------------------- estado */
const store = { env: null, jobs: [], reviews: [], config: null };

async function refreshEnv() {
  try {
    store.env = await api('/env');
  } catch (e) {
    $('#env').innerHTML = '<div class="k">Ambiente local</div><div class="row"><span class="dot bad"></span><span>'
      + esc(e.message) + '</span></div>';
    return;
  }
  const e = store.env;
  $('#env').innerHTML = '<div class="k">Ambiente local</div>'
    + row(e.ffmpeg, e.ffmpeg ? 'FFmpeg pronto' : 'FFmpeg não encontrado')
    + row(true, 'Whisper ' + esc(e.whisper_model))
    + row(e.api_key, e.api_key ? 'Chave Gemini no .env' : 'GEMINI_API_KEY ausente')
    + row(e.gameplays > 0, e.gameplays + ' gameplay(s)');
  function row(ok, label) {
    return '<div class="row" title="' + esc(label) + '"><span class="dot ' + (ok ? '' : 'bad') + '"></span><span>'
      + esc(label) + '</span></div>';
  }
}

async function refreshJobs() {
  try { store.jobs = (await api('/jobs')).jobs || []; } catch (e) { store.jobs = []; }
  const running = store.jobs.filter((j) => j.status === 'running' || j.status === 'cancelling').length;
  const pill = $('#pill-jobs');
  pill.hidden = running === 0;
  pill.textContent = String(running);
  return running;
}

async function refreshReviews() {
  try { store.reviews = (await api('/reviews')).reviews || []; } catch (e) { store.reviews = []; }
  const pending = store.reviews.reduce((n, r) => n + r.items.filter((i) => (i.status || 'pending') === 'pending').length, 0);
  const pill = $('#pill-review');
  pill.hidden = pending === 0;
  pill.textContent = String(pending);
}

/* ---------------------------------------------------------------- visão geral */
async function viewDashboard() {
  await Promise.all([refreshJobs(), refreshReviews()]);
  const e = store.env || {};
  const pending = store.reviews.reduce((n, r) => n + r.items.filter((i) => (i.status || 'pending') === 'pending').length, 0);
  const approved = store.reviews.reduce((n, r) => n + r.items.filter((i) => i.status === 'approved').length, 0);
  const pct = e.daily_budget ? Math.min(100, Math.round(100 * (e.requests_today || 0) / e.daily_budget)) : 0;

  const warnings = [];
  store.reviews.forEach((r) => {
    r.items.forEach((it) => {
      (it.opening_warnings || []).forEach((w) => warnings.push(['warn', r.title, w]));
      if (it.hook_ok === false) warnings.push(['warn', r.title, 'o gancho citado pelo modelo não confere com a transcrição']);
    });
    if (r.kind === 'narration') warnings.push(['info', r.title, 'voz sintética: marque como conteúdo alterado na plataforma']);
    if (!r.source) warnings.push(['alert', r.title, 'fonte/licença não registrada nesta saída']);
  });

  view().innerHTML = `
    <div class="page-head">
      <div>
        <h1>Visão geral</h1>
        <p class="sub">Tudo roda nesta máquina. A postagem continua manual.</p>
      </div>
      <div class="row">
        <a class="btn" href="#/config">Configurações</a>
        <a class="btn primary" href="#/gerar">${ICON.plus}Nova geração</a>
      </div>
    </div>

    <div class="grid g4" style="margin-bottom:20px">
      <div class="card kpi"><div class="k">Cortes aprovados</div><div class="v">${approved}</div><div class="n">em ${store.reviews.length} saída(s)</div></div>
      <div class="card kpi"><div class="k">Aguardando revisão</div><div class="v" style="color:var(--accent)">${pending}</div><div class="n">${store.reviews.length ? esc(store.reviews[0].title) : 'nada gerado ainda'}</div></div>
      <div class="card kpi"><div class="k">Requisições Gemini hoje</div><div class="v">${e.requests_today || 0}<small>/${e.daily_budget || '—'}</small></div><div class="meter"><i style="width:${pct}%"></i></div></div>
      <div class="card kpi"><div class="k">Tokens hoje</div><div class="v">${(e.tokens_today || 0).toLocaleString('pt-BR')}</div><div class="n">entrada + saída</div></div>
    </div>

    <div class="split">
      <section class="wide card" style="padding:0;overflow:hidden">
        <div class="spread" style="padding:16px 18px;border-bottom:1px solid var(--line)">
          <h2>Execuções recentes</h2>
          <a class="btn sm" href="#/execucoes">Ver todas</a>
        </div>
        ${jobsTable(store.jobs.slice(0, 6))}
      </section>

      <div class="side">
        <section class="card">
          <h2 style="margin-bottom:14px">Antes de postar</h2>
          ${warnings.length ? '<div class="stack" style="gap:13px">' + warnings.slice(0, 6).map(([k, who, w]) => `
            <div class="note ${k === 'alert' ? 'bad' : k === 'info' ? 'info' : 'warn'}" style="border:none;background:transparent;padding:0">
              ${k === 'alert' ? ICON.alert : ICON.warn}
              <span><b style="display:block;font-size:13px;margin-bottom:2px">${esc(who)}</b><span style="font-size:12px;color:var(--dim);line-height:1.5">${esc(w)}</span></span>
            </div>`).join('') + '</div>'
            : '<p class="hint" style="margin:0">Nada pendente. Os avisos de licença, abertura fraca e voz sintética aparecem aqui.</p>'}
        </section>

        <section class="card">
          <h2 style="margin-bottom:12px">Saídas</h2>
          <p class="hint" style="margin:0 0 12px">Pasta: <span class="mono">${esc(e.output_dir || 'output/')}</span></p>
          ${store.reviews.slice(0, 4).map((r) => `
            <a class="row" href="#/revisao?r=${encodeURIComponent(r.id)}" style="justify-content:space-between;text-decoration:none;padding:10px 0;border-top:1px solid var(--line)">
              <span style="font-size:13px;color:var(--text)">${esc(r.title)}</span>
              <span class="chip ${r.kind}">${r.items.length} item(ns)</span>
            </a>`).join('') || '<p class="hint" style="margin:0">Nenhuma saída ainda.</p>'}
        </section>
      </div>
    </div>`;
}

function jobsTable(jobs) {
  if (!jobs.length) return '<p class="empty" style="margin:18px">Nenhuma execução nesta sessão.</p>';
  const label = { run: 'Cortes', compile: 'Compilado', narrate: 'Narração', render: 'Re-render' };
  const state = { done: 'Pronto', running: 'Em andamento', cancelling: 'Interrompendo', error: 'Erro', cancelled: 'Interrompida' };
  return '<div class="scroll-x"><table><thead><tr><th>Execução</th><th>Modo</th><th>Status</th><th>Quando</th></tr></thead><tbody>'
    + jobs.map((j) => `<tr>
        <td><a href="#/execucoes/${j.id}" style="font-weight:600;text-decoration:none">${esc(j.label)}</a>
          <div class="mono" style="font-size:12px;color:var(--dim);margin-top:3px">${j.id}</div></td>
        <td><span class="chip ${j.mode === 'narrate' ? 'narration' : j.mode === 'compile' ? 'compilation' : 'cuts'}">${label[j.mode] || j.mode}</span></td>
        <td><span class="state ${j.status === 'cancelling' ? 'running' : j.status}"><span class="dot ${j.status === 'done' ? '' : j.status === 'error' ? 'bad' : 'wait'}"></span>${state[j.status] || j.status}</span>
          ${j.error ? '<div style="font-size:12px;color:var(--err);margin-top:4px">' + esc(j.error.slice(0, 90)) + '</div>' : ''}</td>
        <td class="mono dim" style="font-size:13px">${fmtAgo(j.created)}</td>
      </tr>`).join('')
    + '</tbody></table></div>';
}

/* ---------------------------------------------------------------- nova geração */
const gen = {
  mode: 'run',
  source: '',
  license: '',
  profile: 'talk',
  theme: 'top 5 finalizações',
  clips: 5, min: 30, max: 60,
  layout: 'blur', text_mode: 'both',
  model: '', thinking: 'medium', judge: true,
  narrate_format: 'curiosidade', topic: '', count: 1, target_s: 45, voice: '',
  gameplay_dir: '', game_volume: 6, force: false
};

function genOverrides() {
  const g = gen;
  const o = { layout: g.layout, gemini_model: g.model, thinking_level: g.thinking };
  if (g.mode === 'narrate') {
    Object.assign(o, {
      narrate_format: g.narrate_format, count: Number(g.count), target_s: Number(g.target_s),
      tts_voice: g.voice, game_volume: Number(g.game_volume) / 100, text_mode: 'captions'
    });
    if (g.topic) o.topic = g.topic;
    if (g.gameplay_dir) o.gameplay_dir = g.gameplay_dir;
    return o;
  }
  Object.assign(o, {
    profile: g.profile, clips_per_video: Number(g.clips),
    min_clip_s: Number(g.min), max_clip_s: Number(g.max),
    text_mode: g.text_mode, judge: !!g.judge
  });
  if (g.mode === 'compile') o.theme = g.theme;
  if (g.license) o.license = g.license;
  return o;
}

function genCommand() {
  const g = gen;
  const q = (s) => '"' + String(s).replace(/"/g, '\\"') + '"';
  if (g.mode === 'narrate') {
    return ['python -m darkcnn narrate', '  --format ' + g.narrate_format + (g.topic ? ' --topic ' + q(g.topic) : ''),
      '  --count ' + g.count + ' --target-s ' + g.target_s + ' --voice ' + (g.voice || 'Kore'),
      '  --gameplay-dir ' + (g.gameplay_dir || 'gameplays/') + ' --game-volume ' + (g.game_volume / 100).toFixed(2),
      '  --layout ' + g.layout + ' --model ' + g.model + ' --thinking-level ' + g.thinking
    ].join(' \\\n') + (g.force ? ' \\\n  --force' : '');
  }
  const head = 'python -m darkcnn ' + (g.mode === 'compile' ? 'compile' : 'run') + ' ' + q(g.source || '<link ou arquivo>');
  return [head,
    g.mode === 'compile' ? '  --theme ' + q(g.theme) : '  --profile ' + g.profile,
    '  --clips ' + g.clips + ' --min ' + g.min + ' --max ' + g.max,
    '  --layout ' + g.layout + ' --text-mode ' + g.text_mode,
    '  --model ' + g.model + ' --thinking-level ' + g.thinking + (g.judge ? '' : ' --no-judge') + (g.force ? ' --force' : '')
  ].join(' \\\n');
}

function genEstimate() {
  const g = gen;
  const e = store.env || {};
  if (g.mode === 'narrate') {
    const perVideo = g.narrate_format === 'voce-prefere' ? 4 : 1;
    const voice = perVideo * Number(g.count);
    return [['Requisições ao Gemini', (1 + voice) + ' (1 roteiro + ' + voice + ' de voz)'],
      ['Whisper (alinhar legenda)', 'local, alguns segundos'],
      ['Render', '~' + Math.round(g.target_s * 1.4 * g.count) + ' s'],
      ['Orçamento do dia', (e.requests_today || 0) + 1 + voice + '/' + (e.daily_budget || '—')]];
  }
  const reqs = (g.profile === 'visual' ? 2 : 1) + (g.judge ? 1 : 0);
  return [['Requisições ao Gemini', reqs + (g.judge ? ' (seleção + juiz)' : ' (só seleção)')],
    ['Transcrição', g.profile === 'visual' ? 'não usa Whisper' : '~16 min por hora de vídeo'],
    ['Render', '~' + Math.round(g.clips * ((Number(g.min) + Number(g.max)) / 2) * 1.4) + ' s'],
    ['Orçamento do dia', (e.requests_today || 0) + reqs + '/' + (e.daily_budget || '—')]];
}

async function viewGenerate() {
  if (!store.config) store.config = await api('/config');
  const eff = store.config.effective;
  if (!gen.model) gen.model = eff.gemini_model;
  if (!gen.voice) gen.voice = eff.tts_voice;
  if (!gen.gameplay_dir) gen.gameplay_dir = eff.gameplay_dir || 'gameplays/';
  if (!gen.license) gen.license = eff.license || '';
  const isNarr = gen.mode === 'narrate';

  const sel = (name, value, options) => '<select data-gen="' + name + '">' + options.map(([v, t]) =>
    '<option value="' + esc(v) + '"' + (String(value) === String(v) ? ' selected' : '') + '>' + esc(t) + '</option>').join('') + '</select>';
  const num = (name, value, min, max) => '<input type="number" data-gen="' + name + '" value="' + esc(value) + '" min="' + min + '" max="' + max + '">';

  view().innerHTML = `
    <div class="page-head">
      <div><h1>Nova geração</h1>
      <p class="sub">O Gemini escolhe o que entra; o corte exato vem do Whisper e do FFmpeg, aqui.</p></div>
    </div>

    <div class="modes">
      <button type="button" class="mode ${gen.mode === 'run' ? 'on' : ''}" data-mode="run">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><circle cx="6" cy="6" r="2.6"/><circle cx="6" cy="18" r="2.6"/><path d="M8.4 7.6 20 17M8.4 16.4 20 7"/></svg>
        <span>Cortes virais<span class="d">um vídeo longo → vários cortes</span></span></button>
      <button type="button" class="mode ${gen.mode === 'compile' ? 'on' : ''}" data-mode="compile">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M4 19V9M10 19V5M16 19v-7M22 19h-20"/></svg>
        <span>Compilado Top N<span class="d">contagem #5 → #1 num vídeo</span></span></button>
      <button type="button" class="mode ${gen.mode === 'narrate' ? 'on' : ''}" data-mode="narrate">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>
        <span>Narração sobre gameplay<span class="d">roteiro e voz feitos pela IA</span></span></button>
    </div>

    <div class="split">
      <form class="wide stack" id="gen-form" autocomplete="off">
        ${isNarr ? '' : `
        <section class="card">
          <h2 style="margin-bottom:16px">Fonte</h2>
          <div class="field"><label for="g-src">Link do YouTube ou arquivo local</label>
            <input id="g-src" type="text" class="mono" data-gen="source" value="${esc(gen.source)}" placeholder="https://youtu.be/… ou C:\\videos\\podcast.mp4">
            <p class="hint">O download fica em cache: rodar de novo não baixa outra vez.</p></div>
          <div class="grid g2" style="margin-top:16px">
            <div class="field"><label for="g-lic">Licença ou permissão</label>
              <input id="g-lic" type="text" data-gen="license" value="${esc(gen.license)}" placeholder="CC-BY 4.0 ou autorizado por…"></div>
            <div class="field"><label for="g-prof">Perfil do conteúdo</label>
              ${sel('profile', gen.profile, [['talk', 'talk — tem fala (transcrição)'], ['visual', 'visual — sem fala (planos e volume)']])}</div>
          </div>
        </section>`}

        ${gen.mode === 'compile' ? `
        <section class="card">
          <h2 style="margin-bottom:16px">Tema do compilado</h2>
          <div class="field"><label for="g-theme">O Gemini só aceita momentos que encaixam neste tema</label>
            <input id="g-theme" type="text" data-gen="theme" value="${esc(gen.theme)}">
            <p class="hint">Se menos momentos encaixarem, o compilado sai com menos e o review avisa.</p></div>
        </section>` : ''}

        ${isNarr ? `
        <section class="card">
          <h2 style="margin-bottom:16px">Roteiro</h2>
          <div class="grid g2">
            <div class="field"><label for="g-fmt">Formato</label>
              ${sel('narrate_format', gen.narrate_format, [['curiosidade', 'curiosidade'], ['voce-prefere', 'você prefere (opções na tela)'], ['e-se', 'e se…']])}</div>
            <div class="field"><label for="g-topic">Tema <span class="dim" style="font-weight:500">— vazio: a IA escolhe</span></label>
              <input id="g-topic" type="text" data-gen="topic" value="${esc(gen.topic)}" placeholder="oceano profundo"></div>
          </div>
          <div class="grid g3" style="margin-top:16px">
            <div class="field"><label>Vídeos</label>${num('count', gen.count, 1, 10)}</div>
            <div class="field"><label>Duração alvo (s)</label>${num('target_s', gen.target_s, 10, 180)}</div>
            <div class="field"><label>Voz</label>${sel('voice', gen.voice, (store.voices || ['Puck', 'Kore', 'Charon', 'Fenrir', 'Leda']).map((v) => [v, v]))}</div>
          </div>
        </section>
        <section class="card">
          <h2 style="margin-bottom:16px">Gameplay de fundo</h2>
          <div class="grid g2">
            <div class="field"><label for="g-gdir">Pasta</label>
              <input id="g-gdir" type="text" class="mono" data-gen="gameplay_dir" value="${esc(gen.gameplay_dir)}">
              <p class="hint">${(store.env && store.env.gameplays) || 0} arquivo(s) encontrado(s). O trecho é sorteado e testado antes do render.</p></div>
            <div class="field"><label for="g-vol">Volume da gameplay <span class="mono" style="color:var(--accent)" id="vol-out">${gen.game_volume}%</span></label>
              <input id="g-vol" type="range" min="0" max="30" data-gen="game_volume" value="${gen.game_volume}">
              <p class="hint">Zero deixa só a voz. A trilha do jogo pode gerar reclamação de direitos.</p></div>
          </div>
        </section>` : `
        <section class="card">
          <h2 style="margin-bottom:16px">Recorte</h2>
          <div class="grid g3">
            <div class="field"><label>${gen.mode === 'compile' ? 'Momentos no top' : 'Quantos cortes'}</label>${num('clips', gen.clips, 1, 12)}</div>
            <div class="field"><label>Mínimo (s)</label>${num('min', gen.min, 3, 180)}</div>
            <div class="field"><label>Máximo (s)</label>${num('max', gen.max, 5, 180)}</div>
          </div>
          <div id="range-warn">${rangeWarn()}</div>
        </section>`}

        <section class="card">
          <h2 style="margin-bottom:16px">Aparência</h2>
          <div class="grid g2">
            <div class="field"><label>Enquadramento</label>
              ${sel('layout', gen.layout, [['blur', 'blur — vídeo inteiro sobre fundo desfocado'], ['crop', 'crop — recorte central']])}</div>
            ${isNarr ? '' : `<div class="field"><label>Texto na tela</label>
              ${sel('text_mode', gen.text_mode, [['both', 'contexto no topo + legenda'], ['captions', 'legenda por palavra'], ['titled', 'título + frase-gancho'], ['ranked', 'compilado (posição grande)'], ['none', 'sem texto']])}</div>`}
          </div>
        </section>

        <section class="card">
          <h2 style="margin-bottom:16px">Inteligência</h2>
          <div class="grid g2">
            <div class="field"><label>Modelo</label><input type="text" class="mono" data-gen="model" value="${esc(gen.model)}"></div>
            <div class="field"><label>Raciocínio</label>
              ${sel('thinking', gen.thinking, [['off', 'desligado'], ['low', 'baixo'], ['medium', 'médio'], ['high', 'alto — melhor e mais lento']])}</div>
          </div>
          ${isNarr ? '' : `
          <label class="check" style="margin-top:16px">
            <input type="checkbox" data-gen="judge" ${gen.judge ? 'checked' : ''}>
            <span><span class="t">Segunda passada com o juiz</span>
            <span class="d">O Gemini assiste aos candidatos num vídeo de 360p e os compara entre si. Uma requisição a mais, de uns 50 mil tokens.</span></span>
          </label>`}
          <label class="check" style="margin-top:12px">
            <input type="checkbox" data-gen="force" ${gen.force ? 'checked' : ''}>
            <span><span class="t">Ignorar o cache</span>
            <span class="d">Refaz transcrição, análise e roteiro mesmo que nada tenha mudado. Gasta requisições de novo.</span></span>
          </label>
        </section>
      </form>

      <aside class="side">
        <section class="card">
          <h2 style="margin-bottom:14px">Custo estimado</h2>
          <dl id="estimate" style="margin:0" class="stack">${estimateRows()}</dl>
        </section>
        <section class="card term">
          <div class="spread" style="margin-bottom:12px"><h2 style="font-size:14px">Comando equivalente</h2>
            <button type="button" class="btn sm" id="copy-cmd">Copiar</button></div>
          <pre class="code" id="cmd">${esc(genCommand())}</pre>
          <p class="hint">A tela só monta o comando: quem executa é o mesmo pipeline do terminal.</p>
        </section>
        <div class="stack" style="gap:10px">
          <button type="button" class="btn primary" id="go" style="min-height:48px;font-size:15px">${ICON.play}Gerar agora</button>
        </div>
      </aside>
    </div>`;

  $$('[data-mode]').forEach((b) => b.addEventListener('click', () => {
    gen.mode = b.dataset.mode;
    if (gen.mode === 'compile') { gen.clips = 5; gen.min = 8; gen.max = 25; gen.text_mode = 'ranked'; }
    if (gen.mode === 'run') { gen.min = 30; gen.max = 60; gen.text_mode = 'both'; }
    viewGenerate();
  }));

  $$('[data-gen]').forEach((el) => {
    const evt = el.type === 'range' ? 'input' : (el.tagName === 'SELECT' || el.type === 'checkbox' ? 'change' : 'input');
    el.addEventListener(evt, () => {
      gen[el.dataset.gen] = el.type === 'checkbox' ? el.checked : el.value;
      if (el.dataset.gen === 'game_volume') $('#vol-out').textContent = el.value + '%';
      $('#cmd').textContent = genCommand();
      $('#estimate').innerHTML = estimateRows();
      const rw = $('#range-warn');
      if (rw) rw.innerHTML = rangeWarn();
    });
  });

  $('#copy-cmd').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(genCommand()); toast('Comando copiado.', 'good'); }
    catch (e) { toast('O navegador não liberou a área de transferência.', 'bad'); }
  });

  $('#go').addEventListener('click', async () => {
    const btn = $('#go');
    btn.disabled = true;
    try {
      const job = await api('/jobs', {
        method: 'POST',
        body: JSON.stringify({
          mode: gen.mode, source: gen.mode === 'narrate' ? null : gen.source,
          label: gen.mode === 'narrate' ? (gen.topic || gen.narrate_format) : gen.source,
          force: !!gen.force, overrides: genOverrides()
        })
      });
      location.hash = '#/execucoes/' + job.id;
    } catch (e) {
      toast(e.message, 'bad');
      btn.disabled = false;
    }
  });

  function estimateRows() {
    return genEstimate().map(([k, v], i) => `<div class="spread" style="gap:12px;align-items:baseline${i === 3 ? ';border-top:1px solid var(--line);padding-top:13px' : ''}">
      <dt style="font-size:13px;color:${i === 3 ? 'var(--text)' : 'var(--muted)'};font-weight:${i === 3 ? '600' : '400'}">${esc(k)}</dt>
      <dd class="mono" style="margin:0;font-size:13px;font-weight:700;color:${i === 3 ? 'var(--accent)' : 'var(--text)'}">${esc(v)}</dd></div>`).join('');
  }
}

function rangeWarn() {
  const span = Number(gen.max) - Number(gen.min);
  if (gen.mode === 'narrate' || span >= 20) return '';
  return `<p class="note warn" style="margin-top:14px">${ICON.warn}<span>Faixa de ${span} s é estreita: o corte só fecha em fim de frase, então muitos candidatos caem fora. Abra para uns 20 s de folga.</span></p>`;
}

/* ---------------------------------------------------------------- execuções */
let es = null;

function closeStream() {
  if (es) { es.close(); es = null; }
}

const STEP_RULES = [
  ['Entrada', /baixando|download|fonte|resolve/i],
  ['Transcrição', /whisper|palavras|transcri/i],
  ['Roteiro / seleção', /roteiro|pedindo|Gemini ok|candidat/i],
  ['Juiz', /juiz|reel/i],
  ['Voz', /voz|bloco\(s\) sintetizado|sintetizando/i],
  ['Render', /render|gameplay|corte \d|\.mp4/i]
];

async function viewJob(id) {
  if (!id) {
    await refreshJobs();
    view().innerHTML = `<div class="page-head"><div><h1>Execuções</h1><p class="sub">Desta sessão do painel. O log completo de cada uma fica no run.log do workspace.</p></div>
      <a class="btn primary" href="#/gerar">${ICON.plus}Nova geração</a></div>
      <section class="card" style="padding:0;overflow:hidden">${jobsTable(store.jobs)}</section>`;
    return;
  }

  let job;
  try { job = await api('/jobs/' + id); } catch (e) {
    view().innerHTML = '<p class="empty">' + esc(e.message) + '</p>';
    return;
  }
  const label = { run: 'Cortes', compile: 'Compilado', narrate: 'Narração', render: 'Re-render' };
  const live = job.status === 'running' || job.status === 'cancelling';

  view().innerHTML = `
    <nav class="sub" style="margin-bottom:12px"><a href="#/execucoes" style="color:var(--dim);text-decoration:none">Execuções</a>
      <span style="color:#4C535E"> / </span><span class="mono muted">${job.id}</span></nav>
    <div class="page-head">
      <div>
        <div class="row" style="margin-bottom:8px">
          <span class="chip ${live ? 'pending' : job.status === 'done' ? 'approved' : 'rejected'}">${live ? 'Em andamento' : job.status === 'done' ? 'Concluída' : job.status === 'cancelled' ? 'Interrompida' : 'Erro'}</span>
          <span class="chip ${job.mode === 'narrate' ? 'narration' : job.mode === 'compile' ? 'compilation' : 'cuts'}">${label[job.mode] || job.mode}</span>
        </div>
        <h1>${esc(job.label)}</h1>
        <p class="sub mono">${esc(JSON.stringify(job.params).slice(1, -1).replace(/"/g, '').slice(0, 160))}</p>
      </div>
      <div class="row">
        ${job.review ? `<a class="btn" href="#/revisao">Abrir revisão</a>` : ''}
        ${live ? `<button type="button" class="btn danger" id="cancel">${ICON.stop}Interromper</button>` : `<a class="btn primary" href="#/gerar">${ICON.plus}Nova geração</a>`}
      </div>
    </div>

    <ol class="steps" id="steps" style="grid-template-columns:repeat(${STEP_RULES.length}, minmax(0,1fr))">
      ${STEP_RULES.map(([n]) => `<li class="step"><div class="n">${esc(n)}</div><div class="s">—</div></li>`).join('')}
    </ol>

    ${job.error ? `<p class="note bad" style="margin-bottom:18px">${ICON.alert}<span class="mono" style="font-size:12.5px">${esc(job.error)}</span></p>` : ''}

    <section class="card term" style="padding:0;overflow:hidden">
      <div class="spread" style="padding:14px 18px;border-bottom:1px solid var(--line)">
        <h2 style="font-size:15px">Log</h2>
        <label class="row" style="gap:8px;font-size:13px;color:var(--muted)"><input type="checkbox" id="follow" checked>Acompanhar o fim</label>
      </div>
      <div class="log" id="log"></div>
    </section>`;

  const logEl = $('#log');
  const seen = [];
  const push = (line) => {
    seen.push(line);
    const m = /^(\d\d:\d\d:\d\d)\s+(\S+)\s?([\s\S]*)$/.exec(line);
    const div = document.createElement('div');
    div.innerHTML = m
      ? `<span class="ts">${esc(m[1])}</span> <span class="lv-${esc(m[2])}">${esc(m[2])}</span> ${esc(m[3])}`
      : esc(line);
    logEl.appendChild(div);
    if ($('#follow') && $('#follow').checked) logEl.scrollTop = logEl.scrollHeight;
    paintSteps(seen);
  };

  if ($('#cancel')) {
    $('#cancel').addEventListener('click', async () => {
      $('#cancel').disabled = true;
      try { await api('/jobs/' + job.id + '/cancel', { method: 'POST' }); toast('Interrupção pedida.'); }
      catch (e) { toast(e.message, 'bad'); $('#cancel').disabled = false; }
    });
  }

  closeStream();
  es = new EventSource('/api/jobs/' + job.id + '/stream');
  es.addEventListener('line', (ev) => push(ev.data));
  es.addEventListener('end', () => {
    closeStream();
    refreshJobs();
    refreshReviews();
    if (location.hash.indexOf('/execucoes/' + job.id) >= 0) viewJob(job.id);
  });
  es.onerror = () => closeStream();
}

function paintSteps(lines) {
  const items = $$('#steps .step');
  if (!items.length) return;
  let last = -1;
  const hits = STEP_RULES.map(([, re]) => lines.filter((l) => re.test(l)));
  hits.forEach((h, i) => { if (h.length) last = i; });
  items.forEach((li, i) => {
    li.className = 'step' + (i < last ? ' done' : i === last ? ' now' : '');
    const n = hits[i].length;
    $('.s', li).textContent = n ? n + ' linha(s)' : '—';
  });
}

/* ---------------------------------------------------------------- revisão */
const rev = { id: null, rank: null };

async function viewReview(params) {
  await refreshReviews();
  if (!store.reviews.length) {
    view().innerHTML = `<div class="page-head"><div><h1>Revisão</h1><p class="sub">Nada gerado ainda.</p></div>
      <a class="btn primary" href="#/gerar">${ICON.plus}Nova geração</a></div>
      <p class="empty">Quando uma geração terminar, os cortes aparecem aqui para aprovar ou descartar.</p>`;
    return;
  }
  const wanted = (params && params.get('r')) || rev.id;
  const r = store.reviews.filter((x) => x.id === wanted)[0] || store.reviews[0];
  rev.id = r.id;
  const items = r.items;
  const sel = items.filter((i) => Number(i.rank) === Number(rev.rank))[0] || items[0] || null;
  rev.rank = sel ? sel.rank : null;
  const count = (s) => items.filter((i) => (i.status || 'pending') === s).length;
  const statusLabel = { pending: 'Pendente', approved: 'Aprovado', rejected: 'Descartado' };

  view().innerHTML = `
    <div class="page-head">
      <div>
        <h1>Revisão</h1>
        <p class="sub">${esc(r.title)} ${r.source ? '· <span class="mono">' + esc(String(r.source).slice(0, 60)) + '</span>' : '· fonte não registrada'}</p>
      </div>
      <div class="row">
        ${store.reviews.length > 1 ? '<select id="pick-review" style="min-width:260px">' + store.reviews.map((x) =>
          '<option value="' + esc(x.id) + '"' + (x.id === r.id ? ' selected' : '') + '>' + esc(x.title) + ' (' + x.items.length + ')</option>').join('') + '</select>' : ''}
        ${r.has_review_md ? `<a class="btn" href="/api/reviews/${encodeURIComponent(r.id)}/markdown" target="_blank" rel="noopener">review.md</a>` : ''}
        ${r.kind !== 'narration' ? `<button type="button" class="btn primary" id="rerender">Re-renderizar alterados</button>` : ''}
      </div>
    </div>

    <div class="tabs" style="margin-bottom:18px">
      <span class="tab on">Pendentes <span class="c">${count('pending')}</span></span>
      <span class="tab">Aprovados <span class="c">${count('approved')}</span></span>
      <span class="tab">Descartados <span class="c">${count('rejected')}</span></span>
    </div>

    <div class="split">
      <section class="stack" style="flex:1 1 360px;gap:12px" aria-label="Itens">
        ${items.map((it) => `
          <button type="button" class="cand ${Number(it.rank) === Number(rev.rank) ? 'on' : ''}" data-rank="${esc(it.rank)}">
            <span class="thumb"><span class="r">#${esc(it.rank)}</span><span class="d">${Math.round(it.duration || 0)}s</span></span>
            <span style="min-width:0;flex:1 1 auto">
              <span class="t">${esc(it.title || '(sem título)')}</span>
              <span class="m">${it.start != null ? fmtClock(it.start) + '–' + fmtClock(it.end) : esc(it.topic || '')}</span>
              <span class="row" style="gap:6px">
                ${it.judge_score != null ? '<span class="chip score">juiz ' + esc(it.judge_score) + '</span>' : ''}
                ${it.score != null ? '<span class="chip mono">1ª ' + esc(it.score) + '</span>' : ''}
                ${it.voice ? '<span class="chip narration">' + esc(it.voice) + '</span>' : ''}
                <span class="chip ${it.status || 'pending'}">${statusLabel[it.status || 'pending']}</span>
              </span>
            </span>
          </button>`).join('')}
      </section>

      <section class="wide card" style="padding:0;overflow:hidden">${sel ? detail(r, sel) : '<p class="empty">Esta saída não tem itens.</p>'}</section>
    </div>`;

  if ($('#pick-review')) {
    $('#pick-review').addEventListener('change', (e) => {
      rev.id = e.target.value; rev.rank = null; viewReview();
    });
  }
  $$('[data-rank]').forEach((b) => b.addEventListener('click', () => {
    rev.rank = b.dataset.rank; viewReview();
  }));
  $$('[data-status]').forEach((b) => b.addEventListener('click', async () => {
    await patchItem(r.id, rev.rank, { status: b.dataset.status });
  }));
  if ($('#save-trim')) {
    $('#save-trim').addEventListener('click', async () => {
      const s = parseTime($('#t-start').value), e2 = parseTime($('#t-end').value);
      if (s == null || e2 == null || e2 <= s) { toast('Informe início e fim como MM:SS ou segundos, com fim depois do início.', 'bad'); return; }
      await patchItem(r.id, rev.rank, { start: s, end: e2, title: $('#t-title').value });
    });
  }
  if ($('#rerender')) {
    $('#rerender').addEventListener('click', async () => {
      const info = r.source ? { source: r.source } : null;
      if (!info) { toast('Esta saída não guarda a fonte: re-renderize pelo terminal com `darkcnn render`.', 'bad'); return; }
      try {
        const job = await api('/jobs', {
          method: 'POST',
          body: JSON.stringify({ mode: 'render', source: r.source, label: 'Re-render: ' + r.title, overrides: {} })
        });
        location.hash = '#/execucoes/' + job.id;
      } catch (e) { toast(e.message, 'bad'); }
    });
  }

  async function patchItem(rid, rank, body) {
    try {
      await api('/reviews/' + encodeURI(rid) + '/items/' + rank, { method: 'PATCH', body: JSON.stringify(body) });
      toast('Salvo no selection.json.', 'good');
      await viewReview();
    } catch (e) { toast(e.message, 'bad'); }
  }
}

function parseTime(v) {
  const s = String(v || '').trim();
  if (!s) return null;
  if (/^\d+(\.\d+)?$/.test(s)) return Number(s);
  const m = /^(\d+):([0-5]?\d)(?:\.(\d+))?$/.exec(s);
  if (!m) return null;
  return Number(m[1]) * 60 + Number(m[2]) + (m[3] ? Number('0.' + m[3]) : 0);
}

function detail(r, it) {
  const statusLabel = { pending: 'Pendente', approved: 'Aprovado', rejected: 'Descartado' };
  const mediaUrl = it.file ? '/api/media/' + encodeURI(r.id) + '/' + encodeURIComponent(it.file) : null;
  const sub = [['Gancho', it.hook], ['Autocontido', it.standalone], ['Emoção', it.emotion], ['Payoff', it.payoff]]
    .filter(([, v]) => v != null);
  return `
    <div style="display:flex;flex-wrap:wrap">
      <div style="flex:1 1 280px;padding:20px;background:var(--panel-2);border-right:1px solid var(--line)">
        ${mediaUrl
          ? `<video class="preview" src="${mediaUrl}" controls preload="metadata" style="background:#000"></video>`
          : '<div class="preview" style="display:flex;align-items:center;justify-content:center;color:var(--dim);font-size:13px">sem arquivo renderizado</div>'}
        <div class="row mono dim" style="justify-content:space-between;font-size:11px;max-width:250px;margin:10px 0 16px">
          <span>1080×1920</span><span>${Math.round(it.duration || 0)} s</span><span>${esc(it.file ? it.file.split('.').pop() : '')}</span>
        </div>
        ${it.start != null ? `
        <div class="grid g2" style="max-width:250px;gap:10px">
          <div class="field"><label for="t-start">Início</label><input id="t-start" class="mono" type="text" value="${fmtClock(it.start)}"></div>
          <div class="field"><label for="t-end">Fim</label><input id="t-end" class="mono" type="text" value="${fmtClock(it.end)}"></div>
        </div>
        <div class="field" style="max-width:250px;margin-top:12px"><label for="t-title">Título</label>
          <input id="t-title" type="text" value="${esc(it.title || '')}"></div>
        <button type="button" class="btn sm" id="save-trim" style="margin-top:12px">Salvar limites</button>
        <p class="hint">Depois de salvar, use "Re-renderizar alterados": só este corte é refeito, sem Gemini.</p>` : ''}
      </div>

      <div style="flex:999 1 340px;min-width:0;padding:20px">
        <div class="spread" style="align-items:flex-start;margin-bottom:4px">
          <h2 style="font-size:19px;line-height:1.3">${esc(it.title || '(sem título)')}</h2>
          <span class="chip ${it.status || 'pending'}">${statusLabel[it.status || 'pending']}</span>
        </div>
        <p class="mono dim" style="margin:0 0 18px;font-size:12px">
          ${it.start != null ? fmtClock(it.start) + '–' + fmtClock(it.end) + ' na fonte · ' : ''}${esc(it.file || '')}</p>

        ${it.context ? `<p class="note info" style="margin-bottom:16px">${ICON.warn}<span><b>Contexto no topo:</b> ${esc(it.context)}</span></p>` : ''}
        ${it.hook_text ? `<p style="margin:0 0 16px;font-size:13px;color:var(--muted)"><b>Frase-gancho:</b> ${esc(it.hook_text)}</p>` : ''}

        ${sub.length ? `<div class="grid g4" style="gap:10px;margin-bottom:18px">${sub.map(([k, v]) => `
          <div class="card flat" style="padding:12px"><div style="font-size:11px;color:var(--dim);font-weight:600;margin-bottom:6px">${k}</div>
          <div class="mono" style="font-size:18px;font-weight:700">${esc(v)}</div></div>`).join('')}</div>` : ''}

        ${it.judge_score != null ? `
        <div class="judge" style="margin-bottom:16px">
          <div class="row" style="gap:9px;margin-bottom:11px">
            <span class="chip score">juiz ${esc(it.judge_score)}/100</span>
            ${it.score != null ? '<span class="dim" style="font-size:12px">a 1ª passada deu ' + esc(it.score) + '/10</span>' : ''}
          </div>
          ${it.judge_strength ? '<p><span class="plus">+</span><span>' + esc(it.judge_strength) + '</span></p>' : ''}
          ${it.judge_weakness ? '<p><span class="minus">−</span><span>' + esc(it.judge_weakness) + '</span></p>' : ''}
        </div>` : ''}

        ${it.reason ? `<div style="margin-bottom:16px"><div style="font-size:11px;font-weight:700;color:var(--dim);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px">Por que foi escolhido</div>
          <p style="margin:0;font-size:13px;color:#CBD1DA;line-height:1.65">${esc(it.reason)}</p></div>` : ''}

        ${(it.lines || []).length ? `<div style="margin-bottom:16px"><div style="font-size:11px;font-weight:700;color:var(--dim);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px">Roteiro falado</div>
          <div class="stack" style="gap:7px">${it.lines.map((l) => `<p style="margin:0;font-size:13px;color:#CBD1DA;line-height:1.55">
            <span class="mono dim" style="font-size:11px">${fmtClock(l.start)}</span>
            ${l.kind === 'escolha' ? '<b style="color:var(--accent)">[' + esc(l.option_a) + ' × ' + esc(l.option_b) + ']</b> ' : ''}${esc(l.text)}</p>`).join('')}</div></div>` : ''}

        ${(it.opening_warnings || []).map((w) => `<p class="note warn" style="margin-bottom:12px">${ICON.warn}<span>${esc(w)}</span></p>`).join('')}
        ${it.hook_ok === false ? `<p class="note warn" style="margin-bottom:12px">${ICON.warn}<span>O gancho citado pelo modelo não confere com a transcrição: confira o início.</span></p>` : ''}

        <div class="row" style="padding-top:16px;border-top:1px solid var(--line)">
          <button type="button" class="btn ok" data-status="approved">${ICON.check}Aprovar</button>
          <button type="button" class="btn danger" data-status="rejected">${ICON.x}Descartar</button>
          <button type="button" class="btn ghost" data-status="pending">Voltar para pendente</button>
        </div>
      </div>
    </div>`;
}

/* ---------------------------------------------------------------- configurações */
const CONFIG_SECTIONS = [
  ['gemini', 'Gemini e orçamento', [
    ['gemini_model', 'Modelo de análise', 'text', 'IDs mudam e são descontinuados: nada é fixo no código.'],
    ['thinking_level', 'Raciocínio', ['off', 'low', 'medium', 'high'], 'Se o modelo recusar o nível, o pipeline desliga sozinho e avisa.'],
    ['daily_request_budget', 'Orçamento diário de requisições', 'number', 'Travão local, não o limite do Google.'],
    ['judge', 'Juiz ligado por padrão', 'bool', 'Segunda passada que compara os candidatos assistindo a eles.'],
    ['judge_model', 'Modelo do juiz', 'text', 'Vazio: o mesmo da análise.'],
    ['clips_per_video', 'Cortes por vídeo', 'number', ''],
    ['min_clip_s', 'Duração mínima (s)', 'number', ''],
    ['max_clip_s', 'Duração máxima (s)', 'number', '']
  ]],
  ['voice', 'Voz', [
    ['tts_model', 'Modelo de voz', 'text', 'Os modelos de TTS são preview: confira o que a sua chave enxerga.'],
    ['tts_voice', 'Voz padrão', 'voice', ''],
    ['tts_speed', 'Velocidade', 'number', '1.1 a 1.2 deixa o ritmo mais de short, sem mudar o tom.'],
    ['tts_min_interval_s', 'Intervalo entre chamadas (s)', 'number', 'Espaça os pedidos para não bater no limite por minuto.'],
    ['tts_style', 'Estilo da narração', 'area', 'Vai antes do roteiro. É a alavanca mais forte de qualidade.']
  ]],
  ['video', 'Vídeo e legenda', [
    ['layout', 'Enquadramento', ['blur', 'crop'], ''],
    ['text_mode', 'Texto na tela', ['captions', 'titled', 'both', 'ranked', 'none'], ''],
    ['whisper_model', 'Modelo do Whisper', ['tiny', 'base', 'small', 'medium', 'large-v3'], 'small leva uns 16 min por hora de vídeo em CPU.'],
    ['preset', 'Preset do x264', 'text', ''],
    ['crf', 'CRF', 'number', 'Menor = mais qualidade e arquivo maior.'],
    ['font', 'Fonte da legenda', 'text', '']
  ]],
  ['paths', 'Pastas e narração', [
    ['workspace_dir', 'Workspace (cache)', 'text', 'Apagar obriga a refazer transcrição e análise.'],
    ['output_dir', 'Saída', 'text', ''],
    ['gameplay_dir', 'Pasta das gameplays', 'text', ''],
    ['game_volume', 'Volume da gameplay (0 a 1)', 'number', ''],
    ['narrate_format', 'Formato padrão', 'text', ''],
    ['target_s', 'Duração alvo da narração (s)', 'number', ''],
    ['countdown_s', 'Contagem do "você prefere" (s)', 'number', ''],
    ['source', 'Fonte padrão (rótulo)', 'text', ''],
    ['license', 'Licença padrão', 'text', 'Vai para o review.md de toda saída.']
  ]]
];

let cfgSection = 'gemini';
let cfgDirty = {};

async function viewConfig() {
  store.config = await api('/config');
  if (!store.voices) {
    try { store.voices = (await api('/voices')).known; } catch (e) { store.voices = []; }
  }
  const eff = store.config.effective;
  cfgDirty = {};

  const fieldHtml = ([key, label, type, hint]) => {
    const v = eff[key];
    let input;
    if (Array.isArray(type)) {
      input = '<select data-cfg="' + key + '">' + type.map((o) =>
        '<option value="' + esc(o) + '"' + (String(v) === String(o) ? ' selected' : '') + '>' + esc(o) + '</option>').join('') + '</select>';
    } else if (type === 'voice') {
      const opts = (store.voices && store.voices.length ? store.voices : [v]).map((o) =>
        '<option value="' + esc(o) + '"' + (String(v) === String(o) ? ' selected' : '') + '>' + esc(o) + '</option>').join('');
      input = '<select data-cfg="' + key + '">' + opts + '</select>';
    } else if (type === 'bool') {
      return `<label class="check"><input type="checkbox" data-cfg="${key}" ${v ? 'checked' : ''}>
        <span><span class="t">${esc(label)}</span><span class="d">${esc(hint || '')}</span></span></label>`;
    } else if (type === 'area') {
      input = '<textarea rows="4" data-cfg="' + key + '">' + esc(v == null ? '' : v) + '</textarea>';
    } else {
      input = '<input type="' + (type === 'number' ? 'number' : 'text') + '" step="any" data-cfg="' + key + '" value="'
        + esc(v == null ? '' : v) + '"' + (type === 'text' && /dir|model|path/.test(key) ? ' class="mono"' : '') + '>';
    }
    return `<div class="field"><label for="c-${key}">${esc(label)}</label>${input}${hint ? '<p class="hint">' + esc(hint) + '</p>' : ''}</div>`;
  };

  const section = CONFIG_SECTIONS.filter((s) => s[0] === cfgSection)[0];

  view().innerHTML = `
    <div class="page-head">
      <div><h1>Configurações</h1>
      <p class="sub">Gravado em <span class="mono">${esc(store.config.path)}</span>. As flags do terminal continuam tendo prioridade.</p></div>
      <div class="row">
        <button type="button" class="btn" id="cfg-reset">Descartar</button>
        <button type="button" class="btn primary" id="cfg-save">Salvar config.yaml</button>
      </div>
    </div>

    <div class="split">
      <nav class="stack" style="flex:1 1 200px;max-width:230px;gap:3px" aria-label="Grupos">
        ${CONFIG_SECTIONS.map(([k, t]) => `<button type="button" class="btn ${k === cfgSection ? '' : 'ghost'}" data-section="${k}" style="justify-content:flex-start">${esc(t)}</button>`).join('')}
      </nav>

      <div class="wide stack">
        <section class="card">
          <h2 style="margin-bottom:18px">${esc(section[1])}</h2>
          <div class="grid g2">${section[2].map(fieldHtml).join('')}</div>
          ${cfgSection === 'voice' ? `
            <div class="row" style="margin-top:18px">
              <button type="button" class="btn" id="sample">${ICON.play}Ouvir amostra</button>
              <button type="button" class="btn ghost" id="tts-models">Listar modelos de voz</button>
            </div>
            <div id="sample-out" style="margin-top:14px"></div>` : ''}
        </section>

        <section class="card term">
          <div class="spread" style="margin-bottom:12px"><h2 style="font-size:14px">config.yaml</h2>
            <span class="mono" id="dirty" style="font-size:12px;color:var(--dim)">sem alterações</span></div>
          <pre class="code" id="yaml">${esc(yamlPreview(store.config.saved))}</pre>
          <p class="hint">Chave desconhecida é recusada ao salvar, com o nome do campo errado na mensagem.</p>
        </section>
      </div>
    </div>`;

  $$('[data-section]').forEach((b) => b.addEventListener('click', () => { cfgSection = b.dataset.section; viewConfig(); }));
  $$('[data-cfg]').forEach((el) => el.addEventListener('change', () => {
    const key = el.dataset.cfg;
    let v = el.type === 'checkbox' ? el.checked : el.value;
    if (el.type === 'number') v = v === '' ? null : Number(v);
    if (v === '') v = null;
    cfgDirty[key] = v;
    const n = Object.keys(cfgDirty).length;
    $('#dirty').textContent = n ? n + ' alteração(ões) não salva(s)' : 'sem alterações';
    $('#dirty').style.color = n ? 'var(--warn)' : 'var(--dim)';
    $('#yaml').textContent = yamlPreview(Object.assign({}, store.config.saved, cfgDirty));
  }));

  $('#cfg-reset').addEventListener('click', () => viewConfig());
  $('#cfg-save').addEventListener('click', async () => {
    if (!Object.keys(cfgDirty).length) { toast('Nada mudou.'); return; }
    try {
      store.config = await api('/config', { method: 'PUT', body: JSON.stringify({ values: cfgDirty }) });
      toast('config.yaml salvo.', 'good');
      await refreshEnv();
      viewConfig();
    } catch (e) { toast(e.message, 'bad'); }
  });

  if ($('#sample')) {
    $('#sample').addEventListener('click', async () => {
      const btn = $('#sample');
      btn.disabled = true;
      $('#sample-out').innerHTML = '<p class="hint" style="margin:0">Sintetizando… isso gasta uma requisição de voz.</p>';
      try {
        const voice = (cfgDirty.tts_voice || eff.tts_voice);
        const r = await api('/voices/sample', { method: 'POST', body: JSON.stringify({ voice }) });
        $('#sample-out').innerHTML = `<p class="hint" style="margin:0 0 8px">${esc(r.voice)} · ${r.seconds} s de áudio em ${r.took} s</p>
          <audio controls src="/api/media/${encodeURI(r.file)}" style="width:100%;max-width:420px"></audio>`;
      } catch (e) {
        $('#sample-out').innerHTML = `<p class="note bad" style="margin:0">${ICON.alert}<span>${esc(e.message)}</span></p>`;
      }
      btn.disabled = false;
    });
    $('#tts-models').addEventListener('click', async () => {
      try {
        const r = await api('/voices/models');
        $('#sample-out').innerHTML = '<p class="hint" style="margin:0">Modelos de voz visíveis para a sua chave:</p><pre class="code" style="margin-top:8px">'
          + esc((r.models || []).join('\n') || '(nenhum: o TTS pode não estar liberado para o projeto)') + '</pre>';
      } catch (e) {
        $('#sample-out').innerHTML = `<p class="note bad" style="margin:0">${ICON.alert}<span>${esc(e.message)}</span></p>`;
      }
    });
  }
}

function yamlPreview(obj) {
  const keys = Object.keys(obj || {});
  if (!keys.length) return '# o config.yaml ainda não existe: salvar cria o arquivo';
  return keys.map((k) => {
    const v = obj[k];
    if (v === null || v === undefined) return null;
    if (Array.isArray(v)) return k + ': [' + v.join(', ') + ']';
    if (typeof v === 'object') return k + ':\n' + Object.keys(v).map((i) => '  ' + i + ': ' + v[i]).join('\n');
    if (typeof v === 'string' && (v.length > 60 || v.indexOf(':') >= 0)) return k + ': "' + v.replace(/"/g, '\\"') + '"';
    return k + ': ' + v;
  }).filter(Boolean).join('\n');
}

/* ---------------------------------------------------------------- rotas */
const ROUTES = [
  [/^\/$/, viewDashboard],
  [/^\/gerar$/, viewGenerate],
  [/^\/execucoes$/, () => viewJob(null)],
  [/^\/execucoes\/(\w+)$/, (m) => viewJob(m[1])],
  [/^\/revisao$/, (m, params) => viewReview(params)],
  [/^\/config$/, viewConfig]
];

async function route() {
  closeStream();
  const raw = (location.hash || '#/').slice(1);
  const [path, qs] = raw.split('?');
  const params = new URLSearchParams(qs || '');
  $$('.nav a').forEach((a) => a.classList.toggle('on', a.dataset.route === path
    || (path.indexOf('/execucoes/') === 0 && a.dataset.route === '/execucoes')));
  for (const [re, fn] of ROUTES) {
    const m = re.exec(path);
    if (m) {
      try { await fn(m, params); } catch (e) { view().innerHTML = '<p class="empty">' + esc(e.message) + '</p>'; }
      return;
    }
  }
  location.hash = '#/';
}

window.addEventListener('hashchange', route);
initRail();
refreshEnv().then(route);
setInterval(async () => {
  const running = await refreshJobs();
  await refreshReviews();
  if (running && location.hash === '#/') viewDashboard();
}, 15000);
