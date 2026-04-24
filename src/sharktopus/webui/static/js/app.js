// sharktopus — local UI glue. Intentionally small: htmx does the heavy
// lifting for progress polling and the Submit form is a plain <form>.

(function () {
  "use strict";

  var TS_RE = /^(\d{4})(\d{2})(\d{2})(\d{2})$/;
  var MONTHS = ["Jan","Feb","Mar","Apr","May","Jun",
                "Jul","Aug","Sep","Oct","Nov","Dec"];

  function autoOpenClones() {
    var params = new URLSearchParams(window.location.search);
    if (params.has("clone")) {
      document.querySelectorAll("details").forEach(function (d) {
        d.open = true;
      });
    }
  }

  function autoHideAlerts() {
    document.querySelectorAll(".alert.autohide").forEach(function (el) {
      setTimeout(function () {
        el.style.transition = "opacity .5s ease";
        el.style.opacity = "0";
        setTimeout(function () { el.remove(); }, 600);
      }, 6000);
    });
  }

  // ---- shared helpers -----------------------------------------------

  function pad2(n) { return n < 10 ? "0" + n : "" + n; }

  function daysInMonth(year, month) {
    return new Date(year, month, 0).getDate();
  }

  function parseMinDate(host) {
    var s = host && host.dataset.min ? host.dataset.min : "2015-01-15";
    var p = s.split("-");
    return { y: +p[0], m: +p[1], d: +p[2] };
  }

  function todayUTC() {
    var n = new Date();
    return { y: n.getUTCFullYear(), m: n.getUTCMonth() + 1, d: n.getUTCDate() };
  }

  function cmpYMD(a, b) {
    if (a.y !== b.y) return a.y - b.y;
    if (a.m !== b.m) return a.m - b.m;
    return a.d - b.d;
  }

  // ---- date+cycle picker with calendar/menus modes ------------------

  function populateMenus(picker, host) {
    var ySel = picker.querySelector(".dt-y");
    var mSel = picker.querySelector(".dt-m");
    var dSel = picker.querySelector(".dt-d");
    if (!ySel || !mSel || !dSel) return null;

    var min = parseMinDate(host);
    var today = todayUTC();

    // years: min.y .. current+1
    ySel.innerHTML = "";
    for (var y = min.y; y <= today.y + 1; y++) {
      ySel.insertAdjacentHTML("beforeend",
        '<option value="' + y + '">' + y + '</option>');
    }
    // months 1..12
    mSel.innerHTML = "";
    for (var mi = 1; mi <= 12; mi++) {
      mSel.insertAdjacentHTML("beforeend",
        '<option value="' + pad2(mi) + '">' + pad2(mi) + ' ' + MONTHS[mi - 1] + '</option>');
    }

    function refreshDays() {
      var yv = +ySel.value;
      var mv = +mSel.value;
      if (!yv || !mv) return;
      var max = daysInMonth(yv, mv);
      var prev = +dSel.value || 1;
      dSel.innerHTML = "";
      for (var d = 1; d <= max; d++) {
        dSel.insertAdjacentHTML("beforeend",
          '<option value="' + pad2(d) + '">' + pad2(d) + '</option>');
      }
      dSel.value = pad2(Math.min(prev, max));
    }
    ySel.addEventListener("change", refreshDays);
    mSel.addEventListener("change", refreshDays);
    refreshDays();

    return { ySel: ySel, mSel: mSel, dSel: dSel };
  }

  function wirePicker(picker, host, onChange) {
    var name = picker.dataset.target;
    var label = picker.closest("label");
    var hidden = label
      ? label.querySelector('input[type="hidden"][name="' + name + '"]')
      : null;

    var dateEl = picker.querySelector(".dt-date");
    var cycEl = picker.querySelector(".dt-cycle");
    var menus = populateMenus(picker, host);
    var min = parseMinDate(host);
    if (dateEl) dateEl.min = pad2(min.y) + "-" + pad2(min.m) + "-" + pad2(min.d);

    // hydrate from hidden YYYYMMDDHH
    var initial = hidden && hidden.value ? hidden.value : "";
    var m = TS_RE.exec(initial);
    if (m) {
      if (dateEl) dateEl.value = m[1] + "-" + m[2] + "-" + m[3];
      if (cycEl) cycEl.value = m[4];
      if (menus) {
        menus.ySel.value = String(+m[1]);
        menus.mSel.value = m[2];
        // days are dependent on y+m; trigger rebuild then set
        menus.ySel.dispatchEvent(new Event("change"));
        menus.dSel.value = m[3];
      }
    }

    function readCalendar() {
      if (!dateEl || !dateEl.value) return null;
      var parts = dateEl.value.split("-");
      if (parts.length !== 3) return null;
      return parts[0] + parts[1] + parts[2] + (cycEl ? cycEl.value : "00");
    }
    function readMenus() {
      if (!menus) return null;
      if (!menus.ySel.value || !menus.mSel.value || !menus.dSel.value) return null;
      var y = pad2(+menus.ySel.value);
      if (y.length < 4) y = menus.ySel.value;
      return y + menus.mSel.value + menus.dSel.value + (cycEl ? cycEl.value : "00");
    }

    function sync(source) {
      var val = null;
      if (host.classList.contains("dt-mode-menus")) {
        val = readMenus();
      } else {
        val = readCalendar();
      }
      // enforce floor
      if (val && val < (pad2(min.y) + pad2(min.m) + pad2(min.d) + "00")) {
        val = null;
      }
      if (hidden) hidden.value = val || "";
      // mirror calendar ↔ menus so the other mode stays in sync
      if (val) {
        if (source !== "cal" && dateEl) {
          dateEl.value = val.slice(0, 4) + "-" + val.slice(4, 6) + "-" + val.slice(6, 8);
        }
        if (source !== "menus" && menus) {
          menus.ySel.value = String(+val.slice(0, 4));
          menus.mSel.value = val.slice(4, 6);
          menus.ySel.dispatchEvent(new Event("change"));
          menus.dSel.value = val.slice(6, 8);
        }
      }
      if (onChange) onChange();
    }

    if (dateEl) dateEl.addEventListener("change", function () { sync("cal"); });
    if (cycEl) cycEl.addEventListener("change", function () { sync("cal"); });
    if (menus) {
      menus.ySel.addEventListener("change", function () { sync("menus"); });
      menus.mSel.addEventListener("change", function () { sync("menus"); });
      menus.dSel.addEventListener("change", function () { sync("menus"); });
    }

    return { read: function () { return hidden ? hidden.value : ""; },
             picker: picker, hidden: hidden };
  }

  function wireModeToggle(host) {
    host.querySelectorAll(".mode-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var mode = btn.dataset.mode;
        host.classList.remove("dt-mode-calendar", "dt-mode-menus");
        host.classList.add("dt-mode-" + mode);
        host.querySelectorAll(".mode-btn").forEach(function (b) {
          b.classList.toggle("is-active", b === btn);
        });
      });
    });
  }

  function wireRangeGuard(host, errEl, pickers) {
    var form = host.closest("form");
    if (!form) return;
    var startP = pickers.start;
    var endP = pickers.end;

    function check() {
      if (!startP || !endP) return true;
      var s = startP.hidden ? startP.hidden.value : "";
      var e = endP.hidden ? endP.hidden.value : "";
      if (!s || !e) {
        errEl.hidden = true;
        startP.picker.classList.remove("dt-invalid");
        endP.picker.classList.remove("dt-invalid");
        return true;
      }
      if (s >= e) {
        errEl.textContent = "Start must be earlier than end (got " + s + " ≥ " + e + ").";
        errEl.hidden = false;
        startP.picker.classList.add("dt-invalid");
        endP.picker.classList.add("dt-invalid");
        return false;
      }
      errEl.hidden = true;
      startP.picker.classList.remove("dt-invalid");
      endP.picker.classList.remove("dt-invalid");
      return true;
    }
    form.addEventListener("submit", function (ev) {
      // Only guard when range mode is selected
      var modeEl = form.querySelector('select[name="mode"]');
      if (modeEl && modeEl.value !== "range") return;
      if (!check()) { ev.preventDefault(); errEl.scrollIntoView({ behavior: "smooth", block: "center" }); }
    });
    return check;
  }

  // ---- chip-based timestamps list ------------------------------------

  function fmtHuman(stamp) {
    var m = TS_RE.exec(stamp);
    if (!m) return stamp;
    return m[1] + "-" + m[2] + "-" + m[3] + " " + m[4] + "Z";
  }

  function wireTimestampChips(host) {
    var container = document.getElementById("ts-chips");
    if (!container) return;
    var hidden = document.getElementById("ts-hidden");
    var dateEl = document.getElementById("ts-add-date");
    var cycEl = document.getElementById("ts-add-cycle");
    var addBtn = document.getElementById("ts-add-btn");
    var clearBtn = document.getElementById("ts-clear-btn");
    var min = parseMinDate(host);
    var floor = pad2(min.y) + pad2(min.m) + pad2(min.d) + "00";

    var stamps = [];
    var initial = (container.dataset.initial || hidden.value || "").split(/[,\s;]+/);
    initial.forEach(function (s) {
      s = s.trim();
      if (TS_RE.test(s) && s >= floor && stamps.indexOf(s) === -1) stamps.push(s);
    });
    stamps.sort();
    render();

    function render() {
      container.innerHTML = "";
      stamps.forEach(function (s) {
        var chip = document.createElement("span");
        chip.className = "ts-chip";
        chip.textContent = fmtHuman(s);
        var x = document.createElement("button");
        x.type = "button";
        x.setAttribute("aria-label", "remove " + s);
        x.textContent = "\u00d7";
        x.addEventListener("click", function () {
          stamps = stamps.filter(function (v) { return v !== s; });
          render();
        });
        chip.appendChild(x);
        container.appendChild(chip);
      });
      hidden.value = stamps.join(",");
    }

    function readStamp() {
      // prefer menus if in menus mode, else calendar
      if (host.classList.contains("dt-mode-menus")) {
        var adder = document.querySelector('.ts-adder[data-target="ts-add"]');
        var y = adder.querySelector(".dt-y").value;
        var mo = adder.querySelector(".dt-m").value;
        var d = adder.querySelector(".dt-d").value;
        if (!y || !mo || !d) return null;
        y = pad2(+y); if (y.length < 4) y = (+y).toString();
        return y + mo + d + cycEl.value;
      } else {
        if (!dateEl.value) return null;
        var parts = dateEl.value.split("-");
        if (parts.length !== 3) return null;
        return parts[0] + parts[1] + parts[2] + cycEl.value;
      }
    }

    addBtn.addEventListener("click", function () {
      var stamp = readStamp();
      if (!stamp || !TS_RE.test(stamp)) return;
      if (stamp < floor) return;
      if (stamps.indexOf(stamp) === -1) {
        stamps.push(stamp);
        stamps.sort();
        render();
      }
    });

    clearBtn.addEventListener("click", function () {
      if (stamps.length === 0) return;
      if (!window.confirm("Remove all " + stamps.length + " timestamps?")) return;
      stamps = [];
      render();
    });
  }

  // ---- bounding box: map + manual dual-mode --------------------------

  function wireBBoxHost(host) {
    if (!host) return;
    var form = host.closest("form");
    var mapEl = host.querySelector("#bbox-map");
    var missingEl = host.querySelector("#bbox-map-missing");
    var errEl = host.querySelector("#bbox-error");
    var convHidden = host.querySelector('input[name="lon_convention"]');

    var latS = form.querySelector('input[name="lat_s"]');
    var latN = form.querySelector('input[name="lat_n"]');
    var lonW = form.querySelector('input[name="lon_w"]');
    var lonE = form.querySelector('input[name="lon_e"]');

    function currentConv() {
      return host.classList.contains("bbox-lon-360") ? 360 : 180;
    }

    function clampLat(v) {
      if (isNaN(v)) return v;
      if (v > 90) return 90;
      if (v < -90) return -90;
      return v;
    }
    function clampLonForConv(v, conv) {
      if (isNaN(v)) return v;
      if (conv === 360) {
        if (v < 0) return 0;
        if (v > 360) return 360;
      } else {
        if (v < -180) return -180;
        if (v > 180) return 180;
      }
      return v;
    }
    // convert lon between conventions (for map ↔ input sync, map is always -180..180)
    function lonToMap(v, conv) {
      if (conv === 360 && v > 180) return v - 360;
      return v;
    }
    function lonFromMap(v, conv) {
      if (conv === 360 && v < 0) return v + 360;
      return v;
    }

    function applyConvLimits(conv) {
      if (conv === 360) {
        lonW.min = 0; lonW.max = 360;
        lonE.min = 0; lonE.max = 360;
      } else {
        lonW.min = -180; lonW.max = 180;
        lonE.min = -180; lonE.max = 180;
      }
    }

    function readInputs() {
      var s = parseFloat(latS.value);
      var n = parseFloat(latN.value);
      var w = parseFloat(lonW.value);
      var e = parseFloat(lonE.value);
      if ([s, n, w, e].some(isNaN)) return null;
      return { latS: s, latN: n, lonW: w, lonE: e };
    }

    function validate() {
      // clear prior
      [latS, latN, lonW, lonE].forEach(function (el) {
        el.classList.remove("bbox-invalid");
      });
      if (errEl) { errEl.hidden = true; errEl.textContent = ""; }

      var msgs = [];
      var s = parseFloat(latS.value);
      var n = parseFloat(latN.value);
      var w = parseFloat(lonW.value);
      var e = parseFloat(lonE.value);
      var conv = currentConv();

      if (!isNaN(s) && (s < -90 || s > 90)) {
        msgs.push("lat_s must be in [−90, 90]"); latS.classList.add("bbox-invalid");
      }
      if (!isNaN(n) && (n < -90 || n > 90)) {
        msgs.push("lat_n must be in [−90, 90]"); latN.classList.add("bbox-invalid");
      }
      if (!isNaN(s) && !isNaN(n) && s >= n) {
        msgs.push("lat_s must be south of lat_n");
        latS.classList.add("bbox-invalid"); latN.classList.add("bbox-invalid");
      }
      var lonMin = (conv === 360) ? 0 : -180;
      var lonMax = (conv === 360) ? 360 : 180;
      if (!isNaN(w) && (w < lonMin || w > lonMax)) {
        msgs.push("lon_w must be in [" + lonMin + ", " + lonMax + "]");
        lonW.classList.add("bbox-invalid");
      }
      if (!isNaN(e) && (e < lonMin || e > lonMax)) {
        msgs.push("lon_e must be in [" + lonMin + ", " + lonMax + "]");
        lonE.classList.add("bbox-invalid");
      }
      if (!isNaN(w) && !isNaN(e) && w >= e) {
        msgs.push("lon_w must be west of lon_e");
        lonW.classList.add("bbox-invalid"); lonE.classList.add("bbox-invalid");
      }
      if (msgs.length && errEl) {
        errEl.textContent = msgs.join("; ");
        errEl.hidden = false;
        return false;
      }
      return true;
    }

    // ---- wire mode and convention toggles ----------------------------
    function setMode(m) {
      host.classList.remove("bbox-mode-map", "bbox-mode-manual");
      host.classList.add("bbox-mode-" + m);
      host.querySelectorAll(".mode-btn[data-bmode]").forEach(function (b) {
        b.classList.toggle("is-active", b.dataset.bmode === m);
      });
      if (m === "map" && map) setTimeout(function () { map.invalidateSize(); }, 50);
    }
    host.querySelectorAll("[data-bmode]").forEach(function (btn) {
      btn.addEventListener("click", function () { setMode(btn.dataset.bmode); });
    });

    host.querySelectorAll(".mode-btn[data-conv]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var fromConv = currentConv();
        var toConv = +btn.dataset.conv;
        if (fromConv === toConv) return;
        // convert existing input values
        function convertLon(el) {
          if (!el.value) return;
          var v = parseFloat(el.value);
          if (isNaN(v)) return;
          if (toConv === 360 && v < 0) v = v + 360;
          else if (toConv === 180 && v > 180) v = v - 360;
          el.value = String(v);
        }
        convertLon(lonW);
        convertLon(lonE);
        host.classList.remove("bbox-lon-180", "bbox-lon-360");
        host.classList.add("bbox-lon-" + (toConv === 360 ? "360" : "180"));
        host.querySelectorAll(".mode-btn[data-conv]").forEach(function (b) {
          b.classList.toggle("is-active", b === btn);
        });
        if (convHidden) convHidden.value = (toConv === 360 ? "0..360" : "-180..180");
        applyConvLimits(toConv);
        updateRectFromInputs(false);
        updateReadout();
        validate();
      });
    });

    applyConvLimits(currentConv());

    // ---- input listeners --------------------------------------------
    [latS, latN, lonW, lonE].forEach(function (el) {
      el.addEventListener("input", function () {
        var conv = currentConv();
        if (el === latS || el === latN) {
          var v = parseFloat(el.value);
          if (!isNaN(v)) el.value = String(clampLat(v));
        } else {
          var v2 = parseFloat(el.value);
          if (!isNaN(v2)) el.value = String(clampLonForConv(v2, conv));
        }
        updateRectFromInputs(false);
        updateReadout();
        validate();
      });
    });

    if (form) {
      form.addEventListener("submit", function (ev) {
        if (!validate()) {
          ev.preventDefault();
          errEl.scrollIntoView({ behavior: "smooth", block: "center" });
        }
      });
    }

    // ---- map (Leaflet) ----------------------------------------------
    var map = null;
    var rect = null;
    var drawingActive = false;

    var drawBtn = host.querySelector("#bbox-draw");
    var clearBtn = host.querySelector("#bbox-clear");
    var fitBtn = host.querySelector("#bbox-fit");
    var readoutEl = host.querySelector("#bbox-readout");

    function haveLeaflet() { return typeof window.L !== "undefined"; }

    function updateReadout() {
      if (!readoutEl) return;
      var vals = readInputs();
      if (!vals) {
        readoutEl.classList.remove("has-value");
        readoutEl.innerHTML = "<em>No box yet — click Draw and drag on the map, or enter values in Manual.</em>";
        return;
      }
      readoutEl.classList.add("has-value");
      readoutEl.innerHTML =
        "<strong>S</strong>" + vals.latS.toFixed(2) +
        "  <strong>N</strong>" + vals.latN.toFixed(2) +
        "  <strong>W</strong>" + vals.lonW.toFixed(2) +
        "  <strong>E</strong>" + vals.lonE.toFixed(2);
    }

    function enterDrawMode() {
      if (!map) return;
      drawingActive = true;
      host.classList.add("bbox-drawing");
      map.dragging.disable();
      if (drawBtn) {
        drawBtn.classList.add("is-active");
        drawBtn.textContent = "✖ Cancel";
      }
    }
    function exitDrawMode() {
      drawingActive = false;
      host.classList.remove("bbox-drawing");
      if (map) map.dragging.enable();
      if (drawBtn) {
        drawBtn.classList.remove("is-active");
        drawBtn.textContent = "✎ Draw box";
      }
    }

    function clearBox() {
      if (rect && map) { map.removeLayer(rect); }
      rect = null;
      [latS, latN, lonW, lonE].forEach(function (el) { el.value = ""; });
      updateReadout();
      validate();
    }

    function fitToBox() {
      if (!map) return;
      // If there's no rect (e.g., after clear, or user typed then switched
      // mode before blur), try to (re)build from current inputs first.
      if (!rect) updateRectFromInputs(false);
      if (!rect) return;
      try {
        map.fitBounds(rect.getBounds(), { padding: [24, 24], maxZoom: 9 });
      } catch (_) {}
    }

    function initMap() {
      if (!haveLeaflet() || !mapEl) {
        if (missingEl) missingEl.hidden = false;
        if (mapEl) mapEl.style.display = "none";
        var hintEl = host.querySelector(".bbox-map-hint");
        if (hintEl) hintEl.style.display = "none";
        var tbEl = host.querySelector(".bbox-toolbar");
        if (tbEl) tbEl.style.display = "none";
        return;
      }
      map = L.map(mapEl, {
        worldCopyJump: true,
        doubleClickZoom: false,
      }).setView([-15, -55], 4);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 18, attribution: "© OpenStreetMap",
      }).addTo(map);

      // draw-mode handler: left-click-drag creates rectangle
      map.getContainer().addEventListener("mousedown", function (ev) {
        if (!drawingActive) return;
        if (ev.button !== 0) return;
        ev.preventDefault(); ev.stopPropagation();
        var startLL = map.mouseEventToLatLng(ev);
        var preview = null;

        function onMove(e2) {
          var ll = map.mouseEventToLatLng(e2);
          var b = L.latLngBounds(startLL, ll);
          if (!preview) {
            preview = L.rectangle(b, {
              color: "#0f3d5f", weight: 2, dashArray: "4 3", fillOpacity: 0.05,
            }).addTo(map);
          } else {
            preview.setBounds(b);
          }
        }
        function onUp() {
          document.removeEventListener("mousemove", onMove);
          document.removeEventListener("mouseup", onUp);
          if (!preview) { exitDrawMode(); return; }
          var b = preview.getBounds();
          map.removeLayer(preview); preview = null;
          // reject zero-size boxes (simple click)
          if (b.getSouth() === b.getNorth() || b.getWest() === b.getEast()) {
            exitDrawMode();
            return;
          }
          setRectFromBounds(b);
          syncInputsFromRect();
          updateReadout();
          validate();
          exitDrawMode();
        }
        document.addEventListener("mousemove", onMove);
        document.addEventListener("mouseup", onUp);
      }, true);

      if (drawBtn) drawBtn.addEventListener("click", function () {
        if (drawingActive) exitDrawMode(); else enterDrawMode();
      });
      if (clearBtn) clearBtn.addEventListener("click", clearBox);
      if (fitBtn) fitBtn.addEventListener("click", fitToBox);

      // hydrate from any initial values
      updateRectFromInputs(true);
      updateReadout();
    }

    function setRectFromBounds(b) {
      if (rect) { map.removeLayer(rect); rect = null; }
      rect = L.rectangle(b, {
        color: "#0f3d5f", weight: 2, fillOpacity: 0.10, fillColor: "#f47c53",
      }).addTo(map);
    }

    function syncInputsFromRect() {
      if (!rect) return;
      var b = rect.getBounds();
      var conv = currentConv();
      latS.value = String(+b.getSouth().toFixed(4));
      latN.value = String(+b.getNorth().toFixed(4));
      lonW.value = String(+lonFromMap(b.getWest(), conv).toFixed(4));
      lonE.value = String(+lonFromMap(b.getEast(), conv).toFixed(4));
    }

    function updateRectFromInputs(autoFit) {
      if (!map) return;
      var vals = readInputs();
      if (!vals) {
        if (rect) { map.removeLayer(rect); rect = null; }
        return;
      }
      var conv = currentConv();
      var w = lonToMap(vals.lonW, conv);
      var e = lonToMap(vals.lonE, conv);
      var bounds = L.latLngBounds([vals.latS, w], [vals.latN, e]);
      setRectFromBounds(bounds);
      if (autoFit) {
        try { map.fitBounds(bounds, { padding: [24, 24], maxZoom: 9 }); } catch (_) {}
      }
    }

    // By the time DOMContentLoaded fires, any deferred scripts
    // (including Leaflet, if bundled) have already executed — so a
    // synchronous check is reliable. No Leaflet means Manual mode only.
    if (haveLeaflet()) {
      initMap();
    } else {
      if (missingEl) missingEl.hidden = false;
      if (mapEl) mapEl.style.display = "none";
      var hintEl = host.querySelector(".bbox-map-hint");
      if (hintEl) hintEl.style.display = "none";
    }

    // Coverage overlay — the current product's valid geographic range.
    // Product registry passes (lat_s, lat_n, lon_w, lon_e) via
    //   host._setCoverage([latS, latN, lonW, lonE])  to clamp. Null and
    // world-extent (-90..90, -180..180) both mean "global" — no clamp,
    // no rectangle drawn.
    var coverageLayer = null;
    host._setCoverage = function (bbox) {
      if (!map) return;
      if (coverageLayer) { map.removeLayer(coverageLayer); coverageLayer = null; }
      if (!bbox || isGlobalCoverage(bbox)) {
        try { map.setMaxBounds(null); } catch (_) {}
        return;
      }
      var s = +bbox[0], n = +bbox[1], w = +bbox[2], e = +bbox[3];
      if ([s, n, w, e].some(isNaN)) return;
      var bounds = L.latLngBounds([s, w], [n, e]);
      coverageLayer = L.rectangle(bounds, {
        color: "#45b4ae", weight: 2, dashArray: "5 4",
        fillOpacity: 0.04, fillColor: "#45b4ae",
        interactive: false,
      }).addTo(map);
      try {
        map.setMaxBounds(bounds.pad(0.25));
        map.fitBounds(bounds, { padding: [24, 24], maxZoom: 7 });
      } catch (_) {}
    };
  }

  function isGlobalCoverage(bbox) {
    if (!bbox || bbox.length !== 4) return false;
    var s = +bbox[0], n = +bbox[1], w = +bbox[2], e = +bbox[3];
    return Math.abs(s + 90) < 0.01 && Math.abs(n - 90) < 0.01
        && Math.abs(w + 180) < 0.01 && Math.abs(e - 180) < 0.01;
  }

  // ---- wire everything up --------------------------------------------

  function wireDateHost() {
    var host = document.querySelector(".dt-host");
    if (!host) return;
    wireModeToggle(host);

    var errEl = document.getElementById("dt-range-error");
    var pickers = {};
    host.querySelectorAll(".dt-picker[data-target]").forEach(function (p) {
      var target = p.dataset.target;
      var res = wirePicker(p, host, function () { if (errEl) check(); });
      pickers[target] = res;
    });
    var check = null;
    if (errEl) {
      check = wireRangeGuard(host, errEl, pickers);
    }
    wireTimestampChips(host);

    host._setDateWindow = function (opts) {
      if (!opts || !opts.earliest) return;
      host.dataset.min = opts.earliest;
      if (opts.earliestYear) host.dataset.minYear = String(opts.earliestYear);
      var parts = opts.earliest.split("-");
      if (parts.length !== 3) return;
      var minIso = pad2(+parts[0]) + "-" + pad2(+parts[1]) + "-" + pad2(+parts[2]);
      if (minIso.length < 10) minIso = parts[0] + "-" + parts[1] + "-" + parts[2];
      host.querySelectorAll(".dt-date").forEach(function (d) { d.min = minIso; });
      var today = todayUTC();
      host.querySelectorAll(".dt-y").forEach(function (ySel) {
        var prev = ySel.value;
        ySel.innerHTML = "";
        for (var y = +parts[0]; y <= today.y + 1; y++) {
          ySel.insertAdjacentHTML("beforeend",
            '<option value="' + y + '">' + y + '</option>');
        }
        if (prev && +prev >= +parts[0] && +prev <= today.y + 1) {
          ySel.value = prev;
        }
      });
      var note = host.querySelector(".dt-floor-note");
      if (note) {
        var model = opts.model || "Model";
        note.innerHTML = model + " cycles from <strong>" + minIso +
          " 00Z</strong> (earliest supported).";
      }
    };
  }

  // ------------------------------------------------------------ var/level cascade

  function wireVarLevelHost(host) {
    if (!host) return;
    var varListEl   = host.querySelector("#vl-var-list");
    var lvlListEl   = host.querySelector("#vl-lvl-list");
    var lvlHintEl   = host.querySelector("#vl-lvl-hint");
    var lvlSearchEl = host.querySelector("#vl-lvl-search");
    var varSearchEl = host.querySelector("#vl-var-search");
    var varCountEl  = host.querySelector("#vl-var-count");
    var lvlCountEl  = host.querySelector("#vl-lvl-count");
    var varChipsEl  = host.querySelector("#vl-var-chips");
    var lvlChipsEl  = host.querySelector("#vl-lvl-chips");
    var varHidden   = host.querySelector("#vl-variables");
    var lvlHidden   = host.querySelector("#vl-levels");
    var presetSel   = host.querySelector("#vl-preset-select");
    var presetApply = host.querySelector("#vl-preset-apply");
    var presetDel   = host.querySelector("#vl-preset-delete");
    var saveName    = host.querySelector("#vl-save-name");
    var saveDesc    = host.querySelector("#vl-save-desc");
    var saveBtn     = host.querySelector("#vl-save-btn");
    var feedbackEl  = host.querySelector("#vl-feedback");

    var state = {
      catalog: null,
      selectedVars: new Set(),
      selectedLevels: new Set(),
      presets: [],
    };

    function parseCsv(value) {
      if (!value) return [];
      return value.split(",").map(function (s) { return s.trim(); }).filter(Boolean);
    }

    (parseCsv(varHidden.value)).forEach(function (v) { state.selectedVars.add(v); });
    (parseCsv(lvlHidden.value)).forEach(function (lv) { state.selectedLevels.add(lv); });

    function setFeedback(msg, isError) {
      if (!feedbackEl) return;
      if (!msg) {
        feedbackEl.hidden = true;
        feedbackEl.textContent = "";
        feedbackEl.classList.remove("is-error", "is-ok");
        return;
      }
      feedbackEl.hidden = false;
      feedbackEl.textContent = msg;
      feedbackEl.classList.toggle("is-error", !!isError);
      feedbackEl.classList.toggle("is-ok", !isError);
    }

    function levelsValidForSelectedVars() {
      if (!state.catalog) return new Set();
      var out = new Set();
      if (state.selectedVars.size === 0) {
        return out;
      }
      state.catalog.variables.forEach(function (v) {
        if (!state.selectedVars.has(v.name)) return;
        (v.levels || []).forEach(function (lv) { out.add(lv); });
      });
      return out;
    }

    function syncHidden() {
      varHidden.value = Array.from(state.selectedVars).join(",");
      lvlHidden.value = Array.from(state.selectedLevels).join(",");
    }

    function renderCounts() {
      varCountEl.textContent = state.selectedVars.size + " selected";
      lvlCountEl.textContent = state.selectedLevels.size + " selected";
    }

    function renderChips(container, set, removeCb) {
      container.innerHTML = "";
      var values = Array.from(set).sort();
      values.forEach(function (val) {
        var chip = document.createElement("span");
        chip.className = "vl-chip";
        chip.textContent = val;
        var rm = document.createElement("button");
        rm.type = "button";
        rm.innerHTML = "&times;";
        rm.setAttribute("aria-label", "remove " + val);
        rm.addEventListener("click", function () { removeCb(val); });
        chip.appendChild(rm);
        container.appendChild(chip);
      });
    }

    function renderVarList() {
      if (!state.catalog) return;
      var filter = (varSearchEl.value || "").trim().toLowerCase();
      var groups = {};
      state.catalog.variables.forEach(function (v) {
        if (filter) {
          var hay = (v.name + " " + (v.desc || "")).toLowerCase();
          if (hay.indexOf(filter) === -1) return;
        }
        var cat = v.category || "Other";
        if (!groups[cat]) groups[cat] = [];
        groups[cat].push(v);
      });
      var catOrder = state.catalog.variables.map(function (v) { return v.category || "Other"; });
      var seenCats = {};
      varListEl.innerHTML = "";
      catOrder.forEach(function (c) {
        if (seenCats[c] || !groups[c]) return;
        seenCats[c] = true;
        var block = document.createElement("div");
        block.className = "vl-group";
        var title = document.createElement("div");
        title.className = "vl-group-title";
        title.textContent = c;
        block.appendChild(title);
        groups[c].forEach(function (v) {
          var label = document.createElement("label");
          label.className = "vl-option";
          var cb = document.createElement("input");
          cb.type = "checkbox";
          cb.checked = state.selectedVars.has(v.name);
          cb.addEventListener("change", function () {
            if (cb.checked) state.selectedVars.add(v.name);
            else state.selectedVars.delete(v.name);
            pruneInvalidLevels();
            onSelectionChanged();
          });
          var name = document.createElement("span");
          name.className = "vl-name";
          name.textContent = v.name;
          var desc = document.createElement("span");
          desc.className = "vl-desc";
          var unit = v.unit ? " [" + v.unit + "]" : "";
          desc.textContent = (v.desc || "") + unit;
          label.appendChild(cb);
          label.appendChild(name);
          label.appendChild(desc);
          block.appendChild(label);
        });
        varListEl.appendChild(block);
      });
      if (!varListEl.children.length) {
        var empty = document.createElement("p");
        empty.className = "vl-lvl-hint";
        empty.textContent = "No variables match \"" + filter + "\".";
        varListEl.appendChild(empty);
      }
    }

    function renderLevelList() {
      if (!state.catalog) return;
      var valid = levelsValidForSelectedVars();
      if (state.selectedVars.size === 0) {
        lvlListEl.innerHTML = "";
        lvlHintEl.hidden = false;
        lvlSearchEl.hidden = true;
        return;
      }
      lvlHintEl.hidden = true;
      lvlSearchEl.hidden = false;
      var filter = (lvlSearchEl.value || "").trim().toLowerCase();
      lvlListEl.innerHTML = "";
      (state.catalog.level_groups || []).forEach(function (g) {
        var members = (g.levels || []).filter(function (lv) {
          if (!valid.has(lv)) return false;
          if (filter && lv.toLowerCase().indexOf(filter) === -1) return false;
          return true;
        });
        if (!members.length) return;
        var block = document.createElement("div");
        block.className = "vl-group";
        var title = document.createElement("div");
        title.className = "vl-group-title";
        title.textContent = g.name;
        block.appendChild(title);
        members.forEach(function (lv) {
          var label = document.createElement("label");
          label.className = "vl-option";
          var cb = document.createElement("input");
          cb.type = "checkbox";
          cb.checked = state.selectedLevels.has(lv);
          cb.addEventListener("change", function () {
            if (cb.checked) state.selectedLevels.add(lv);
            else state.selectedLevels.delete(lv);
            onSelectionChanged();
          });
          var name = document.createElement("span");
          name.className = "vl-name";
          name.textContent = lv;
          label.appendChild(cb);
          label.appendChild(name);
          block.appendChild(label);
        });
        lvlListEl.appendChild(block);
      });
      var grouped = new Set();
      (state.catalog.level_groups || []).forEach(function (g) {
        (g.levels || []).forEach(function (lv) { grouped.add(lv); });
      });
      var orphans = [];
      valid.forEach(function (lv) {
        if (!grouped.has(lv)) {
          if (!filter || lv.toLowerCase().indexOf(filter) !== -1) orphans.push(lv);
        }
      });
      if (orphans.length) {
        var block = document.createElement("div");
        block.className = "vl-group";
        var title = document.createElement("div");
        title.className = "vl-group-title";
        title.textContent = "Other";
        block.appendChild(title);
        orphans.sort().forEach(function (lv) {
          var label = document.createElement("label");
          label.className = "vl-option";
          var cb = document.createElement("input");
          cb.type = "checkbox";
          cb.checked = state.selectedLevels.has(lv);
          cb.addEventListener("change", function () {
            if (cb.checked) state.selectedLevels.add(lv);
            else state.selectedLevels.delete(lv);
            onSelectionChanged();
          });
          var name = document.createElement("span");
          name.className = "vl-name";
          name.textContent = lv;
          label.appendChild(cb);
          label.appendChild(name);
          block.appendChild(label);
        });
        lvlListEl.appendChild(block);
      }
      if (!lvlListEl.children.length) {
        var empty = document.createElement("p");
        empty.className = "vl-lvl-hint";
        empty.textContent = filter
          ? "No valid levels match \"" + filter + "\"."
          : "Selected variables have no shared levels.";
        lvlListEl.appendChild(empty);
      }
    }

    function pruneInvalidLevels() {
      var valid = levelsValidForSelectedVars();
      var toDrop = [];
      state.selectedLevels.forEach(function (lv) {
        if (!valid.has(lv)) toDrop.push(lv);
      });
      toDrop.forEach(function (lv) { state.selectedLevels.delete(lv); });
    }

    function onSelectionChanged() {
      syncHidden();
      renderCounts();
      renderChips(varChipsEl, state.selectedVars, function (val) {
        state.selectedVars.delete(val);
        pruneInvalidLevels();
        onSelectionChanged();
        renderVarList();
        renderLevelList();
      });
      renderChips(lvlChipsEl, state.selectedLevels, function (val) {
        state.selectedLevels.delete(val);
        onSelectionChanged();
        renderLevelList();
      });
      renderLevelList();
    }

    function renderPresetOptions() {
      presetSel.innerHTML = "";
      var blank = document.createElement("option");
      blank.value = "";
      blank.textContent = "— choose a preset —";
      presetSel.appendChild(blank);
      var defOpt = document.createElement("option");
      defOpt.value = "__defaults__";
      defOpt.textContent = "WRF defaults (13 vars × 49 levels)";
      presetSel.appendChild(defOpt);
      state.presets.forEach(function (p) {
        var opt = document.createElement("option");
        opt.value = String(p.id);
        opt.textContent = p.name + " (" + p.variables.length + "v × " + p.levels.length + "l)";
        presetSel.appendChild(opt);
      });
    }

    function applyPreset(variables, levels) {
      state.selectedVars = new Set(variables);
      state.selectedLevels = new Set(levels);
      pruneInvalidLevels();
      onSelectionChanged();
      renderVarList();
    }

    function wrfDefaults() {
      if (!state.catalog) return { variables: [], levels: [] };
      var wrfVars = ["HGT","LAND","MSLET","PRES","PRMSL","RH","SOILL","SOILW","SPFH","TMP","TSOIL","UGRD","VGRD"];
      var wrfLevels = [
        "0-0.1 m below ground","0.1-0.4 m below ground","0.4-1 m below ground","1-2 m below ground",
        "0.01 mb","0.02 mb","0.04 mb","0.07 mb","0.1 mb","0.2 mb","0.4 mb","0.7 mb",
        "1 mb","2 mb","3 mb","5 mb","7 mb","10 mb","15 mb","20 mb","30 mb","40 mb","50 mb",
        "70 mb","100 mb","150 mb","200 mb","250 mb","300 mb","350 mb","400 mb","450 mb",
        "500 mb","550 mb","600 mb","650 mb","700 mb","750 mb","800 mb","850 mb","900 mb",
        "925 mb","950 mb","975 mb","1000 mb",
        "2 m above ground","10 m above ground","mean sea level","surface",
      ];
      return { variables: wrfVars, levels: wrfLevels };
    }

    presetApply.addEventListener("click", function () {
      var val = presetSel.value;
      if (!val) return;
      if (val === "__defaults__") {
        var d = wrfDefaults();
        applyPreset(d.variables, d.levels);
        setFeedback("Loaded WRF defaults.", false);
        return;
      }
      var p = state.presets.find(function (x) { return String(x.id) === val; });
      if (!p) return;
      applyPreset(p.variables, p.levels);
      setFeedback("Loaded preset \"" + p.name + "\".", false);
    });

    presetDel.addEventListener("click", function () {
      var val = presetSel.value;
      if (!val || val === "__defaults__") {
        setFeedback("Pick a user preset to delete.", true);
        return;
      }
      var p = state.presets.find(function (x) { return String(x.id) === val; });
      if (!p) return;
      if (!window.confirm("Delete preset \"" + p.name + "\"?")) return;
      fetch("/api/presets/" + p.id, { method: "DELETE" })
        .then(function (r) { return r.ok ? r.json() : r.json().then(function (e) { throw e; }); })
        .then(function () {
          state.presets = state.presets.filter(function (x) { return x.id !== p.id; });
          renderPresetOptions();
          setFeedback("Deleted preset \"" + p.name + "\".", false);
        })
        .catch(function (e) { setFeedback(e.error || "Delete failed.", true); });
    });

    saveBtn.addEventListener("click", function () {
      var name = (saveName.value || "").trim();
      if (!name) { setFeedback("Preset needs a name.", true); return; }
      if (!state.selectedVars.size || !state.selectedLevels.size) {
        setFeedback("Pick at least one variable and one level first.", true);
        return;
      }
      var body = JSON.stringify({
        name: name,
        description: (saveDesc.value || "").trim(),
        variables: Array.from(state.selectedVars),
        levels: Array.from(state.selectedLevels),
      });
      fetch("/api/presets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: body,
      })
        .then(function (r) { return r.ok ? r.json() : r.json().then(function (e) { throw e; }); })
        .then(function (saved) {
          var idx = state.presets.findIndex(function (x) { return x.id === saved.id; });
          if (idx >= 0) state.presets[idx] = saved;
          else state.presets.push(saved);
          state.presets.sort(function (a, b) { return a.name.localeCompare(b.name); });
          renderPresetOptions();
          presetSel.value = String(saved.id);
          saveName.value = "";
          saveDesc.value = "";
          setFeedback("Saved preset \"" + saved.name + "\".", false);
        })
        .catch(function (e) { setFeedback(e.error || "Save failed.", true); });
    });

    varSearchEl.addEventListener("input", renderVarList);
    lvlSearchEl.addEventListener("input", renderLevelList);

    function loadCatalog(productId) {
      var url = productId
        ? "/api/catalog?product=" + encodeURIComponent(productId)
        : "/api/catalog";
      return fetch(url).then(function (r) { return r.json(); }).then(function (cat) {
        state.catalog = cat;
        renderVarList();
        // Keep only selections that still exist in the new catalog.
        pruneInvalidLevels();
        var validVars = new Set((cat.variables || []).map(function (v) { return v.name; }));
        state.selectedVars.forEach(function (name) {
          if (!validVars.has(name)) state.selectedVars.delete(name);
        });
        onSelectionChanged();
      });
    }

    // Reload-on-demand hook used by the product <select>.
    host._reloadCatalog = loadCatalog;

    Promise.all([
      loadCatalog(host.dataset.productId || null),
      fetch("/api/presets").then(function (r) { return r.json(); }),
    ]).then(function (results) {
      state.presets = Array.isArray(results[1]) ? results[1] : [];
      renderPresetOptions();
    }).catch(function () {
      varListEl.innerHTML = "<p class=\"vl-lvl-hint\">Catalog failed to load — submit with empty fields to use WRF defaults.</p>";
    });
  }

  // ------------------------------------------------------------ product picker

  function wireProductSelect() {
    var sel = document.getElementById("product-select");
    var descEl = document.getElementById("product-desc");
    var covEl = document.getElementById("product-coverage");
    var vlHost = document.querySelector(".vl-host");
    var bboxHost = document.querySelector(".bbox-host");
    var dtHost = document.querySelector(".dt-host");
    var srcHost = document.querySelector(".sources-host");
    if (!sel) return;

    function parseAllowedSources(opt) {
      if (!opt) return null;
      var raw = opt.dataset.allowedSources || "";
      if (!raw) return null;
      try {
        var a = JSON.parse(raw);
        if (Array.isArray(a)) return a;
      } catch (_) {}
      return null;
    }

    function parseCoverage(opt) {
      if (!opt) return null;
      var raw = opt.dataset.coverageBbox || "";
      if (!raw) return null;
      try {
        var a = JSON.parse(raw);
        if (Array.isArray(a) && a.length === 4) return a.map(Number);
      } catch (_) {}
      return null;
    }

    function syncDesc() {
      var opt = sel.options[sel.selectedIndex];
      if (descEl && opt) descEl.textContent = opt.dataset.description || "";
      if (covEl) {
        var cov = parseCoverage(opt);
        if (cov && !isGlobalCoverage(cov)) {
          covEl.textContent =
            "Coverage: S " + cov[0].toFixed(1) + "  N " + cov[1].toFixed(1) +
            "  W " + cov[2].toFixed(1) + "  E " + cov[3].toFixed(1) +
            " — map clamped to this area.";
          covEl.hidden = false;
        } else {
          covEl.textContent = "Coverage: global — any bounding box is valid.";
          covEl.hidden = false;
        }
      }
    }
    syncDesc();

    function applyCoverage() {
      if (!bboxHost || typeof bboxHost._setCoverage !== "function") return;
      var opt = sel.options[sel.selectedIndex];
      bboxHost._setCoverage(parseCoverage(opt));
    }

    function applyDateWindow() {
      if (!dtHost || typeof dtHost._setDateWindow !== "function") return;
      var opt = sel.options[sel.selectedIndex];
      if (!opt || !opt.dataset.earliest) return;
      dtHost._setDateWindow({
        earliest: opt.dataset.earliest,
        earliestYear: opt.dataset.earliestYear,
        model: opt.dataset.model || "",
      });
    }

    function applySources() {
      if (!srcHost || typeof srcHost._setAllowedSources !== "function") return;
      var opt = sel.options[sel.selectedIndex];
      srcHost._setAllowedSources(parseAllowedSources(opt));
    }

    sel.addEventListener("change", function () {
      syncDesc();
      applyCoverage();
      applyDateWindow();
      applySources();
      var opt = sel.options[sel.selectedIndex];
      if (!opt || !vlHost || typeof vlHost._reloadCatalog !== "function") return;
      vlHost.dataset.productId = opt.dataset.productId || "";
      vlHost._reloadCatalog(opt.dataset.productId || null);
    });

    // Seed the vl-host with the initially selected product_id so its
    // first catalog fetch hits the right endpoint.
    if (vlHost) {
      var opt0 = sel.options[sel.selectedIndex];
      if (opt0) vlHost.dataset.productId = opt0.dataset.productId || "";
    }

    // wireBBoxHost, wireDateHost, and wireSourcesHost all run before us
    // in DOMContentLoaded, so their _setCoverage / _setDateWindow /
    // _setAllowedSources hooks are already attached.
    applyCoverage();
    applyDateWindow();
    applySources();
  }

  // ------------------------------------------------------------ sources chips

  function wireSourcesHost(host) {
    if (!host) return;
    var enabledEl = host.querySelector("#sources-enabled");
    var poolEl    = host.querySelector("#sources-pool");
    var hiddenEl  = host.querySelector("#sources-priority");
    var enCountEl = host.querySelector("#sources-enabled-count");
    var poolCountEl = host.querySelector("#sources-pool-count");
    var catalogEl = host.querySelector("#sources-catalog");
    if (!enabledEl || !poolEl || !hiddenEl || !catalogEl) return;

    var catalog;
    try {
      catalog = JSON.parse(catalogEl.textContent);
    } catch (e) {
      catalog = [];
    }
    var metaByName = {};
    catalog.forEach(function (s) { metaByName[s.name] = s; });

    var initial = (host.dataset.initial || hiddenEl.value || "").trim();
    var enabled = initial
      ? initial.split(/[\s,]+/).filter(function (n) { return n && metaByName[n]; })
      : catalog.map(function (s) { return s.name; });
    var seen = {};
    enabled = enabled.filter(function (n) {
      if (seen[n]) return false;
      seen[n] = true;
      return true;
    });

    // Allowlist state — null means "all registered sources are valid for
    // this product". Set by the product picker on change.
    var allowed = null;
    function visibleNames() {
      var all = catalog.map(function (s) { return s.name; });
      if (!allowed) return all;
      return all.filter(function (n) { return allowed.indexOf(n) !== -1; });
    }

    function chip(name, isEnabled, order) {
      var meta = metaByName[name] || { name: name, label: name, hint: "" };
      var el = document.createElement("span");
      el.className = "src-chip";
      el.dataset.name = name;
      el.setAttribute("role", "listitem");
      if (isEnabled) {
        el.draggable = true;
        el.title = "Drag to reorder, click × to disable";
      } else {
        el.title = "Click to enable";
      }
      el.innerHTML =
        (isEnabled ? '<span class="src-handle" aria-hidden="true">⋮⋮</span>' : "") +
        (isEnabled ? '<span class="src-order">' + order + '</span>' : "") +
        '<span class="src-label"></span>' +
        '<span class="src-hint"></span>' +
        (isEnabled ? '<button type="button" class="src-remove" aria-label="disable">×</button>' : "");
      el.querySelector(".src-label").textContent = meta.label;
      el.querySelector(".src-hint").textContent = meta.hint ? "— " + meta.hint : "";
      return el;
    }

    function render() {
      enabledEl.innerHTML = "";
      poolEl.innerHTML = "";
      var visible = visibleNames();
      enabled = enabled.filter(function (n) { return visible.indexOf(n) !== -1; });
      enabled.forEach(function (name, idx) {
        enabledEl.appendChild(chip(name, true, idx + 1));
      });
      var inPool = visible.filter(function (n) { return enabled.indexOf(n) === -1; });
      inPool.forEach(function (name) {
        poolEl.appendChild(chip(name, false, 0));
      });
      enCountEl.textContent = enabled.length + (enabled.length === 1 ? " enabled" : " enabled");
      poolCountEl.textContent = inPool.length + (inPool.length === 1 ? " disabled" : " disabled");
      hiddenEl.value = enabled.join(",");
      wireChipEvents();
    }

    host._setAllowedSources = function (names) {
      if (!names || !names.length) {
        allowed = null;
      } else {
        allowed = names.slice();
      }
      render();
    };

    function removeFromEnabled(name) {
      enabled = enabled.filter(function (n) { return n !== name; });
      render();
    }

    function addToEnabled(name) {
      if (enabled.indexOf(name) === -1) enabled.push(name);
      render();
    }

    function moveEnabled(srcName, targetName, placeAfter) {
      var src = enabled.indexOf(srcName);
      if (src < 0) return;
      enabled.splice(src, 1);
      if (!targetName) { enabled.push(srcName); render(); return; }
      var tgt = enabled.indexOf(targetName);
      if (tgt < 0) { enabled.push(srcName); render(); return; }
      enabled.splice(placeAfter ? tgt + 1 : tgt, 0, srcName);
      render();
    }

    var dragName = null;

    function wireChipEvents() {
      Array.prototype.forEach.call(enabledEl.querySelectorAll(".src-chip"), function (el) {
        var name = el.dataset.name;
        var btn = el.querySelector(".src-remove");
        if (btn) btn.addEventListener("click", function (e) {
          e.stopPropagation();
          removeFromEnabled(name);
        });
        el.addEventListener("dragstart", function (e) {
          dragName = name;
          el.classList.add("is-dragging");
          if (e.dataTransfer) {
            e.dataTransfer.effectAllowed = "move";
            try { e.dataTransfer.setData("text/plain", name); } catch (_) {}
          }
        });
        el.addEventListener("dragend", function () {
          el.classList.remove("is-dragging");
          Array.prototype.forEach.call(enabledEl.querySelectorAll(".is-drag-over"), function (c) {
            c.classList.remove("is-drag-over");
          });
          enabledEl.classList.remove("is-drop-target");
          dragName = null;
        });
        el.addEventListener("dragover", function (e) {
          if (!dragName || dragName === name) return;
          e.preventDefault();
          el.classList.add("is-drag-over");
          if (e.dataTransfer) e.dataTransfer.dropEffect = "move";
        });
        el.addEventListener("dragleave", function () {
          el.classList.remove("is-drag-over");
        });
        el.addEventListener("drop", function (e) {
          e.preventDefault();
          e.stopPropagation();
          el.classList.remove("is-drag-over");
          if (!dragName || dragName === name) return;
          var rect = el.getBoundingClientRect();
          var placeAfter = (e.clientX - rect.left) > rect.width / 2;
          moveEnabled(dragName, name, placeAfter);
        });
      });

      Array.prototype.forEach.call(poolEl.querySelectorAll(".src-chip"), function (el) {
        var name = el.dataset.name;
        el.addEventListener("click", function () { addToEnabled(name); });
      });
    }

    enabledEl.addEventListener("dragover", function (e) {
      if (!dragName) return;
      e.preventDefault();
      enabledEl.classList.add("is-drop-target");
    });
    enabledEl.addEventListener("dragleave", function (e) {
      if (e.target === enabledEl) enabledEl.classList.remove("is-drop-target");
    });
    enabledEl.addEventListener("drop", function (e) {
      e.preventDefault();
      enabledEl.classList.remove("is-drop-target");
      if (!dragName) return;
      if (!e.target.closest(".src-chip")) {
        moveEnabled(dragName, null, true);
      }
    });

    render();
  }

  // ------------------------------------------------------------ directory picker
  //
  // The modal shell lives in submit.html; the body is fetched from
  // /api/fs/browse as an HTMX fragment. This wiring opens/closes the
  // modal and writes the chosen path back to the correct form input.

  function wireFsPicker() {
    var modal = document.getElementById("fs-modal");
    if (!modal) return;
    var body = modal.querySelector("#fs-browser-body");
    var currentTarget = null;

    function open(targetName, seed) {
      currentTarget = targetName;
      modal.hidden = false;
      modal.setAttribute("aria-hidden", "false");
      var path = seed || "";
      var url = "/api/fs/browse?path=" + encodeURIComponent(path) +
                "&target=" + encodeURIComponent(targetName);
      if (window.htmx) {
        window.htmx.ajax("GET", url, { target: "#fs-browser-body", swap: "innerHTML" });
      }
    }

    function close() {
      modal.hidden = true;
      modal.setAttribute("aria-hidden", "true");
    }

    Array.prototype.forEach.call(
      document.querySelectorAll(".fs-open-btn"),
      function (btn) {
        btn.addEventListener("click", function () {
          var target = btn.dataset.fsTarget;
          var input = document.querySelector('input[name="' + target + '"]');
          open(target, input ? input.value : "");
        });
      }
    );

    Array.prototype.forEach.call(
      modal.querySelectorAll(".fs-cancel"),
      function (btn) { btn.addEventListener("click", close); }
    );

    modal.addEventListener("click", function (e) {
      if (e.target === modal) close();
    });

    document.addEventListener("keydown", function (e) {
      if (!modal.hidden && e.key === "Escape") close();
    });

    var confirm = modal.querySelector(".fs-confirm");
    confirm.addEventListener("click", function () {
      if (!currentTarget) return close();
      var cur = body.querySelector(".fs-current");
      var path = cur ? cur.dataset.fsCurrentPath : null;
      if (!path) return close();
      var input = document.querySelector('input[name="' + currentTarget + '"]');
      if (input) {
        input.value = path;
        input.dispatchEvent(new Event("input", { bubbles: true }));
      }
      close();
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    autoOpenClones();
    autoHideAlerts();
    wireDateHost();
    wireBBoxHost(document.querySelector(".bbox-host"));
    wireSourcesHost(document.querySelector(".sources-host"));
    wireProductSelect();  // must run AFTER sourcesHost+dateHost+bboxHost so
                          // their _set*() hooks are wired, and BEFORE
                          // wireVarLevelHost — it seeds the product id there.
    wireVarLevelHost(document.querySelector(".vl-host"));
    wireFsPicker();
  });
})();
