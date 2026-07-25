(() => {
  "use strict";

  const UI = window.ReClipUI;
  const T = window.ReClipTranscript;
  const STAGE_PROGRESS = {
    queued: 3, uploading: 10, acquiring: 14, probing: 27, preparing: 42,
    enhancing: 60, transcribing: 79, validating: 94, completed: 100,
    failed: 100, canceled: 100
  };
  const TERMINAL = new Set(["completed", "failed", "canceled"]);
  const state = {
    jobs: [], nodes: new Map(), signatures: new Map(), expanded: new Set(),
    details: new Map(), transcripts: new Map(), versions: new Map(),
    busy: new Set(), loading: false, loaded: false, timer: null
  };
  const dom = {
    refresh: document.getElementById("refreshHistoryButton"),
    message: document.getElementById("historyMessage"),
    list: document.getElementById("transcriptionHistory")
  };
  const icons = {
    download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12m0 0 4-4m-4 4-4-4"/><path d="M4 19h16"/></svg>',
    empty: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5h16v14H4z"/><path d="M8 9h8M8 13h5"/></svg>'
  };

  function status(raw) {
    const value = String(raw || "queued").toLowerCase();
    return ({ done: "completed", error: "failed", cancelled: "canceled", pending: "queued" })[value] || value;
  }
  const terminal = (job) => TERMINAL.has(status(job.status));
  function progress(job) {
    const supplied = Number(job.progress);
    if (Number.isFinite(supplied)) return Math.max(0, Math.min(100, supplied));
    return STAGE_PROGRESS[status(job.stage || job.status)] ?? 8;
  }
  function jobTitle(job) {
    const metadata = job.metadata || {};
    const source = metadata.source || {};
    return job.title || source.title || job.source_name || job.filename
      || (job.source_type === "url" ? "Online video" : "Local media");
  }
  const sourceText = (job) => job.source_url || job.source_name || job.filename || "";
  function stageText(job) {
    const current = status(job.stage || job.status);
    if (job.cancel_requested && !terminal(job)) return "Cancellation requested";
    if (current === "queued") {
      const position = Number(job.queue_position);
      if (Number.isFinite(position) && position > 0) {
        return position === 1 ? "Next in queue" : "Position " + position + " in queue";
      }
      return "Waiting for the worker";
    }
    return ({
      uploading: "Receiving local file",
      acquiring: "Downloading source media",
      probing: "Inspecting source audio",
      preparing: "Preparing Whisper input",
      enhancing: "Enhancing speech",
      transcribing: "Recognizing speech",
      validating: "Checking transcript artifacts",
      completed: "All artifacts are ready",
      failed: "Pipeline stopped",
      canceled: "Job canceled"
    })[current] || "Worker is processing this job";
  }
  function statusText(job) {
    if (job.cancel_requested && !terminal(job)) return "Canceling";
    const current = status(job.stage || job.status);
    const position = Number(job.queue_position);
    return current === "queued" && position > 0 ? "Queued #" + position : UI.title(current);
  }
  function artifacts(detail) {
    const raw = detail && detail.artifacts;
    if (!raw) return [];
    if (Array.isArray(raw)) {
      return raw.map((item, index) => Object.assign({ key: item.key || String(index) }, item));
    }
    return Object.keys(raw).map((key) => typeof raw[key] === "string"
      ? { key: key, filename: raw[key] }
      : Object.assign({ key: key }, raw[key] || {}));
  }
  function endpoint(id, key, download) {
    return "/api/transcriptions/" + encodeURIComponent(id) + "/artifacts/"
      + encodeURIComponent(key) + (download ? "?download=1" : "");
  }
  function localPath(value, fallback) {
    if (!value) return fallback;
    try {
      const parsed = new URL(value, location.origin);
      if (parsed.origin === location.origin) return parsed.pathname + parsed.search + parsed.hash;
    } catch (_) {}
    return fallback;
  }
  function inlineUrl(id, item) {
    return localPath(item.inline_url || item.url, endpoint(id, item.key, false));
  }
  function downloadUrl(id, item) {
    const fallback = endpoint(id, item.key, true);
    const base = localPath(item.url, fallback);
    try {
      const parsed = new URL(base, location.origin);
      parsed.searchParams.set("download", "1");
      return parsed.pathname + parsed.search;
    } catch (_) {
      return fallback;
    }
  }
  function transcriptArtifact(items) {
    const preferred = ["transcript", "transcript_txt", "timestamped_txt", "primary_transcript", "enhanced_transcript"];
    for (const key of preferred) {
      const match = items.find((item) => String(item.key).toLowerCase() === key);
      if (match) return match;
    }
    return items.find((item) => {
      const key = String(item.key || "").toLowerCase();
      const filename = String(item.filename || "").toLowerCase();
      const type = String(item.media_type || "").toLowerCase();
      return !key.includes("log") && !key.includes("checksum")
        && (filename.endsWith(".txt") || type.startsWith("text/plain"));
    }) || null;
  }

  function fact(list, label, value) {
    if (value === undefined || value === null || value === "") return;
    const wrapper = UI.el("div");
    wrapper.append(UI.el("dt", "", label), UI.el("dd", "", value));
    list.appendChild(wrapper);
  }
  function detailNode(job) {
    const id = String(job.id);
    const section = UI.el("div", "job-detail");
    const inner = UI.el("div", "job-detail-inner");
    section.appendChild(inner);
    const detail = state.details.get(id);
    if (!detail) {
      inner.appendChild(UI.el("div", "transcript-loading", "Loading job details?"));
      return section;
    }

    const transcript = state.transcripts.get(id);
    const toolbar = UI.el("div", "transcript-toolbar");
    const heading = UI.el("div");
    heading.append(UI.el("strong", "", "Timestamped transcript"),
      UI.el("span", "", transcript && transcript.status === "ready"
        ? transcript.text.split(/\r?\n/).filter(Boolean).length + " lines" : ""));
    const copy = UI.button("Copy", "job-action transcript-copy", () => copyTranscript(id));
    copy.disabled = !transcript || transcript.status !== "ready" || !transcript.text;
    toolbar.append(heading, copy);
    inner.appendChild(toolbar);

    if (!transcript || transcript.status === "loading") {
      inner.appendChild(UI.el("div", "transcript-loading", "Loading transcript?"));
    } else if (transcript.status === "error") {
      inner.appendChild(UI.el("div", "transcript-empty", transcript.error));
    } else if (!transcript.text) {
      inner.appendChild(UI.el("div", "transcript-empty", "The transcript is empty."));
    } else {
      const pre = UI.el("pre", "transcript-view", transcript.text);
      pre.tabIndex = 0;
      pre.setAttribute("aria-label", "Read-only timestamped transcript");
      inner.appendChild(pre);
    }

    const files = artifacts(detail);
    if (files.length) {
      const section = UI.el("div", "artifacts");
      section.appendChild(UI.el("h4", "", "Artifacts"));
      const list = UI.el("div", "artifact-list");
      files.forEach((item) => {
        const link = UI.el("a", "artifact-link");
        link.href = downloadUrl(id, item);
        link.innerHTML = icons.download;
        link.appendChild(document.createTextNode(item.filename || UI.title(item.key)));
        if (item.size) link.title = UI.bytes(item.size);
        list.appendChild(link);
      });
      section.appendChild(list);
      inner.appendChild(section);
    }

    const metadata = detail.metadata || {};
    const facts = UI.el("dl", "detail-facts");
    fact(facts, "Detected language", metadata.detected_language || metadata.language_detected
      || (metadata.language && metadata.language.detected));
    fact(facts, "Duration", UI.duration(metadata.duration_seconds || metadata.duration || metadata.source_duration || metadata.audio_duration));
    fact(facts, "Segments", metadata.segment_count);
    fact(facts, "Source SHA-256", metadata.source_sha256 || (metadata.source && (metadata.source.checksum_sha256 || metadata.source.sha256)));
    if (facts.children.length) inner.appendChild(facts);
    return section;
  }

  function jobNode(job) {
    const id = String(job.id);
    const current = status(job.status);
    const article = UI.el("article", "job-card");
    article.dataset.jobId = id;
    article.dataset.status = current;
    const summary = UI.el("div", "job-summary");
    const heading = UI.el("div", "job-heading");
    const titleArea = UI.el("div");
    titleArea.appendChild(UI.el("h3", "job-title", jobTitle(job)));
    if (sourceText(job)) titleArea.appendChild(UI.el("span", "job-source", sourceText(job)));
    heading.appendChild(titleArea);

    const badge = UI.el("span", "status-badge"
      + (TERMINAL.has(current) || current === "queued" ? " is-" + current : ""), statusText(job));
    if (!terminal(job) && current !== "queued") {
      const spinner = UI.el("span", "spinner");
      spinner.setAttribute("aria-hidden", "true");
      badge.prepend(spinner);
    }
    heading.appendChild(badge);
    summary.appendChild(heading);

    const meta = UI.el("div", "job-meta");
    meta.appendChild(UI.el("span", "", UI.title(job.profile || "standard")));
    const language = job.requested_language || (job.metadata && job.metadata.detected_language);
    meta.appendChild(UI.el("span", "", language ? "Language: " + language : "Auto language"));
    if (UI.date(job.created_at)) meta.appendChild(UI.el("span", "", UI.date(job.created_at)));
    summary.appendChild(meta);

    const percent = progress(job);
    const track = UI.el("div", "progress-track");
    track.setAttribute("role", "progressbar");
    track.setAttribute("aria-label", "Transcription progress");
    track.setAttribute("aria-valuemin", "0");
    track.setAttribute("aria-valuemax", "100");
    track.setAttribute("aria-valuenow", String(Math.round(percent)));
    const fill = UI.el("span");
    fill.style.setProperty("--progress", percent + "%");
    track.appendChild(fill);
    summary.appendChild(track);
    const stage = UI.el("div", "job-stage-line");
    stage.append(UI.el("span", "", stageText(job)), UI.el("span", "", Math.round(percent) + "%"));
    summary.appendChild(stage);

    if (job.error) summary.appendChild(UI.el("p", "job-error", UI.friendly(job.error)));
    const actions = UI.el("div", "job-actions");
    const busy = state.busy.has(id);
    if (current === "completed") {
      const view = UI.button(state.expanded.has(id) ? "Hide transcript" : "View transcript",
        "job-action is-primary", () => toggleDetail(id));
      view.setAttribute("aria-expanded", String(state.expanded.has(id)));
      actions.appendChild(view);
    }
    if (!terminal(job)) {
      const cancel = UI.button(job.cancel_requested ? "Canceling?" : "Cancel",
        "job-action", () => runAction(id, "cancel"));
      cancel.disabled = busy || Boolean(job.cancel_requested);
      actions.appendChild(cancel);
    }
    if (current === "failed" || current === "canceled") {
      const retry = UI.button("Retry", "job-action", () => runAction(id, "retry"));
      retry.disabled = busy || !T.state.available;
      if (!T.state.available) retry.title = "The transcription worker is unavailable.";
      actions.appendChild(retry);
    }
    if (terminal(job)) {
      const remove = UI.button("Delete", "job-action is-danger", () => runAction(id, "delete"));
      remove.disabled = busy;
      actions.appendChild(remove);
    }
    if (actions.children.length) summary.appendChild(actions);
    article.appendChild(summary);
    if (state.expanded.has(id)) article.appendChild(detailNode(job));
    return article;
  }

  function emptyNode(text, failed) {
    const node = UI.el("div", "empty-history");
    node.innerHTML = icons.empty;
    node.append(
      UI.el("strong", "", failed ? "History unavailable" : "No transcriptions yet"),
      UI.el("span", "", text || "Add a link or local file to create your first transcript.")
    );
    return node;
  }
  function bump(id) {
    state.versions.set(id, (state.versions.get(id) || 0) + 1);
  }
  function signature(job) {
    const id = String(job.id);
    return JSON.stringify(job) + "|" + state.expanded.has(id)
      + "|" + (state.versions.get(id) || 0) + "|" + state.busy.has(id);
  }
  function render() {
    dom.list.setAttribute("aria-busy", String(state.loading));
    const jobs = state.jobs.slice().sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0));
    if (!jobs.length) {
      state.nodes.clear();
      state.signatures.clear();
      dom.list.replaceChildren(emptyNode());
      return;
    }
    const wanted = new Set(jobs.map((job) => String(job.id)));
    Array.from(state.nodes.keys()).forEach((id) => {
      if (!wanted.has(id)) {
        const old = state.nodes.get(id);
        if (old) old.remove();
        state.nodes.delete(id);
        state.signatures.delete(id);
      }
    });
    jobs.forEach((job) => {
      const id = String(job.id);
      const next = signature(job);
      let node = state.nodes.get(id);
      if (!node || state.signatures.get(id) !== next) {
        const replacement = jobNode(job);
        if (node && node.isConnected) node.replaceWith(replacement);
        node = replacement;
        state.nodes.set(id, node);
        state.signatures.set(id, next);
      }
      dom.list.appendChild(node);
    });
  }
  function schedule(delay) {
    if (state.timer) clearTimeout(state.timer);
    state.timer = setTimeout(() => {
      if (document.hidden) schedule(3000);
      else refreshHistory({ silent: true });
    }, delay);
  }
  async function refreshHistory(options) {
    const silent = options && options.silent;
    if (state.loading) return;
    state.loading = true;
    dom.refresh.disabled = true;
    dom.refresh.classList.add("is-spinning");
    if (!silent) UI.setMessage(dom.message, "");
    try {
      const jobs = [];
      let offset = 0;
      let total = null;
      do {
        const payload = await UI.api("/api/transcriptions?limit=200&offset=" + offset);
        const batch = Array.isArray(payload)
          ? payload : payload && Array.isArray(payload.jobs) ? payload.jobs : [];
        jobs.push(...batch);
        total = Number.isFinite(Number(payload && payload.total)) ? Number(payload.total) : jobs.length;
        offset = jobs.length;
        if (!batch.length) break;
      } while (offset < total);
      state.jobs = jobs;
      state.loaded = true;
      render();
      if (state.jobs.some((job) => !terminal(job))) schedule(1800);
      else if (state.timer) {
        clearTimeout(state.timer);
        state.timer = null;
      }
    } catch (error) {
      if (!silent || !state.jobs.length) UI.setMessage(dom.message, UI.friendly(error), "error");
      if (!state.jobs.length) dom.list.replaceChildren(emptyNode(UI.friendly(error), true));
      if (state.jobs.some((job) => !terminal(job))) schedule(4000);
    } finally {
      state.loading = false;
      dom.list.setAttribute("aria-busy", "false");
      dom.refresh.disabled = false;
      dom.refresh.classList.remove("is-spinning");
    }
  }

  async function loadDetail(id) {
    if (state.details.has(id)) return state.details.get(id);
    try {
      const payload = await UI.api("/api/transcriptions/" + encodeURIComponent(id));
      const detail = payload && payload.job ? payload.job : payload;
      state.details.set(id, detail || {});
      bump(id);
      render();
      return detail || {};
    } catch (error) {
      state.details.set(id, { artifacts: [] });
      state.transcripts.set(id, { status: "error", error: UI.friendly(error), text: "" });
      bump(id);
      render();
      throw error;
    }
  }
  async function loadTranscript(id, detail) {
    if (state.transcripts.has(id)) return;
    const direct = detail.transcript_text || detail.transcript;
    if (typeof direct === "string") {
      state.transcripts.set(id, { status: "ready", text: direct, error: "" });
      bump(id);
      render();
      return;
    }
    const item = transcriptArtifact(artifacts(detail));
    if (!item) {
      state.transcripts.set(id, {
        status: "error", text: "",
        error: "A timestamped TXT artifact is not available for this job."
      });
      bump(id);
      render();
      return;
    }
    state.transcripts.set(id, { status: "loading", text: "", error: "" });
    bump(id);
    render();
    try {
      const response = await fetch(inlineUrl(id, item));
      if (!response.ok) throw new Error("Could not load transcript (" + response.status + ").");
      state.transcripts.set(id, { status: "ready", text: await response.text(), error: "" });
    } catch (error) {
      state.transcripts.set(id, { status: "error", text: "", error: UI.friendly(error) });
    }
    bump(id);
    render();
  }
  async function toggleDetail(id) {
    if (state.expanded.has(id)) {
      state.expanded.delete(id);
      render();
      return;
    }
    state.expanded.add(id);
    render();
    try {
      const detail = await loadDetail(id);
      await loadTranscript(id, detail);
    } catch (error) {
      UI.toast(UI.friendly(error), "error");
    }
  }
  async function copyTranscript(id) {
    const item = state.transcripts.get(id);
    if (!item || item.status !== "ready") return;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(item.text);
      } else {
        const input = document.createElement("textarea");
        input.value = item.text;
        input.readOnly = true;
        input.style.cssText = "position:fixed;opacity:0";
        document.body.appendChild(input);
        input.select();
        document.execCommand("copy");
        input.remove();
      }
      UI.toast("Transcript copied.", "success");
    } catch (_) {
      UI.toast("Could not copy the transcript.", "error");
    }
  }
  async function runAction(id, action) {
    const job = state.jobs.find((item) => String(item.id) === id);
    if (!job || state.busy.has(id)) return;
    if (action === "delete" && !confirm('Delete "' + jobTitle(job)
      + '" and all saved artifacts? This cannot be undone.')) return;
    state.busy.add(id);
    render();
    try {
      if (action === "cancel") {
        await UI.api("/api/transcriptions/" + encodeURIComponent(id) + "/cancel", { method: "POST" });
        UI.toast("Cancellation requested.");
      } else if (action === "retry") {
        await UI.api("/api/transcriptions/" + encodeURIComponent(id) + "/retry", { method: "POST" });
        UI.toast("This job was returned to the queue.", "success");
      } else {
        await UI.api("/api/transcriptions/" + encodeURIComponent(id), { method: "DELETE" });
        state.expanded.delete(id);
        state.details.delete(id);
        state.transcripts.delete(id);
        state.versions.delete(id);
        UI.toast("Transcription and artifacts deleted.", "success");
      }
      await refreshHistory({ silent: true });
    } catch (error) {
      UI.toast(UI.friendly(error), "error");
    } finally {
      state.busy.delete(id);
      render();
    }
  }

  dom.refresh.addEventListener("click", () => refreshHistory());
  document.addEventListener("reclip:mode", (event) => {
    if (event.detail.mode === "transcript") {
      if (!T.state.checked) T.checkAsr();
      if (!state.loaded) refreshHistory();
    }
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && !document.getElementById("transcriptWorkspace").hidden) {
      refreshHistory({ silent: true });
    }
  });
  window.addEventListener("online", () => refreshHistory({ silent: true }));

  T.refreshHistory = refreshHistory;
  refreshHistory({ silent: true });
})();
