/* Preload transversal al cambiar de VERTICAL del Hub.
   REUTILIZA el overlay del login (.auth-overlay + .auth-spinner de webapp/static/hub_overlay.css, la fuente ÚNICA que comparten
   login y pantallas autenticadas); NO crea un segundo spinner ni copia CSS:
   sólo cambia el DISPARADOR (body.hub-transitioning, una regla propia) y el TEXTO. Mecanismo ÚNICO (este archivo), sin lógica
   por vertical: el macro del topbar lo carga en todas las pantallas autenticadas.

   Comportamiento:
     - Aparece al iniciar un cambio de vertical (click en un link del switcher de workspace a OTRA vertical).
     - NO aparece al navegar dentro de la misma vertical, ni en el link activo, ni en clicks modificados / nueva pestaña.
     - Desaparece cuando la vertical destino carga (navegación de página completa: el DOM se reemplaza y el overlay deja de existir).
     - Al volver con el navegador (bfcache), se limpia en `pageshow(persisted)`.
     - Sin temporizador para OCULTAR en el caso normal; el fin responde al estado real de navegación (reemplazo de página).
       Único umbral: un watchdog de seguridad SÓLO para navegación cancelada/bloqueada (p. ej. un `beforeunload` que el usuario
       cancela) — documentado abajo; el fin normal nunca depende de él. */
(function () {
  "use strict";

  var OVERLAY_ID = "hub-transition-overlay";
  var SWITCH_SELECTOR = ".workspace-switch-menu a[href], a.workspace-switch-platform[href]";
  // Watchdog de SEGURIDAD (no es el mecanismo de cierre normal): si la navegación no ocurrió en este lapso, limpiamos el overlay
  // para no dejarlo pegado tras una navegación cancelada/bloqueada. Un cambio de vertical real reemplaza la página mucho antes.
  var SAFETY_MS = 10000;
  var watchdog = null;

  function ensureOverlay() {
    var el = document.getElementById(OVERLAY_ID);
    if (el) return el;
    el = document.createElement("div");
    el.id = OVERLAY_ID;
    el.className = "auth-overlay";                 // MISMO visual que el login
    el.setAttribute("data-hub-transition", "");
    el.setAttribute("role", "status");
    el.setAttribute("aria-live", "polite");
    el.setAttribute("aria-hidden", "true");
    var spin = document.createElement("span");
    spin.className = "auth-spinner";               // MISMO spinner que el login
    spin.setAttribute("aria-hidden", "true");
    var text = document.createElement("span");
    text.setAttribute("data-hub-transition-text", "");
    text.textContent = "Abriendo espacio…";
    el.appendChild(spin);
    el.appendChild(text);
    document.body.appendChild(el);                 // hijo de <body>: `position:fixed; inset:0` cubre el viewport
    return el;
  }

  function show(text) {
    var el = ensureOverlay();
    var t = el.querySelector("[data-hub-transition-text]");
    if (t && text) t.textContent = text;
    el.setAttribute("aria-hidden", "false");
    document.body.classList.add("hub-transitioning");
    if (watchdog) clearTimeout(watchdog);
    watchdog = setTimeout(hide, SAFETY_MS);
  }

  function hide() {
    if (watchdog) { clearTimeout(watchdog); watchdog = null; }
    document.body.classList.remove("hub-transitioning");
    var el = document.getElementById(OVERLAY_ID);
    if (el) el.setAttribute("aria-hidden", "true");
  }

  document.addEventListener("click", function (ev) {
    if (ev.defaultPrevented) return;
    if (ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;  // nueva pestaña / click modificado
    var a = ev.target.closest ? ev.target.closest(SWITCH_SELECTOR) : null;
    if (!a) return;
    if (a.classList.contains("is-active")) return;                 // misma vertical (link activo) → no overlay
    var tgt = a.getAttribute("target");
    if (tgt && tgt !== "_self") return;                            // abre en otro contexto
    var href = a.getAttribute("href") || "";
    if (!href || href.charAt(0) === "#") return;                   // ancla en la misma página
    var label = (a.textContent || "").trim();
    show(label ? ("Abriendo " + label + "…") : "Abriendo espacio…");
    // La navegación de página completa continúa; el overlay persiste hasta que la vertical destino reemplaza la página.
  }, true);

  // Volver con el navegador restaura la página saliente desde bfcache CON el overlay puesto → limpiarlo.
  window.addEventListener("pageshow", function (ev) { if (ev.persisted) hide(); });
})();
