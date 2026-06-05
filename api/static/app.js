'use strict';

let auth0Client   = null;
let accessToken   = null;
let devMode       = false;

let currentPage   = 0;
const PAGE_SIZE   = 15;
let currentFilter = { severity: '', llm_only: false };

let refreshTimer  = null;
const POLL_MS     = 10_000; 

window._cachedDevices = [];

window._auth0Client = null;

const DEVICE_ICONS = {
  inverter:           '🔆',
  charge_controller:  '☀️',
  battery:            '🔋',
  sensor:             '🌡️',
};

const SEVERITY_LABELS = {
  info:      'Info',
  warning:   'Warning',
  critical:  'Critical',
  emergency: 'Emergency',
};

window.addEventListener('DOMContentLoaded', init);

async function init() {
  try {
    const resp = await fetch('/api/auth/config');
    const cfg  = await resp.json();
    devMode = cfg.dev_mode;

    if (devMode) {
      console.warn('[Auth] Dev mode: Auth0 не налаштований, верифікацію пропущено');
      showDashboard();
      startPolling();
      return;
    }

    auth0Client = await auth0.createAuth0Client({
      domain:   cfg.domain,
      clientId: cfg.clientId,
      authorizationParams: {
        audience:     cfg.audience,
        redirect_uri: window.location.origin,
        scope:        'openid profile email',
      },
    });
    window._auth0Client = auth0Client;

    if (window.location.search.includes('code=') || window.location.search.includes('error=')) {
      await auth0Client.handleRedirectCallback();
      window.history.replaceState({}, document.title, '/');
    }

    const isAuthenticated = await auth0Client.isAuthenticated();

    if (isAuthenticated) {
      await onAuthenticated();
    } else {
      showLogin();
    }
  } catch (err) {
    console.error('[Init] Помилка:', err);
    showLogin();
  }
}

async function onAuthenticated() {
  try {
    accessToken = await auth0Client.getTokenSilently();
    const user  = await auth0Client.getUser();

    if (user) {
      const avatar = document.getElementById('user-avatar');
      const name   = document.getElementById('user-name');
      if (user.picture) avatar.src = user.picture;
      if (user.name)    name.textContent = user.name;
    }
  } catch (e) {
    console.warn('[Auth] Не вдалося отримати токен:', e);
  }

  showDashboard();
  startPolling();
}


async function login() {
  if (!auth0Client) return;
  await auth0Client.loginWithRedirect();
}

async function logout() {
  if (devMode) { location.reload(); return; }
  stopPolling();
  await auth0Client.logout({ logoutParams: { returnTo: window.location.origin } });
}

async function getToken() {
  if (devMode) return '';
  try {
    accessToken = await auth0Client.getTokenSilently();
    return accessToken;
  } catch (e) {
    console.error('[Auth] Не вдалося оновити токен:', e);
    showLogin();
    return null;
  }
}

async function api(path) {
  const token = await getToken();
  if (token === null) throw new Error('Unauthenticated');

  const headers = token ? { 'Authorization': `Bearer ${token}` } : {};
  const resp = await fetch(path, { headers });

  if (resp.status === 401) { showLogin(); throw new Error('401'); }
  if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);

  return resp.json();
}

function startPolling() {
  loadAll();
  refreshTimer = setInterval(loadAll, POLL_MS);
  animateRefreshBar();
}

function stopPolling() {
  if (refreshTimer) clearInterval(refreshTimer);
}

function animateRefreshBar() {
  const bar = document.getElementById('refresh-bar');
  if (!bar) return;
  bar.style.transition = 'none';
  bar.style.width = '0%';
  requestAnimationFrame(() => {
    bar.style.transition = `width ${POLL_MS}ms linear`;
    bar.style.width = '100%';
  });
}

async function loadAll() {
  animateRefreshBar();
  await Promise.allSettled([
    loadSystemHealth(),
    loadSystemStats(),
    loadDevices(),
    loadEvents(),
  ]);
}

