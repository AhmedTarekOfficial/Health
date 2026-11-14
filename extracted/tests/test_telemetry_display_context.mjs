/**
 * Frontend display-epoch / chat-readiness tests.
 * Exercises the same acceptance ordering as dashboard.js (acceptLiveTelemetryResponse)
 * rather than binding before checking shouldApplyLiveResponse.
 *
 * Run: node tests/test_telemetry_display_context.mjs
 */
import {
  __resetTelemetryDisplayStateForTests,
  acceptLiveTelemetryResponse,
  beginDisplayTransition,
  bindDisplayedSnapshot,
  completeDisplayTransition,
  getDisplayedSnapshotId,
  getDisplayedSnapshotMeta,
  isDisplayReady,
  isModeTransitionPending,
  liveApplyToken,
  resolveChatSnapshotRequest,
  shouldApplyLiveResponse,
  waitForDisplayReady,
} from '../ASTRA/website/src/shared/telemetry-context.js';

let passed = 0;
let failed = 0;

function assert(cond, msg) {
  if (cond) {
    passed += 1;
    console.log(`  ok — ${msg}`);
  } else {
    failed += 1;
    console.error(`  FAIL — ${msg}`);
  }
}

async function testDemoToRealtimeFirstResponseCompletesTransition() {
  console.log('A. Demo → Real-Time: first live response completes transition');
  __resetTelemetryDisplayStateForTests();

  const demoEpoch = beginDisplayTransition('demo');
  bindDisplayedSnapshot('demo', demoEpoch, {
    snapshot_id: 'snap-demo',
    heart_rate_bpm: 72,
    source: 'demo',
    version: 1,
  });
  assert(getDisplayedSnapshotId() === 'snap-demo', 'demo bound');

  beginDisplayTransition('realtime');
  assert(isModeTransitionPending(), 'realtime transition pending');
  const token = liveApplyToken();

  // Critical: acceptance path with pending=true (mirrors dashboard.js).
  assert(
    shouldApplyLiveResponse(token, { version: 2 }) === true,
    'pending current-epoch live response is eligible'
  );

  const result = acceptLiveTelemetryResponse(token, {
    snapshot_id: 'snap-live',
    version: 2,
    source: 'simulation',
    telemetry: { heart_rate_bpm: 110, snapshot_id: 'snap-live', version: 2 },
    observed_at: '2022-01-01T00:00:00+00:00',
  });
  assert(result.accepted === true, 'first live response accepted');
  assert(result.reason === 'completed_transition', 'reason is completed_transition');
  assert(getDisplayedSnapshotId() === 'snap-live', 'displayed snapshot changed to live');
  assert(getDisplayedSnapshotMeta().heart_rate_bpm === 110, 'live HR fingerprint stored');
  assert(isModeTransitionPending() === false, 'pending becomes false');
  assert(isDisplayReady() === true, 'display ready after live bind');
}

async function testDelayedLiveCannotOverwriteDemo() {
  console.log('B. Live → Demo: delayed live cannot overwrite Demo');
  __resetTelemetryDisplayStateForTests();

  const liveEpoch = beginDisplayTransition('realtime');
  const token = liveApplyToken();
  const accepted = acceptLiveTelemetryResponse(token, {
    snapshot_id: 'snap-live-A',
    version: 5,
    telemetry: { heart_rate_bpm: 110 },
    source: 'simulation',
  });
  assert(accepted.accepted, 'initial live applied');

  // In-flight second live request, then switch to Demo.
  const delayedToken = liveApplyToken();
  const demoEpoch = beginDisplayTransition('demo');
  assert(isModeTransitionPending(), 'demo transition pending');
  assert(
    acceptLiveTelemetryResponse(delayedToken, {
      snapshot_id: 'snap-live-LATE',
      version: 6,
      telemetry: { heart_rate_bpm: 999 },
    }).accepted === false,
    'delayed live rejected after demo transition'
  );
  assert(getDisplayedSnapshotId() === 'snap-live-A', 'prior ID retained until demo binds');

  const demoBound = bindDisplayedSnapshot('demo', demoEpoch, {
    snapshot_id: 'snap-demo-B',
    heart_rate_bpm: 72,
    source: 'demo',
    version: 7,
  });
  assert(demoBound === true, 'demo snapshot binds');
  assert(getDisplayedSnapshotId() === 'snap-demo-B', 'chat ID is demo snapshot');
}

async function testOutOfOrderPollingDoesNotMoveBackward() {
  console.log('C. Out-of-order polling: older response cannot overwrite newer');
  __resetTelemetryDisplayStateForTests();

  beginDisplayTransition('realtime');
  const first = liveApplyToken(); // seq 1
  const second = liveApplyToken(); // seq 2

  const newerFirst = acceptLiveTelemetryResponse(second, {
    snapshot_id: 'snap-v2',
    version: 20,
    telemetry: { heart_rate_bpm: 130 },
    source: 'simulation',
  });
  assert(newerFirst.accepted === true, 'newer request applied first');
  assert(getDisplayedSnapshotId() === 'snap-v2', 'display shows newer snapshot');

  const olderLater = acceptLiveTelemetryResponse(first, {
    snapshot_id: 'snap-v1',
    version: 10,
    telemetry: { heart_rate_bpm: 90 },
    source: 'simulation',
  });
  assert(olderLater.accepted === false, 'older request rejected');
  assert(getDisplayedSnapshotId() === 'snap-v2', 'display did not move backward');
  assert(getDisplayedSnapshotMeta().heart_rate_bpm === 130, 'HR stays at newer value');
}

