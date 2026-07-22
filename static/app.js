/*
 * App-Shell: iBOM-Verwaltung (Sidebar/History, Upload), Rollen (Viewer/Scanner),
 * Viewer-Einbettung, Scanner-Steuerung, Pairing-QR und Einstellungen.
 */
(function () {
  "use strict";

  var state = {
    iboms: [],
    currentId: null,
    role: "viewer",       // 'viewer' (PC) | 'scanner' (Smartphone)
    settings: {},
    progress: { phase: "sourcing", total: 0, sourced: 0, placed: 0 },
  };

  var $ = function (sel) { return document.querySelector(sel); };
  var el = {};

  function isMobile() {
    return /Mobi|Android|iPhone|iPad|iPod/i.test(navigator.userAgent)
      || (("ontouchstart" in window) && window.innerWidth < 900);
  }

  function fmtDate(iso) {
    if (!iso) return "";
    try {
      return new Date(iso).toLocaleString("de-DE", {
        day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit",
      });
    } catch (e) { return iso; }
  }

  // ---- API ---------------------------------------------------------------
  function api(path, opts) {
    return fetch(path, opts).then(function (r) {
      if (!r.ok) return r.json().then(function (j) { throw new Error(j.detail || r.statusText); },
                                       function () { throw new Error(r.statusText); });
      return r.status === 204 ? null : r.json();
    });
  }

  // ---- iBOM-Liste --------------------------------------------------------
  function loadIboms() {
    return api("/api/iboms").then(function (data) {
      state.iboms = data.iboms || [];
      renderSidebar();
    });
  }

  function metaById(id) {
    return state.iboms.filter(function (m) { return m.id === id; })[0] || null;
  }

  function renderSidebar() {
    var list = el.ibomList;
    list.innerHTML = "";
    if (!state.iboms.length) {
      list.innerHTML = '<li class="empty">No iBOMs yet. Upload one above.</li>';
      return;
    }
    state.iboms.forEach(function (m) {
      var li = document.createElement("li");
      li.className = "ibom-item" + (m.id === state.currentId ? " active" : "");
      li.dataset.id = m.id;
      var warn = m.warning ? '<span class="warn" title="' + m.warning + '"> ⚠</span>' : "";
      li.innerHTML =
        '<div class="info">' +
          '<div class="name">' + escapeHtml(m.name) + warn + '</div>' +
          '<div class="meta">' + fmtDate(m.last_modified) +
            (m.distinct_lcsc ? " · " + m.distinct_lcsc + " parts" : "") + '</div>' +
        '</div>' +
        '<button class="del" title="Delete iBOM" aria-label="Delete">' +
          '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
               'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            '<path d="M3 6h18"/>' +
            '<path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/>' +
            '<path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>' +
            '<line x1="10" x2="10" y1="11" y2="17"/>' +
            '<line x1="14" x2="14" y1="11" y2="17"/>' +
          '</svg>' +
        '</button>';
      li.querySelector(".info").addEventListener("click", function () { selectIbom(m.id); });
      li.querySelector(".del").addEventListener("click", function (ev) {
        ev.stopPropagation(); deleteIbom(m);
      });
      list.appendChild(li);
    });
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // ---- Upload ------------------------------------------------------------
  var uploadHideTimer = null;

  function handleUpload(file) {
    if (!file) return;
    clearTimeout(uploadHideTimer);
    renderUpload(file.name, 0, "Uploading…");
    var fd = new FormData();
    fd.append("file", file);
    var xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/iboms");
    xhr.upload.onprogress = function (e) {
      if (!e.lengthComputable) return;
      var pct = Math.round(e.loaded / e.total * 100);
      renderUpload(file.name, pct, pct < 100 ? "Uploading…" : "Processing…", pct >= 100);
    };
    xhr.upload.onload = function () {
      // bytes are on the wire — the server now parses/indexes the iBOM
      renderUpload(file.name, 100, "Processing…", true);
    };
    xhr.onload = function () {
      if (xhr.status >= 200 && xhr.status < 300) {
        var meta = {};
        try { meta = JSON.parse(xhr.responseText); } catch (e) {}
        renderUploadDone(meta);
        loadIboms().then(function () { if (meta.id) selectIbom(meta.id); });
      } else {
        renderUploadError(uploadError(xhr));
      }
    };
    xhr.onerror = function () { renderUploadError("Network error"); };
    xhr.send(fd);
  }

  function uploadError(xhr) {
    try { return JSON.parse(xhr.responseText).detail || ("Upload failed (" + xhr.status + ")"); }
    catch (e) { return "Upload failed (" + xhr.status + ")"; }
  }

  function renderUpload(name, pct, status, indeterminate) {
    el.uploadStatus.className = "upload-status active" + (indeterminate ? " indet" : "");
    el.uploadStatus.innerHTML =
      '<div class="up-head"><span class="up-name">' + escapeHtml(name) + "</span>" +
      '<span class="up-pct">' + (indeterminate ? escapeHtml(status) : pct + "%") + "</span></div>" +
      '<div class="up-track"><div class="up-fill" style="width:' + pct + '%"></div></div>';
  }

  function renderUploadDone(meta) {
    el.uploadStatus.className = "upload-status ok";
    el.uploadStatus.innerHTML =
      '<div class="up-head"><span class="up-name">✓ ' + escapeHtml(meta.name || "iBOM") + "</span>" +
      '<span class="up-pct">done</span></div>' +
      '<div class="up-track"><div class="up-fill" style="width:100%"></div></div>' +
      (meta.warning ? '<div class="up-warn">⚠ ' + escapeHtml(meta.warning) + "</div>" : "");
    uploadHideTimer = setTimeout(function () {
      if (el.uploadStatus.classList.contains("ok")) {
        el.uploadStatus.className = "upload-status";
        el.uploadStatus.innerHTML = "";
      }
    }, 4500);
  }

  function renderUploadError(msg) {
    el.uploadStatus.className = "upload-status error";
    el.uploadStatus.textContent = "✗ " + msg;
  }

  function deleteIbom(m) {
    if (!confirm('Delete iBOM "' + m.name + '"? The saved progress will be lost.')) return;
    api("/api/iboms/" + encodeURIComponent(m.id), { method: "DELETE" }).then(function () {
      if (state.currentId === m.id) { state.currentId = null; showHome(); }
      loadIboms();
    });
  }

  // ---- Views -------------------------------------------------------------
  function setView(name) {
    ["home", "viewer", "scanner"].forEach(function (v) {
      el["view_" + v].hidden = (v !== name);
    });
    el.roleSwitch.hidden = !state.currentId;
  }

  function showHome() {
    setView("home");
    el.topbarTitle.textContent = "scan2place";
    updateSidebarActive();
    stopScanner();
    el.frame.src = "about:blank";
  }

  function selectIbom(id) {
    state.currentId = id;
    updateSidebarActive();
    closeSidebarMobile();
    var m = metaById(id);
    el.topbarTitle.textContent = m ? m.name : id;
    if (state.role === "scanner") showScanner(id);
    else showViewer(id);
    updateRoleSwitch();
  }

  function showViewer(id) {
    stopScanner();
    setView("viewer");
    el.scanFeedback.textContent = "";
    el.scanFeedback.className = "scan-feedback";
    el.frame.src = "/ibom/" + encodeURIComponent(id);
    setPresence(null);
    updatePipelineUI();
    api("/api/iboms/" + encodeURIComponent(id) + "/progress").then(onProgress).catch(function () {});
  }

  function showScanner(id) {
    el.frame.src = "about:blank";
    setView("scanner");
    el.scanResult.className = "scan-result idle";
    el.scanResult.textContent = "Starting camera…";
    el.scanLog.innerHTML = "";
    updateScannerPhase();
    api("/api/iboms/" + encodeURIComponent(id) + "/progress").then(onProgress).catch(function () {});
    startScanner(id);
  }

  // ---- Scanner ----------------------------------------------------------
  function startScanner(id) {
    if (!window.BomScanner) return;
    BomScanner.start(id, {
      getSettings: function () { return state.settings; },
      onStatus: function (text, connected) {
        setConnDot(connected ? "ok" : "off", connected ? "connected" : text);
      },
      onPresence: function (c) {
        var v = c.viewers || 0;
        setConnDot(v > 0 ? "ok" : "warn", v > 0 ? "viewer connected" : "no viewer — open the iBOM on the PC");
      },
      onSettings: function (s) { if (s) { state.settings = s; updateScannerPhase(); } },
      onProgress: function (pr) { onProgress(pr); },
      onCameraReady: function () {
        if (el.scanResult.classList.contains("idle")) {
          el.scanResult.textContent = "Point the camera at a part's QR code";
        }
      },
      onScanning: function (raw) { showScanning(raw); },
      onResult: function (msg) { showScanResult(msg); },
      onError: function (m) {
        el.scanResult.className = "scan-result err";
        el.scanResult.textContent = m;
      },
    }).catch(function () { /* Fehler bereits via onError angezeigt */ });
  }

  function stopScanner() {
    if (window.BomScanner && BomScanner.isRunning()) BomScanner.stop();
  }
  function pauseScanner() { if (window.BomScanner && BomScanner.pause) BomScanner.pause(); }
  function resumeScanner() { if (window.BomScanner && BomScanner.resume) BomScanner.resume(); }

  function showScanning(raw) {
    var m = /pc[:=]\s*(C\d{3,})/i.exec(raw || "") || /^(C\d{3,})$/i.exec((raw || "").trim());
    var lcsc = m ? m[1].toUpperCase() : "";
    hideSuggest();
    el.scanResult.className = "scan-result loading";
    el.scanResult.innerHTML = '<span class="spinner"></span> ' +
      (lcsc ? escapeHtml(lcsc) + " checking…" : "checking…");
  }

  function showScanResult(msg) {
    var m = msg.matched || (msg.ok ? "exact" : "none");
    hideSuggest();
    if (m === "exact" && msg.result) {
      if (msg.needs_confirm) {
        renderPlacingConfirm(msg);   // scanner stays paused until confirmation
      } else {
        var refs = (msg.result.refs || []).join(", ");
        el.scanResult.className = "scan-result ok";
        el.scanResult.innerHTML = "✓ <b>" + escapeHtml(msg.lcsc) + "</b> → " + escapeHtml(refs) +
          (msg.result.value ? " <span style='opacity:.8'>(" + escapeHtml(msg.result.value) + ")</span>" : "");
        addScanLog("exact", msg.lcsc, refs);
        resumeScanner();
      }
    } else if (m === "alternative") {
      renderSuggest(msg);   // scanner stays paused until decision
    } else {
      var pi = msg.part_info;
      el.scanResult.className = "scan-result err";
      if (pi && pi.ok) {
        el.scanResult.innerHTML = "✗ <b>" + escapeHtml(msg.lcsc) + "</b> · " +
          escapeHtml(((pi.value_raw || "") + " " + (pi.size || pi.package || "")).trim()) +
          "<br><span style='opacity:.85'>not part of this project</span>";
      } else if (pi && pi.error) {
        el.scanResult.innerHTML = "⚠ <b>" + escapeHtml(msg.lcsc) + "</b>" +
          "<br><span style='opacity:.85'>online lookup failed (" + escapeHtml(pi.error) + ")</span>";
      } else {
        el.scanResult.textContent = msg.lcsc
          ? "✗ " + msg.lcsc + " — cannot be matched"
          : "✗ No LCSC number found in the code";
      }
      addScanLog("none", msg.lcsc || "?", "");
      resumeScanner();
    }
  }

  function renderSuggest(msg) {
    pauseScanner();  // camera stays paused until decision
    var pi = msg.part_info || {};
    var cands = msg.candidates || [];
    var cat = pi.category ? pi.category.split(">").pop().trim() : "";
    var html =
      '<div class="sg-info">⚠ not in BOM · <b>' + escapeHtml(msg.lcsc) + '</b> · ' +
        escapeHtml((pi.value_raw || "?") + " · " + (pi.size || pi.package || "?")) +
        (cat ? " · " + escapeHtml(cat) : "") + '</div>' +
      '<div class="sg-actions">';
    cands.forEach(function (c, i) {
      var note = c.size_match ? "" : " · ⚠ different package";
      var st = c.already_sourced ? "already Sourced ✓"
             : (c.already_placed ? "already Placed ✓" : "still open");
      html += '<button class="sg-big sg-yes" data-i="' + i + '">' +
        '<span class="sg-big-title">✓ Adopt</span>' +
        '<span class="sg-big-sub">' +
          escapeHtml(c.value + " → " + (c.refs || []).join(", ") + " · " + st + note) +
        '</span></button>';
    });
    html += '<button class="sg-big sg-no">✗ Reject</button></div>';

    el.scanResult.className = "scan-result idle";
    el.scanResult.textContent = "Alternative found — please decide";
    el.scanSuggest.hidden = false;
    el.scanSuggest.innerHTML = html;
    el.scanSuggest.querySelectorAll(".sg-yes").forEach(function (btn) {
      btn.addEventListener("click", function () {
        confirmAlt(msg, pi, cands[parseInt(btn.dataset.i, 10)]);
      });
    });
    el.scanSuggest.querySelector(".sg-no").addEventListener("click", function () {
      hideSuggest();
      el.scanResult.className = "scan-result err";
      el.scanResult.textContent = "✗ " + msg.lcsc + " rejected";
      addScanLog("none", msg.lcsc, "rejected");
      resumeScanner();
    });
  }

  function confirmAlt(msg, pi, c) {
    if (!c || !window.BomScanner) return;
    BomScanner.confirmAlternative({
      altLcsc: msg.lcsc,
      altMpn: pi.mpn || msg.name,
      altValue: pi.value_raw,
      altPackage: pi.size || pi.package,
      targetLcsc: c.original_lcsc,
      refs: c.refs,
      footprints: c.footprints,
    });
    hideSuggest();
    el.scanResult.className = "scan-result ok";
    el.scanResult.innerHTML = "✚ <b>" + escapeHtml(msg.lcsc) + "</b> adopted as alternative → " +
      escapeHtml((c.refs || []).join(", "));
    addScanLog("alt", msg.lcsc, (c.refs || []).join(", "));
    resumeScanner();
  }

  function hideSuggest() {
    el.scanSuggest.hidden = true;
    el.scanSuggest.innerHTML = "";
  }

  // Placing phase: confirm "all placed?" (large overlay, like the suggestion)
  function renderPlacingConfirm(msg) {
    pauseScanner();
    var r = msg.result || {};
    var refs = (r.refs || []).join(", ");
    var n = (r.refs || r.footprints || []).length;
    el.scanResult.className = "scan-result idle";
    el.scanResult.textContent = "Please confirm";
    el.scanSuggest.hidden = false;
    el.scanSuggest.innerHTML =
      '<div class="sg-info">📍 Placing · <b>' + escapeHtml(msg.lcsc) + '</b>' +
        (r.value ? ' · ' + escapeHtml(r.value) : '') + '</div>' +
      '<div class="sg-actions">' +
        '<div class="pl-q">All <b>' + n + '</b> components placed?' +
          '<br><span class="pl-refs">' + escapeHtml(refs) + '</span></div>' +
        '<button class="sg-big sg-yes">✓ Yes, all placed</button>' +
        '<button class="sg-big sg-no">✗ Not yet</button></div>';
    el.scanSuggest.querySelector(".sg-yes").addEventListener("click", function () {
      if (window.BomScanner) BomScanner.confirmPlaced({
        lcsc: msg.lcsc, footprints: r.footprints, refs: r.refs, value: r.value,
      });
      hideSuggest();
      el.scanResult.className = "scan-result ok";
      el.scanResult.innerHTML = "✓ <b>" + escapeHtml(msg.lcsc) + "</b> placed → " + escapeHtml(refs);
      addScanLog("exact", msg.lcsc, refs);
      resumeScanner();
    });
    el.scanSuggest.querySelector(".sg-no").addEventListener("click", function () {
      hideSuggest();
      el.scanResult.className = "scan-result err";
      el.scanResult.textContent = "⏸ " + msg.lcsc + " — not placed yet";
      resumeScanner();
    });
  }

  function addScanLog(kind, lcsc, refs) {
    var badge = kind === "exact" ? "✅" : (kind === "alt" ? "✚" : "⛔");
    var li = document.createElement("li");
    li.innerHTML = '<span class="badge">' + badge + '</span>' +
      '<span class="l-lcsc">' + escapeHtml(lcsc) + '</span>' +
      '<span class="l-refs">' + escapeHtml(refs) + '</span>';
    el.scanLog.insertBefore(li, el.scanLog.firstChild);
    while (el.scanLog.children.length > 30) el.scanLog.removeChild(el.scanLog.lastChild);
  }

  // ---- Presence (Viewer, via postMessage aus dem iframe) ----------------
  function setPresence(counts) {
    if (!counts) { el.presence.textContent = ""; return; }
    var s = counts.scanners || 0;
    el.presence.innerHTML = s > 0
      ? '<span class="badge-status ok">scanner</span>'
      : '<span class="badge-status off">no scanner</span>';
  }

  window.addEventListener("message", function (ev) {
    var d = ev.data;
    if (!d || !d.__lcsc) return;
    if (d.type === "presence") {
      setPresence(d);
    } else if (d.type === "progress") {
      onProgress(d);
    } else if (d.type === "scan") {
      if (d.found) {
        el.scanFeedback.className = "scan-feedback ok";
        el.scanFeedback.textContent = "✓ " + d.lcsc + " → " + (d.refs || []).join(", ") +
          (d.value ? " (" + d.value + ")" : "");
      } else {
        el.scanFeedback.className = "scan-feedback err";
        el.scanFeedback.textContent = "✗ " + (d.lcsc || "?") + " not in this BOM";
      }
    }
  });

  // ---- Rolle -------------------------------------------------------------
  function updateRoleSwitch() {
    el.roleSwitch.querySelectorAll("button").forEach(function (b) {
      b.classList.toggle("active", b.dataset.role === state.role);
    });
  }

  function switchRole(role) {
    if (role === state.role) return;
    state.role = role;
    updateRoleSwitch();
    if (state.currentId) selectIbom(state.currentId);
  }

  // ---- Sidebar-Helfer ----------------------------------------------------
  function updateSidebarActive() {
    el.ibomList.querySelectorAll(".ibom-item").forEach(function (li) {
      li.classList.toggle("active", li.dataset.id === state.currentId);
    });
  }
  function openSidebarMobile() { $("#app").classList.add("sidebar-open"); }
  function closeSidebarMobile() { $("#app").classList.remove("sidebar-open"); }

  // ---- Pairing-QR --------------------------------------------------------
  function renderPairing() {
    var host = (el.pairHost.value || "").trim() || location.host;
    // always a full https URL so the camera app recognises it as a link
    var url = "https://" + host + "/scan?ibom=" + encodeURIComponent(state.currentId);
    el.pairUrl.value = url;
    el.pairQr.innerHTML = "";
    try {
      var qr = qrcode(0, "M");
      qr.addData(url);
      qr.make();
      // ohne 'scalable': das SVG bekommt feste width/height in px und ist sichtbar
      el.pairQr.innerHTML = qr.createSvgTag({ cellSize: 6, margin: 2 });
    } catch (e) {
      el.pairQr.textContent = url;
    }
    var isLocal = /^(localhost|127\.|0\.0\.0\.0|\[?::1)/i.test(host);
    el.pairWarn.hidden = !isLocal;
    if (isLocal) {
      el.pairWarn.textContent = "⚠ The phone can't open a localhost address — "
        + "enter your PC's LAN IP above (e.g. 192.168.x.x:8090).";
    }
    try { localStorage.setItem("lcsc_pair_host", host); } catch (e) {}
  }

  function openPairing() {
    if (!state.currentId) return;
    var saved = "";
    try { saved = localStorage.getItem("lcsc_pair_host") || ""; } catch (e) {}
    el.pairHost.value = saved || location.host;
    renderPairing();
    openModal(el.pairModal);
  }

  // ---- Einstellungen -----------------------------------------------------
  function loadSettings() {
    return api("/api/settings").then(function (s) { state.settings = s; applySettingsToUI(); });
  }

  function applySettingsToUI() {
    document.querySelectorAll("[data-setting]").forEach(function (input) {
      var key = input.dataset.setting;
      if (input.type === "color" || input.tagName === "SELECT") {
        if (state.settings[key] !== undefined) input.value = state.settings[key];
      } else {
        input.checked = !!state.settings[key];
      }
    });
  }

  function saveSetting(key, value) {
    var patch = {}; patch[key] = value;
    state.settings[key] = value;
    api("/api/settings", {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch),
    }).then(function (s) {
      state.settings = s;
      updatePipelineUI();
      el.settingsSaved.textContent = "✓ saved";
      setTimeout(function () { el.settingsSaved.textContent = ""; }, 1500);
    });
  }

  // ---- Pipeline-Anzeige (Viewer) ----------------------------------------
  function updatePipelineUI() {
    el.pipelineBar.hidden = false;
    el.btnPair.classList.add("icon-only");  // CSS hides the label text
    var p = state.progress;
    el.phaseSwitch.querySelectorAll("button").forEach(function (b) {
      b.classList.toggle("active", b.dataset.phase === p.phase);
    });
    // beide Balken: Sourced (blau, breiter) + Placed (gruen, liegt darueber)
    el.progressSourced.style.width = (p.total ? Math.round(p.sourced / p.total * 100) : 0) + "%";
    el.progressPlaced.style.width = (p.total ? Math.round(p.placed / p.total * 100) : 0) + "%";
    el.progressLabel.innerHTML =
      '<span class="p-s">Sourced ' + p.sourced + "/" + p.total + '</span> · ' +
      '<span class="p-p">Placed ' + p.placed + "/" + p.total + '</span>';
  }

  // ---- Pipeline-Anzeige (Scanner) ---------------------------------------
  function updateScannerPhase() {
    var p = state.progress;
    el.scannerPhaseSwitch.querySelectorAll("button").forEach(function (b) {
      b.classList.toggle("active", b.dataset.phase === p.phase);
    });
    el.scannerProgressSourced.style.width = (p.total ? Math.round(p.sourced / p.total * 100) : 0) + "%";
    el.scannerProgressPlaced.style.width = (p.total ? Math.round(p.placed / p.total * 100) : 0) + "%";
    // Label zeigt die aktuelle Phase (Sourced ODER Placed) + Prozent
    var placing = p.phase === "placing";
    var done = placing ? p.placed : p.sourced;
    var pct = p.total ? Math.round(done / p.total * 100) : 0;
    el.scannerProgressLabel.innerHTML = (placing
      ? '<span class="p-p">Placed ' + p.placed + "/" + p.total + "</span>"
      : '<span class="p-s">Sourced ' + p.sourced + "/" + p.total + "</span>")
      + " · " + pct + "%";
  }

  // Verbindungs-Punkt im Scanner-Header. Bei 'ok' nur der Punkt, sonst + Kurztext.
  function setConnDot(kind, label) {
    if (!el.scannerConn) return;
    el.scannerConn.className = "conn-dot " + kind;
    el.scannerConn.textContent = kind === "ok" ? "" : label;
    el.scannerConn.title = label;
  }

  function onProgress(p) {
    state.progress = {
      phase: p.phase || "sourcing",
      total: p.total || 0, sourced: p.sourced || 0, placed: p.placed || 0,
    };
    updatePipelineUI();
    updateScannerPhase();
  }

  // ---- Modals ------------------------------------------------------------
  function openModal(m) { m.hidden = false; }
  function closeModal(m) { m.hidden = true; }

  // ---- Init --------------------------------------------------------------
  function cacheEls() {
    el.ibomList = $("#ibom-list");
    el.uploadStatus = $("#upload-status");
    el.topbarTitle = $("#topbar-title");
    el.presence = $("#presence");
    el.roleSwitch = $("#role-switch");
    el.frame = $("#ibom-frame");
    el.scanFeedback = $("#scan-feedback");
    el.pipelineBar = $("#pipeline-bar");
    el.phaseSwitch = $("#phase-switch");
    el.progressSourced = $("#progress-sourced");
    el.progressPlaced = $("#progress-placed");
    el.progressLabel = $("#progress-label");
    el.btnPair = $("#btn-pair");
    el.scannerConn = $("#scanner-conn");
    el.scannerProgressLabel = $("#scanner-progress-label");
    el.scannerProgressSourced = $("#scanner-progress-sourced");
    el.scannerProgressPlaced = $("#scanner-progress-placed");
    el.scannerPhaseSwitch = $("#scanner-phase-switch");
    el.scanResult = $("#scan-result");
    el.scanSuggest = $("#scan-suggest");
    el.scanLog = $("#scan-log");
    el.pairModal = $("#pair-modal");
    el.pairQr = $("#pair-qr");
    el.pairUrl = $("#pair-url");
    el.pairHost = $("#pair-host");
    el.pairWarn = $("#pair-warn");
    el.settingsModal = $("#settings-modal");
    el.settingsSaved = $("#settings-saved");
    el.view_home = $("#view-home");
    el.view_viewer = $("#view-viewer");
    el.view_scanner = $("#view-scanner");
  }

  function bindEvents() {
    $("#file-input").addEventListener("change", function (e) {
      handleUpload(e.target.files[0]); e.target.value = "";
    });
    $("#open-settings").addEventListener("click", function () {
      applySettingsToUI(); el.settingsSaved.textContent = ""; openModal(el.settingsModal);
    });
    $("#btn-pair").addEventListener("click", openPairing);
    el.pairHost.addEventListener("input", function () { if (state.currentId) renderPairing(); });
    $("#copy-url").addEventListener("click", function () {
      el.pairUrl.select();
      try { navigator.clipboard.writeText(el.pairUrl.value); } catch (e) { document.execCommand("copy"); }
    });
    document.querySelectorAll("[data-close-modal]").forEach(function (b) {
      b.addEventListener("click", function () { closeModal(b.closest(".modal")); });
    });
    document.querySelectorAll(".modal").forEach(function (m) {
      m.addEventListener("click", function (e) { if (e.target === m) closeModal(m); });
    });
    document.querySelectorAll("[data-setting]").forEach(function (input) {
      input.addEventListener("change", function () {
        var isValue = input.type === "color" || input.tagName === "SELECT";
        saveSetting(input.dataset.setting, isValue ? input.value : input.checked);
      });
    });
    el.roleSwitch.querySelectorAll("button").forEach(function (b) {
      b.addEventListener("click", function () { switchRole(b.dataset.role); });
    });
    // Phasen-Umschalter im Viewer UND im Scanner
    [el.phaseSwitch, el.scannerPhaseSwitch].forEach(function (sw) {
      sw.querySelectorAll("button").forEach(function (b) {
        b.addEventListener("click", function () {
          if (!state.currentId) return;
          api("/api/iboms/" + encodeURIComponent(state.currentId) + "/phase", {
            method: "PUT", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ phase: b.dataset.phase }),
          }).catch(function () {});  // UI folgt dem progress-Broadcast
        });
      });
    });
    $("#menu-toggle").addEventListener("click", openSidebarMobile);
    $("#sidebar-close").addEventListener("click", closeSidebarMobile);
    $("#sidebar-backdrop").addEventListener("click", closeSidebarMobile);
  }

  function init() {
    cacheEls();
    bindEvents();

    var params = new URLSearchParams(location.search);
    var ibomParam = params.get("ibom");
    var wantScan = location.pathname === "/scan";
    state.role = (wantScan || isMobile()) ? "scanner" : "viewer";
    updateRoleSwitch();

    $("#home-hint").textContent = "This app is reachable at: " + location.origin;

    Promise.all([loadSettings(), loadIboms()]).then(function () {
      if (ibomParam && metaById(ibomParam)) selectIbom(ibomParam);
      else showHome();
    }).catch(function () { showHome(); });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