async function loadSystemHealth() {
  try {
    const data = await fetch('/health').then(r => r.json());
    const dot  = document.getElementById('status-dot');
    const txt  = document.getElementById('status-text');

    dot.className  = 'status-dot ' + data.status;
    txt.textContent = data.status === 'ok' ? 'Система в нормі' : 'Деградація сервісів';
  } catch (e) {
    document.getElementById('status-dot').className = 'status-dot error';
    document.getElementById('status-text').textContent = 'API недоступне';
  }
}

async function loadSystemStats() {
  try {
    const s = await fetch('/api/system/stats').then(r => r.json());
    document.getElementById('hstat-total').textContent    = fmt(s.total_events);
    document.getElementById('hstat-critical').textContent = fmt(s.critical_count);
    document.getElementById('hstat-llm').textContent      = fmt(s.llm_processed);
  } catch (e) { /* mute */ }
}

function fmt(n) {
  if (n == null) return '—';
  if (n >= 1000) return (n/1000).toFixed(1) + 'k';
  return String(n);
}

async function loadDevices() {
  try {
    const { devices } = await api('/api/devices');
    const online = devices.filter(d => d.is_online).length;
    document.getElementById('devices-online').textContent =
      `${online} / ${devices.length} онлайн`;

    const grid = document.getElementById('devices-grid');
    grid.innerHTML = devices.map(renderDeviceCard).join('');


    window._cachedDevices = devices;
    window.dispatchEvent(new CustomEvent('devices:loaded'));
  } catch (e) {
    console.error('[Devices]', e);
  }
}

function renderDeviceCard(d) {
  const icon  = DEVICE_ICONS[d.device_type] || '📡';
  const sev   = d.max_severity || 'ok';
  const badge = sev === 'ok'
    ? `<span class="status-badge ${d.is_online ? 'online' : 'offline'}">${d.is_online ? '● Online' : '○ Offline'}</span>`
    : `<span class="status-badge ${sev}">${sev.toUpperCase()}</span>`;

  const metrics = buildMetrics(d.device_type, d.current_state);

  return `
    <div class="device-card severity-${sev}" onclick="openDeviceModal('${d.device_uid}')">
      <div class="device-header">
        <div class="device-type-icon">${icon}</div>
        <div class="device-status">${badge}</div>
      </div>
      <div class="device-name">${d.name}</div>
      <div class="device-uid">${d.device_uid}</div>
      <div class="device-metrics">${metrics}</div>
      ${d.anomaly_count > 0 ? `
        <div class="device-anom-bar">
          ⚠ <span class="anom-count">${d.anomaly_count}</span> аномалій в Redis
        </div>` : ''}
    </div>`;
}

function buildMetrics(type, state) {
  if (!state || Object.keys(state).length === 0)
    return '<div class="metric-row"><span class="metric-label">Немає даних</span></div>';

  const fields = {
    battery:           ['soc_pct', 'voltage_v', 'current_a', 'temperature_c'],
    inverter:          ['dc_voltage_v', 'ac_output_power_w', 'temperature_c'],
    charge_controller: ['pv_power_w', 'battery_voltage_v', 'temperature_c'],
    sensor:            ['temperature_c', 'humidity_pct', 'ac_mains_v'],
  }[type] || Object.keys(state).filter(k => !['timestamp','device_type'].includes(k)).slice(0, 4);

  const units = {
    soc_pct: '%', voltage_v: 'V', current_a: 'A', temperature_c: '°C',
    dc_voltage_v: 'V', ac_output_power_w: 'W', pv_power_w: 'W',
    battery_voltage_v: 'V', humidity_pct: '%', ac_mains_v: 'V',
  };

  return fields
    .filter(f => state[f] != null)
    .map(f => {
      const val  = typeof state[f] === 'number' ? state[f].toFixed(1) : state[f];
      const flag = state[f + '_flag'];
      const cls  = (flag && flag !== 'ok') ? 'metric-value anomaly' : 'metric-value';
      return `<div class="metric-row">
        <span class="metric-label">${f.replace(/_/g,' ')}</span>
        <span class="${cls}">${val}${units[f] || ''}</span>
      </div>`;
    })
    .join('');
}

