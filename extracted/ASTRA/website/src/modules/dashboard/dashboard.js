import { vitals as defaultVitals } from './data.js';
import {
  acceptLiveTelemetryResponse,
  beginDisplayTransition,
  bindDisplayedSnapshot,
  completeDisplayTransition,
  getDisplayEpoch,
  isModeTransitionPending,
  liveApplyToken,
} from '../../shared/telemetry-context.js';

export const sparkline = points =>
  `<svg class="sparkline" viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden="true"><polyline points="${points}" fill="none" stroke="#c4c7d0" stroke-width="1.2"/></svg>`;

let currentVitals = JSON.parse(JSON.stringify(defaultVitals));
let realTimeInterval = null;
let currentMode = 'demo'; // 'demo' or 'realtime'
let smartwatchConnected = true;
let modeToast = () => {};

export function initDashboard(showDialog, toast = () => {}) {
  renderVitals(showDialog);
  initModeAndWatchControls(toast);
  // Bind a server-owned Demo snapshot so chat matches the initial fixtures.
  activateDemoSnapshot({ toast, announce: false });
}

// ── Vitals Rendering ────────────────────────────────────────────────────────
function renderVitals(showDialog) {
  const container = document.querySelector('#vital-cards');
  if (!container) return;
  container.innerHTML = currentVitals
    .map(
      v => `
    <button class="vital-card panel" data-metric="${v.id}" aria-label="${v.name}: ${v.value} ${v.unit}. View explanation">
      <span class="vital-label">
        ${v.name}
      </span>
      <span class="vital-value" id="val-${v.id}">
        ${v.value}<small>${v.unit}</small>
      </span>
      ${sparkline(v.points)}
      <span class="vital-status">
        <span class="status-dot"></span>
        <span id="note-${v.id}">${v.note}</span>
      </span>
    </button>`
    )
    .join('');

  document.querySelectorAll('[data-metric]').forEach(button =>
    button.addEventListener('click', () => {
      const v = currentVitals.find(item => item.id === button.dataset.metric);
      if (!v) return;
      showDialog(`
        <h2>${v.name}</h2>
        <div class="dialog-reading">${v.value} <small>${v.unit}</small></div>
        <div style="margin: 14px 0; line-height: 1.6;">
          <p style="color: #e5e7eb; margin-bottom: 10px; font-size: 13px;">${v.description}</p>
          <div style="background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); padding: 12px 14px; border-radius: 8px; font-size: 11px; color: #9ca3af; display: grid; gap: 6px;">
            <div><strong style="color: #fff;">Operational Target:</strong> ${v.note}</div>
            <div><strong style="color: #fff;">Sensor Telemetry Source:</strong> ${v.source || 'Optical / Wearable Biosensor Suite'}</div>
            <div><strong style="color: #fff;">Flight Mode:</strong> ${currentMode === 'realtime' ? 'Live Telemetry Stream (OrbitWatch-01 BLE 125Hz)' : 'Trial Demo Baseline Sample'}</div>
          </div>
        </div>
      `);
    })
  );
}

