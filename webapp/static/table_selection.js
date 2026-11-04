/* Shared selection primitives extracted from CCHH; no business actions. */
(function () {
  function createStore(storage, options) {
    options = options || {};
    var STORAGE_KEY = options.key || "cchh-candidatos-selection";
    var STORAGE_VERSION = 1;
    var ID_RE = options.idPattern || /^[A-Za-z0-9_-]{1,64}$/;
    var MAX_STORED = options.max || 2000;
    var ids = [];
    var known = Object.create(null);

    function persist() {
      if (!storage) return;
      try { storage.setItem(STORAGE_KEY, JSON.stringify({ v: STORAGE_VERSION, ids: ids })); } catch (e) { /* modo privado/cuota: sigue en memoria, no rompe */ }
    }

    (function load() {
      if (!storage) return;
      try {
        var raw = storage.getItem(STORAGE_KEY);
        if (!raw) return;
        var parsed = JSON.parse(raw);
        if (!parsed || parsed.v !== STORAGE_VERSION || !Array.isArray(parsed.ids)) return;     // forma desconocida: se descarta entera (fail-closed)
        parsed.ids.forEach(function (id) {
          if (typeof id === "string" && ID_RE.test(id) && !known[id] && ids.length < MAX_STORED) { known[id] = true; ids.push(id); }
        });
      } catch (e) { ids = []; known = Object.create(null); }
    })();

    return {
      ids: function () { return ids.slice(); },
      has: function (id) { return !!known[id]; },
      size: function () { return ids.length; },
      add: function (list) {
        var changed = false;
        [].concat(list).forEach(function (id) {
          if (typeof id === "string" && ID_RE.test(id) && !known[id] && ids.length < MAX_STORED) { known[id] = true; ids.push(id); changed = true; }
        });
        if (changed) persist();
        return changed;
      },
      remove: function (list) {
        var drop = Object.create(null);
        [].concat(list).forEach(function (id) { drop[id] = true; });
        var before = ids.length;
        ids = ids.filter(function (id) { if (drop[id]) { delete known[id]; return false; } return true; });
        if (ids.length !== before) persist();
        return ids.length !== before;
      },
      reconcile: function (valid) { var allowed = new Set(valid); return this.remove(ids.filter(function(id) { return !allowed.has(id); })); },
      clear: function () { ids = []; known = Object.create(null); persist(); }
    };
  }
  function scroller(doc, el) {
    var win = doc.defaultView;
    for (var node = el && el.parentElement; node && win && win.getComputedStyle; node = node.parentElement) {
      var oy = win.getComputedStyle(node).overflowY;
      if ((oy === "auto" || oy === "scroll") && node.scrollHeight > node.clientHeight) return node;
    }
    return doc.scrollingElement || doc.documentElement || null;
  }

  function keepInPlace(doc, anchor, change) {
    var canMeasure = anchor && anchor.getBoundingClientRect;
    var before = canMeasure ? anchor.getBoundingClientRect().top : 0;
    change();
    if (!canMeasure || !anchor.isConnected) return;
    var delta = anchor.getBoundingClientRect().top - before;
    var box = delta ? scroller(doc, anchor) : null;
    if (box) box.scrollTop += delta;
  }

  function syncRows(doc, store, options) {
    options = options || {};
    var rowSelector = options.row || "[data-hc-select]", pageSelector = options.page || "[data-hc-select-page]", attribute = options.attribute || "data-hc-select";
    var boxes = [].slice.call(doc.querySelectorAll(rowSelector));
    var visibleSelected = 0;
    boxes.forEach(function (box) {
      var on = store.has(box.getAttribute(attribute));
      box.checked = on;
      if (on) visibleSelected++;
      var row = box.closest ? box.closest("tr") : null;
      if (row && row.classList) { if (on) row.classList.add("is-selected"); else row.classList.remove("is-selected"); }
    });
    [].slice.call(doc.querySelectorAll(pageSelector)).forEach(function (head) {
      head.disabled = boxes.length === 0;
      head.checked = boxes.length > 0 && visibleSelected === boxes.length;
      head.indeterminate = visibleSelected > 0 && visibleSelected < boxes.length;
    });
    return {boxes: boxes, visibleSelected: visibleSelected};
  }

  window.dwTableSelection = {createStore:createStore, syncRows:syncRows, keepInPlace:keepInPlace};
})();