async function loadEvents(page = 0) {
  currentPage = page;
  const el = document.getElementById('events-list');
  el.innerHTML = '<div class="loading-spinner"><div class="spinner"></div><span>Завантаження...</span></div>';

  try {
    const qs = new URLSearchParams({
      limit:  PAGE_SIZE,
      offset: page * PAGE_SIZE,
    });
    if (currentFilter.severity) qs.set('severity', currentFilter.severity);
    if (currentFilter.llm_only) qs.set('llm_only', 'true');

    const { events, total } = await api(`/api/events?${qs}`);

    if (events.length === 0) {
      el.innerHTML = `<div class="empty-state"><div class="empty-icon">📭</div>Подій не знайдено</div>`;
      document.getElementById('pagination').innerHTML = '';
      return;
    }

    el.innerHTML = events.map(renderEventCard).join('');
    renderPagination(total, page);
  } catch (e) {
    el.innerHTML = `<div class="empty-state"><div class="empty-icon">❌</div>Помилка завантаження</div>`;
  }
}

function renderEventCard(ev) {
  const sev  = ev.severity || 'info';
  const time = timeAgo(ev.created_at);
  const desc = ev.description || '';


  let llmHtml = '';
  if (ev.llm_explanation) {
    const text = ev.llm_explanation.split('\n').filter(Boolean).slice(0, 3).join(' · ');
    llmHtml = `<div class="event-llm">
      <span class="event-llm-icon">🤖</span>
      <span>${esc(text.slice(0, 250))}${text.length > 250 ? '…' : ''}</span>
    </div>`;
  }

  let cmdHtml = '';
  if (ev.command_json) {
    const cmd = typeof ev.command_json === 'object' ? ev.command_json : JSON.parse(ev.command_json);
    if (cmd && cmd.type && cmd.type !== 'none') {
      cmdHtml = `<div class="event-command">⚡ ${esc(cmd.type)}</div>`;
    }
  }

  return `
    <div class="event-card sev-${sev}" onclick="openEventModal(${ev.id})">
      <div class="event-header">
        <span class="sev-badge sev-${sev}">${sev}</span>
        <span class="event-device">${esc(ev.device_uid || '—')}</span>
        <span class="event-time">${time}</span>
      </div>
      <div class="event-desc">${esc(desc)}</div>
      ${llmHtml}
      ${cmdHtml}
    </div>`;
}

function renderPagination(total, page) {
  const pages = Math.ceil(total / PAGE_SIZE);
  if (pages <= 1) { document.getElementById('pagination').innerHTML = ''; return; }

  let html = `<button class="page-btn" onclick="loadEvents(${page-1})" ${page === 0 ? 'disabled' : ''}>← Назад</button>`;

  const start = Math.max(0, page - 2);
  const end   = Math.min(pages - 1, page + 2);
  for (let i = start; i <= end; i++) {
    html += `<button class="page-btn ${i === page ? 'active' : ''}" onclick="loadEvents(${i})">${i+1}</button>`;
  }

  html += `<button class="page-btn" onclick="loadEvents(${page+1})" ${page >= pages-1 ? 'disabled' : ''}>Вперед →</button>`;
  html += `<span style="font-size:12px;color:var(--text-muted);margin-left:8px">${total} всього</span>`;

  document.getElementById('pagination').innerHTML = html;
}

function applyFilters() {
  currentFilter.severity = document.getElementById('filter-severity').value;
  currentFilter.llm_only = document.getElementById('filter-llm').checked;
  loadEvents(0);
}

