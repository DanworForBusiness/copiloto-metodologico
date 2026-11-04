(function () {
  function wire() {
    const panel = document.getElementById("solicitudes-panel");
    for (const container of panel?.querySelectorAll("[data-draggable-columns-key]") || []) {
    if (container.dataset.solicitudesLayoutWired) continue;
    container.dataset.solicitudesLayoutWired = "1";
    const key = container.dataset.draggableColumnsKey;
    const keys = Array.from(container.querySelectorAll("th[data-column-key]"), th => th.dataset.columnKey);

    function restoreOrder() {
      const order = readStoredColumnOrder(key, keys);
      if (!order) return;
      container.querySelectorAll("colgroup, tr").forEach(row => {
        const cells = Array.from(row.children);
        for (const cell of cells) if (cell.dataset.columnFixed === 'start') row.appendChild(cell);
        for (const column of order) row.appendChild(cells.find(cell => cell.dataset.columnKey === column));
        for (const cell of cells) if (!cell.dataset.columnKey && cell.dataset.columnFixed !== 'start') row.appendChild(cell);
      });
    }

    restoreOrder();
    restorePersistedColumnWidths(container);
    initDraggableColumns(panel);
    initResizableColumns(panel);
    container.addEventListener("dw:columns-reordered", restoreOrder);
    let dragged = false;
    container.addEventListener("pointerdown", () => { dragged = false; });
    container.addEventListener("dragstart", () => { dragged = true; });
    // Column drags belong to the table, not the enclosing dashboard sections.
    for (const type of ["dragstart", "dragenter", "dragover", "drop", "dragend"]) {
      container.addEventListener(type, event => event.stopPropagation());
    }
    container.addEventListener("click", event => {
      if (dragged && event.detail && event.target.closest(".solicitudes-sort")) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    }, true);
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();
  document.body.addEventListener("htmx:afterSwap", event => {
    if (event.detail.target?.id === "solicitudes-panel") wire();
  });
})();
