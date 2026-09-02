(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  } else {
    root.ReclipJobs = api;
  }
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  function newerJob(previous, incoming) {
    const previousAttempt = Number(previous.attempt_no || 0);
    const incomingAttempt = Number(incoming.attempt_no || 0);
    if (incomingAttempt !== previousAttempt) return incomingAttempt > previousAttempt;
    return Number(incoming.updated_at || 0) >= Number(previous.updated_at || 0);
  }

  function mergeJobs(existing, incoming) {
    const result = Array.isArray(existing) ? existing.slice() : [];
    const positions = new Map(result.map((job, index) => [job.job_id, index]));
    for (const job of (Array.isArray(incoming) ? incoming : [])) {
      if (!job || typeof job.job_id !== 'string') continue;
      const position = positions.get(job.job_id);
      if (position === undefined) {
        positions.set(job.job_id, result.length);
        result.push(job);
      } else if (newerJob(result[position], job)) {
        result[position] = job;
      }
    }
    return result;
  }

  function shouldApplyResponse(current, incoming, generation) {
    const currentGeneration = current &&
      (current.generation === undefined ? current.pollGeneration : current.generation);
    return Boolean(
      current && currentGeneration === generation && incoming &&
      Number(incoming.attempt_no) === Number(current.attemptNo)
    );
  }

  function shouldAutoSave(job, pageStartedJobs) {
    return Boolean(
      job && job.state === 'completed' && pageStartedJobs &&
      pageStartedJobs.has(job.job_id)
    );
  }

  function jobToCard(job) {
    const active = ['preparing', 'downloading', 'processing'].includes(job.state);
    return {
      jobId: job.job_id,
      attemptNo: job.attempt_no,
      url: '',
      title: job.title || '',
      format: job.format || 'video',
      status: job.state === 'completed' ? 'done' : active ? 'downloading' : 'error',
      phase: active ? job.state : job.state === 'interrupted' ? 'interrupted' : 'failed',
      progress: job.progress || null,
      lastProgress: job.last_progress || null,
      filename: job.filename || '',
      error: job.error || '',
      canRetry: job.can_retry === undefined
        ? ['failed', 'interrupted'].includes(job.state)
        : Boolean(job.can_retry),
      restored: true,
    };
  }

  return { mergeJobs, shouldApplyResponse, shouldAutoSave, jobToCard };
}));
