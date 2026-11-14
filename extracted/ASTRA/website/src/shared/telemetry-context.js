/**
 * Shared dashboard ↔ chat telemetry display context.
 *
 * Transition policy
 * -----------------
 * - beginDisplayTransition(mode) bumps displayEpoch and sets pending=true.
 * - The response that belongs to the *current* epoch may complete the transition
 *   (pending does NOT block it). That is distinct from:
 *     - steady-state polls after pending is cleared, and
 *     - superseded responses from an older epoch / other mode (always rejected).
 * - Failed first live/demo fetch: completeDisplayTransition(epoch) clears pending
 *   without changing the bound snapshot. Retained readings stay paired with their
 *   snapshot_id and source — they are not relabeled as live device data.
 *
 * Live poll ordering
 * ------------------
 * - Each liveApplyToken() gets a monotonic seq within the process.
 * - beginDisplayTransition resets the applied-seq / version gates for the new mode.
 * - An older seq or lower server snapshot version cannot move the display backward.
 *
 * Chat readiness policy
 * ---------------------
 * - Prefer waiting until isDisplayReady() (pending cleared + snapshot_id bound).
 * - On timeout: if a non-null displayed snapshot_id remains, it still matches the
 *   visible readings (UI has not swapped) — chat may send that explicit ID.
 * - If snapshot_id is null, do NOT call /api/chat (avoids server substituting an
 *   unrelated latest snapshot). Restore the message for retry.
 */

let displayedSnapshotId = null;
let displayedObservedAt = null;
let displayedSource = null;
let displayedHeartRate = null;
let displayedVersion = null;

let displayEpoch = 0;
let activeMode = 'demo';
let modeTransitionPending = false;

let liveRequestSeq = 0;
let lastAppliedLiveSeq = 0;

export function getDisplayEpoch() {
  return displayEpoch;
}

export function getActiveMode() {
  return activeMode;
}

export function isModeTransitionPending() {
  return modeTransitionPending;
}

/** True when the UI is bound to a server snapshot for the active mode. */
export function isDisplayReady() {
  return !modeTransitionPending && Boolean(displayedSnapshotId);
}

/**
 * Begin a mode transition. Increments the display epoch so in-flight responses
 * from the previous mode are ignored. Resets live ordering gates for the new mode.
 * @returns {number} epoch token for this transition
 */
export function beginDisplayTransition(mode) {
  displayEpoch += 1;
  modeTransitionPending = true;
  activeMode = mode;
  lastAppliedLiveSeq = 0;
  displayedVersion = null;
  return displayEpoch;
}

/**
 * Atomically bind snapshot metadata (and optional reading fingerprint) after
 * the matching server response arrives for this epoch.
 * @returns {boolean} false if a newer transition superseded this response
 */
export function bindDisplayedSnapshot(mode, epoch, meta = {}) {
  if (epoch !== displayEpoch) return false;
  if (activeMode !== mode) return false;

  if (
    meta.version != null &&
    displayedVersion != null &&
    Number(meta.version) < Number(displayedVersion)
  ) {
    return false;
  }
  if (
    mode === 'realtime' &&
    meta.seq != null &&
    Number(meta.seq) < lastAppliedLiveSeq
  ) {
    return false;
  }

  displayedSnapshotId = meta.snapshot_id || null;
  displayedObservedAt = meta.observed_at || null;
  displayedSource = meta.source || null;
  if (Object.prototype.hasOwnProperty.call(meta, 'heart_rate_bpm')) {
    displayedHeartRate = meta.heart_rate_bpm;
  }
  if (meta.version != null) {
    displayedVersion = Number(meta.version);
  }
  if (mode === 'realtime' && meta.seq != null) {
    lastAppliedLiveSeq = Number(meta.seq);
  }
  modeTransitionPending = false;
  return true;
}

/**
 * End a pending transition without changing the bound snapshot.
 * Used when Demo/live fetch fails: prior on-screen values and their snapshot_id
 * stay paired so chat does not attach an unrelated ID.
 * @returns {boolean} false if a newer transition superseded this attempt
 */
export function completeDisplayTransition(epoch) {
  if (epoch !== displayEpoch) return false;
  modeTransitionPending = false;
  return true;
}

/**
 * Wait until the display is bound to a server snapshot for the active mode,
 * or until timeoutMs elapses. Resolves with isDisplayReady().
 */
export function waitForDisplayReady(timeoutMs = 2500) {
  if (isDisplayReady()) return Promise.resolve(true);
  const started = Date.now();
  return new Promise(resolve => {
    const tick = () => {
      if (isDisplayReady()) {
        resolve(true);
        return;
      }
      if (Date.now() - started >= timeoutMs) {
        resolve(false);
        return;
      }
      setTimeout(tick, 40);
    };
    tick();
  });
}

