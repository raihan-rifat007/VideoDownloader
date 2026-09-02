(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  } else {
    root.ReclipProgress = api;
  }
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const BYTE_UNITS = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  const PHASE_TEXT = {
    preparing: 'Preparing download',
    downloading: 'Downloading current stream',
    finalizing: 'Finishing current stream',
    processing: 'Processing media',
    complete: 'Ready to save',
    failed: 'Download failed',
  };

  function finiteNonnegative(value) {
    return typeof value === 'number' && Number.isFinite(value) && value >= 0
      ? value : null;
  }

  function formatBytes(value) {
    value = finiteNonnegative(value);
    if (value === null) return '—';
    let unit = 0;
    let amount = value;
    while (amount >= 1024 && unit < BYTE_UNITS.length - 1) {
      amount /= 1024;
      unit += 1;
    }
    const decimals = unit === 0 || amount >= 100 ? 0 : amount >= 10 ? 1 : 2;
    return `${amount.toFixed(decimals)} ${BYTE_UNITS[unit]}`;
  }

  function formatSpeed(value) {
    const formatted = formatBytes(value);
    return formatted === '—' ? formatted : `${formatted}/s`;
  }

  function formatEta(value) {
    value = finiteNonnegative(value);
    if (value === null) return '—';
    let seconds = Math.ceil(value);
    const hours = Math.floor(seconds / 3600);
    seconds %= 3600;
    const minutes = Math.floor(seconds / 60);
    seconds %= 60;
    const mm = String(minutes).padStart(2, '0');
    const ss = String(seconds).padStart(2, '0');
    return hours > 0 ? `${hours}:${mm}:${ss}` : `${mm}:${ss}`;
  }

  function formatSize(progress) {
    if (!progress || progress.downloaded_bytes === null || progress.downloaded_bytes === undefined) {
      return '—';
    }
    const downloaded = formatBytes(progress.downloaded_bytes);
    if (progress.total_bytes === null || progress.total_bytes === undefined) {
      return `${downloaded} downloaded`;
    }
    const estimate = progress.total_is_estimate ? '~' : '';
    return `${downloaded} / ${estimate}${formatBytes(progress.total_bytes)}`;
  }

  function progressViewModel(job, nowMs) {
    const progress = job && job.progress ? job.progress : {};
    const rawPercent = finiteNonnegative(progress.percent);
    const percent = rawPercent === null ? null : Math.min(100, rawPercent);
    const updatedAt = finiteNonnegative(progress.updated_at);
    const stale = updatedAt !== null && Number.isFinite(nowMs)
      ? (nowMs / 1000) - updatedAt > 5 : false;
    const phase = job && job.phase ? job.phase : 'preparing';
    return {
      phaseText: PHASE_TEXT[phase] || 'Downloading',
      percent,
      sizeText: formatSize(progress),
      speedText: stale ? '—' : formatSpeed(progress.speed_bps),
      etaText: stale ? '—' : formatEta(progress.eta_seconds),
      stale,
      indeterminate: percent === null,
      waitingForUpdate: stale,
    };
  }

  return { formatBytes, formatSpeed, formatEta, progressViewModel };
}));
