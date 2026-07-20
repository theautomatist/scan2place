/*
 * scan2place — wird vom Server in die iBOM-HTML injiziert und laeuft im
 * iframe-Kontext der iBOM. Nutzt die iBOM-internen Funktionen/Variablen:
 *   pcbdata, config, settings (iBOM-intern, via window.settings),
 *   populateBomTable(), checkBomCheckbox(), footprintIndexToHandler,
 *   currentHighlightedRowId, smoothScrollToRow(), readStorage()/writeStorage(),
 *   EventHandler / IBOM_EVENT_TYPES
 *
 * Aufgaben:
 *   1. gespeicherten Checkbox-Zustand vom Server anwenden
 *   2. WebSocket zum Server (Rolle 'viewer'): auf Scans reagieren
 *   3. Bauteil hervorheben, hinscrollen und (je nach Einstellung) abhaken
 *   4. jede Checkbox-Aenderung zurueck an den Server (Persistenz)
 */
(function () {
  "use strict";

  var helper = window.__LCSC_HELPER__ || {};
  var helperSettings = helper.settings || {}; // eigene Einstellungen (NICHT iBOM-'settings')
  var altList = (helper.alternatives || []).slice(); // bekannte Alternativen
  var ws = null;
  var reconnectTimer = null;

  function log() {
    try { console.log.apply(console, ["[lcsc-helper]"].concat([].slice.call(arguments))); } catch (e) {}
  }

  // Nachricht an die umgebende App-Shell (fuer Presence-/Scan-Anzeige im Viewer).
  function toParent(obj) {
    try {
      if (window.parent && window.parent !== window) {
        window.parent.postMessage(Object.assign({ __lcsc: true }, obj), "*");
      }
    } catch (e) {}
  }

  // ---- Warten, bis die iBOM initialisiert ist ---------------------------
  function ibomReady() {
    return typeof pcbdata !== "undefined"
      && typeof populateBomTable === "function"
      && typeof checkBomCheckbox === "function"
      && typeof readStorage === "function"
      && typeof writeStorage === "function";
  }

  function whenReady(cb) {
    if (ibomReady()) { cb(); return; }
    var tries = 0;
    var iv = setInterval(function () {
      if (ibomReady()) { clearInterval(iv); cb(); }
      else if (++tries > 200) { clearInterval(iv); log("iBOM nicht bereit — Abbruch"); }
    }, 50);
  }

  // ---- Checkbox-Namen der iBOM -----------------------------------------
  function checkboxNames() {
    try {
      if (window.settings && Array.isArray(window.settings.checkboxes) && window.settings.checkboxes.length) {
        return window.settings.checkboxes;
      }
    } catch (e) {}
    try {
      if (typeof config !== "undefined" && config.checkboxes) {
        return String(config.checkboxes).split(",").map(function (s) { return s.trim(); }).filter(Boolean);
      }
    } catch (e) {}
    return ["Sourced", "Placed"];
  }

  // ---- Zustand einsammeln / anwenden -----------------------------------
  function collectState() {
    var out = {};
    checkboxNames().forEach(function (name) {
      var v = "";
      try { v = readStorage("checkbox_" + name) || ""; } catch (e) {}
      out[name] = v;
    });
    return out;
  }

  function applyServerState() {
    var st = helper.state || {};
    var names = Object.keys(st);
    if (!names.length) return;
    names.forEach(function (name) {
      try {
        writeStorage("checkbox_" + name, st[name] || "");
        if (window.settings && window.settings.checkboxStoredRefs) {
          window.settings.checkboxStoredRefs[name] = st[name] || "";
        }
      } catch (e) {}
    });
    try { populateBomTable(); } catch (e) {}
  }

  function sendState() {
    if (ws && ws.readyState === 1) {
      ws.send(JSON.stringify({ type: "checkbox_state", checkboxes: collectState() }));
    }
  }

  function sendAck(lcsc, found, refs) {
    if (ws && ws.readyState === 1) {
      ws.send(JSON.stringify({ type: "scan_ack", lcsc: lcsc, found: found, refs: refs || [] }));
    }
  }

  // ---- Fallback: Zeile weggefiltert -> direkt ueber Storage abhaken -----
  function storageCheck(idx, doSourced, doPlaced) {
    function addTo(name) {
      try {
        var raw = readStorage("checkbox_" + name) || "";
        var set = raw ? raw.split(",").filter(Boolean) : [];
        if (set.indexOf(String(idx)) === -1) set.push(String(idx));
        var val = set.join(",");
        writeStorage("checkbox_" + name, val);
        if (window.settings && window.settings.checkboxStoredRefs) {
          window.settings.checkboxStoredRefs[name] = val;
        }
      } catch (e) {}
    }
    if (doSourced) addTo("Sourced");
    if (doPlaced) addTo("Placed");
  }

  // ---- Alternative-Teile: Original ausgrauen + Ersatz-Zeile darunter ----

  // Feldname aus config.fields, der eines der Stichwoerter enthaelt
  function fieldName(keywords, fallbackIndex) {
    var f = (typeof config !== "undefined" && config.fields) || [];
    for (var i = 0; i < f.length; i++) {
      var low = String(f[i]).toLowerCase();
      for (var k = 0; k < keywords.length; k++) {
        if (low.indexOf(keywords[k]) >= 0) return f[i];
      }
    }
    return (fallbackIndex != null && f[fallbackIndex]) || null;
  }

  function headerRow() {
    return document.querySelector(".bom thead tr")
      || document.querySelector("#bomhead tr")
      || document.querySelector("table.bom tr");
  }

  // Spalten-Index ueber die Header-Zeile (gleiche Zellstruktur wie die bomrows,
  // inkl. der impliziten numCol) — robuster als aus columnOrder zu rechnen.
  function colIndex(colName) {
    if (!colName) return -1;
    var head = headerRow();
    if (!head) return -1;
    for (var i = 0; i < head.children.length; i++) {
      if (head.children[i].textContent.trim() === colName) return i;
    }
    return -1;
  }

  // Footprints -> aktuell gerenderte bomrow-<tr>, die diese Position zeigt
  function rowForFootprints(fps) {
    var bt = window.bomtable;
    if (!Array.isArray(bt) || !fps || !fps.length) return null;
    var target = String(fps[0]);
    for (var i = 0; i < bt.length; i++) {
      var g = bt[i];
      for (var j = 0; j < g.length; j++) {
        var fp = String(Array.isArray(g[j]) ? g[j][1] : g[j]);
        if (fp === target) return document.getElementById("bomrow" + (i + 1));
      }
    }
    return null;
  }

  function setCellText(tr, colName, text) {
    if (text == null || text === "") return;
    var idx = colIndex(colName);
    if (idx < 0 || !tr.children[idx]) return;
    tr.children[idx].textContent = text;
  }

  // Klon-Checkboxen togglen dieselbe Position wie das Original
  function linkCheckboxes(cloneTr, origTr) {
    var cCbx = cloneTr.querySelectorAll('input[type="checkbox"]');
    var oCbx = origTr.querySelectorAll('input[type="checkbox"]');
    for (var i = 0; i < cCbx.length; i++) {
      (function (c, o) {
        if (!o) return;
        c.checked = o.checked; c.indeterminate = o.indeterminate;
        c.onchange = function () {
          o.checked = c.checked;
          if (typeof o.onchange === "function") o.onchange();
        };
      })(cCbx[i], oCbx[i]);
    }
  }

  // Ersatz-Zeile: Klon des Originals mit den Daten des Alternativteils
  function buildAltRow(origTr, alt) {
    var tr = origTr.cloneNode(true);
    tr.removeAttribute("id");
    tr.className = "lcsc-altrow";
    // Highlight-Handler des Originals uebernehmen -> Hover zeigt die echten Footprints
    tr.onmousemove = origTr.onmousemove;
    tr.onmousedown = origTr.onmousedown;
    setCellText(tr, fieldName(["lcsc"]), alt.altLcsc || "");
    setCellText(tr, fieldName(["value"], 0), alt.altValue);
    setCellText(tr, fieldName(["mfr", "manufacturer", "mpn"]), alt.altMpn);
    setCellText(tr, fieldName(["package"]), alt.altPackage);
    linkCheckboxes(tr, origTr);
    // Loesch-Icon rechts (blendet bei Hover ueber die Zeile ein)
    var last = tr.lastElementChild;
    if (last) {
      var del = document.createElement("span");
      del.className = "lcsc-del";
      del.title = "Remove alternative";
      del.textContent = "✕";
      del.onmousedown = function (e) { e.stopPropagation(); };
      del.onclick = function (e) { e.stopPropagation(); removeAlternative(alt); };
      last.appendChild(del);
    }
    return tr;
  }

  function removeAlternative(alt) {
    altList = altList.filter(function (x) {
      return !(x.altLcsc === alt.altLcsc && x.targetLcsc === alt.targetLcsc);
    });
    if (ws && ws.readyState === 1) {
      ws.send(JSON.stringify({ type: "remove_alternative", altLcsc: alt.altLcsc, targetLcsc: alt.targetLcsc }));
    }
    try { decorate(); } catch (e) {}
    toast("Alternative " + alt.altLcsc + " removed", true);
  }

  function clearAltRows() {
    var rows = document.querySelectorAll("tr.lcsc-altrow");
    for (var i = 0; i < rows.length; i++) rows[i].parentNode.removeChild(rows[i]);
    var sup = document.querySelectorAll("tr.lcsc-superseded");
    for (var k = 0; k < sup.length; k++) sup[k].classList.remove("lcsc-superseded");
  }

  function applyAltRows() {
    clearAltRows();
    if (helperSettings.highlightAlt === false || !altList.length) return;
    altList.forEach(function (a) {
      var origTr = rowForFootprints(a.footprints);
      if (!origTr) return;
      origTr.classList.add("lcsc-superseded");
      var altTr = buildAltRow(origTr, a);
      origTr.parentNode.insertBefore(altTr, origTr.nextSibling);
    });
  }

  // ---- Alternative-Zeilen einfaerben -----------------------------------
  var DEFAULT_ALT_COLOR = "#3b82f6";

  function hexToRgba(hex, alpha) {
    hex = String(hex || DEFAULT_ALT_COLOR).replace("#", "");
    if (hex.length === 3) hex = hex.split("").map(function (c) { return c + c; }).join("");
    var r = parseInt(hex.slice(0, 2), 16), g = parseInt(hex.slice(2, 4), 16), b = parseInt(hex.slice(4, 6), 16);
    if (isNaN(r) || isNaN(g) || isNaN(b)) return "";
    return "rgba(" + r + "," + g + "," + b + "," + alpha + ")";
  }

  function altFootprintSet() {
    var s = {};
    altList.forEach(function (a) {
      (a.footprints || []).forEach(function (f) { s[String(f)] = true; });
    });
    return s;
  }

  function placedFootprintSet() {
    var s = {};
    try {
      (readStorage("checkbox_Placed") || "").split(",").forEach(function (f) { if (f) s[f] = true; });
    } catch (e) {}
    return s;
  }

  // Faerbungs-Regeln als eigenes <style>. Spezifitaet bewusst NIEDRIGER als
  // '.bom tr.highlighted' (via :not(.highlighted)), damit Hover/Klick-Gruen der
  // iBOM erhalten bleibt und nur der Ruhe-Hintergrund eingefaerbt wird.
  function updateRowStyles() {
    var st = document.getElementById("__lcsc_row_style");
    if (!st) {
      st = document.createElement("style");
      st.id = "__lcsc_row_style";
      (document.head || document.documentElement).appendChild(st);
    }
    var alt = hexToRgba(helperSettings.altColor, 0.16);
    var accent = helperSettings.altColor || DEFAULT_ALT_COLOR;
    st.textContent =
      // Ersatz-Zeile dezent in Alt-Farbe hinterlegt + linker Farbakzent
      ".dark .bom tr.lcsc-altrow:not(.highlighted),.bom tr.lcsc-altrow:not(.highlighted)" +
      "{background-color:" + alt + ";}" +
      ".bom tr.lcsc-altrow td:first-child{box-shadow:inset 4px 0 0 " + accent + ";}" +
      // Loesch-Icon (Hover-only)
      ".bom .lcsc-del{float:right;margin-left:8px;cursor:pointer;font-weight:700;color:#999;opacity:0;transition:opacity .15s;}" +
      ".bom tr.lcsc-altrow:hover .lcsc-del{opacity:.7;}" +
      ".bom .lcsc-del:hover{opacity:1;color:#f87171;}" +
      // ersetztes Original: Inhalt deutlich abgedunkelt/ausgegraut (nicht durchgestrichen)
      ".bom tr.lcsc-superseded td{opacity:.38;}" +
      // Placed: sehr dezent gruen
      ".dark .bom tr.lcsc-placed:not(.highlighted),.bom tr.lcsc-placed:not(.highlighted)" +
      "{background-color:rgba(34,197,94,0.10);}";
  }

  function decorate() {
    var bt = window.bomtable;
    if (!Array.isArray(bt)) return;
    updateRowStyles();
    var placedOn = helperSettings.highlightPlaced !== false;
    var placedFps = placedOn ? placedFootprintSet() : {};
    for (var i = 0; i < bt.length; i++) {
      var row = document.getElementById("bomrow" + (i + 1));
      if (!row) continue;
      var group = bt[i];
      var allPlaced = placedOn && group.length > 0;
      for (var j = 0; j < group.length; j++) {
        var pair = group[j];
        var fp = String(Array.isArray(pair) ? pair[1] : pair);
        if (placedOn && !placedFps[fp]) allPlaced = false;
      }
      row.classList.toggle("lcsc-placed", allPlaced);
    }
    applyAltRows();
  }

  // colorize nach jedem Tabellen-Rendern ausloesen
  function installColorizer() {
    if (typeof populateBomTable !== "function" || populateBomTable.__lcscWrapped) return;
    var orig = populateBomTable;
    window.populateBomTable = function () {
      var r = orig.apply(this, arguments);
      try { decorate(); } catch (e) {}
      return r;
    };
    window.populateBomTable.__lcscWrapped = true;
  }

  // ---- Hervorheben + Abhaken -------------------------------------------
  function highlightAndCheck(footprints, doSourced, doPlaced) {
    footprints = footprints || [];
    if (!footprints.length) return;

    var doScroll = helperSettings.scrollTo !== false;
    var seenRows = {};
    var firstScrolled = false;
    var usedFallback = false;

    for (var i = 0; i < footprints.length; i++) {
      var idx = footprints[i];
      var handler = (typeof footprintIndexToHandler !== "undefined") ? footprintIndexToHandler[idx] : null;

      if (handler) {
        try { handler(); } catch (e) { continue; }
        var rowid = (typeof currentHighlightedRowId !== "undefined") ? currentHighlightedRowId : null;
        if (!rowid || seenRows[rowid]) continue;
        seenRows[rowid] = true;
        if (!firstScrolled && doScroll) {
          try { smoothScrollToRow(rowid); } catch (e) {}
          firstScrolled = true;
        }
        if (doSourced) { try { checkBomCheckbox(rowid, "Sourced"); } catch (e) {} }
        if (doPlaced) { try { checkBomCheckbox(rowid, "Placed"); } catch (e) {} }
      } else if (doSourced || doPlaced) {
        storageCheck(idx, doSourced, doPlaced);
        usedFallback = true;
      }
    }
    if (usedFallback) { try { populateBomTable(); } catch (e) {} }
  }

  // ---- kleine Statusmeldung im Viewer ----------------------------------
  function toast(text, ok) {
    var el = document.getElementById("__lcsc_toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "__lcsc_toast";
      el.style.cssText =
        "position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:99999;" +
        "padding:10px 18px;border-radius:8px;font:600 14px/1.35 system-ui,sans-serif;" +
        "color:#fff;box-shadow:0 6px 20px rgba(0,0,0,.35);pointer-events:none;" +
        "transition:opacity .3s;max-width:90vw;text-align:center;";
      document.body.appendChild(el);
    }
    el.style.background = ok ? "#16a34a" : "#dc2626";
    el.textContent = text;
    el.style.opacity = "1";
    clearTimeout(el._t);
    el._t = setTimeout(function () { el.style.opacity = "0"; }, 2600);
  }

  function onAlternative(msg) {
    var a = msg.alt || {};
    altList = altList.filter(function (x) {
      return !(x.altLcsc === a.altLcsc && x.targetLcsc === a.targetLcsc);
    });
    altList.push(a);
    try { populateBomTable(); } catch (e) {}
    highlightAndCheck(a.footprints || [], !!msg.check_sourced, !!msg.check_placed);
    toast("✚ Alternative " + a.altLcsc + "  →  " + (a.refs || []).join(", ")
          + (a.altValue ? "  (" + a.altValue + ")" : ""), true);
    sendState();
    toParent({ type: "scan", found: true, lcsc: a.altLcsc, refs: a.refs || [], value: a.altValue });
  }

  function onScan(msg) {
    var result = msg.result;
    if (result && result.footprints && result.footprints.length) {
      // The server decides (per phase) what gets ticked.
      highlightAndCheck(result.footprints, !!msg.check_sourced, !!msg.check_placed);
      var refs = (result.refs || []).join(", ");
      toast("✓ " + msg.lcsc + "  →  " + refs + (result.value ? "  (" + result.value + ")" : ""), true);
      sendState();
      sendAck(msg.lcsc, true, result.refs || []);
      toParent({ type: "scan", found: true, lcsc: msg.lcsc, refs: result.refs || [], value: result.value });
    } else {
      toast("✗ " + (msg.lcsc || "?") + " — not in this BOM", false);
      sendAck(msg.lcsc, false, []);
      toParent({ type: "scan", found: false, lcsc: msg.lcsc });
    }
  }

  // ---- WebSocket --------------------------------------------------------
  function connect() {
    var proto = location.protocol === "https:" ? "wss:" : "ws:";
    var url = proto + "//" + location.host + "/ws/viewer/" + encodeURIComponent(helper.ibomId || "");
    try { ws = new WebSocket(url); } catch (e) { scheduleReconnect(); return; }

    ws.onopen = function () { log("verbunden"); sendState(); };
    ws.onmessage = function (ev) {
      var msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      if (msg.type === "scan") onScan(msg);
      else if (msg.type === "alternative" && msg.alt) onAlternative(msg);
      else if (msg.type === "settings" && msg.settings) {
        helperSettings = msg.settings;
        try { decorate(); } catch (e) {}
      }
      else if (msg.type === "presence") toParent({ type: "presence", viewers: msg.viewers, scanners: msg.scanners });
      else if (msg.type === "progress") toParent({
        type: "progress", phase: msg.phase,
        total: msg.total, sourced: msg.sourced, placed: msg.placed,
      });
    };
    ws.onclose = function () { toParent({ type: "ws", connected: false }); scheduleReconnect(); };
    ws.onerror = function () { try { ws.close(); } catch (e) {} };
  }

  function scheduleReconnect() {
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(connect, 2000);
  }

  // ---- Start ------------------------------------------------------------
  whenReady(function () {
    applyServerState();
    installColorizer();
    try { populateBomTable(); } catch (e) {}
    // EventHandler/IBOM_EVENT_TYPES sind 'const' -> NICHT auf window; ueber die
    // Scope-Chain referenzieren (typeof vermeidet ReferenceError).
    try {
      var EH = (typeof EventHandler !== "undefined") ? EventHandler : null;
      var ET = (typeof IBOM_EVENT_TYPES !== "undefined") ? IBOM_EVENT_TYPES : null;
      if (EH && ET && ET.CHECKBOX_CHANGE_EVENT) {
        EH.registerCallback(ET.CHECKBOX_CHANGE_EVENT, function () {
          sendState();
          try { decorate(); } catch (e) {}
        });
      }
    } catch (e) { log("EventHandler nicht verfuegbar", e); }
    connect();
  });
})();
