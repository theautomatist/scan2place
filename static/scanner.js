/*
 * Scanner-Modul (Smartphone). Kamera per html5-qrcode, sendet den rohen
 * QR-Inhalt an den Server (Rolle 'scanner'), der ihn parst, in der iBOM
 * nachschlaegt und die Rueckmeldung liefert.
 *
 * API:  BomScanner.start(ibomId, callbacks)  /  BomScanner.stop()
 *   callbacks = { onResult(res), onStatus(text, connected), onPresence(counts),
 *                 getSettings() -> {sound, vibrate, ...} }
 */
window.BomScanner = (function () {
  "use strict";

  var html5 = null;
  var ws = null;
  var ibomId = null;
  var reconnectTimer = null;
  var lastText = "";
  var lastTime = 0;
  var running = false;
  var cb = {};

  function settings() {
    try { return cb.getSettings ? (cb.getSettings() || {}) : {}; } catch (e) { return {}; }
  }

  // ---- WebSocket --------------------------------------------------------
  function wsUrl() {
    var p = location.protocol === "https:" ? "wss:" : "ws:";
    return p + "//" + location.host + "/ws/scanner/" + encodeURIComponent(ibomId);
  }

  function connectWs() {
    try { ws = new WebSocket(wsUrl()); } catch (e) { scheduleReconnect(); return; }
    ws.onopen = function () { status("connected", true); };
    ws.onmessage = function (ev) {
      var msg; try { msg = JSON.parse(ev.data); } catch (e) { return; }
      if (msg.type === "scan_result") handleResult(msg);
      else if (msg.type === "alternative_saved" && cb.onAlternativeSaved) cb.onAlternativeSaved(msg);
      else if (msg.type === "progress" && cb.onProgress) cb.onProgress(msg);
      else if (msg.type === "presence" && cb.onPresence) cb.onPresence(msg);
      else if (msg.type === "settings" && cb.onSettings) cb.onSettings(msg.settings);
    };
    ws.onclose = function () { status("connection lost", false); scheduleReconnect(); };
    ws.onerror = function () { try { ws.close(); } catch (e) {} };
  }

  function scheduleReconnect() {
    clearTimeout(reconnectTimer);
    if (running) reconnectTimer = setTimeout(connectWs, 2000);
  }

  function status(text, connected) {
    if (cb.onStatus) cb.onStatus(text, connected);
  }

  // ---- Feedback (Ton/Vibration) ----------------------------------------
  // kind: 'exact' (Treffer) | 'alt' (Alternative moeglich) | 'none' (nichts)
  function feedback(kind) {
    var s = settings();
    if (s.sound !== false) beep(kind);
    if (s.vibrate !== false && navigator.vibrate) {
      var pattern = kind === "exact" ? 80 : (kind === "alt" ? [40, 30, 40] : [60, 40, 60]);
      try { navigator.vibrate(pattern); } catch (e) {}
    }
  }

  function beep(kind) {
    try {
      var AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      var ctx = window.__bomAudioCtx || (window.__bomAudioCtx = new AC());
      if (ctx.state === "suspended") ctx.resume();
      var o = ctx.createOscillator(), g = ctx.createGain();
      o.connect(g); g.connect(ctx.destination);
      o.type = "sine";
      o.frequency.value = kind === "exact" ? 900 : (kind === "alt" ? 620 : 260);
      g.gain.value = 0.07;
      o.start();
      var dur = kind === "exact" ? 120 : (kind === "alt" ? 150 : 240);
      setTimeout(function () { try { o.stop(); } catch (e) {} }, dur);
    } catch (e) {}
  }

  function handleResult(msg) {
    var kind = msg.needs_confirm ? "alt" : (msg.matched || (msg.ok ? "exact" : "none"));
    feedback(kind);
    if (cb.onResult) cb.onResult(msg);
  }

  // Bestaetigte Alternative an den Server senden
  function confirmAlternative(payload) {
    if (ws && ws.readyState === 1) {
      ws.send(JSON.stringify(Object.assign({ type: "set_alternative" }, payload)));
    }
  }

  // Placing-Bestaetigung ("alle platziert") an den Server senden
  function confirmPlaced(payload) {
    if (ws && ws.readyState === 1) {
      ws.send(JSON.stringify(Object.assign({ type: "confirm_placed" }, payload)));
    }
  }

  // ---- Kamera / Decode --------------------------------------------------
  // Pull an LCSC/JLCPCB code out of the raw decode. Packaging labels carry several
  // codes next to the QR (DataMatrix, 1D barcode); a partial/garbled read has none.
  // Only a real LCSC code may pass — otherwise a clean part would wrongly report
  // "not recognised" and the user has to rescan. No LCSC pattern -> keep scanning.
  function extractLcsc(text) {
    if (!text) return null;
    var m = /(?:^|[^A-Za-z0-9])(C\d{3,})(?![0-9])/i.exec(text);
    return m ? m[1].toUpperCase() : null;
  }

  function onDecode(text) {
    var lcsc = extractLcsc(text);
    if (!lcsc) return;  // not an LCSC code (other barcode / unclean read) -> keep scanning
    var now = Date.now();
    if (lcsc === lastText && (now - lastTime) < 2500) return; // debounce on the LCSC number
    lastText = lcsc; lastTime = now;
    if (ws && ws.readyState === 1) {
      ws.send(JSON.stringify({ type: "scan", payload: text }));  // send raw text, server parses it
      pause();  // freeze until the result is processed/decided
      if (cb.onScanning) cb.onScanning(text);
    }
  }

  // Kamera anhalten (Bild einfrieren) / fortsetzen. State: 2=SCANNING, 3=PAUSED.
  function pause() {
    try { if (html5 && html5.getState && html5.getState() === 2) html5.pause(true); } catch (e) {}
  }
  function resume() {
    try { if (html5 && html5.getState && html5.getState() === 3) html5.resume(); } catch (e) {}
  }

  function qrbox(viewW, viewH) {
    var m = Math.min(viewW, viewH);
    var s = Math.max(160, Math.floor(m * 0.72));
    return { width: s, height: s };
  }

  function supportedFormats() {
    try {
      return [
        Html5QrcodeSupportedFormats.QR_CODE,
        Html5QrcodeSupportedFormats.DATA_MATRIX,
      ];
    } catch (e) { return undefined; }
  }

  async function startCamera() {
    html5 = new Html5Qrcode("qr-reader", { verbose: false });
    var config = { fps: 10, qrbox: qrbox, aspectRatio: 1.0 };
    var fmts = supportedFormats();
    if (fmts) config.formatsToSupport = fmts;
    await html5.start({ facingMode: "environment" }, config, onDecode, function () {});
  }

  // ---- Oeffentliche API -------------------------------------------------
  async function start(id, callbacks) {
    await stop();
    ibomId = id;
    cb = callbacks || {};
    running = true;
    connectWs();
    try {
      await startCamera();
      status("connected", ws && ws.readyState === 1);
      if (cb.onCameraReady) cb.onCameraReady();   // camera is live -> clear "Starting camera…"
    } catch (err) {
      running = true; // WS stays, camera does not
      var msg = "Camera could not be started.";
      if (location.protocol !== "https:" && location.hostname !== "localhost") {
        msg = "Camera needs HTTPS. Open the page via https://…";
      } else if (err && (err.name === "NotAllowedError" || String(err).indexOf("Permission") >= 0)) {
        msg = "Camera access was denied. Please allow it in your browser settings.";
      } else if (err && err.name === "NotFoundError") {
        msg = "No camera found.";
      }
      if (cb.onError) cb.onError(msg);
      throw err;
    }
  }

  async function stop() {
    running = false;
    clearTimeout(reconnectTimer);
    if (ws) { try { ws.close(); } catch (e) {} ws = null; }
    if (html5) {
      try { await html5.stop(); } catch (e) {}
      try { await html5.clear(); } catch (e) {}
      html5 = null;
    }
    lastText = ""; lastTime = 0;
  }

  return {
    start: start,
    stop: stop,
    pause: pause,
    resume: resume,
    confirmAlternative: confirmAlternative,
    confirmPlaced: confirmPlaced,
    isRunning: function () { return running; },
  };
})();