/**
 * Resolve which snapshot_id chat may send after an optional readiness wait.
 * Never returns a "use null and let the server pick" outcome.
 *
 * @returns {{ok: true, snapshot_id: string, basis: string} | {ok: false, reason: string}}
 */
export function resolveChatSnapshotRequest({ waited = false, ready = true } = {}) {
  if (ready && isDisplayReady()) {
    return {
      ok: true,
      snapshot_id: displayedSnapshotId,
      basis: 'display_ready',
    };
  }

  // Timeout / not ready: retained ID still pairs with currently visible readings.
  if (displayedSnapshotId) {
    return {
      ok: true,
      snapshot_id: displayedSnapshotId,
      basis: waited ? 'retained_visible_after_timeout' : 'retained_visible',
      pending: modeTransitionPending,
      mode: activeMode,
      source: displayedSource,
    };
  }

  return {
    ok: false,
    reason: 'no_displayed_snapshot',
    pending: modeTransitionPending,
    mode: activeMode,
  };
}

export function setDisplayedSnapshot(meta = {}) {
  displayedSnapshotId = meta.snapshot_id || null;
  displayedObservedAt = meta.observed_at || null;
  displayedSource = meta.source || null;
  if (Object.prototype.hasOwnProperty.call(meta, 'heart_rate_bpm')) {
    displayedHeartRate = meta.heart_rate_bpm;
  }
  if (meta.version != null) {
    displayedVersion = Number(meta.version);
  }
}

export function getDisplayedSnapshotId() {
  return displayedSnapshotId;
}

export function getDisplayedSnapshotMeta() {
  return {
    snapshot_id: displayedSnapshotId,
    observed_at: displayedObservedAt,
    source: displayedSource,
    heart_rate_bpm: displayedHeartRate,
    version: displayedVersion,
    mode: activeMode,
    ready: isDisplayReady(),
    pending: modeTransitionPending,
  };
}

/** Capture a token at the start of a live fetch (monotonic seq for ordering). */
export function liveApplyToken() {
  liveRequestSeq += 1;
  return { epoch: displayEpoch, mode: 'realtime', seq: liveRequestSeq };
}

/**
 * Whether a completed live response may update the UI.
 *
 * Allows the current-epoch response that completes a pending Real-Time transition.
 * Rejects superseded epochs/modes, older request seq, and older snapshot versions.
 */
export function shouldApplyLiveResponse(token, meta = {}) {
  if (!token) return false;
  if (activeMode !== 'realtime') return false;
  if (token.mode !== 'realtime') return false;
  if (token.epoch !== displayEpoch) return false;
  // NOTE: modeTransitionPending must NOT reject the current-epoch completer.
  if (token.seq != null && Number(token.seq) < lastAppliedLiveSeq) return false;
  if (
    meta.version != null &&
    displayedVersion != null &&
    Number(meta.version) < Number(displayedVersion)
  ) {
    return false;
  }
  return true;
}

/**
 * Full live acceptance path matching dashboard.js ordering:
 * token check → version/seq gates → bindDisplayedSnapshot.
 * Tests should prefer this over manually binding before shouldApplyLiveResponse.
 */
export function acceptLiveTelemetryResponse(token, data = {}) {
  const t = data.telemetry || {};
  const version = data.version ?? t.version ?? null;
  const completing = modeTransitionPending;

  if (!shouldApplyLiveResponse(token, { version })) {
    let reason = 'rejected';
    if (!token) reason = 'missing_token';
    else if (token.epoch !== displayEpoch || activeMode !== 'realtime') reason = 'superseded';
    else if (token.seq != null && Number(token.seq) < lastAppliedLiveSeq) reason = 'stale_request';
    else if (
      version != null &&
      displayedVersion != null &&
      Number(version) < Number(displayedVersion)
    ) {
      reason = 'older_version';
    }
    return { accepted: false, reason, completing };
  }

  const snapshot_id = data.snapshot_id || t.snapshot_id || null;
  if (!snapshot_id) {
    return { accepted: false, reason: 'missing_snapshot_id', completing };
  }

  const bound = bindDisplayedSnapshot('realtime', token.epoch, {
    snapshot_id,
    observed_at: data.observed_at || t.observed_at || null,
    source: data.source || t.source || null,
    heart_rate_bpm: t.heart_rate_bpm,
    version,
    seq: token.seq,
  });

  if (!bound) {
    return { accepted: false, reason: 'bind_rejected', completing };
  }

  return {
    accepted: true,
    reason: completing ? 'completed_transition' : 'poll',
    snapshot_id,
    completing,
  };
}

/** Test helper: reset module state. */
export function __resetTelemetryDisplayStateForTests() {
  displayedSnapshotId = null;
  displayedObservedAt = null;
  displayedSource = null;
  displayedHeartRate = null;
  displayedVersion = null;
  displayEpoch = 0;
  activeMode = 'demo';
  modeTransitionPending = false;
  liveRequestSeq = 0;
  lastAppliedLiveSeq = 0;
}