async function openEventModal(id) {
  try {
    const ev = await api(`/api/events/${id}`);
    const overlay = document.getElementById('modal-overlay');
    const content = document.getElementById('modal-content');

    const sev = ev.severity || 'info';
    let html = `
      <div class="modal-title">
        <span class="sev-badge sev-${sev}" style="margin-right:10px">${sev}</span>
        Подія #${ev.id}
      </div>`;

    html += section('Пристрій', `
      <div class="modal-field"><div class="modal-label">UID</div>
        <div class="modal-value" style="font-family:var(--mono);color:var(--blue-light)">${esc(ev.device_uid || '—')}</div></div>
      <div class="modal-field"><div class="modal-label">Назва</div>
        <div class="modal-value">${esc(ev.device_name || '—')}</div></div>
      <div class="modal-field"><div class="modal-label">Час</div>
        <div class="modal-value">${ev.created_at ? new Date(ev.created_at).toLocaleString('uk') : '—'}</div></div>
    `);

    html += section('Опис аномалії', `<div class="modal-value">${esc(ev.description)}</div>`);

    if (ev.llm_explanation) {
      html += section('🤖 LLM-аналіз', `<div class="modal-value" style="line-height:1.7">${esc(ev.llm_explanation).replace(/\n/g,'<br>')}</div>`);
    }

    if (ev.command_json) {
      const cmd = typeof ev.command_json === 'object' ? ev.command_json : JSON.parse(ev.command_json);
      html += section('⚡ Команда', `<pre class="modal-code">${JSON.stringify(cmd, null, 2)}</pre>`);
    }

    content.innerHTML = html;
    overlay.classList.remove('hidden');
  } catch (e) {
    console.error('[Modal]', e);
  }
}

async function openDeviceModal(uid) {
  const overlay = document.getElementById('modal-overlay');
  const content = document.getElementById('modal-content');

  content.innerHTML = `<div class="modal-title">📡 ${esc(uid)}</div>
    <div class="loading-spinner" style="padding:40px"><div class="spinner"></div></div>`;
  overlay.classList.remove('hidden');

  try {
    const data = await api(`/api/devices/${uid}/state`);

    let html = `<div class="modal-title">📡 ${esc(uid)}</div>`;

    if (!data.is_online) {
      html += `<div style="text-align:center;padding:32px;color:var(--text-muted)">
        <div style="font-size:40px;margin-bottom:12px">📡</div>
        <div style="font-size:16px;font-weight:600;margin-bottom:8px">Пристрій офлайн</div>
        <div style="font-size:13px">Немає даних в Redis. Можливо пристрій ще не надсилав телеметрію.</div>
      </div>`;
      content.innerHTML = html;
      return;
    }

    const cur = data.current || {};
    if (Object.keys(cur).length > 0) {
      const rows = Object.entries(cur)
        .filter(([k]) => !['timestamp','device_type'].includes(k))
        .map(([k, v]) => {
          const flag  = cur[k + '_flag'];
          const color = (flag && flag !== 'ok') ? 'color:var(--orange)' : '';
          return `<div class="modal-field">
            <div class="modal-label">${k.replace(/_/g,' ')}</div>
            <div class="modal-value" style="font-family:var(--mono);${color}">${typeof v === 'number' ? v.toFixed(3) : v}</div>
          </div>`;
        }).join('');
      html += section('Поточний стан', rows);
    }

    const trends = data.trends || {};
    if (Object.keys(trends).length > 0) {
      const sparklines = Object.entries(trends).map(([fieldName, points]) => {
        const canvasId = `spark-${uid}-${fieldName}`.replace(/[^a-zA-Z0-9-]/g,'_');
        setTimeout(() => drawSparkline(canvasId, points), 50);
        const last = points[points.length - 1];
        const lastVal = last ? (typeof last.v === 'number' ? last.v.toFixed(1) : '—') : '—';
        return `<div style="margin-bottom:14px">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
            <span class="modal-label">${fieldName.replace(/_/g,' ')}</span>
            <span style="font-family:var(--mono);font-size:13px;color:var(--blue-light)">${lastVal}</span>
          </div>
          <canvas id="${canvasId}" width="540" height="48"
            style="width:100%;height:48px;border-radius:6px;background:rgba(0,0,0,0.2)"></canvas>
        </div>`;
      }).join('');
      html += section('📈 Тренди (ковзне вікно)', sparklines);
    }

    if (data.anomalies && data.anomalies.length > 0) {
      const rows = data.anomalies.slice(0, 5).map(a =>
        `<div class="modal-field">
          <div class="modal-label">${timeAgo(a.ts * 1000)} — ${esc(a.field)} [${esc(a.flag)}]</div>
          <div class="modal-value" style="color:var(--orange)">${a.value != null ? a.value.toFixed(2) : 'N/A'} — ${esc(a.note || '')}</div>
        </div>`
      ).join('');
      html += section('⚠ Останні аномалії', rows);
    }

    content.innerHTML = html;
  } catch (e) {
    content.innerHTML = `<div style="text-align:center;padding:32px;color:var(--red)">
      <div style="font-size:32px;margin-bottom:12px">❌</div>
      <div>Не вдалось завантажити стан пристрою</div>
      <div style="font-size:12px;color:var(--text-muted);margin-top:8px">${esc(String(e.message || e))}</div>
    </div>`;
    console.error('[DeviceModal]', e);
  }
}

