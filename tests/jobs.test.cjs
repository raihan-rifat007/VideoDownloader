const test = require('node:test');
const assert = require('node:assert/strict');
const jobs = require('../static/jobs.js');

test('mergeJobs deduplicates by job id and keeps newest attempt', () => {
  const merged = jobs.mergeJobs(
    [{ job_id: 'a', attempt_no: 1, state: 'failed' }],
    [
      { job_id: 'a', attempt_no: 2, state: 'downloading' },
      { job_id: 'b', attempt_no: 1, state: 'completed' },
    ]
  );
  assert.deepEqual(merged.map((job) => job.job_id), ['a', 'b']);
  assert.equal(merged[0].attempt_no, 2);
});

test('stale response is rejected after resume starts', () => {
  assert.equal(jobs.shouldApplyResponse({ attemptNo: 2, generation: 4 }, { attempt_no: 1 }, 4), false);
  assert.equal(jobs.shouldApplyResponse({ attemptNo: 2, generation: 4 }, { attempt_no: 2 }, 4), true);
  assert.equal(jobs.shouldApplyResponse({ attemptNo: 2, generation: 4 }, { attempt_no: 2 }, 3), false);
});

test('restored completed task never auto-saves', () => {
  assert.equal(jobs.shouldAutoSave({ job_id: 'a', state: 'completed' }, new Set()), false);
  assert.equal(jobs.shouldAutoSave({ job_id: 'a', state: 'completed' }, new Set(['a'])), true);
});

test('job card maps interrupted state to retryable error', () => {
  const card = jobs.jobToCard({
    job_id: 'a',
    title: 'Sample',
    format: 'video',
    state: 'interrupted',
    attempt_no: 3,
    error: 'Download interrupted',
    progress: null,
    last_progress: { percent: 42 },
  });
  assert.equal(card.status, 'error');
  assert.equal(card.jobId, 'a');
  assert.equal(card.canRetry, true);
});

test('restored completed card keeps the server filename', () => {
  const job = {
    job_id: 'a'.repeat(32), title: '熊猫的一天', format: 'video',
    state: 'completed', attempt_no: 1, filename: '熊猫的一天.mp4',
  };
  const card = jobs.jobToCard(job);
  assert.equal(card.filename, '熊猫的一天.mp4');
  assert.equal(jobs.shouldAutoSave(job, new Set()), false);
});

test('job card maps cancelling state to a non-retryable active card', () => {
  const card = jobs.jobToCard({
    job_id: 'a',
    title: 'Sample',
    format: 'video',
    state: 'cancelling',
    attempt_no: 1,
    progress: null,
  });
  assert.equal(card.status, 'cancelling');
  assert.equal(card.phase, 'cancelling');
  assert.equal(card.canCancel, false);
  assert.equal(card.canRetry, false);
});

test('job card maps cancelled state to a retryable cancelled card', () => {
  const card = jobs.jobToCard({
    job_id: 'a',
    title: 'Sample',
    format: 'video',
    state: 'cancelled',
    attempt_no: 1,
    last_progress: { percent: 42 },
  });
  assert.equal(card.status, 'cancelled');
  assert.equal(card.phase, 'cancelled');
  assert.equal(card.canCancel, false);
  assert.equal(card.canRetry, true);
});
