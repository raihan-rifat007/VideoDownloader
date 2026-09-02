const test = require('node:test');
const assert = require('node:assert/strict');
const {
  formatBytes,
  formatSpeed,
  formatEta,
  progressViewModel,
} = require('../static/progress.js');


test('unknown and zero have different meanings', () => {
  assert.equal(formatBytes(null), '—');
  assert.equal(formatBytes(0), '0 B');
  assert.equal(formatSpeed(0), '0 B/s');
  assert.equal(formatEta(null), '—');
  assert.equal(formatEta(65), '01:05');
  assert.equal(formatEta(3665), '1:01:05');
});


test('unknown percent remains indeterminate', () => {
  const view = progressViewModel({
    status: 'downloading',
    phase: 'downloading',
    progress: {
      percent: null,
      downloaded_bytes: 1024,
      total_bytes: null,
      total_is_estimate: false,
      speed_bps: null,
      eta_seconds: null,
      updated_at: 100,
    },
  }, 100000);
  assert.equal(view.indeterminate, true);
  assert.equal(view.percent, null);
  assert.equal(view.speedText, '—');
  assert.equal(view.etaText, '—');
});


test('stale progress hides speed and eta without changing the job phase', () => {
  const view = progressViewModel({
    status: 'downloading',
    phase: 'downloading',
    progress: {
      percent: 50,
      downloaded_bytes: 50,
      total_bytes: 100,
      total_is_estimate: false,
      speed_bps: 10,
      eta_seconds: 5,
      updated_at: 10,
    },
  }, 16000);
  assert.equal(view.phaseText, 'Downloading current stream');
  assert.equal(view.percent, 50);
  assert.equal(view.stale, true);
  assert.equal(view.speedText, '—');
  assert.equal(view.etaText, '—');
});