function section(title, content) {
  return `<div class="modal-section">
    <div class="modal-section-title">${title}</div>
    ${content}
  </div>`;
}

function closeModal() {
  document.getElementById('modal-overlay').classList.add('hidden');
}

function showLogin()     { document.getElementById('login-screen').classList.remove('hidden'); }
function showDashboard() {
  document.getElementById('login-screen').classList.add('hidden');
  document.getElementById('dashboard').classList.remove('hidden');
}

function esc(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function timeAgo(ts) {
  if (!ts) return '—';
  const now = Date.now();
  let d = ts;
  if (typeof ts === 'number') d = ts < 1e12 ? ts * 1000 : ts;
  const diff = Math.max(0, Math.floor((now - new Date(d).getTime()) / 1000));
  if (diff < 60)    return `${diff}с тому`;
  if (diff < 3600)  return `${Math.floor(diff/60)}хв тому`;
  if (diff < 86400) return `${Math.floor(diff/3600)}год тому`;
  return new Date(d).toLocaleDateString('uk');
}

function drawSparkline(canvasId, points) {
  const canvas = document.getElementById(canvasId);
  if (!canvas || points.length < 2) return;

  const ctx = canvas.getContext('2d');
  const W   = canvas.width;
  const H   = canvas.height;
  const PAD = 4;

  ctx.clearRect(0, 0, W, H);

  const vals = points.map(p => p.v).filter(v => typeof v === 'number' && isFinite(v));
  if (vals.length < 2) return;

  const minV = Math.min(...vals);
  const maxV = Math.max(...vals);
  const rangeV = maxV - minV || 1;

  const toX = i => PAD + (i / (points.length - 1)) * (W - PAD * 2);
  const toY = v => H - PAD - ((v - minV) / rangeV) * (H - PAD * 2);

  const grad = ctx.createLinearGradient(0, 0, 0, H);
  grad.addColorStop(0, 'rgba(59, 130, 246, 0.3)');
  grad.addColorStop(1, 'rgba(59, 130, 246, 0.01)');

  ctx.beginPath();
  ctx.moveTo(toX(0), H);
  points.forEach((p, i) => {
    if (typeof p.v === 'number' && isFinite(p.v)) {
      ctx.lineTo(toX(i), toY(p.v));
    }
  });
  ctx.lineTo(toX(points.length - 1), H);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();

  ctx.beginPath();
  let first = true;
  points.forEach((p, i) => {
    if (typeof p.v !== 'number' || !isFinite(p.v)) return;
    if (first) { ctx.moveTo(toX(i), toY(p.v)); first = false; }
    else        ctx.lineTo(toX(i), toY(p.v));
  });
  ctx.strokeStyle = '#3b82f6';
  ctx.lineWidth   = 1.5;
  ctx.lineJoin    = 'round';
  ctx.stroke();

  points.forEach((p, i) => {
    if (p.f && p.f !== 'ok' && typeof p.v === 'number' && isFinite(p.v)) {
      ctx.beginPath();
      ctx.arc(toX(i), toY(p.v), 3, 0, Math.PI * 2);
      ctx.fillStyle = '#f97316';
      ctx.fill();
    }
  });

  ctx.fillStyle = 'rgba(148, 163, 184, 0.7)';
  ctx.font      = '9px Inter, sans-serif';
  ctx.textAlign = 'left';
  ctx.fillText(maxV.toFixed(1), PAD + 2, PAD + 9);
  ctx.textAlign = 'left';
  ctx.fillText(minV.toFixed(1), PAD + 2, H - 2);
}