async function testFailedLiveTransitionRecovery() {
  console.log('D. Failed first live request: recoverable retained state');
  __resetTelemetryDisplayStateForTests();

  const demoEpoch = beginDisplayTransition('demo');
  bindDisplayedSnapshot('demo', demoEpoch, {
    snapshot_id: 'snap-demo',
    heart_rate_bpm: 72,
    source: 'demo',
  });

  const liveEpoch = beginDisplayTransition('realtime');
  const token = liveApplyToken();
  assert(isModeTransitionPending(), 'pending during failed attempt');

  // Simulate dashboard catch/!ok path.
  completeDisplayTransition(token.epoch);
  assert(token.epoch === liveEpoch, 'same epoch');
  assert(isModeTransitionPending() === false, 'pending cleared after failure');
  assert(getDisplayedSnapshotId() === 'snap-demo', 'prior demo ID retained');
  assert(getDisplayedSnapshotMeta().source === 'demo', 'source remains demo (not relabeled live)');
  assert(isDisplayReady() === true, 'ready with retained paired snapshot');

  // Recoverable: a later successful poll can complete.
  const retry = liveApplyToken();
  const recovered = acceptLiveTelemetryResponse(retry, {
    snapshot_id: 'snap-live-retry',
    version: 3,
    telemetry: { heart_rate_bpm: 118 },
    source: 'simulation',
  });
  assert(recovered.accepted === true, 'later live poll recovers');
  assert(getDisplayedSnapshotId() === 'snap-live-retry', 'live snapshot bound after recovery');
}

async function testChatReadinessTimeoutWithoutAmbiguousNull() {
  console.log('chat readiness: timeout without snapshot does not allow null send');
  __resetTelemetryDisplayStateForTests();
  beginDisplayTransition('demo');
  // Never bind — initial Demo load failure case.
  const resolution = resolveChatSnapshotRequest({ waited: true, ready: false });
  assert(resolution.ok === false, 'timeout with no ID is not ok');
  assert(resolution.reason === 'no_displayed_snapshot', 'explicit no_displayed_snapshot');
}

async function testChatReadinessRetainedAfterTimeout() {
  console.log('chat readiness: retained visible ID after timeout is explicit');
  __resetTelemetryDisplayStateForTests();
  const demoEpoch = beginDisplayTransition('demo');
  bindDisplayedSnapshot('demo', demoEpoch, {
    snapshot_id: 'snap-visible',
    heart_rate_bpm: 72,
    source: 'demo',
  });
  beginDisplayTransition('realtime');
  assert(isModeTransitionPending(), 'transition pending');
  const resolution = resolveChatSnapshotRequest({ waited: true, ready: false });
  assert(resolution.ok === true, 'retained visible ID may be sent');
  assert(resolution.snapshot_id === 'snap-visible', 'uses still-visible snapshot');
  assert(resolution.basis === 'retained_visible_after_timeout', 'basis documents retention');
}

async function testChatReadyUsesNewSnapshot() {
  console.log('chat readiness: successful transition uses newly displayed snapshot');
  __resetTelemetryDisplayStateForTests();
  const demoEpoch = beginDisplayTransition('demo');
  bindDisplayedSnapshot('demo', demoEpoch, { snapshot_id: 'snap-old', heart_rate_bpm: 72 });

  beginDisplayTransition('realtime');
  const waiter = waitForDisplayReady(1000);
  const token = liveApplyToken();
  // Deferred completion (no real sleep beyond microtask timer).
  await Promise.resolve();
  acceptLiveTelemetryResponse(token, {
    snapshot_id: 'snap-new',
    version: 4,
    telemetry: { heart_rate_bpm: 121 },
  });
  const ready = await waiter;
  assert(ready === true, 'wait resolves after bind');
  const resolution = resolveChatSnapshotRequest({ waited: true, ready: true });
  assert(resolution.ok === true, 'ready resolution ok');
  assert(resolution.snapshot_id === 'snap-new', 'queued message uses new snapshot');
  assert(resolution.basis === 'display_ready', 'basis is display_ready');
}

async function testSnapshotIdMatchesRenderedValues() {
  console.log('stored snapshot ID corresponds to rendered values');
  __resetTelemetryDisplayStateForTests();
  const epoch = beginDisplayTransition('demo');
  bindDisplayedSnapshot('demo', epoch, {
    snapshot_id: 'snap-fixture',
    heart_rate_bpm: 72.0,
    source: 'demo',
  });
  const meta = getDisplayedSnapshotMeta();
  assert(meta.snapshot_id === 'snap-fixture', 'snapshot_id bound');
  assert(meta.heart_rate_bpm === 72.0, 'HR fingerprint matches rendered demo value');
  assert(meta.ready === true, 'ready when ID present and not pending');
}

await testDemoToRealtimeFirstResponseCompletesTransition();
await testDelayedLiveCannotOverwriteDemo();
await testOutOfOrderPollingDoesNotMoveBackward();
await testFailedLiveTransitionRecovery();
await testChatReadinessTimeoutWithoutAmbiguousNull();
await testChatReadinessRetainedAfterTimeout();
await testChatReadyUsesNewSnapshot();
await testSnapshotIdMatchesRenderedValues();

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