// ── Mode Switcher & Smartwatch Detection ────────────────────────────────────
function initModeAndWatchControls(toast) {
  const btnDemo = document.querySelector('#btn-mode-demo');
  const btnRealtime = document.querySelector('#btn-mode-realtime');
  const watchPill = document.querySelector('#watch-status-pill');
  const btnToggleWatch = document.querySelector('#btn-toggle-watch');

  // Fetch initial smartwatch and mode status from backend
  fetch('/api/smartwatch/status')
    .then(r => r.json())
    .then(data => {
      smartwatchConnected = Boolean(data.connected);
      updateWatchUI(watchPill, btnToggleWatch);
    })
    .catch(() => {});

  if (btnDemo) {
    btnDemo.addEventListener('click', () => {
      setMode('demo', toast);
    });
  }

  if (btnRealtime) {
    btnRealtime.addEventListener('click', async () => {
      // Check if smartwatch is detected
      try {
        const res = await fetch('/api/smartwatch/status');
        const sw = await res.json();
        smartwatchConnected = Boolean(sw.connected);
        updateWatchUI(watchPill, btnToggleWatch);

        if (!smartwatchConnected) {
          toast('Smartwatch not detected — no BLE stream found. Returning to Trial Demo mode.');
          setMode('demo', toast);
          return;
        }

        // Smartwatch detected: activate real-time mode
        setMode('realtime', toast);
      } catch (err) {
        toast('Unable to connect to smartwatch service. Remaining in Demo mode.');
        setMode('demo', toast);
      }
    });
  }

  if (btnToggleWatch) {
    btnToggleWatch.addEventListener('click', async () => {
      const newState = !smartwatchConnected;
      try {
        const res = await fetch('/api/smartwatch/status', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ connected: newState })
        });
        const data = await res.json();
        smartwatchConnected = Boolean(data.smartwatch.connected);
        updateWatchUI(watchPill, btnToggleWatch);

        if (smartwatchConnected) {
          toast('OrbitWatch-01 connected via BLE 125Hz.');
        } else {
          toast('OrbitWatch-01 disconnected (simulated).');
          if (currentMode === 'realtime') {
            toast('Smartwatch lost — reverting to Trial Demo mode.');
            setMode('demo', toast);
          }
        }
      } catch (err) {
        toast('Connection toggle failed.');
      }
    });
  }
}

function updateWatchUI(watchPill, btnToggleWatch) {
  if (watchPill) {
    if (smartwatchConnected) {
      watchPill.className = 'watch-pill connected';
      watchPill.innerHTML = `<span class="status-dot" style="background:#22c55e"></span> OrbitWatch-01 (BLE 125Hz Connected)`;
    } else {
      watchPill.className = 'watch-pill disconnected';
      watchPill.innerHTML = `<span class="status-dot" style="background:#ef4444"></span> Smartwatch: Not Detected`;
    }
  }
  if (btnToggleWatch) {
    btnToggleWatch.textContent = smartwatchConnected ? 'Disconnect Watch' : 'Connect Watch';
  }
}

function setMode(mode, toast) {
  currentMode = mode;
  modeToast = toast || (() => {});
  const btnDemo = document.querySelector('#btn-mode-demo');
  const btnRealtime = document.querySelector('#btn-mode-realtime');

  if (mode === 'realtime') {
    // Invalidate in-flight demo responses; first successful live response completes transition.
    beginDisplayTransition('realtime');
    if (btnDemo) btnDemo.classList.remove('active');
    if (btnRealtime) btnRealtime.classList.add('active', 'realtime');
    toast('Real-Time mode active: dynamic 2s streaming via OrbitWatch-01.');
    startRealTimeLoop();
  } else {
    if (btnRealtime) btnRealtime.classList.remove('active', 'realtime');
    if (btnDemo) btnDemo.classList.add('active');
    stopRealTimeLoop();
    // Keep prior UI visible until the Demo snapshot arrives, then swap together.
    activateDemoSnapshot({ toast, announce: true });
  }
}

/**
 * Fetch the server Demo snapshot and apply readings + snapshot_id together.
 * Delayed live responses are ignored via display-epoch tokens.
 */
async function activateDemoSnapshot({ toast = () => {}, announce = true } = {}) {
  const epoch = beginDisplayTransition('demo');
  currentMode = 'demo';
  try {
    const res = await fetch('/api/telemetry/demo');
    if (!res.ok) throw new Error(`demo snapshot HTTP ${res.status}`);
    const data = await res.json();
    const t = data.telemetry || {};
    const bound = bindDisplayedSnapshot('demo', epoch, {
      snapshot_id: data.snapshot_id || t.snapshot_id || null,
      observed_at: data.observed_at || t.observed_at || null,
      source: data.source || t.source || 'demo',
      heart_rate_bpm: t.heart_rate_bpm,
      version: data.version ?? t.version ?? null,
    });
    if (!bound) return; // superseded by a newer mode transition
    applyTelemetryToDashboard(data, { notePrefix: 'Trial Demo' });
    if (announce) toast('Trial Demo mode: showing baseline astronaut fixtures.');
  } catch (err) {
    console.error('Failed to activate demo snapshot:', err);
    // Clear pending flag for this epoch only; keep prior readings + snapshot_id paired.
    completeDisplayTransition(epoch);
    if (announce) toast('Demo snapshot unavailable; chat telemetry may be incomplete.');
  }
}

