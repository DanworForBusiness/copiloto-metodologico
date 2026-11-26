/* ssr_table_columns.js — adapter ÚNICO para tablas renderizadas en el servidor (Jinja) que usan los primitivos COMPARTIDOS del Hub
   (app.js): `initDraggableColumns` (reordenar), `initResizableColumns` (redimensionar), `readStoredColumnOrder` y
   `restorePersistedColumnWidths` (preferencias en localStorage `dw-col-order:*` / `dw-col-widths:*`, el mecanismo existente).
   Es el MISMO algoritmo que los adapters inline de Candidatos/Entrevistas (aplicar el orden guardado moviendo los nodos ya renderizados,
   nunca reconstruir filas), escrito UNA vez y opt-in por atributo: cualquier `.matrix-scroll[data-ssr-columns]` con
   `data-draggable-columns-key` / `data-resizable-columns-key` queda cableado, en la carga y tras cada swap de HTMX.
   El orden de registros lo resuelve el servidor (tablas paginadas) o el orden en cliente del Hub (`table[data-client-sort]`, app.js). */
(function () {
  "use strict";

  function applyOrder(container) {
    if (typeof readStoredColumnOrder !== "function") return;
    var key = container.dataset.draggableColumnsKey;
    var header = container.querySelector("table > thead > tr");
    if (!key || !header) return;
    var valid = [].slice.call(header.querySelectorAll("th[data-column-key]")).map(function (th) { return th.dataset.columnKey; });
    if (!valid.length) return;
    var order = readStoredColumnOrder(key, valid);
    if (!order) return;                                                   // sin preferencia: queda el orden del servidor
    container.querySelectorAll("tr").forEach(function (row) {
      var cells = [].slice.call(row.children);
      var keyed = order.map(function (k) { return row.querySelector(':scope > [data-column-key="' + k + '"]'); }).filter(Boolean);
      var rest = cells.filter(function (c) { return !c.dataset.columnKey; });   // «Acciones» (sin key) siempre al final
      keyed.concat(rest).forEach(function (c) { row.appendChild(c); });
    });
    // El <colgroup> acompaña el orden (el resize ubica cada <col> por su key; el ancho default debe seguir a su columna).
    var group = container.querySelector("table > colgroup");
    if (group) {
      var cols = [].slice.call(group.children);
      order.map(function (k) { return group.querySelector('col[data-column-key="' + k + '"]'); }).filter(Boolean)
        .concat(cols.filter(function (c) { return !c.getAttribute("data-column-key"); }))
        .forEach(function (c) { group.appendChild(c); });
    }
  }

  function wire(scope) {
    var found = false;
    (scope && scope.querySelectorAll ? scope : document).querySelectorAll(".matrix-scroll[data-ssr-columns]").forEach(function (c) {
      found = true;
      applyOrder(c);
      if (typeof restorePersistedColumnWidths === "function") restorePersistedColumnWidths(c);
      if (c.dataset.ssrReorderWired) return;
      c.dataset.ssrReorderWired = "1";
      c.addEventListener("dw:columns-reordered", function () { applyOrder(c); });
    });
    if (!found) return;
    // Los primitivos buscan los contenedores como DESCENDIENTES del scope: siempre `document` (idempotentes por contenedor).
    if (typeof initDraggableColumns === "function") initDraggableColumns(document);
    if (typeof initResizableColumns === "function") initResizableColumns(document);
  }

  window.dwSsrColumns = { wire: wire, applyOrder: applyOrder };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { wire(document); });
  else wire(document);
  document.addEventListener("htmx:afterSwap", function (e) { wire(e.target && e.target.parentNode ? e.target.parentNode : document); });
})();
