(() => {
  "use strict";

  const UI = window.ReClipUI;
  const state = { available: false, checked: false, files: [], submitting: false, system: null };
  const id = (value) => document.getElementById(value);
  const dom = {
    dot: id("asrModeDot"), banner: id("asrBanner"), title: id("asrBannerTitle"),
    detail: id("asrBannerDetail"), retry: id("retryAsrButton"),
    urls: id("transcriptUrls"), drop: id("dropZone"), input: id("fileInput"),
    files: id("pendingFiles"), language: id("languageCode"),
    submit: id("transcribeAllButton"), message: id("transcribeMessage")
  };
  const icons = {
    check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12 4 4L19 6"/></svg>',
    warning: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16.5h.01"/></svg>',
    close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="m7 7 10 10M17 7 7 17"/></svg>'
  };

  function profile() {
    const checked = document.querySelector('input[name="profile"]:checked');
    return checked ? checked.value : "standard";
  }
  function enable(available) {
    dom.urls.disabled = !available;
    dom.input.disabled = !available;
    dom.language.disabled = !available;
    dom.drop.setAttribute("aria-disabled", String(!available));
    document.querySelectorAll('input[name="profile"]').forEach((input) => { input.disabled = !available; });
    updateSubmit();
  }
  function workerText(payload) {
    const worker = payload && payload.worker;
    if (!worker) return "";
    return [
      worker.gpu_name || worker.gpu || worker.device_name || worker.device,
      worker.compute_type
    ].filter(Boolean).join(" ? ");
  }
  async function checkAsr() {
    dom.banner.className = "availability-banner is-checking";
    dom.title.textContent = "Checking transcription worker?";
    dom.detail.textContent = "Transcript controls will become available when the worker responds.";
    dom.banner.querySelector(".availability-icon").innerHTML = '<span class="spinner"></span>';
    dom.retry.hidden = true;
    try {
      const payload = await UI.api("/api/system/asr");
      state.checked = true;
      state.available = Boolean(payload && payload.available);
      state.system = payload || {};
      if (state.available) {
        dom.banner.className = "availability-banner is-ready";
        dom.banner.querySelector(".availability-icon").innerHTML = icons.check;
        dom.title.textContent = "Transcription worker ready";
        const queued = payload.queue && Number(payload.queue.queued);
        dom.detail.textContent = [
          workerText(payload),
          Number.isFinite(queued) && queued > 0 ? queued + " queued" : ""
        ].filter(Boolean).join(" ? ") || "GPU worker is available.";
      } else {
        dom.banner.className = "availability-banner is-unavailable";
        dom.banner.querySelector(".availability-icon").innerHTML = icons.warning;
        dom.title.textContent = "Transcription unavailable";
        dom.detail.textContent = payload && (payload.reason || payload.error)
          ? String(payload.reason || payload.error)
          : "The ASR worker is not ready. MP4 and MP3 downloads still work.";
        dom.retry.hidden = false;
      }
    } catch (error) {
      state.checked = true;
      state.available = false;
      state.system = null;
      dom.banner.className = "availability-banner is-unavailable";
      dom.banner.querySelector(".availability-icon").innerHTML = icons.warning;
      dom.title.textContent = "Transcription service unavailable";
      dom.detail.textContent = UI.friendly(error) + " MP4 and MP3 downloads are unaffected.";
      dom.retry.hidden = false;
    }
    dom.dot.className = "status-dot " + (state.available ? "is-ready" : "is-unavailable");
    enable(state.available);
  }

  const fileKey = (file) => [file.name, file.size, file.lastModified].join(":");
  function addFiles(list) {
    if (!state.available || state.submitting) return;
    const known = new Set(state.files.map((item) => fileKey(item.file)));
    Array.from(list || []).forEach((file) => {
      const key = fileKey(file);
      if (!known.has(key)) {
        known.add(key);
        state.files.push({
          id: Date.now() + "-" + Math.random(),
          file: file, status: "pending", progress: 0, error: ""
        });
      }
    });
    dom.input.value = "";
    renderFiles();
    updateSubmit();
  }
  function fileStatus(item) {
    if (item.status === "uploading") return "Uploading " + Math.round(item.progress) + "%";
    if (item.status === "done") return "Added to queue";
    if (item.status === "error") return UI.friendly(item.error);
    return UI.bytes(item.file.size);
  }
  function renderFiles() {
    const fragment = document.createDocumentFragment();
    state.files.forEach((item) => {
      const row = UI.el("div", "pending-file is-" + item.status);
      const main = UI.el("div", "pending-file-main");
      main.append(
        UI.el("span", "pending-file-name", item.file.name),
        UI.el("span", "pending-file-meta", fileStatus(item))
      );
      row.appendChild(main);
      const remove = UI.button("", "remove-file-button", () => {
        if (item.status === "uploading") return;
        state.files = state.files.filter((candidate) => candidate.id !== item.id);
        renderFiles();
        updateSubmit();
      });
      remove.innerHTML = icons.close;
      remove.setAttribute("aria-label", "Remove " + item.file.name);
      remove.disabled = item.status === "uploading";
      row.appendChild(remove);
      if (item.status === "uploading" || item.status === "done" || item.progress > 0) {
        const progress = UI.el("div", "upload-progress");
        progress.setAttribute("role", "progressbar");
        progress.setAttribute("aria-label", "Upload progress for " + item.file.name);
        progress.setAttribute("aria-valuemin", "0");
        progress.setAttribute("aria-valuemax", "100");
        progress.setAttribute("aria-valuenow", String(Math.round(item.progress)));
        const fill = UI.el("span");
        fill.style.setProperty("--progress", Math.max(0, Math.min(100, item.progress)) + "%");
        progress.appendChild(fill);
        row.appendChild(progress);
      }
      fragment.appendChild(row);
    });
    dom.files.replaceChildren(fragment);
  }
  function updateSubmit() {
    const hasUrls = UI.parseUrls(dom.urls.value).length > 0;
    const hasFiles = state.files.some((item) => item.status === "pending" || item.status === "error");
    dom.submit.disabled = !state.available || state.submitting || (!hasUrls && !hasFiles);
  }
  function upload(item, selectedProfile, language) {
    return new Promise((resolve, reject) => {
      const request = new XMLHttpRequest();
      const form = new FormData();
      form.append("file", item.file, item.file.name);
      form.append("profile", selectedProfile);
      if (language) form.append("language", language);
      item.status = "uploading";
      item.progress = 0;
      item.error = "";
      renderFiles();
      request.open("POST", "/api/transcriptions/upload");
      request.responseType = "text";
      request.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable) {
          item.progress = event.loaded / event.total * 100;
          renderFiles();
        }
      });
      request.addEventListener("load", () => {
        let payload = request.responseText;
        try { payload = request.responseText ? JSON.parse(request.responseText) : null; } catch (_) {}
        if (request.status >= 200 && request.status < 300) {
          item.status = "done";
          item.progress = 100;
          renderFiles();
          resolve(payload);
          return;
        }
        item.status = "error";
        item.error = payload && typeof payload === "object" ? payload.error || payload.message : payload;
        item.error = item.error || "Upload failed (" + request.status + ")";
        renderFiles();
        reject(new Error(item.error));
      });
      request.addEventListener("error", () => {
        item.status = "error";
        item.error = "The upload connection was interrupted.";
        renderFiles();
        reject(new Error(item.error));
      });
      request.send(form);
    });
  }

  async function transcribeAll() {
    await checkAsr();
    if (!state.available) {
      UI.setMessage(dom.message, "The transcription worker is not ready. MP4 and MP3 downloads are unaffected.", "error");
      return;
    }

    const sourceUrls = UI.parseUrls(dom.urls.value);
    const uploadItems = state.files.filter((item) => item.status === "pending" || item.status === "error");
    if (!sourceUrls.length && !uploadItems.length) {
      UI.setMessage(dom.message, dom.urls.value.trim()
        ? "No valid public http/https links were found."
        : "Add at least one link or local file.", "error");
      return;
    }
    const selectedProfile = profile();
    const language = dom.language.value.trim() || null;
    state.submitting = true;
    updateSubmit();
    UI.setMessage(dom.message, "Adding sources to the queue?");
    let created = 0;
    let linksDone = false;
    const errors = [];

    if (sourceUrls.length) {
      try {
        dom.submit.innerHTML = '<span class="spinner"></span> Expanding links';
        const urls = await UI.expandUrls(sourceUrls);
        dom.submit.innerHTML = '<span class="spinner"></span> Queueing links';
        const payload = await UI.api("/api/transcriptions/url", {
          method: "POST",
          json: { urls: urls, profile: selectedProfile, language: language }
        });
        const jobs = payload && Array.isArray(payload.jobs)
          ? payload.jobs : payload && payload.job ? [payload.job] : [];
        created += jobs.length || urls.length;
        linksDone = true;
      } catch (error) {
        errors.push("Links: " + UI.friendly(error));
      }
    }

    for (let index = 0; index < uploadItems.length; index += 1) {
      const item = uploadItems[index];
      dom.submit.innerHTML = '<span class="spinner"></span> Uploading ' + (index + 1) + " / " + uploadItems.length;
      try {
        await upload(item, selectedProfile, language);
        created += 1;
      } catch (error) {
        errors.push(item.file.name + ": " + UI.friendly(error));
      }
    }

    if (linksDone) dom.urls.value = "";
    state.files = state.files.filter((item) => item.status !== "done");
    renderFiles();
    state.submitting = false;
    dom.submit.textContent = "Transcribe All";
    updateSubmit();

    if (created) {
      const result = created + (created === 1
        ? " transcription added to the queue."
        : " transcriptions added to the queue.");
      UI.setMessage(dom.message, errors.length ? result + " Some sources failed." : result,
        errors.length ? "error" : "success");
      UI.toast(result, "success");
      if (window.ReClipTranscript.refreshHistory) {
        await window.ReClipTranscript.refreshHistory({ silent: true });
      }
      document.getElementById("historyHeading").scrollIntoView({ behavior: "smooth", block: "start" });
    } else {
      UI.setMessage(dom.message, errors.join(" ") || "No transcriptions were created.", "error");
    }
    if (errors.length) UI.toast(errors[0], "error");
  }

  dom.retry.addEventListener("click", checkAsr);
  dom.submit.addEventListener("click", transcribeAll);
  dom.urls.addEventListener("input", updateSubmit);
  dom.urls.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      transcribeAll();
    }
  });
  dom.drop.addEventListener("click", () => {
    if (state.available && !state.submitting) dom.input.click();
  });
  dom.drop.addEventListener("keydown", (event) => {
    if ((event.key === "Enter" || event.key === " ") && state.available && !state.submitting) {
      event.preventDefault();
      dom.input.click();
    }
  });
  dom.input.addEventListener("change", () => addFiles(dom.input.files));
  ["dragenter", "dragover"].forEach((type) => dom.drop.addEventListener(type, (event) => {
    event.preventDefault();
    if (state.available && !state.submitting) dom.drop.classList.add("is-dragging");
  }));
  ["dragleave", "drop"].forEach((type) => dom.drop.addEventListener(type, (event) => {
    event.preventDefault();
    dom.drop.classList.remove("is-dragging");
  }));
  dom.drop.addEventListener("drop", (event) => {
    if (state.available && !state.submitting) addFiles(event.dataTransfer.files);
  });
  document.querySelectorAll('input[name="profile"]').forEach((input) => {
    input.addEventListener("change", () => {
      document.querySelectorAll(".profile-option").forEach((label) => {
        label.classList.toggle("is-selected", label.querySelector("input").checked);
      });
    });
  });
  window.addEventListener("online", checkAsr);

  window.ReClipTranscript = {
    state: state,
    checkAsr: checkAsr,
    refreshHistory: null
  };
  enable(false);
  checkAsr();
})();