// ── Dynamic 2-Second Real-Time Telemetry Loop ──────────────────────────────
function startRealTimeLoop() {
  stopRealTimeLoop();
  // Fetch immediately
  fetchLiveTelemetry();
  // Loop every 2 seconds
  realTimeInterval = setInterval(fetchLiveTelemetry, 2000);
}

function stopRealTimeLoop() {
  if (realTimeInterval) {
    clearInterval(realTimeInterval);
    realTimeInterval = null;
  }
}

async function fetchLiveTelemetry() {
  if (currentMode !== 'realtime' || !smartwatchConnected) return;
  // Token captured before await — discarded if user switches to Demo mid-flight.
  const token = liveApplyToken();
  const completingTransition =
    isModeTransitionPending() && token.epoch === getDisplayEpoch();

  try {
    const res = await fetch('/api/telemetry/live');
    if (!res.ok) {
      // Recoverable: clear pending, keep prior readings+ID paired (not labeled live).
      if (completingTransition) {
        completeDisplayTransition(token.epoch);
        modeToast('Live telemetry unavailable. Retaining previous readings (not live device data).');
      }
      return;
    }
    const data = await res.json();
    // Same acceptance path as regression tests (pending current-epoch may complete).
    const result = acceptLiveTelemetryResponse(token, data);
    if (!result.accepted) return;

    // 1. Dynamic pulse effect on all vital cards
    document.querySelectorAll('.vital-card').forEach(card => {
      card.classList.remove('is-pulsing');
      void card.offsetWidth; // trigger reflow
      card.classList.add('is-pulsing');
    });

    applyTelemetryToDashboard(data, { notePrefix: 'Live BLE 125Hz' });
  } catch (err) {
    console.error('Error fetching real-time telemetry:', err);
    if (completingTransition) {
      completeDisplayTransition(token.epoch);
      modeToast(
        '⚠️ Live telemetry unavailable. Retaining previous readings (not live device data).'
      );
    }
  }
}

function applyTelemetryToDashboard(data, { notePrefix = 'Live' } = {}) {
  const t = data.telemetry || {};

  updateVitalValue('heart', t.heart_rate_bpm ?? t.heart_rate ?? 74.2, 'bpm', notePrefix);
  updateVitalValue('oxygen', t.spo2_percent ?? t.spo2 ?? 98.6, '%', 'Pulse Oximeter');
  updateVitalValue('temperature', t.core_temp_c ?? t.temperature ?? 36.85, '°C', 'Heat Flux Sensor');
  updateVitalValue('water', t.blood_water_percent ?? t.blood_water ?? 62.4, '%', 'Bioimpedance Hydration');
  updateVitalValue(
    'pressure',
    t.systolic_bp != null
      ? `${Math.round(t.systolic_bp)}/${Math.round(t.diastolic_bp)}`
      : (t.blood_pressure || '120/78'),
    'mmHg',
    'Continuous BP'
  );
  updateVitalValue('respiration', t.respiration_rate ?? 16.2, 'brpm', 'Transthoracic');
  updateVitalValue('hrv', t.hrv_rmssd ?? 48.5, 'ms', 'RMSSD Parasympathetic');

  const act = t.activity && typeof t.activity === 'object' ? t.activity.activity_name : (t.activity || 'Resting');
}

function updateVitalValue(id, val, unit, note) {
  const valEl = document.querySelector(`#val-${id}`);
  const noteEl = document.querySelector(`#note-${id}`);
  if (valEl) valEl.innerHTML = `${val}<small>${unit}</small>`;
  if (noteEl) noteEl.textContent = note;
}

