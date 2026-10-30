/* commercial_strategy_share.js — Comercial → Estrategia: «Compartir» copia al portapapeles la URL de acceso directo a esta página.
   La URL NO es una credencial ni amplía permisos: quien la abre pasa por la sesión y la autorización normales del Hub/Comercial (si no las tiene, aplica el comportamiento normal del sistema).
   Reutiliza `copyTextToClipboard` (app.js, con su fallback) y el aviso transitorio del Hub: `[data-cs-status]` lleva `data-dw-auto-dismiss` (app.js `initAutoDismissFeedback`) y se arma el
   MISMO markup `.callout` con `textContent` (nunca con markup armado a mano), reemplazando lo anterior: varios clics seguidos muestran un único aviso. */
(function () {
  "use strict";

  function toast(title, text, tone) {
    var host = document.querySelector("[data-cs-status]");
    if (!host) return;
    var box = document.createElement("div");
    box.className = "callout callout--" + tone;
    box.setAttribute("role", "status");
    var head = document.createElement("div");
    head.className = "callout__head";
    var icon = document.createElement("span");
    icon.className = "callout__icon";
    icon.textContent = tone === "punch" ? "⚠️" : "✓";
    var heading = document.createElement("span");
    heading.className = "callout__title";
    heading.textContent = title;
    head.appendChild(icon);
    head.appendChild(heading);
    box.appendChild(head);
    if (text) {
      var body = document.createElement("div");
      body.className = "callout__body";
      var p = document.createElement("p");
      p.textContent = text;
      body.appendChild(p);
      box.appendChild(body);
    }
    host.replaceChildren(box);
  }

  document.addEventListener("click", async function (ev) {
    var btn = ev.target.closest("[data-cs-copy-link]");
    if (!btn) return;
    var url = window.location.origin + btn.getAttribute("data-cs-copy-link");
    try {
      if (!(await window.copyTextToClipboard(url))) throw new Error("copy-failed");
      toast("Enlace copiado", "Quien lo reciba lo abre con su propia sesión del Hub.", "success");
    } catch (e) {
      toast("No se pudo copiar", "Copiá la dirección desde la barra del navegador.", "punch");
    }
  });
})();
