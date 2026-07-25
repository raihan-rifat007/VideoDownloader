(() => {
  "use strict";

  const ICONS = {
    warning: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16.5h.01"/></svg>',
    media: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/></svg>',
    play: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="m7 4 13 8-13 8z"/></svg>'
  };
  const media = { mode: "video", cards: [], timers: new Map() };
  const id = (value) => document.getElementById(value);
  const dom = {
    tagline: id("tagline"), modes: [...document.querySelectorAll(".mode-pill")],
    download: id("downloadWorkspace"), transcript: id("transcriptWorkspace"),
    urls: id("mediaUrls"), fetch: id("fetchButton"), message: id("downloadMessage"),
    cards: id("downloadCards"), toasts: id("toastRegion")
  };

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }
  function button(label, className, click) {
    const node = el("button", className, label);
    node.type = "button";
    if (click) node.addEventListener("click", click);
    return node;
  }
  function setMessage(node, value, kind) {
    node.textContent = value || "";
    node.className = "inline-message" + (kind ? " is-" + kind : "");
  }
  function friendly(error) {
    const raw = error instanceof Error ? error.message : String(error || "Something went wrong");
    const cases = [
      [/Unsupported URL/i, "This URL is not supported."],
      [/Video unavailable|Private video/i, "This video is unavailable or private."],
      [/HTTP Error 403|access denied/i, "The platform denied access to this media."],
      [/HTTP Error 404|not found/i, "The requested media was not found."],
      [/copyright/i, "This media is blocked due to copyright restrictions."],
      [/geo/i, "This media is unavailable in your region."],
      [/timed out|timeout/i, "The request timed out. Try again."],
      [/network|failed to fetch/i, "Could not reach ReClip. Check that the containers are running."]
    ];
    const match = cases.find((entry) => entry[0].test(raw));
    return match ? match[1] : raw.length > 180 ? raw.slice(0, 177) + "?" : raw;
  }
  async function api(path, options) {
    const config = Object.assign({}, options || {});
    if (Object.prototype.hasOwnProperty.call(config, "json")) {
      config.headers = Object.assign({}, config.headers || {}, { "Content-Type": "application/json" });
      config.body = JSON.stringify(config.json);
      delete config.json;
    }
    const response = await fetch(path, config);
    if (response.status === 204) return null;
    const raw = await response.text();
    let payload = raw;
    if (raw) try { payload = JSON.parse(raw); } catch (_) {}
    if (!response.ok) {
      const detail = payload && typeof payload === "object"
        ? payload.error || payload.message || payload.reason : payload;
      throw new Error(detail || "Request failed (" + response.status + ")");
    }
    return payload;
  }
  function parseUrls(text) {
    const result = [];
    String(text || "").split(/[\s,]+/).map((item) => item.trim()).filter(Boolean).forEach((value) => {
      try {
        const parsed = new URL(value);
        if (/^https?:$/.test(parsed.protocol) && !result.includes(parsed.href)) result.push(parsed.href);
      } catch (_) {}
    });
    return result;
  }
  async function expandUrls(urls) {
    const result = [];
    for (const url of urls) {
      try {
        const payload = await api("/api/playlist", { method: "POST", json: { url: url } });
        if (payload && Array.isArray(payload.urls) && payload.urls.length) {
          payload.urls.forEach((item) => { if (item && !result.includes(item)) result.push(item); });
          continue;
        }
      } catch (_) {
        // A normal single-video extractor may not expose playlist entries.
      }
      if (!result.includes(url)) result.push(url);
    }
    return result;
  }
  function duration(seconds) {
    const value = Number(seconds);
    if (!Number.isFinite(value) || value <= 0) return "";
    const h = Math.floor(value / 3600);
    const m = Math.floor((value % 3600) / 60);
    const s = Math.floor(value % 60);
    return h ? h + ":" + String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0")
      : m + ":" + String(s).padStart(2, "0");
  }
  function bytes(value) {
    let size = Number(value);
    if (!Number.isFinite(size) || size < 0) return "";
    if (size < 1024) return size + " B";
    const units = ["KB", "MB", "GB", "TB"];
    let unit = 0;
    size /= 1024;
    while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit += 1; }
    return size.toFixed(size >= 10 ? 1 : 2) + " " + units[unit];
  }
  function date(value) {
    const parsed = new Date(value);
    if (!value || Number.isNaN(parsed.getTime())) return "";
    return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(parsed);
  }
  function title(value) {
    return String(value || "").replace(/[_-]+/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
  }
  function toast(value, kind) {
    const node = el("div", "toast" + (kind ? " is-" + kind : ""), value);
    dom.toasts.appendChild(node);
    setTimeout(() => node.remove(), 4500);
  }

  window.ReClipUI = { el, button, setMessage, friendly, api, parseUrls, expandUrls, duration, bytes, date, title, toast };

  function setMode(mode) {
    media.mode = mode;
    dom.modes.forEach((item) => {
      const active = item.dataset.mode === mode;
      item.classList.toggle("is-active", active);
      item.setAttribute("aria-pressed", String(active));
    });
    const transcript = mode === "transcript";
    dom.download.hidden = transcript;
    dom.transcript.hidden = !transcript;
    dom.tagline.textContent = transcript ? "Local, timestamped speech transcription"
      : mode === "audio" ? "Free audio downloader" : "Free media downloader";
    if (!transcript) render();
    document.dispatchEvent(new CustomEvent("reclip:mode", { detail: { mode: mode } }));
  }

  async function fetchMedia() {
    const urls = parseUrls(dom.urls.value);
    if (!urls.length) {
      setMessage(dom.message, "Enter at least one public http/https URL.", "error");
      dom.urls.focus();
      return;
    }
    setMessage(dom.message, "");
    dom.fetch.disabled = true;
    dom.fetch.innerHTML = '<span class="spinner"></span> Loading';
    media.cards = [];
    render();
    let expanded;
    try { expanded = await expandUrls(urls); }
    catch (error) {
      setMessage(dom.message, friendly(error), "error");
      dom.fetch.disabled = false;
      dom.fetch.textContent = "Fetch";
      return;
    }
    media.cards = expanded.map((url) => ({ url: url, status: "loading" }));
    render();
    await Promise.all(media.cards.map(async (card, index) => {
      try {
        const info = await api("/api/info", { method: "POST", json: { url: card.url } });
        if (info && info.error) throw new Error(info.error);
        media.cards[index] = Object.assign({}, card, {
          status: "ready", title: info && info.title || "", thumbnail: info && info.thumbnail || "",
          duration: info && info.duration, uploader: info && info.uploader || "",
          formats: info && Array.isArray(info.formats) ? info.formats : [],
          selectedFormatId: info && info.formats && info.formats[0] ? info.formats[0].id : null
        });
      } catch (error) {
        media.cards[index] = Object.assign({}, card, { status: "info-error", error: friendly(error) });
      }
      render();
    }));
    dom.fetch.disabled = false;
    dom.fetch.textContent = "Fetch";
  }

  function thumbnail(card) {
    const node = el("div", "media-thumb");
    if (card.status === "loading") node.appendChild(el("div", "loading-block"));
    else if (card.status === "info-error") {
      const icon = el("div", "media-error-icon"); icon.innerHTML = ICONS.warning; node.appendChild(icon);
    } else if (media.mode === "audio") {
      const icon = el("div", "no-thumb"); icon.innerHTML = ICONS.play; node.appendChild(icon);
    } else {
      let valid = "";
      try { const parsed = new URL(card.thumbnail); if (/^https?:$/.test(parsed.protocol)) valid = parsed.href; } catch (_) {}
      if (valid) {
        const image = document.createElement("img");
        image.src = valid; image.alt = ""; image.loading = "lazy"; image.referrerPolicy = "no-referrer";
        node.appendChild(image);
      } else {
        const icon = el("div", "no-thumb"); icon.innerHTML = ICONS.media; node.appendChild(icon);
      }
    }
    return node;
  }

  function cardNode(card, index) {
    const article = el("article", "media-card" + (card.status === "info-error" ? " is-error" : ""));
    article.appendChild(thumbnail(card));
    const body = el("div", "media-body");
    if (card.status === "loading") body.append(el("div", "loading-line"), el("div", "loading-line is-short"));
    else if (card.status === "info-error") {
      body.append(el("h3", "media-title", "Could not fetch media"),
        el("div", "media-error-message", card.error || "Unknown error"),
        el("div", "media-error-url", card.url));
    } else {
      body.appendChild(el("h3", "media-title", card.title || "Untitled"));
      body.appendChild(el("div", "media-meta", [card.uploader, duration(card.duration)].filter(Boolean).join(" ? ")));
      const actions = el("div", "media-actions");
      if (card.status === "ready") {
        actions.appendChild(button("Download", "action-button", () => download(index)));
        if (media.mode === "video" && card.formats && card.formats.length > 1) {
          card.formats.forEach((format) => {
            const chip = button(format.label, "quality-chip" + (format.id === card.selectedFormatId ? " is-active" : ""), () => {
              media.cards[index].selectedFormatId = format.id;
              render();
            });
            chip.setAttribute("aria-pressed", String(format.id === card.selectedFormatId));
            actions.appendChild(chip);
          });
        }
      } else if (card.status === "downloading") {
        const status = el("span", "media-status is-progress");
        status.innerHTML = '<span class="spinner"></span> Downloading?';
        actions.appendChild(status);
      } else if (card.status === "done") {
        actions.append(button("Save", "action-button is-done", () => save(index)),
          el("span", "media-status is-done", card.filename || "Ready"));
      } else if (card.status === "error") {
        actions.append(button("Retry", "action-button", () => download(index)),
          el("span", "media-status is-error", friendly(card.error)));
      }
      body.appendChild(actions);
    }
    article.appendChild(body);
    return article;
  }

  function render() {
    const fragment = document.createDocumentFragment();
    media.cards.forEach((card, index) => fragment.appendChild(cardNode(card, index)));
    if (media.cards.filter((card) => card.status === "ready").length > 1) {
      const bar = el("div", "download-all");
      bar.appendChild(button("Download All", "secondary-button", downloadAll));
      fragment.appendChild(bar);
    }
    dom.cards.replaceChildren(fragment);
  }

  async function download(index) {
    const card = media.cards[index];
    if (!card || card.status === "downloading") return;
    card.status = "downloading";
    render();
    try {
      const payload = await api("/api/download", { method: "POST", json: {
        url: card.url, format: media.mode, format_id: card.selectedFormatId, title: card.title || ""
      } });
      if (!payload || !payload.job_id) throw new Error("The download did not return a job ID.");
      card.jobId = payload.job_id;
      poll(index);
    } catch (error) {
      card.status = "error"; card.error = friendly(error); render();
    }
  }

  function poll(index) {
    const card = media.cards[index];
    const tick = async () => {
      try {
        const payload = await api("/api/status/" + encodeURIComponent(card.jobId));
        if (payload.status === "done") {
          media.timers.delete(card.jobId); card.status = "done"; card.filename = payload.filename;
          render(); save(index); return;
        }
        if (payload.status === "error") {
          media.timers.delete(card.jobId); card.status = "error"; card.error = payload.error || "Download failed";
          render(); return;
        }
        media.timers.set(card.jobId, setTimeout(tick, 1000));
      } catch (error) {
        media.timers.delete(card.jobId); card.status = "error"; card.error = friendly(error); render();
      }
    };
    tick();
  }

  function save(index) {
    const card = media.cards[index];
    if (!card || !card.jobId) return;
    const link = document.createElement("a");
    link.href = "/api/file/" + encodeURIComponent(card.jobId);
    link.download = card.filename || "";
    document.body.appendChild(link); link.click(); link.remove();
  }

  async function downloadAll() {
    const pending = media.cards.map((card, index) => ({ card, index })).filter((item) => item.card.status === "ready");
    for (const item of pending) await download(item.index);
  }

  dom.modes.forEach((item) => item.addEventListener("click", () => setMode(item.dataset.mode)));
  dom.fetch.addEventListener("click", fetchMedia);
  dom.urls.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); fetchMedia(); }
  });
  setMode("video");
})();
