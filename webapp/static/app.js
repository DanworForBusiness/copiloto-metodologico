function copilotLayout(activeGroup = "core", readonly = false, initialTab = "") {
  // Retrocompat: antes recibía un booleano isAppendix.
  if (activeGroup === true) activeGroup = "appendix";
  else if (activeGroup === false) activeGroup = "core";
  return {
    navOpen: false,
    panelOpen: true,
    diagnosticOpen: false,
    gestionOpen: false,
    // En perfil de solo lectura la pestaña Sección (análisis IA) se oculta; arrancamos
    // en Asistente (router determinista que sirve contenido curado; la salida LLM se
    // reemplaza por un aviso, ver assistant_chat).
    // `initialTab` permite que una URL (p. ej. un link de Novedades → ?tab=catalogo)
    // posicione el panel derecho en una pestaña puntual; si no viene, el default.
    toolTab: initialTab || (readonly ? "assistant" : "section"),
    activeSection: "",
    // Hub Transversal Module Section Navigation (ver HANDOFF): visibilidad
    // del nav anidado persistente (`.module-subnav`) -- SOLO relevante en
    // páginas que lo tienen (Estrategia, primer consumidor). Arranca oculto
    // a propósito: mientras el índice in-page "EN ESTE MÓDULO" está en
    // viewport, mostrar TAMBIÉN el nav lateral es redundante (owner
    // feedback tras browser validation) -- fade in recién cuando ese
    // índice in-page sale por ARRIBA del viewport (scrolleado, no
    // "todavía no llegamos"), nunca los dos a la vez. Sin efecto en
    // páginas sin `.module-subnav` (ver init(), gateado por su presencia).
    subnavVisible: false,
    // Grupos colapsables del nav izquierdo (Core / Apéndices / Danwor Academy).
    // Abrimos el grupo del documento activo y dejamos los otros colapsados. Al
    // scrollear el contenido, syncNavToActive() reabre el activo y cierra el resto.
    activeNavGroup: activeGroup,
    navCore: activeGroup === "core",
    navAppendix: activeGroup === "appendix",
    navAcademy: activeGroup === "academy",
    contentTheme: localStorage.getItem("dw-content-theme") || "light",
    // Asset de Academy actualmente visible DENTRO del iframe (puede diferir del
    // que sirvió la página si se navegó internamente, p. ej. paradigmas→datasphere).
    // Lo alimenta el mismo postMessage "danwor-academy-nav" de más abajo; lo usa
    // el botón "pantalla completa" para apuntar siempre a lo que se está viendo.
    academyAsset: null,
    init() {
      const shell = this.$root;
      const savedLeft = Number(localStorage.getItem("dw-nav-width"));
      const savedRight = Number(localStorage.getItem("dw-panel-width"));
      if (savedLeft) shell.style.setProperty("--nav-width", `${savedLeft}px`);
      if (savedRight) shell.style.setProperty("--panel-width", `${savedRight}px`);
      shell.querySelectorAll("[data-resize]").forEach(handle => {
        handle.addEventListener("pointerdown", event => this.startResize(event, handle.dataset.resize));
      });
      // Los grupos del nav se pueden colapsar/abrir libremente, pero apenas el
      // contenido central scrollea, el nav vuelve a reflejar "dónde estás":
      // abre el grupo del doc activo y cierra el otro (no se navega desfasado).
      const syncNavToActive = () => {
        const g = this.activeNavGroup;
        this.navCore = g === "core";
        this.navAppendix = g === "appendix";
        this.navAcademy = g === "academy";
      };
      const contentEl = shell.querySelector(".module-content");
      contentEl?.addEventListener("scroll", syncNavToActive, { passive: true });

      // Sincronización del nav con Danwor Academy: al navegar entre páginas DENTRO
      // del iframe (p. ej. paradigmas→datasphere), el HTML embebido avisa qué asset
      // está viendo; acá reflejamos el resaltado y la URL sin recargar el copiloto.
      window.addEventListener("message", event => {
        const data = event.data;
        if (!data || data.type !== "danwor-academy-nav") return;
        if (data.asset) this.academyAsset = data.asset;
        let matched = null;
        shell.querySelectorAll(".module-link[data-asset]").forEach(link => {
          const isCurrent = !!data.asset && link.dataset.asset === data.asset;
          link.classList.toggle("is-active", isCurrent);
          if (isCurrent) matched = link;
        });
        if (matched) {
          this.navAcademy = true;
          const href = matched.getAttribute("href");
          if (href) { try { history.replaceState(null, "", href); } catch (e) {} }
        }
      });

      // Barra de progreso de lectura (estilo de la web de referencia: una línea
      // que se "llena" vía scaleX). No asumimos QUÉ elemento scrollea: elegimos
      // dinámicamente el de mayor rango de scroll (panel central, <html> o <body>).
      const progressFill = document.querySelector(".read-progress__fill");
      if (progressFill) {
        const pickScroller = () => {
          const cands = [contentEl, document.scrollingElement, document.documentElement, document.body];
          let best = null, bestRange = 8;
          for (const el of cands) {
            if (!el) continue;
            const range = (el.scrollHeight || 0) - (el.clientHeight || 0);
            if (range > bestRange) { best = el; bestRange = range; }
          }
          return { el: best, range: bestRange };
        };
        const updateProgress = () => {
          const { el, range } = pickScroller();
          const ratio = el ? Math.min(1, Math.max(0, el.scrollTop / range)) : 0;
          progressFill.style.width = (ratio * 100).toFixed(2) + "%";
        };
        // Captura en document: el evento scroll no burbujea, pero sí llega en
        // fase de captura, así atrapamos el scroll venga del elemento que venga.
        document.addEventListener("scroll", updateProgress, { capture: true, passive: true });
        window.addEventListener("resize", updateProgress, { passive: true });
        setTimeout(updateProgress, 100);   // reevaluar tras montar embeds/iframes
        updateProgress();
      }
      // Lectura continua: al llegar al final del módulo y seguir scrolleando con
      // intención, pasar al siguiente (además del click en el cue .module-continue).
      // Requiere overscroll deliberado (umbral + reset por inactividad) para no
      // disparar con la inercia de llegar al fondo.
      // Re-consultamos .module-continue en cada evento (no lo cacheamos): así, si
      // el panel central muestra un contenido sin cue (p.ej. el detalle de un
      // flujo del catálogo), el overscroll no navega a ningún lado — la línea de
      // storytelling no continúa donde no corresponde. Con el detalle abierto el
      // módulo sigue en el DOM (oculto), así que además cortamos por la clase.
      if (contentEl) {
        let overscroll = 0, navigating = false, idle = null;
        contentEl.addEventListener("wheel", event => {
          if (navigating) return;
          if (contentEl.classList.contains("catalog-detail-open")) { overscroll = 0; return; }
          const continueEl = contentEl.querySelector(".module-continue");
          if (!continueEl) { overscroll = 0; return; }
          const atBottom = contentEl.scrollTop + contentEl.clientHeight >= contentEl.scrollHeight - 2;
          if (event.deltaY > 0 && atBottom) {
            overscroll += event.deltaY;
            continueEl.classList.add("is-armed");
            clearTimeout(idle);
            idle = setTimeout(() => { overscroll = 0; continueEl.classList.remove("is-armed"); }, 700);
            if (overscroll > 320) {
              navigating = true;
              dwBeacon("module_continue", { via: "overscroll", href: continueEl.getAttribute("href") || "" });
              window.location.href = continueEl.getAttribute("href");
            }
          } else {
            overscroll = 0;
            continueEl.classList.remove("is-armed");
          }
        }, { passive: true });
      }
      // Scroll-spy: el hero (#__module__) marca "Módulo (general)" arriba de todo, y
      // al entrar a una sección marca esa sección. La activa es la MÁS PROFUNDA cuyo
      // top ya cruzó una línea de referencia ≈ donde aterriza un #anchor (topbar 74px
      // + scroll-margin 32px ≈ 110px). Antes tomaba "la primera que intersecta una
      // banda", y el sliver inferior de la sección anterior ganaba → al redirigir por
      // anchor el panel quedaba una sección atrás (y el router creía que no estabas ahí).
      const sections = [...document.querySelectorAll(".module-hero, .content-section")];
      const SPY_REF = 120;
      const recomputeActive = () => {
        let active = null;
        for (const s of sections) {          // en orden vertical de documento
          if (s.getBoundingClientRect().top <= SPY_REF) active = s; else break;
        }
        const id = (active || sections[0])?.id;
        if (id) this.activeSection = id;
      };
      this.activeSection = sections[0]?.id || "";
      let spyRaf = false;
      const scheduleSpy = () => {
        if (spyRaf) return;
        spyRaf = true;
        requestAnimationFrame(() => { spyRaf = false; recomputeActive(); });
      };
      // El scroll no burbujea pero llega en captura → atrapamos venga del scroller que venga.
      document.addEventListener("scroll", scheduleSpy, { capture: true, passive: true });
      window.addEventListener("resize", scheduleSpy, { passive: true });
      requestAnimationFrame(recomputeActive);   // aterrizaje inicial (incluye landing por #anchor)
      setTimeout(recomputeActive, 150);         // reevaluar tras montar embeds / scroll del anchor
      initContentReveals(shell);

      // Hub Transversal Module Section Navigation (ver HANDOFF): el nav
      // lateral persistente (`.module-subnav`) y el índice in-page "EN
      // ESTE MÓDULO" nunca deben verse los dos a la vez (owner feedback).
      // GATEADO por la presencia de AMBOS -- páginas sin `.module-subnav`
      // (todo módulo que no optó a section-nav) no instancian NINGÚN
      // observer nuevo, cero costo.
      const subnav = shell.querySelector(".module-subnav");
      const inPageIndex = shell.querySelector(".module-content .section-index");
      if (subnav && inPageIndex && "IntersectionObserver" in window) {
        const subnavObserver = new IntersectionObserver(entries => {
          const entry = entries[0];
          // Visible recién cuando el índice in-page salió por ARRIBA del
          // viewport (scrolleado de verdad) -- `isIntersecting === false`
          // solo no alcanza: también es false ANTES de llegar a verlo.
          this.subnavVisible = !entry.isIntersecting && entry.boundingClientRect.top < 0;
        }, { root: contentEl, threshold: 0 });
        subnavObserver.observe(inPageIndex);
      } else if (subnav) {
        // `.module-subnav` existe pero no hay índice in-page que observar
        // (no debería pasar en Estrategia hoy) -- fail-safe: mostrar
        // directo, nunca ocultar navegación sin una razón real.
        this.subnavVisible = true;
      }
    },
    toggleTheme() {
      this.contentTheme = this.contentTheme === "light" ? "dark" : "light";
      localStorage.setItem("dw-content-theme", this.contentTheme);
      dwBeacon("theme_change", { theme: this.contentTheme });
      // El tema vive en <html> (lo aplica un script síncrono del <head> para
      // evitar el flash en cada navegación SSR); acá solo lo conmutamos.
      document.cookie = "dw-content-theme=" + this.contentTheme + ";path=/;max-age=31536000;samesite=lax";
      const root = document.documentElement.classList;
      root.remove("content-theme-light", "content-theme-dark");
      root.add("content-theme-" + this.contentTheme);
      // Avisar a los HTML embebidos (iframes) para que ajusten su fondo en vivo,
      // sin recargar. El asset escucha message {type:'danwor-theme', theme}.
      document.querySelectorAll("iframe.content-embed").forEach(frame => {
        try { frame.contentWindow.postMessage({ type: "danwor-theme", theme: this.contentTheme }, "*"); } catch (e) {}
      });
    },
    openSection() {
      if (!this.activeSection) return;
      document.getElementById(this.activeSection)?.scrollIntoView({ behavior: "smooth", block: "start" });
    },
    // "← Volver" de Danwor Academy embebida: al navegar DENTRO del iframe a otro
    // asset (p. ej. paradigmas→datasphere) no hay forma de volver — el historial
    // que se mueve es el del iframe, no el del copiloto, así que el botón Atrás
    // del navegador no ayuda. Retrocede el historial PROPIO del iframe.
    academyGoBack() {
      const frame = this.$root.querySelector(".academy-embed");
      try { frame && frame.contentWindow.history.back(); } catch (e) {}
    },
    sectionChanged() {
      this.openSection();
    },
    startResize(event, side) {
      if (window.innerWidth < 900) return;
      event.preventDefault();
      const shell = this.$root;
      const startX = event.clientX;
      const property = side === "left" ? "--nav-width" : "--panel-width";
      const current = parseFloat(getComputedStyle(shell).getPropertyValue(property));
      const min = side === "left" ? 190 : 300;
      const max = side === "left" ? 420 : 620;
      document.body.classList.add("is-resizing");
      const move = moveEvent => {
        const delta = side === "left" ? moveEvent.clientX - startX : startX - moveEvent.clientX;
        shell.style.setProperty(property, `${Math.max(min, Math.min(max, current + delta))}px`);
      };
      const stop = () => {
        document.body.classList.remove("is-resizing");
        const value = parseFloat(getComputedStyle(shell).getPropertyValue(property));
        localStorage.setItem(side === "left" ? "dw-nav-width" : "dw-panel-width", String(value));
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", stop);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", stop, { once: true });
    }
  };
}

function initContentReveals(shell) {
  const container = shell?.querySelector(".module-content");
  if (!container || !("IntersectionObserver" in window)) return;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

  const selector = [
    ".module-hero",
    ".section-index",
    ".content-section > .content-heading",
    ".content-section > p",
    ".content-section > h3",
    ".content-section > h4",
    ".content-section > blockquote",
    ".content-section > ul",
    ".content-section > ol",
    ".content-section > table",
    ".content-section > pre",
    ".content-section > .callout",
    ".content-section > .callout-row",
    ".content-section > .html-embed-shell",
    ".content-section > .support-links",
    // Viewport Experience Standardization gate (ver HANDOFF): hook GENÉRICO
    // para bloques que no calzan en ninguno de los tags/clases de arriba
    // (grillas de KPIs, tablas/listas envueltas en un `<div x-show>` por el
    // toggle VIEW/EDIT de Estrategia Comercial, timelines, árboles, etc.) --
    // CUALQUIER módulo (no solo Estrategia) puede optar un bloque propio a
    // este MISMO reveal marcándolo `.content-block`, en vez de que cada
    // forma de contenedor nueva necesite su propia entrada acá.
    ".content-section > .content-block",
    ".module-feedback",
    ".module-continue",
  ].join(",");
  const elements = [...container.querySelectorAll(selector)];
  if (!elements.length) return;

  const rootRect = container.getBoundingClientRect();
  elements.forEach(element => {
    element.classList.add("content-reveal");
    const rect = element.getBoundingClientRect();
    if (rect.bottom > rootRect.top && rect.top < rootRect.bottom) {
      element.classList.add("is-visible");
    }
  });

  const observer = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      entry.target.classList.toggle("is-visible", entry.isIntersecting);
      // Telemetría: el cue de "scroll para continuar" entrando en viewport = el
      // usuario llegó al final del módulo (lectura completa). Una vez por carga.
      if (entry.isIntersecting && entry.target.classList.contains("module-continue")) {
        dwBeacon("module_reached_end", {}, true);
      }
    });
  }, {
    root: container,
    threshold: 0.05,
    rootMargin: "0px 0px -4% 0px",
  });
  elements.forEach(element => observer.observe(element));
}

// ── Telemetría cliente (solo VERBOSE) ────────────────────────────────────────
// Envía un beacon liviano a /telemetry/event. sendBeacon sobrevive a la
// navegación (para el evento "continuar"). Gateado por window.DW_VERBOSE, que el
// template setea según el nivel de LOG_ACTIVITY; si está off, no hace nada.
const _dwFired = new Set();
function dwBeacon(event, payload = {}, once = false) {
  if (!window.DW_VERBOSE) return;
  if (once) {
    if (_dwFired.has(event)) return;
    _dwFired.add(event);
  }
  try {
    const stem = (location.pathname.match(/\/modules\/([^/?#]+)/) || [])[1] || "";
    // Hub Telemetry Origin Attribution gate (ver HANDOFF): identidad
    // ESTABLE del módulo actual (module_registry.module_id, ver
    // base.html window.DW_MODULE_ID) -- el backend la resuelve vía
    // module_registry, nunca confía en scope/area mandados por el
    // cliente. Vacío para páginas que todavía no lo exponen (comportamiento
    // histórico sin cambios, ver telemetry.py).
    const body = JSON.stringify({ event, stem, module_id: window.DW_MODULE_ID || "", ...payload });
    if (navigator.sendBeacon) {
      navigator.sendBeacon("/telemetry/event", new Blob([body], { type: "application/json" }));
    } else {
      fetch("/telemetry/event", { method: "POST", headers: { "Content-Type": "application/json" }, body, keepalive: true });
    }
  } catch (e) { /* nunca romper la experiencia por telemetría */ }
}

// Interacciones de navegación/consumo (VERBOSE). Un solo listener delegado en
// captura: continuar al módulo, saltar por el índice/roadmap/nav lateral
// persistente, y links externos del contenido (qué activo se llevó al
// usuario fuera del copiloto).
// `.module-subnav a` (Hub Transversal Module Section Navigation, ver
// HANDOFF): el nav anidado OPCIONAL que un módulo activo expone en el
// sidebar del workspace (ver workspace_shell.html::module_section_nav) —
// generalización del MISMO selector, nunca una segunda implementación de
// "revelar antes de saltar" + beacon nav_jump.
document.addEventListener("click", event => {
  const el = event.target;
  if (!el || !el.closest) return;
  const cont = el.closest(".module-continue");
  if (cont) { dwBeacon("module_continue", { via: "click", href: cont.getAttribute("href") || "" }); return; }
  const nav = el.closest(".section-index a, .module-roadmap__links a, .module-subnav a");
  if (nav) {
    dwBeacon("nav_jump", { href: nav.getAttribute("href") || "" });
    // Viewport Experience Standardization gate (ver HANDOFF): un anchor jump
    // (click, no scroll) puede aterrizar sobre contenido que el
    // IntersectionObserver de initContentReveals() todavía no marcó
    // `.is-visible` (nunca estuvo en viewport hasta este click) — el
    // navegador ejecuta el jump nativo DESPUÉS de este listener, así que
    // acá todavía no sabemos la posición final de scroll para revelar solo
    // lo que quedará a la vista. En vez de arriesgar un flash de contenido
    // opacity:0, reconciliamos TODO el bloque destino a visible de una:
    // transversal (Copiloto Y Estrategia comparten `.section-index`), sin
    // condicional por módulo.
    const href = nav.getAttribute("href") || "";
    const targetId = href.startsWith("#") ? href.slice(1) : "";
    const target = targetId && document.getElementById(targetId);
    if (target) {
      target.querySelectorAll(".content-reveal:not(.is-visible)").forEach(node => node.classList.add("is-visible"));
    }
    return;
  }
  const ext = el.closest(".methodology a[target='_blank']");
  if (ext) { dwBeacon("link_external", { href: ext.getAttribute("href") || "", cls: (ext.className || "").trim() }); }
}, true);

// Apertura/cierre de bloques colapsables del contenido (referencia técnica,
// "¿qué hace cada acción?"). El evento toggle no burbujea → captura en document.
document.addEventListener("toggle", event => {
  const d = event.target;
  if (!d || d.tagName !== "DETAILS") return;
  if (d.classList.contains("content-collapsible") || d.classList.contains("section-action-help")) {
    dwBeacon("disclosure_toggle", {
      kind: d.classList.contains("content-collapsible") ? "collapsible" : "action-help",
      open: !!d.open,
    });
  }
}, true);

let sectionAnalysisLoadingTimer = null;

function startSectionAnalysisMessages(form) {
  const copy = form.querySelector(".assistant-request-loading .section-analysis-loading-copy");
  if (!copy) return;
  const selected = form.querySelector('input[name="action"]:checked');
  const messages = [
    selected?.dataset.loadingMessage || copy.textContent.trim() || "Preparando análisis metodológico...",
    "📂 Recuperando casos reales...",
    "🔍 Obteniendo la mejor respuesta...",
    "🧠 Ya casi estamos...",
  ];
  let index = 0;
  copy.textContent = messages[index];
  window.clearInterval(sectionAnalysisLoadingTimer);
  sectionAnalysisLoadingTimer = window.setInterval(() => {
    index = (index + 1) % messages.length;
    copy.textContent = messages[index];
  }, 2200);
}

function stopSectionAnalysisMessages() {
  window.clearInterval(sectionAnalysisLoadingTimer);
  sectionAnalysisLoadingTimer = null;
}

// Modo diagnóstico: persiste en cookie y recarga (el server re-renderiza el
// selector de marco y la pestaña Analítica según corresponda). La cookie la
// setea el SERVER: enmascarada en el frame de danwor.com, una cookie puesta
// por JS (SameSite=Lax implícito) no viaja en requests cross-site y el modo
// "se apagaba solo"; el server la emite con los mismos atributos que la
// cookie de sesión (SameSite=None; Secure en producción).
window.dwToggleDiagnostic = function (on) {
  const body = new URLSearchParams({ on: on ? "on" : "off" });
  fetch("/api/diagnostic", { method: "POST", body }).finally(() => window.location.reload());
};

// Logout: limpia la cookie de sesión en el backend y vuelve a la entrada del
// Hub PRESERVANDO desde dónde se salió, en `next`. Antes iba a "/" a secas, así
// que al volver a autenticarse el usuario aterrizaba siempre en el destino por
// defecto de su perfil aunque hubiera salido desde otro workspace.
//
// `next` NO es un mecanismo nuevo: es el MISMO contrato del camino inverso
// —entrar sin sesión a una página protegida, ver `workspaces.platform_login_redirect`,
// que arma `next` igual, con `path` + `query`— y lo consume el mismo endpoint.
// Quien lo VALIDA es el server (`render_landing`/`/onedanwor`): sólo acepta un
// path propio que empiece con "/" y no con "//". Acá no se duplica esa regla:
// se manda `pathname + search`, relativo, sin origin (una URL absoluta nunca
// pasaría esa validación) y sin hash (el fragmento no viaja al server). Si
// llegara algo no aceptable, el server lo descarta y cae en su default — nunca
// redirige afuera.
//
// `URLSearchParams` da el encoding exacto que el server espera
// ("/comercial" → "%2Fcomercial") sin doble-encodear, y es el mismo helper que
// ya usa `dwToggleDiagnostic`. Transversal por construcción: el destino se lee
// de la ubicación actual, así que no hay una rama por capability.
// HIGIENE DE AUTH (auditoría cross-vertical, owner 2026-08-23): el logout
// borraba la cookie de sesión y dejaba VIVO el artefacto de Graph. Un token
// válido sobrevivía al cierre de sesión — inválido: la autorización delegada
// es parte del contexto de auth, así que se va con él.
// `dwClearGraphAuth` vive en la AUTORIDAD (hub_auth.js), no acá: el chrome
// decide CUÁNDO se limpia, la autoridad sabe QUÉ se limpia. En la Fase 1B eso
// además incluye bajar la cuenta y la cache de MSAL, sin tocar este archivo.

window.dwLogout = function () {
  const next = window.location.pathname + window.location.search;
  const target = `/onedanwor?${new URLSearchParams({ next }).toString()}`;
  // Se limpia ANTES del fetch: si la red falla, el artefacto de auth ya no
  // está. `finally` garantiza la navegación igual.
  window.dwClearGraphAuth();
  fetch("/api/auth/logout", { method: "POST" }).finally(() => { window.location.href = target; });
};

document.body.addEventListener("htmx:beforeRequest", event => {
  event.detail.elt?.closest("#wizard-shell")?.classList.add("is-loading");
  const assistantRequest = event.detail.elt?.closest(".section-analysis-form, .assistant-chat-form, .assistant-route-explain");
  if (assistantRequest) {
    assistantRequest.querySelector(".section-analysis-warning")?.replaceChildren();
    startSectionAnalysisMessages(assistantRequest);
  }
});
// Quitar el estado de carga al terminar CUALQUIER request. Sin esto, el feedback
// (que swapea solo #tree-feedback, no el #wizard-shell) dejaba el árbol con
// opacidad .45 + pointer-events:none pegados. afterRequest dispara siempre.
document.body.addEventListener("htmx:afterRequest", event => {
  document.getElementById("wizard-shell")?.classList.remove("is-loading");
  const analysisForm = event.detail.elt?.closest(".section-analysis-form");
  const chatForm = event.detail.elt?.closest(".assistant-chat-form");
  const routeForm = event.detail.elt?.closest(".assistant-route-explain");
  if (analysisForm || chatForm || routeForm) stopSectionAnalysisMessages();
  const duplicateAnalysis = event.detail.xhr?.getResponseHeader("X-Danwor-Duplicate") === "true";
  if (event.detail.successful && analysisForm && !duplicateAnalysis) {
    window.dispatchEvent(new CustomEvent("section-analysis-complete"));
  }
  if (event.detail.successful && (chatForm || routeForm)) {
    if (chatForm) chatForm.reset();
    requestAnimationFrame(() => {
      const chat = document.getElementById("assistant-conversation");
      if (chat) chat.scrollTop = chat.scrollHeight;
    });
  }
});

// Fallback de última instancia cuando el navegador NO recibe una respuesta HTTP
// (conexión caída / timeout de red): htmx no tiene nada que swapear, así que sin
// esto la acción queda "muerta" — el spinner lo frena htmx:afterRequest, pero no
// aparece ni resultado ni error (el incidente real: ERR_CONNECTION_CLOSED en
// POST .../assistant/chat). OPT-IN y reusable: solo actúa sobre elementos que
// declaran `data-dw-request-error-target` (+ opcional `data-dw-request-error-message`).
// NO se engancha a htmx:responseError a propósito: las fallas de proveedor
// conocidas ya vuelven como fragmento 200 renderizado por el server, y los estados
// 403/409/410 del feedback tienen su propio manejo — no los duplicamos. NUNCA
// auto-reintenta (el SDK del server ya reintentó; reenviar duplicaría estado).
function dwRenderRequestErrorFallback(event) {
  const elt = event.detail && event.detail.elt;
  if (!elt || typeof elt.closest !== "function") return;
  const trigger = elt.closest("[data-dw-request-error-target]");
  if (!trigger) return;
  const target = document.querySelector(trigger.getAttribute("data-dw-request-error-target"));
  if (!target) return;
  const message = trigger.getAttribute("data-dw-request-error-message")
    || "No pudimos completar la solicitud por un problema de conectividad. Intentá nuevamente en unos minutos.";
  const block = document.createElement("div");
  block.className = "assistant-exchange dw-request-error";
  block.innerHTML =
    '<div class="assistant-message assistant-message--assistant">'
    + '<span class="assistant-message-label">Arquitecto Danwor</span>'
    + '<div class="section-analysis-error"><strong>No se pudo completar</strong><p></p></div>'
    + '</div>';
  // textContent (no innerHTML) para el mensaje → sin riesgo de inyección.
  block.querySelector(".section-analysis-error p").textContent = message;
  target.appendChild(block);
}
document.body.addEventListener("htmx:sendError", dwRenderRequestErrorFallback);
document.body.addEventListener("htmx:timeout", dwRenderRequestErrorFallback);

// Detalle de flujo del catálogo: el detalle se swapea en un slot dedicado
// (#catalog-detail-slot) DENTRO de #content, y solo OCULTAMOS el módulo (no lo
// destruimos). Así el scroll-spy, los IntersectionObserver y los formularios de
// feedback — inicializados una sola vez con referencias a esos nodos — quedan
// intactos; "Volver" solo desoculta, sin recargar (lo que reiniciaba el panel
// derecho). Guardamos el scroll del módulo para restaurarlo al volver.
let dwModuleScroll = 0;
window.dwCatalogBack = function () {
  const content = document.getElementById("content");
  const slot = document.getElementById("catalog-detail-slot");
  if (content && slot && content.classList.contains("catalog-detail-open")) {
    content.classList.remove("catalog-detail-open");
    slot.replaceChildren();
    content.scrollTop = dwModuleScroll || 0;
    return false;
  }
  // Fallback (p. ej. se entró al detalle por URL directa: no hay módulo debajo).
  window.location.reload();
  return false;
};

// Lightbox de imágenes para contenido embebido (p. ej. la guía "Cómo publicar",
// cuyas slides viven en un iframe). El iframe pide ampliar por postMessage y el
// PADRE muestra la imagen sola sobre fondo oscuro, a pantalla completa del
// copiloto — no un dialog confinado al iframe. Cierra con ✕, click afuera o Esc.
function dwShowLightbox(src) {
  if (!src) return;
  let ov = document.getElementById("dw-lightbox");
  if (!ov) {
    ov = document.createElement("div");
    ov.id = "dw-lightbox";
    ov.className = "dw-lightbox";
    ov.innerHTML = '<button class="dw-lightbox__close" type="button" aria-label="Cerrar">&times;</button><img alt="Captura ampliada">';
    document.body.appendChild(ov);
    const close = () => ov.classList.remove("is-open");
    ov.addEventListener("click", e => { if (e.target === ov || e.target.classList.contains("dw-lightbox__close")) close(); });
    document.addEventListener("keydown", e => { if (e.key === "Escape") close(); });
  }
  ov.querySelector("img").src = src;
  ov.classList.add("is-open");
}
// Regeneración self-service pedida desde un iframe embebido (la guía "Cómo
// publicar"). El token de Graph vive en el sessionStorage del copiloto (este
// contexto), no en el del iframe; por eso el POST lo hace el PADRE y devuelve el
// resultado al iframe que lo pidió.
async function dwRunRegenerate(source) {
  const token = await dwGraphToken();
  const reply = (ok, message) => {
    if (source) { try { source.postMessage({ type: "dw-regenerate-result", ok: ok, message: message }, window.location.origin); } catch (e) {} }
  };
  fetch("/catalog/regenerate", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ graph_token: token }),
  })
    .then(r => r.ok ? r.json() : Promise.reject(r.status))
    .then(d => reply(!!d.ok, String(d.message || "")))
    .catch(() => reply(false, "No se pudo regenerar. Reintentá en unos segundos."));
}
window.addEventListener("message", event => {
  if (event.origin !== window.location.origin) return;   // solo mensajes del mismo origen
  const d = event.data;
  if (!d) return;
  if (d.type === "dw-lightbox") dwShowLightbox(d.src);
  else if (d.type === "dw-regenerate") dwRunRegenerate(event.source);
});

async function copyTextToClipboard(text) {
  if (!text) return false;
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return true;
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.select();
  const ok = document.execCommand("copy");
  textarea.remove();
  return ok;
}

document.addEventListener("click", async event => {
  const button = event.target.closest("[data-copy-code]");
  if (!button) return;
  const box = button.closest(".code-copybox");
  const code = box?.querySelector("pre code");
  if (!code) return;
  event.preventDefault();
  try {
    const copied = await copyTextToClipboard(code.innerText || code.textContent || "");
    if (!copied) return;
    button.classList.add("is-copied");
    button.setAttribute("aria-label", "Código copiado");
    button.setAttribute("title", "Código copiado");
    window.setTimeout(() => {
      button.classList.remove("is-copied");
      button.setAttribute("aria-label", "Copiar código");
      button.setAttribute("title", "Copiar código");
    }, 1400);
  } catch (error) {
    console.warn("No se pudo copiar el codigo", error);
  }
});

// Detalle de flujo abierto (catalog-detail-open): los links de ancla internos
// (índice de secciones = linaje, nodos del SVG, y referencias entre artefactos)
// saltan a su sección con scrollIntoView. El salto nativo por #hash es poco fiable
// dentro de un fragmento swapeado con contenedor de scroll ambiguo. behavior:"auto"
// (instantáneo, sin animación) para EMULAR el índice de módulos y ser consistente.
document.addEventListener("click", event => {
  const content = document.getElementById("content");
  if (!content || !content.classList.contains("catalog-detail-open")) return;
  const a = event.target.closest('a[href^="#"]');
  if (!a || !content.contains(a)) return;
  const el = document.getElementById(a.getAttribute("href").slice(1));
  if (!el) return;
  event.preventDefault();
  el.scrollIntoView({ behavior: "auto", block: "start" });
});

function syncAssistantFeedbackState() {
  const exchanges = [...document.querySelectorAll("#assistant-conversation > .assistant-exchange")];
  exchanges.forEach((exchange, index) => {
    const isLatest = index === exchanges.length - 1;
    exchange.querySelectorAll(".assistant-feedback button, .assistant-feedback textarea").forEach(control => {
      control.disabled = !isLatest;
      control.setAttribute("aria-disabled", String(!isLatest));
    });
  });
}

document.body.addEventListener("htmx:afterSwap", event => {
  if (event.detail.target?.id === "wizard-shell") {
    event.detail.target.focus({ preventScroll: true });
  }
  if (event.detail.target?.id === "assistant-conversation") {
    syncAssistantFeedbackState();
  }
  // Detalle de flujo del catálogo cargado en su slot: ocultar el módulo (sin
  // destruirlo) y arrancar el detalle desde arriba. Guardamos el scroll del
  // módulo para restaurarlo en dwCatalogBack().
  if (event.detail.target?.id === "catalog-detail-slot") {
    const content = document.getElementById("content");
    if (content) {
      if (!content.classList.contains("catalog-detail-open")) {
        dwModuleScroll = content.scrollTop;
      }
      content.classList.add("catalog-detail-open");
      content.scrollTop = 0;
    }
  }
});
document.body.addEventListener("htmx:load", syncAssistantFeedbackState);

// Reordenamiento drag-and-drop de grupos colapsables dentro de un dashboard.
// Genérico: cualquier futuro dashboard que envuelva sus <details class="dashboard-section">
// en un contenedor [data-dashboard-key="algo"] queda reordenable gratis. Orden persistido
// en sessionStorage (volátil a propósito: se pierde al cerrar la pestaña, no es preferencia
// permanente). Cada filtro server-side reemplaza el contenedor entero (outerHTML), por eso
// esto se reaplica en cada htmx:load, que dispara tanto en la carga inicial de página
// completa como después de cualquier swap.
function initDashboardSections(scope) {
  scope.querySelectorAll("[data-dashboard-key]").forEach(container => {
    const storageKey = `dw-dash-order:${container.dataset.dashboardKey}`;
    applyStoredSectionOrder(container, storageKey);
    // Persistencia de COLAPSO opt-in (`data-persist-collapse`): sólo la piden
    // los consumidores que RE-RENDERIZAN el panel server-side (HTMX swap, p. ej.
    // CCHH Candidatos al filtrar) y necesitan que el estado colapsado/expandido
    // sobreviva ese swap. Los demás verticales (filtran client-side, sin swap)
    // NO llevan el atributo y conservan el comportamiento nativo exacto de
    // siempre -- cero cambio para ellos. Se re-aplica en CADA htmx:load (antes
    // del guard), igual que el orden.
    const collapseKey = `dw-dash-collapse:${container.dataset.dashboardKey}`;
    const persistCollapse = container.dataset.persistCollapse != null;
    if (persistCollapse) applyStoredSectionCollapse(container, collapseKey);
    if (container.dataset.dashWired) return;
    container.dataset.dashWired = "1";
    if (persistCollapse) {
      // `toggle` NO burbujea -> captura en el contenedor. Persiste el estado
      // open/colapsado de cada sección al abrir/cerrar.
      container.addEventListener("toggle", event => {
        const s = event.target;
        if (s && s.classList && s.classList.contains("dashboard-section")) saveSectionCollapse(container, collapseKey);
      }, true);
    }
    let dragging = null;
    // Línea de inserción: marca el punto EXACTO donde caería la sección (antes/después
    // del tile sobre el que estás), además del resaltado del tile en sí — el resaltado
    // solo no era suficientemente intuitivo (no distinguía "antes" de "después").
    const dropLine = document.createElement("div");
    dropLine.className = "dashboard-section__drop-line";
    container.addEventListener("dragstart", event => {
      const section = event.target.closest(".dashboard-section");
      if (!event.target.closest(".dashboard-section__head") || !section) { event.preventDefault(); return; }
      dragging = section;
      section.classList.add("is-dragging");
      event.dataTransfer.effectAllowed = "move";
    });
    container.addEventListener("dragend", () => {
      dragging?.classList.remove("is-dragging");
      dragging = null;
      container.querySelectorAll(".is-drop-target").forEach(el => el.classList.remove("is-drop-target"));
      dropLine.remove();
    });
    // dragenter/dragover DEBEN llamar preventDefault para que el navegador considere
    // válido el drop en esa posición — si no, al soltar justo sobre la línea o en un
    // hueco entre tiles (donde event.target no resuelve a un .dashboard-section), el
    // navegador cancela el drag y la sección "vuelve a su lugar" sin moverse.
    container.addEventListener("dragenter", event => { if (dragging) event.preventDefault(); });
    container.addEventListener("dragover", event => {
      if (!dragging) return;
      event.preventDefault();
      const target = event.target.closest(".dashboard-section");
      if (!target || target === dragging) return;
      container.querySelectorAll(".is-drop-target").forEach(el => el.classList.remove("is-drop-target"));
      target.classList.add("is-drop-target");
      const before = (event.clientY - target.getBoundingClientRect().top) < target.offsetHeight / 2;
      container.insertBefore(dropLine, before ? target : target.nextSibling);
    });
    container.addEventListener("drop", event => {
      event.preventDefault();
      if (!dragging || !dropLine.isConnected) return;
      container.insertBefore(dragging, dropLine);
      dropLine.remove();
      container.querySelectorAll(".is-drop-target").forEach(el => el.classList.remove("is-drop-target"));
      saveSectionOrder(container, storageKey);
    });
  });
}
function applyStoredSectionOrder(container, storageKey) {
  let order;
  try { order = JSON.parse(sessionStorage.getItem(storageKey) || "null"); } catch (e) { order = null; }
  if (!Array.isArray(order)) return;
  const byId = new Map([...container.querySelectorAll(":scope > .dashboard-section")].map(el => [el.dataset.sectionId, el]));
  order.forEach(id => { const el = byId.get(id); if (el) container.appendChild(el); });
}
function saveSectionOrder(container, storageKey) {
  const order = [...container.querySelectorAll(":scope > .dashboard-section")].map(el => el.dataset.sectionId);
  try { sessionStorage.setItem(storageKey, JSON.stringify(order)); } catch (e) { /* storage lleno/deshabilitado: no rompe nada */ }
}

// Colapso persistido (opt-in, ver initDashboardSections): sessionStorage igual
// que el orden -> sobrevive HTMX swaps y reloads dentro de la sesión.
function applyStoredSectionCollapse(container, storageKey) {
  let state;
  try { state = JSON.parse(sessionStorage.getItem(storageKey) || "null"); } catch (e) { state = null; }
  if (!state || typeof state !== "object") return;
  container.querySelectorAll(":scope > .dashboard-section").forEach(el => {
    const id = el.dataset.sectionId;
    if (id in state) { if (state[id]) el.setAttribute("open", ""); else el.removeAttribute("open"); }
  });
}
function saveSectionCollapse(container, storageKey) {
  const state = {};
  container.querySelectorAll(":scope > .dashboard-section").forEach(el => { state[el.dataset.sectionId] = el.hasAttribute("open"); });
  try { sessionStorage.setItem(storageKey, JSON.stringify(state)); } catch (e) { /* storage lleno/deshabilitado: no rompe nada */ }
}
// Expandir/Contraer TODOS los `dashboard-section` de uno o más grupos a la
// vez (owner request, Candidate Edit — CCHH, ver HANDOFF): extensión del
// primitivo COMPARTIDO, nunca un mecanismo bespoke por vertical (otro
// consumidor ya había resuelto esto puntualmente de forma bespoke propia
// para uno de sus paneles, pero con un segundo consumidor real corresponde
// promoverlo acá en vez de duplicar la lógica una vez más; ese consumidor
// previo queda intacto, fuera de alcance de este cambio — app.js sigue sin
// saber nada de ninguna vertical puntual). `keys`: array de
// `data-dashboard-key` a afectar — soporta MÚLTIPLES
// grupos de persistencia independientes con una sola acción (p.ej.
// Candidate Edit: los 5 bloques de campos + Documentos, dos namespaces
// distintos, una sola botonera). Persiste el colapso resultante
// EXACTAMENTE igual que un toggle individual (mismo `saveSectionCollapse`)
// — sólo para los grupos que YA declaran `data-persist-collapse`, nunca
// uno nuevo silencioso. No reordena nada (el orden vive en una key
// sessionStorage totalmente independiente, `saveSectionOrder` — esta
// función nunca la toca).
function setDashboardGroupsCollapse(keys, open) {
  keys.forEach(key => {
    const container = document.querySelector(`[data-dashboard-key="${CSS.escape(key)}"]`);
    if (!container) return;
    container.querySelectorAll(":scope > .dashboard-section").forEach(section => {
      if (open) section.setAttribute("open", ""); else section.removeAttribute("open");
    });
    if (container.dataset.persistCollapse != null) {
      saveSectionCollapse(container, `dw-dash-collapse:${container.dataset.dashboardKey}`);
    }
  });
}
// Delegado sobre `document` (MISMO criterio que el resto del archivo):
// un botón con `data-dashboard-expand-all`/`data-dashboard-collapse-all` +
// `data-dashboard-target="key1 key2 ..."` (IDs separados por espacio,
// MISMO valor que `data-dashboard-key` de cada grupo objetivo) dispara
// `setDashboardGroupsCollapse`. Cero JS específico por vertical: cualquier
// consumidor de `dashboard-section` puede optar a esto con sólo marcado.
document.addEventListener("click", event => {
  const button = event.target.closest("[data-dashboard-expand-all], [data-dashboard-collapse-all]");
  if (!button) return;
  const open = button.hasAttribute("data-dashboard-expand-all");
  const keys = (button.dataset.dashboardTarget || "").split(/\s+/).filter(Boolean);
  setDashboardGroupsCollapse(keys, open);
});
document.body.addEventListener("htmx:load", () => initDashboardSections(document));

// Indicator visibility toggles (primitivo Hub-wide nuevo, primer consumidor:
// CCHH Candidatos, "Indicadores"): un grupo de checkboxes que controla
// SOLO presentación (qué metadata contextual se ve al lado de la identidad
// de cada fila), nunca filtrado/query ni persistencia de negocio — por eso
// vive 100% en cliente, sin `name=` en los checkboxes (así ni un submit
// nativo ni un `hx-include="closest form"` ajeno los confunde con un
// filtro real). Contrato puramente por atributos, sin saber nada de
// ninguna vertical puntual:
//   [data-indicator-toggles][data-indicator-storage-key="<key>"]  -- el grupo
//     [data-indicator-toggle="<name>"]                            -- cada checkbox
//   [data-indicator-scope="<key>"]                                -- contenedor de destino
//     [data-indicator="<name>"]                                   -- elemento que se oculta/muestra
// Persiste en localStorage (sobrevive reload, a diferencia del
// sessionStorage de colapso/orden de dashboard-section arriba — esto es
// preferencia de usuario de más largo aliento, no estado de sesión) bajo
// `dw-indicators:<key>`. Se reaplica en cada `htmx:load` (carga inicial +
// cualquier swap que reemplace el scope), MISMO criterio que
// initDashboardSections.
function initIndicatorToggles(scope) {
  scope.querySelectorAll("[data-indicator-toggles]").forEach(group => {
    const key = group.dataset.indicatorStorageKey;
    const storageKey = `dw-indicators:${key}`;
    const target = document.querySelector(`[data-indicator-scope="${CSS.escape(key)}"]`);
    let prefs;
    try { prefs = JSON.parse(localStorage.getItem(storageKey) || "null"); } catch (e) { prefs = null; }
    if (!prefs || typeof prefs !== "object") prefs = {};
    function apply() {
      group.querySelectorAll("[data-indicator-toggle]").forEach(input => {
        const name = input.dataset.indicatorToggle;
        if (Object.prototype.hasOwnProperty.call(prefs, name)) input.checked = prefs[name];
        if (!target) return;
        target.querySelectorAll(`[data-indicator="${CSS.escape(name)}"]`).forEach(el => {
          el.hidden = !input.checked;
        });
      });
    }
    apply();
    if (group.dataset.indicatorWired) return;
    group.dataset.indicatorWired = "1";
    group.addEventListener("change", event => {
      const input = event.target.closest("[data-indicator-toggle]");
      if (!input) return;
      prefs[input.dataset.indicatorToggle] = input.checked;
      try { localStorage.setItem(storageKey, JSON.stringify(prefs)); } catch (e) { /* privado/cuota: no persiste, no rompe */ }
      apply();
    });
  });
}
document.body.addEventListener("htmx:load", () => initIndicatorToggles(document));

// ── Rating de estrellas 1..N (primitivo COMPARTIDO Hub) ───────────────────
// Cablea los controles editables `[data-star-rating]` (NO los readonly, que
// son puro display server-rendered). Contrato de UI (owner):
//   - hover sobre la estrella i -> preview acumulativo (se llenan 1..i);
//   - mouseleave del grupo -> vuelve al valor COMMITEADO;
//   - click en la estrella i -> commitea i (actualiza el <input> hidden);
//   - teclado: ←/↓ baja, →/↑ sube, 1..N fija, Enter/Espacio commitea la que
//     tiene foco; ←/↓ desde 1 llega a 0 (sin rating, campo opcional).
// El valor posteado es un entero (o "" = sin rating). Semántica de negocio
// intacta: esto sólo reemplaza el <input> numérico por el control visual.
function initStarRatings(scope) {
  scope.querySelectorAll("[data-star-rating]:not([data-star-readonly])").forEach(group => {
    if (group.dataset.starWired) return;
    group.dataset.starWired = "1";
    const input = group.querySelector("[data-star-input]");
    const stars = Array.prototype.slice.call(group.querySelectorAll("[data-star-index]"));
    const max = stars.length;
    let committed = parseInt(group.dataset.starValue, 10) || 0;
    function paint(fill) {
      stars.forEach(s => {
        const idx = parseInt(s.dataset.starIndex, 10);
        s.classList.toggle("is-filled", idx <= fill);
      });
    }
    function commit(val) {
      committed = val;
      if (input) input.value = val ? String(val) : "";
      group.dataset.starValue = String(val);
      stars.forEach(s => {
        const idx = parseInt(s.dataset.starIndex, 10);
        s.setAttribute("aria-checked", idx === val ? "true" : "false");
        s.tabIndex = (idx === val || (val === 0 && idx === 1)) ? 0 : -1;
      });
      paint(val);
    }
    stars.forEach(s => {
      const idx = parseInt(s.dataset.starIndex, 10);
      s.addEventListener("mouseenter", () => paint(idx));
      s.addEventListener("click", () => { commit(idx); s.focus(); });
      s.addEventListener("keydown", ev => {
        let next = null;
        if (ev.key === "ArrowRight" || ev.key === "ArrowUp") next = Math.min(max, committed + 1);
        else if (ev.key === "ArrowLeft" || ev.key === "ArrowDown") next = Math.max(0, committed - 1);
        else if (/^[1-9]$/.test(ev.key)) next = Math.min(max, parseInt(ev.key, 10));
        else if (ev.key === " " || ev.key === "Enter") next = idx;
        if (next === null) return;
        ev.preventDefault();
        commit(next);
        const focusIdx = next > 0 ? next : 1;
        const target = stars.find(x => parseInt(x.dataset.starIndex, 10) === focusIdx);
        if (target) target.focus();
      });
    });
    group.addEventListener("mouseleave", () => paint(committed));
    paint(committed);
  });
}
document.body.addEventListener("htmx:load", () => initStarRatings(document));

// ── Confirmación destructiva COMPARTIDA (primitivo <dialog> nativo) ────────
// UNA sola UX de confirmación para todo el Hub (owner: "one confirmation
// pattern"). `dwConfirm({title,message,confirmLabel,cancelLabel}) -> Promise
// <boolean>`. El <dialog> (base.html) aporta focus-trap/Escape/backdrop
// nativos; enfoca "Cancelar" por default (seguro para acciones destructivas).
function dwConfirm(opts) {
  opts = opts || {};
  const dlg = document.getElementById("dwConfirm");
  if (!dlg || typeof dlg.showModal !== "function") {
    // Fallback (navegador sin <dialog>): confirm nativo, nunca omitir el paso.
    const txt = (opts.title ? opts.title + "\n\n" : "") + (opts.message || "¿Confirmar?");
    return Promise.resolve(window.confirm(txt));
  }
  dlg.querySelector(".dw-confirm__title").textContent = opts.title || "Confirmar";
  dlg.querySelector("[data-dw-confirm-msg]").textContent = opts.message || "";
  const cancelBtn = dlg.querySelector("[data-dw-confirm-cancel]");
  const okBtn = dlg.querySelector("[data-dw-confirm-ok]");
  cancelBtn.textContent = opts.cancelLabel || "Cancelar";
  okBtn.textContent = opts.confirmLabel || "Eliminar";
  return new Promise(resolve => {
    function onClose() { dlg.removeEventListener("close", onClose); resolve(dlg.returnValue === "confirm"); }
    dlg.addEventListener("close", onClose);
    dlg.returnValue = "";
    dlg.showModal();
    cancelBtn.focus();
  });
}
// Abridor de <dialog> COMPARTIDO: `[data-dialog-open="<id>"]` abre por
// showModal() el <dialog> con ese id. Reusable por cualquier modal no-confirm
// del Hub (p.ej. "Agregar candidato"/"Cerrar búsqueda" en Búsquedas). El cierre
// lo maneja el propio <dialog> (Escape/backdrop nativo o un form method="dialog").
document.body.addEventListener("click", function (ev) {
  const opener = ev.target.closest && ev.target.closest("[data-dialog-open]");
  if (!opener) return;
  const dlg = document.getElementById(opener.getAttribute("data-dialog-open"));
  if (dlg && typeof dlg.showModal === "function") { ev.preventDefault(); dlg.showModal(); }
}, false);

function _dwConfirmOptsFrom(el) {
  return {
    title: el.getAttribute("data-dw-confirm-title") || "Confirmar",
    message: el.getAttribute("data-dw-confirm-message") || "",
    confirmLabel: el.getAttribute("data-dw-confirm-ok") || "Eliminar",
  };
}
// (a) Elementos HTMX con [data-dw-confirm]: el elemento se dispara con
// `hx-trigger="confirmed"` (NO con click), así un click normal NO emite el
// request. Interceptamos el click, mostramos el diálogo compartido y sólo al
// confirmar disparamos el trigger `confirmed` con `htmx.trigger`, que emite el
// request htmx (con su hx-include/hx-target/hx-swap). Se evita a propósito el
// patrón `htmx:confirm` + `issueRequest` diferido (poco fiable tras un diálogo
// asíncrono). Cubre el borrado desde el listado.
document.body.addEventListener("click", function (ev) {
  const el = ev.target.closest && ev.target.closest("[data-dw-confirm]");
  if (!el) return;
  ev.preventDefault();
  ev.stopPropagation();
  dwConfirm(_dwConfirmOptsFrom(el)).then(function (ok) {
    if (!ok) return;
    if (window.htmx && window.htmx.trigger) window.htmx.trigger(el, "confirmed");
    else el.dispatchEvent(new CustomEvent("confirmed"));
  });
}, true);
// (b) Forms NO-htmx con [data-dw-confirm-form]: se intercepta el submit, se
// confirma y recién ahí se re-envía. Cubre el borrado full-page desde Editar.
function initConfirmForms(scope) {
  scope.querySelectorAll("form[data-dw-confirm-form]").forEach(function (form) {
    if (form.dataset.dwConfirmWired) return;
    form.dataset.dwConfirmWired = "1";
    form.addEventListener("submit", function (ev) {
      if (form.dataset.dwConfirmed === "1") { form.dataset.dwConfirmed = ""; return; }
      ev.preventDefault();
      dwConfirm({
        title: form.getAttribute("data-dw-confirm-title") || "Confirmar",
        message: form.getAttribute("data-dw-confirm-message") || "",
        confirmLabel: form.getAttribute("data-dw-confirm-ok") || "Eliminar",
      }).then(function (ok) {
        if (!ok) return;
        form.dataset.dwConfirmed = "1";
        if (form.requestSubmit) form.requestSubmit(); else form.submit();
      });
    });
  });
}
document.body.addEventListener("htmx:load", function () { initConfirmForms(document); });

// Reordenamiento drag-and-drop HORIZONTAL de columnas de tabla — MISMO
// idioma exacto que initDashboardSections (arriba: dragstart/dragenter/
// dragover/drop/dragend + resaltado is-dragging/is-drop-target), aplicado
// a <th> en vez de <details>. Genérico: cualquier tabla que envuelva su
// <thead>/<tbody> en [data-draggable-columns-key="algo"] con
// <th draggable="true" data-column-key="..."> headers queda reordenable
// gratis. A propósito NO re-renderiza nada por sí mismo — persiste el
// nuevo orden en localStorage (`dw-col-order:<key>`, preferencia LOCAL de
// UI/columna, nunca dato de negocio) y dispara "dw:columns-reordered"
// (bubbles) en el contenedor con `detail.order`; el caller (que conoce su
// propio esquema de columnas/celdas) decide cómo repintar. El orden
// "actual" para calcular el nuevo se lee del DOM ya renderizado
// (`[data-column-key]` en orden), nunca de un catálogo hardcodeado acá —
// este primitivo no sabe nada de negocio, sólo mueve/persiste/avisa.
function initDraggableColumns(scope) {
  scope.querySelectorAll("[data-draggable-columns-key]").forEach(container => {
    if (container.dataset.dragColsWired) return;
    container.dataset.dragColsWired = "1";
    const storageKey = `dw-col-order:${container.dataset.draggableColumnsKey}`;
    let draggingKey = null;
    container.addEventListener("dragstart", event => {
      const th = event.target.closest("[data-column-key]");
      if (!th) { event.preventDefault(); return; }
      draggingKey = th.dataset.columnKey;
      th.classList.add("is-dragging");
      event.dataTransfer.effectAllowed = "move";
    });
    container.addEventListener("dragend", () => {
      container.querySelectorAll(".is-dragging, .is-drop-target").forEach(el => el.classList.remove("is-dragging", "is-drop-target"));
      draggingKey = null;
    });
    container.addEventListener("dragenter", event => { if (draggingKey) event.preventDefault(); });
    container.addEventListener("dragover", event => {
      if (!draggingKey) return;
      event.preventDefault();
      const target = event.target.closest("[data-column-key]");
      container.querySelectorAll(".is-drop-target").forEach(el => el.classList.remove("is-drop-target"));
      if (target && target.dataset.columnKey !== draggingKey) target.classList.add("is-drop-target");
    });
    container.addEventListener("drop", event => {
      event.preventDefault();
      const target = event.target.closest("[data-column-key]");
      container.querySelectorAll(".is-drop-target").forEach(el => el.classList.remove("is-drop-target"));
      const dragged = draggingKey;
      draggingKey = null;
      if (!dragged || !target || target.dataset.columnKey === dragged) return;
      // `th[data-column-key]` (no el genérico `[data-column-key]`): el
      // primitivo de resize (initResizableColumns, abajo) marca el <col> de
      // cada columna con la MISMA key para poder ubicarlo por identidad, no
      // por posición — sin este filtro, un contenedor con resize habilitado
      // reconstruiría el orden contando CADA columna dos veces (el <th> del
      // header y su <col> en <colgroup>).
      const order = [...container.querySelectorAll("th[data-column-key]")].map(th => th.dataset.columnKey);
      const fromIdx = order.indexOf(dragged);
      if (fromIdx === -1) return;
      order.splice(fromIdx, 1);
      order.splice(order.indexOf(target.dataset.columnKey), 0, dragged);
      try { window.localStorage.setItem(storageKey, JSON.stringify(order)); } catch (e) { /* privado/cuota: no persiste, no rompe */ }
      container.dispatchEvent(new CustomEvent("dw:columns-reordered", { detail: { order }, bubbles: true }));
    });
  });
}
// Lectura fail-closed de la preferencia guardada: `validKeys` es el
// catálogo COMPLETO que el caller espera — cualquier drift (falta una key,
// sobra una, duplicados) descarta la preferencia entera y vuelve a
// `null` (el caller decide su propio default, este helper no lo conoce).
function readStoredColumnOrder(key, validKeys) {
  try {
    const raw = window.localStorage.getItem(`dw-col-order:${key}`);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return null;
    const valid = parsed.filter(k => validKeys.includes(k));
    if (valid.length !== validKeys.length || new Set(valid).size !== valid.length) return null;
    return valid;
  } catch (e) {
    return null;
  }
}

// Redimensionado horizontal de columnas — MISMO idioma/alcance que
// initDraggableColumns (arriba): genérico, opt-in por atributo, no sabe nada
// de negocio. Cualquier contenedor [data-resizable-columns-key="algo"] con
// headers <th data-column-key="..."> adentro queda redimensionable gratis —
// el PRIMITIVO inyecta el handle (nunca el consumidor): el opt-in es un solo
// atributo en el contenedor, sin markup extra por columna. Puede convivir en
// el MISMO contenedor que data-draggable-columns-key (drag y resize son dos
// capabilities independientes, cada tabla decide cuáles habilita) o vivir
// solo.
//
// A propósito NO dispara un evento de "repintar" como el reorder: el ancho
// se aplica en vivo directo sobre el <col> de esa columna (identificado por
// `data-column-key`, nunca por posición/nth-child — evita que un reorder
// previo deje el resize apuntando al <col> equivocado) — no hay <tbody> que
// reconstruir para un cambio de ancho. Sólo persiste
// (`dw-col-widths:<key>`, mismo namespace que dw-col-order) para que el
// PRÓXIMO render completo —el caller arma su propio <colgroup>— lo lea con
// `readStoredColumnWidths` y lo aplique como default.
function initResizableColumns(scope) {
  scope.querySelectorAll("[data-resizable-columns-key]").forEach(container => {
    if (container.dataset.resizeColsWired) return;
    container.dataset.resizeColsWired = "1";
    const storageKey = `dw-col-widths:${container.dataset.resizableColumnsKey}`;
    // `th[data-column-key]`: el handle es una decoración de HEADER, nunca de
    // su <col> asociado en <colgroup> (que también lleva `data-column-key`,
    // para que el resize lo ubique por identidad — ver el pointerdown de
    // abajo). Un <col> ni siquiera puede recibir hijos de forma útil en un
    // browser real; MISMO criterio que el filtro agregado en
    // initDraggableColumns.
    [...container.querySelectorAll("th[data-column-key]")].forEach(th => {
      if (th.querySelector(":scope > .col-resize-handle")) return;
      const handle = document.createElement("span");
      handle.className = "col-resize-handle";
      handle.setAttribute("aria-hidden", "true");
      // `draggable="false"` explícito: el <th> ancestro puede tener
      // draggable="true" (reorder, ver initDraggableColumns arriba) — sin
      // esto, empezar a arrastrar DESDE el handle dispararía el dragstart
      // nativo del <th> y arrancaría un reorder en vez de un resize. Esto
      // es lo que hace que el browser NUNCA emita dragstart para un gesto
      // iniciado acá — no una convención nuestra a mantener a mano.
      handle.draggable = false;
      // Frontera EXPLÍCITA (no un timing hack): un click que se origine en
      // el handle nunca debe llegar al header (que puede tener su propio
      // sort-on-click delegado en document) ni a ningún delegado de
      // fila/acción más arriba.
      handle.addEventListener("click", event => event.stopPropagation());
      th.appendChild(handle);
    });
    let resizing = null;
    container.addEventListener("pointerdown", event => {
      const handle = event.target.closest(".col-resize-handle");
      if (!handle || !container.contains(handle)) return;
      const th = handle.closest("[data-column-key]");
      if (!th) return;
      const table = container.querySelector("table");
      const col = table && table.querySelector(`colgroup > col[data-column-key="${CSS.escape(th.dataset.columnKey)}"]`);
      if (!col || !table) return;
      event.preventDefault();
      event.stopPropagation();
      // FREEZE al inicio del resize (owner T1): la tabla es `width:100%;
      // table-layout:fixed`, así que agrandar UNA columna hacía que el browser
      // REDISTRIBUYERA el ancho entre las demás -- incluidas las < N. Fix:
      // congelar TODAS las columnas a su ancho EFECTIVO actual en px (leer TODO
      // antes de escribir, para no medir un layout ya mutado) y gestionar el
      // ancho TOTAL de la tabla EXPLÍCITAMENTE en px (nunca `max-content`
      // global ni cambiar el default `width:100%` de fresh-load: esto es
      // inline y sólo existe DURANTE/DESPUÉS de un resize; un fresh render no
      // lo tiene). Con eso, mover SOLO la columna N: las < N quedan fijas (px
      // congelado), N cambia por el delta, las > N conservan su px (se corren
      // porque la tabla crece), y el total += delta (desborda vía .matrix-scroll).
      const cols = [...table.querySelectorAll("colgroup > col[data-column-key]")];
      const widths = cols.map(c => {
        const cth = container.querySelector(`th[data-column-key="${CSS.escape(c.dataset.columnKey)}"]`);
        return Math.round((cth || c).getBoundingClientRect().width);
      });
      cols.forEach((c, i) => { c.style.width = `${widths[i]}px`; });
      // Total = suma de TODAS las columnas (keyed congeladas + no-keyed como
      // Acciones, por su ancho efectivo actual). La tabla pasa a ese px exacto.
      const baseTotal = [...table.querySelectorAll("colgroup > col")].reduce((sum, c) => {
        const idx = cols.indexOf(c);
        return sum + (idx >= 0 ? widths[idx] : Math.round(c.getBoundingClientRect().width));
      }, 0);
      table.style.width = `${baseTotal}px`;
      const startWidth = widths[cols.indexOf(col)] || Math.round(th.getBoundingClientRect().width);
      resizing = { key: th.dataset.columnKey, startX: event.clientX, startWidth, col, table, baseTotal, handle };
      handle.classList.add("is-resizing");
      try { handle.setPointerCapture(event.pointerId); } catch (e) { /* browsers viejos: el drag sigue andando sin capture */ }
    });
    container.addEventListener("pointermove", event => {
      if (!resizing) return;
      const next = Math.max(60, Math.round(resizing.startWidth + (event.clientX - resizing.startX)));
      resizing.col.style.width = `${next}px`;
      // El total crece/decrece EXACTAMENTE por el delta de la columna N; las
      // demás columnas conservan su px congelado (nunca se redistribuyen).
      resizing.table.style.width = `${resizing.baseTotal + (next - resizing.startWidth)}px`;
    });
    const finish = () => {
      if (!resizing) return;
      const { key, col, handle } = resizing;
      handle.classList.remove("is-resizing");
      const widths = Object.assign({}, readStoredColumnWidths(container.dataset.resizableColumnsKey) || {});
      widths[key] = col.style.width;
      // Contrato de compatibilidad de persistencia (owner post-#107 acceptance,
      // corrective T1): CADA escritura graba el ancho de CONTENEDOR vigente en
      // este momento junto con los anchos -- ver readStoredColumnWidthsEnvelope/
      // restorePersistedColumnWidths, abajo, para el porqué completo. Medido
      // ACÁ (al terminar un resize real), nunca en el restore -- es la única
      // fuente de verdad de "para qué ancho de contenedor son válidos estos px".
      const envelope = {
        schemaVersion: COLUMN_WIDTHS_SCHEMA_VERSION,
        containerWidth: container.clientWidth,
        widths,
      };
      try { window.localStorage.setItem(storageKey, JSON.stringify(envelope)); } catch (e) { /* privado/cuota: no persiste, no rompe */ }
      container.dispatchEvent(new CustomEvent("dw:columns-resized", { detail: { key, width: col.style.width }, bubbles: true }));
      resizing = null;
    };
    container.addEventListener("pointerup", finish);
    container.addEventListener("pointercancel", finish);
  });
}
// Contrato de compatibilidad de persistencia de anchos (owner post-#107
// acceptance, corrective T1: "persisted widths metadata: schema/version,
// container width at save"). ROOT CAUSE que este corrective cierra: un
// ancho persistido es un valor en PX -- válido sólo para el ancho de
// CONTENEDOR bajo el que se midió. `restorePersistedColumnWidths` (abajo)
// los re-aplicaba ciegamente sin importar el ancho ACTUAL del contenedor,
// así que un resize hecho en una ventana angosta podía "congelar" una
// tabla visiblemente angosta/con overflow evitable en una ventana ancha, y
// viceversa -- el reporte de producción de tabla "rota/comprimida".
// `COLUMN_WIDTHS_SCHEMA_VERSION` (único, acá) es el candado: cualquier
// valor sin este envelope exacto (el shape plano `{colKey:"Npx"}` de antes
// de este corrective, o cualquier versión futura distinta) se trata como
// INCOMPATIBLE y se descarta -- nunca se "adivina"/migra en caliente, la
// tabla cae a su layout responsive normal (`width:100%`, ver el fresh-load
// de siempre) en vez de arriesgar un total congelado potencialmente erróneo.
const COLUMN_WIDTHS_SCHEMA_VERSION = 1;
// Tolerancia de "mismo ancho de contenedor", RELATIVA (no un px fijo: un
// layout angosto y uno ancho no deberían compartir el mismo margen
// absoluto) -- ÚNICO lugar que la define, documentada acá, nunca repetida
// ni resuelta con CSS. 8% cubre diferencias triviales de renderizado
// (scrollbar apareciendo/desapareciendo, redondeo) sin tolerar un cambio
// real de layout (sidebar colapsado/expandido, viewport distinto).
const COLUMN_WIDTHS_TOLERANCE_RATIO = 0.08;
// Lectura de BAJO NIVEL del envelope completo (schemaVersion+containerWidth+
// widths) -- uso EXCLUSIVO de `restorePersistedColumnWidths` (necesita
// `containerWidth` para el chequeo de compatibilidad) y del `finish()` de
// arriba (necesita los `widths` previos para mergear el override nuevo).
// Cualquier shape que NO matchee el contrato exacto (incluido el plano
// `{colKey:"Npx"}` pre-corrective) es tratado como ausente -- null, nunca
// una migración implícita.
function readStoredColumnWidthsEnvelope(key) {
  try {
    const raw = window.localStorage.getItem(`dw-col-widths:${key}`);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    if (parsed.schemaVersion !== COLUMN_WIDTHS_SCHEMA_VERSION) return null;
    if (typeof parsed.containerWidth !== "number" || !(parsed.containerWidth > 0)) return null;
    if (!parsed.widths || typeof parsed.widths !== "object" || Array.isArray(parsed.widths)) return null;
    return parsed;
  } catch (e) {
    return null;
  }
}
// Lectura PÚBLICA, MISMO contrato de siempre para los consumidores
// existentes (Comercial vía `commercialColumnWidthsFor`, y cualquier test):
// devuelve el mapa plano `{colKey:"Npx"}`, o `null` si no hay preferencia
// compatible -- nunca expone `containerWidth`/`schemaVersion` acá, eso es
// un detalle del contrato de restore de TABLAS QUE CONGELAN UN TOTAL
// EXPLÍCITO (ver arriba), irrelevante para un consumidor que resuelve cada
// columna de forma independiente contra su propio default declarativo.
function readStoredColumnWidths(key) {
  const envelope = readStoredColumnWidthsEnvelope(key);
  return envelope ? envelope.widths : null;
}

// Reconstrucción COHERENTE de anchos persistidos tras reload (contrato T1
// COMPARTIDO — vive acá, en la capa del primitivo de tablas, NO en un consumidor
// puntual). Cualquier consumidor de `initResizableColumns` que restaure anchos
// EN CLIENTE debe usar esto (CCHH lo hace vía su adapter; otros que arman el
// <colgroup> server-side pueden llamarlo igual si quieren el total explícito).
// Si hay overrides COMPATIBLES guardados (ver readStoredColumnWidthsEnvelope):
// aplica cada override a su columna, congela las demás a su ancho efectivo
// actual en px (medido en el <th> por identidad; columnas sin key —p. ej.
// Acciones— por su propio <col>), y fija el TOTAL EXPLÍCITO de la tabla =
// suma. Reconstruye EXACTAMENTE el estado post-resize (mismo contrato que
// el resize en vivo), nunca el `width:100%` que redistribuye. Lee TODOS
// los anchos antes de mutar (no medir un layout ya movido). Devuelve true
// si aplicó algo. `container` = el elemento con `data-resizable-columns-key`
// (el mismo scope que usa initResizableColumns).
//
// Chequeo de compatibilidad (owner post-#107 acceptance, corrective T1):
// ANTES de aplicar nada, compara el ancho de contenedor ACTUAL contra el
// que quedó grabado junto a los anchos. Fuera de tolerancia (ver
// COLUMN_WIDTHS_TOLERANCE_RATIO) = contenedor MATERIALMENTE distinto del
// que produjo esos px -- se descarta la preferencia completa (nunca se
// re-escala/adivina) y la función devuelve false sin tocar el DOM, dejando
// el layout responsive normal (`width:100%` declarativo de SSR) intacto.
// Esto es lo que hace que un resize hecho en una ventana angosta no deje
// una tabla congelada/comprimida en una ventana ancha (o viceversa) --
// el reporte de producción que este corrective cierra.
function restorePersistedColumnWidths(container) {
  if (!container || typeof readStoredColumnWidthsEnvelope !== "function") return false;
  const key = container.dataset ? container.dataset.resizableColumnsKey : null;
  if (!key) return false;
  const envelope = readStoredColumnWidthsEnvelope(key);
  if (!envelope || !Object.keys(envelope.widths).length) return false;
  const currentWidth = container.clientWidth;
  const savedWidth = envelope.containerWidth;
  if (Math.abs(currentWidth - savedWidth) > savedWidth * COLUMN_WIDTHS_TOLERANCE_RATIO) {
    return false;
  }
  const stored = envelope.widths;
  const table = container.querySelector("table");
  if (!table) return false;
  const cols = [].slice.call(table.querySelectorAll("colgroup > col"));
  const targets = cols.map(function (col) {
    const ck = col.getAttribute("data-column-key");
    if (ck && stored[ck]) return stored[ck];
    const measured = ck ? container.querySelector('th[data-column-key="' + ck + '"]') : col;
    return Math.round((measured || col).getBoundingClientRect().width) + "px";
  });
  let total = 0;
  cols.forEach(function (col, i) { col.style.width = targets[i]; total += parseInt(targets[i], 10) || 0; });
  table.style.width = total + "px";
  return true;
}

// Panel interactivo de "Eventos y catálogo": clic en un evento de la izquierda →
// muestra su desglose (precalculado en summary.event_breakdowns) a la derecha.
// Sin request extra; el swap de htmx recrea el grupo, por eso se re-cablea en
// cada htmx:load igual que initDashboardSections.
//
// `openFirst` (KPI → Contextual Drilldown, ver HANDOFF): default `true`
// preserva el comportamiento histórico de "Eventos y catálogo" (auto-abre el
// primer trigger al cablear, "el evento más frecuente"). Un caller con un
// grupo que debe arrancar CERRADO (ver commercial.js — la tarjeta
// "Oportunidades activas" no debe auto-abrir su análisis al cargar el
// dashboard) pasa `{openFirst:false}`; el estado inicial hidden/is-active en
// ese caso lo define el propio markup, no esta función.
function initEventBreakdown(scope, { openFirst = true } = {}) {
  scope.querySelectorAll("[data-breakdown-group]").forEach(group => {
    const rows = [...group.querySelectorAll("[data-event]")];
    const panels = [...group.querySelectorAll("[data-breakdown]")];
    // Dimensión OPCIONAL de módulo. SÓLO Métricas de Uso la declara (emite
    // paneles con `data-breakdown-module`); Comercial y Reporting consumen
    // este mismo motor sin ningún módulo, así que para ellos la condición es
    // vacua y el comportamiento no cambia — incluido que `.is-active` se
    // marque por VALOR de atributo en TODOS los triggers de ese valor.
    //
    // Sin esto el panel se resolvía por NOMBRE DE EVENTO solo: con un módulo
    // filtrado a la izquierda, la derecha seguía mostrando el universo global
    // del evento (175 vs 978 en vivo) — y `module_viewed` existe a propósito
    // en Copiloto, Operaciones y Comercial.
    const show = ev => {
      group.dataset.activeEvent = ev || "";
      const mod = group.dataset.activeModule || "";
      panels.forEach(p => {
        const suyo = p.dataset.breakdownModule;
        p.hidden = !(p.dataset.breakdown === ev
          && (suyo === undefined || suyo === (ev ? mod : "")));
      });
      rows.forEach(r => {
        const dentro = r.closest("[data-module-breakdown]");
        const suyo = dentro ? (dentro.dataset.moduleBreakdown || "") : null;
        r.classList.toggle("is-active",
          r.dataset.event === ev && (suyo === null || suyo === mod));
      });
    };
    // La referencia vive en el NODO del grupo, no en un símbolo de módulo:
    // el motor tiene que seguir siendo evaluable EN AISLAMIENTO — hay harnesses
    // que extraen esta función sola, y un símbolo de módulo la rompería ahí.
    group.dwBreakdownShow = show;
    // Clic en la fila YA activa la des-selecciona (vuelve al estado "sin
    // desglose elegido") en vez de dejarla pegada — mismo espíritu que el
    // reset del filtro de módulo.
    rows.forEach(r => {
      r.addEventListener("click", () => show(r.classList.contains("is-active") ? "" : r.dataset.event));
      r.addEventListener("keydown", event => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          show(r.classList.contains("is-active") ? "" : r.dataset.event);
        }
      });
    });
    // Botón "✕" explícito en el header de cada panel (mismo patrón visible que
    // el reset del filtro de módulo) — alternativa al toggle de la fila.
    group.querySelectorAll("[data-event-reset]").forEach(btn => {
      btn.addEventListener("click", () => show(""));
    });
    if (openFirst && rows.length) show(rows[0].dataset.event);  // por defecto: el evento más frecuente
  });
}
document.body.addEventListener("htmx:load", () => initEventBreakdown(document));

// Conecta "Módulos y usuarios" con "Eventos y catálogo": clic en un módulo filtra
// la lista "Eventos por tipo" a los eventos de ese módulo. A diferencia de
// initEventBreakdown, trigger y paneles NO comparten un ancestro [data-breakdown-group]
// (viven en <details> distintos), así que acá se buscan en todo `scope` y se
// conectan por el valor del atributo, no por jerarquía DOM.
function initModuleFilter(scope) {
  const rows = [...scope.querySelectorAll("[data-module]")];
  const panels = [...scope.querySelectorAll("[data-module-breakdown]")];
  if (!rows.length || !panels.length) return;
  const labelWrap = scope.querySelector("[data-module-filter-label]");
  const labelText = labelWrap ? labelWrap.querySelector("strong") : null;
  const resetBtn = labelWrap ? labelWrap.querySelector("[data-module-reset]") : null;
  // Eventos disponibles DENTRO de un módulo (las filas de su panel).
  const eventosDe = mod => {
    const panel = panels.find(p => (p.dataset.moduleBreakdown || "") === mod);
    return panel ? [...panel.querySelectorAll("[data-event]")].map(r => r.dataset.event) : [];
  };
  const show = mod => {
    panels.forEach(p => { p.hidden = p.dataset.moduleBreakdown !== mod; });
    rows.forEach(r => r.classList.toggle("is-active", !!mod && r.dataset.module === mod));
    if (labelWrap) {
      labelWrap.hidden = !mod;
      if (mod && labelText) labelText.textContent = mod;
    }
    // LA ETAPA SIGUIENTE HEREDA EL MÓDULO. Antes no pasaba: los dos toggles
    // eran independientes y sólo coincidían en pantalla.
    //
    // Se busca `[data-cascade-module]` —opt-in EXPLÍCITO del template de
    // Métricas— y no `[data-breakdown-group]`: ese selector es del motor y de
    // nadie más, que es lo que permite que Comercial y Reporting compartan el
    // motor sin enterarse de que existe una dimensión de módulo.
    scope.querySelectorAll("[data-cascade-module]").forEach(group => {
      group.dataset.activeModule = mod;
      // El evento elegido puede no existir en el módulo nuevo: se conserva si
      // existe, y si no cae al más frecuente de ESE módulo — mismo criterio
      // que el `openFirst` de siempre, ahora dentro del módulo activo.
      const disponibles = eventosDe(mod);
      const actual = group.dataset.activeEvent || "";
      const elegido = disponibles.includes(actual) ? actual : (disponibles[0] || "");
      if (group.dwBreakdownShow) group.dwBreakdownShow(elegido);
    });
  };
  rows.forEach(r => {
    r.addEventListener("click", () => show(r.dataset.module));
    r.addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); show(r.dataset.module); }
    });
  });
  if (resetBtn) resetBtn.addEventListener("click", () => show(""));
  show(""); // default: vista global, sin módulo seleccionado
}

// Ámbito/Área (Activity Dashboard Hub-wide, admin_activity_content.html):
// una selección vacía enviada al server es indistinguible de "sin filtro
// tocado todavía" (ambos mandan cero parámetros `scope`/`area`) — el server
// ya cae a "todos" en ese caso, pero eso deja al owner sin poder realmente
// destildar el último ámbito/área de un grupo (su clic se revierte a "todo
// tildado" en vez de "nada"). Contrato preferido (ver HANDOFF, reconciliación
// A2): jamás permitir destildar la ÚLTIMA opción tildada de un grupo — así el
// estado ambiguo (cero seleccionados) nunca llega a enviarse. Delegado en
// `document` (no en `#activity-dashboard`): sobrevive el swap HTMX del
// fragmento entero sin necesidad de re-cablearse en cada htmx:load. Filtra
// por `name` ("scope"/"area") para no interferir con los checkboxes de
// Estado de Staffing (Alpine `x-model`, sin atributo `name`). CAPTURE (no
// bubble): el form de Actividad auto-aplica con `hx-trigger="change from:
// .dashboard-checkbox-group input"` — si este guard corriera en bubble
// perdería la carrera contra el listener de htmx (atado directo al input,
// dispara ANTES de burbujear a `document`) y htmx llegaría a mandar el
// request con el checkbox todavía destildado. En capture, este handler
// corre primero y revierte el `checked` ANTES de que htmx lo lea.
document.addEventListener("change", event => {
  const input = event.target;
  if (!(input instanceof HTMLInputElement) || input.type !== "checkbox") return;
  if (input.name !== "scope" && input.name !== "area") return;
  if (input.checked) return;
  const group = input.closest(".dashboard-checkbox-group");
  if (!group) return;
  const stillChecked = group.querySelector(`input[name="${input.name}"]:checked`);
  if (!stillChecked) input.checked = true;
}, { capture: true });

// Asignaciones Dashboard v1 (Operaciones/Staffing) — Alpine x-data, mismo
// estilo que copilotLayout(). `payload` llega YA normalizado desde el
// server (webapp.services.staffing_service.build_dashboard_payload):
// { assignments: [...], horizon: [...] }. A partir de acá, TODO el
// filtrado (KPIs/timeline/disponibilidad/detalle) es client-side sobre
// `assignments` — no hay una segunda request a Graph por cambio de filtro.
function staffingDashboard(payload) {
  const assignments = (payload && payload.assignments) || [];
  const horizon = (payload && payload.horizon) || [];
  const todayMarkerPct = (payload && payload.today_marker_pct != null) ? payload.today_marker_pct : null;

  // Estados conocidos (Batch 3, reales) + cualquier otro que aparezca en el
  // dato real (nunca se inventa uno que no existe, y nunca se pierde uno
  // real fuera de los 3 conocidos). Blank -> "Sin estado" — sigue siendo
  // una opción SELECCIONABLE, pero (corrective pass, review con dato real)
  // el default congelado es EXCLUSIVAMENTE Activo+Tentativo: Inactivo y
  // "Sin estado" (y cualquier otro status futuro) arrancan destildados.
  const KNOWN_STATUSES = ["Activo", "Tentativo", "Inactivo"];
  const presentStatuses = [...new Set(assignments.map(a => a.status || "Sin estado"))];
  const statusOptions = [...new Set([...KNOWN_STATUSES, ...presentStatuses])];

  // Staffing Director Priority V2 (ver HANDOFF): default ahora es
  // EXCLUSIVAMENTE Activo — Tentativo pasa a disponible-pero-no-tildado por
  // default (sigue 100% funcional/seleccionable, sin restricción de carga:
  // la carga inicial del server ya lo incluye, solo Inactivo queda excluido
  // del fetch inicial — ver `_loadInactiveHistory`).
  const defaultStatusFilter = () =>
    Object.fromEntries(statusOptions.map(s => [s, s === "Activo"]));

  // Staffing Approved Absence Overlay (ver HANDOFF) — filtro Concepto:
  // EXACTAMENTE 4 checkboxes fijos (nunca derivados del dato, a diferencia
  // de Estado) — los 4 conceptos canónicos que ya clasifica el server (ver
  // staffing_repository._classify_absence_concept). Sin checkbox "Todos" a
  // propósito (ver HANDOFF: la fila extra rompería el límite de 4 filas por
  // columna del panel de filtros, ya establecido por Estado) — los 4 ya
  // cubren el universo completo, así que "Todos" sería redundante con
  // "los 4 tildados". Default: los 4 tildados (a diferencia de Estado, que
  // arranca solo con Activo).
  const CONCEPT_OPTIONS = ["Vacaciones", "Danwor Day", "Cumpleaños", "Otros"];
  const defaultConceptFilter = () =>
    Object.fromEntries(CONCEPT_OPTIONS.map(c => [c, true]));

  // Techo del lookback de ausencias — DINÁMICO (Dirección corrective, ver
  // HANDOFF), ya NO un número fijo. El backend hidrata ausencias sobre el
  // MISMO horizonte que Asignaciones (`compute_horizon_months`,
  // staffing_service.py) y ahora lo declara EXPLÍCITO en el payload del
  // overlay (`absence_history_start`/`absence_lookback_max_days`, ver
  // `_loadAbsences`) — este archivo NUNCA reimplementa "primer día del mes
  // anterior", sólo LEE ese número. 28 fue el PISO mínimo garantizado
  // (peor caso: "hoy" el 1º de marzo de un año no bisiesto) que motivó el
  // discovery original — dejó de ser el contrato productivo: el máximo
  // real puede ser mayor (hasta ~59 días según dónde caiga "hoy" en el
  // mes) y la UI debe reflejar exactamente eso, nunca menos ni más de lo
  // que el backend confirma. `absenceLookbackMaxDays` (estado, ver abajo)
  // arranca en un valor SEGURO antes de hidratar (10, el propio default —
  // nunca promete más de lo que el default ya necesita) y se actualiza en
  // cuanto la respuesta real llega. El `<input max>` del template usa
  // `:max="absenceLookbackMaxDays"` — MISMA fuente runtime que este
  // clamp, nunca un segundo número fijo en Jinja (a diferencia del 365 de
  // `projectionDays`, que sí es fijo porque su dataset no tiene este
  // límite de historia garantizada).
  //
  // OWNER CORRECTIVE previo (ver HANDOFF): la primera versión de este
  // control ("Vacaciones · últimos N días") filtraba SOLO el concepto
  // "Vacaciones" — el owner detectó en su propia captura que el lane
  // seguía mostrando registros VIEJOS de OTROS conceptos (Danwor Day) y
  // revocó esa decisión: el control es del LANE COMPLETO de ausencias (los
  // 4 conceptos), independiente de los checkboxes de Concepto (N = cuánto
  // histórico veo; checkboxes = qué conceptos veo — dos preguntas
  // ortogonales). Renombrado de
  // `vacationLookbackDays`/`vacationCutoffISO`/`setVacationLookbackDays` a
  // `absenceLookbackDays`/`absenceCutoffISO`/`setAbsenceLookbackDays` para
  // que el nombre describa la semántica real.

  // Paleta determinística de identidad (project_id, o assignment_id si no
  // hay proyecto) — NUNCA aleatoria, mismo hash en cada render/filtro. El
  // color identifica la asignación/proyecto; el estado se superpone aparte
  // (borde/opacidad, ver CSS) — nunca se pierde la semántica de estado por
  // pintar identidad.
  const PALETTE_SIZE = 6;
  function paletteIndex(row) {
    const seed = row.project_id || row.assignment_id || "";
    let hash = 0;
    for (let i = 0; i < seed.length; i++) hash = (hash * 31 + seed.charCodeAt(i)) >>> 0;
    return hash % PALETTE_SIZE;
  }

  // Staffing Director Priority V2 (ver HANDOFF) — helpers de fecha/efectividad
  // puros, SIN objetos Date para comparar (las fechas del payload ya son
  // "YYYY-MM-DD" cero-rellenadas, comparan correctamente como string —
  // nunca se necesita parsear a Date para eso). `Date` solo se usa para
  // conocer HOY/HOY+N en la zona horaria LOCAL del browser — siempre
  // leyendo componentes locales (getFullYear/getMonth/getDate), nunca
  // `toISOString()` (that convierte a UTC y puede correr un día — el mismo
  // bug de off-by-one que pide evitar el requerimiento de formato de fecha).
  function _pad2(n) { return String(n).padStart(2, "0"); }
  function _isoFromLocalDate(d) { return `${d.getFullYear()}-${_pad2(d.getMonth() + 1)}-${_pad2(d.getDate())}`; }
  function _todayISO() { return _isoFromLocalDate(new Date()); }
  function _addDaysISO(iso, days) {
    const [y, m, d] = iso.split("-").map(Number);
    const dt = new Date(y, m - 1, d);
    dt.setDate(dt.getDate() + days);
    return _isoFromLocalDate(dt);
  }
  function _localDateFromISO(iso) {
    const [y, m, d] = iso.split("-").map(Number);
    return new Date(y, m - 1, d);
  }
  // Ventana de ausencias — CLIPPING VISUAL (Dirección corrective, ver
  // HANDOFF — aplica a los 4 conceptos, no sólo Vacaciones, ver
  // `absenceLookbackMaxDays` más abajo). `start_pct`/`end_pct` llegan
  // PRECOMPUTADOS server-side
  // (`compute_timeline_position`, staffing_service.py) sobre un horizonte
  // cuyo mes de arranque el payload NUNCA manda como fecha — solo como
  // `horizon[i].label` (texto para humanos). Recomputar `visible_start_pct`
  // desde cero necesitaría esa fecha ancla, así que en vez de eso se
  // DERIVA algebraicamente a partir de un punto YA confiable: el propio
  // `start_pct` que el server calculó para ESA ausencia. La fórmula real
  // (`_date_position_fraction`, Python) es:
  //     pct(d) = (month_offset(d) + day_fraction(d)) / total_months * 100
  // `month_offset(d)` es un desplazamiento ENTERO de meses respecto del
  // ancla del horizonte — la diferencia `month_offset(d2) - month_offset(d1)`
  // es EXACTAMENTE la cantidad de meses de calendario entre d1 y d2
  // (`_monthsBetween`, aritmética pura, el ancla se cancela algebraicamente
  // y nunca hace falta conocerla). `day_fraction(d)` sólo depende del PROPIO
  // mes de `d` (día-1 sobre días-del-mes) — MISMA fórmula exacta que usa
  // Python (`(d - month_start).days / days_in_month`), reimplementada acá
  // con aritmética de fecha LOCAL pura (sin UTC, MISMO criterio que
  // `_isoFromLocalDate`). Con esos dos ingredientes:
  //     pct(cutoff) = start_pct_real + [_monthsBetween(start, cutoff) +
  //       (_dayFractionOfMonth(cutoff) - _dayFractionOfMonth(start))] /
  //       total_months * 100
  // EXACTA (no aproximada — verificado algebraicamente y con un caso
  // cruzando mes de distinta longitud, ver
  // staffing_timeline_vacation_lookback.node.js), sin pedir ningún dato
  // nuevo al backend y sin tocar `start_pct` del objeto fuente (se aplica
  // sobre una COPIA en `filteredAbsences`, ver ahí — nunca se muta
  // `this.absences`).
  function _monthsBetween(a, b) {
    return (b.getFullYear() - a.getFullYear()) * 12 + (b.getMonth() - a.getMonth());
  }
  function _dayFractionOfMonth(d) {
    const daysInMonth = new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate();
    return (d.getDate() - 1) / daysInMonth;
  }
  function _visibleAbsenceStartPct(absence, cutoffISO, totalMonths) {
    const startDate = _localDateFromISO(absence.start_date);
    const cutoffDate = _localDateFromISO(cutoffISO);
    const monthsDelta = _monthsBetween(startDate, cutoffDate);
    const deltaFraction = monthsDelta + (_dayFractionOfMonth(cutoffDate) - _dayFractionOfMonth(startDate));
    return absence.start_pct + (deltaFraction / totalMonths) * 100;
  }
  // Asignación "efectiva" en una fecha puntual: abierta hacia atrás sin
  // start_date, abierta hacia adelante sin end_date — mismo criterio
  // determinístico que ya usa `compute_timeline_position` (Python) para
  // asignaciones sin un extremo conocido, aplicado acá a una comparación
  // puntual en vez de un rango visible.
  function _isEffectiveOn(row, iso) {
    return (!row.start_date || row.start_date <= iso) && (!row.end_date || row.end_date >= iso);
  }
  function _hoursIfEffective(row, iso) {
    if (!_isEffectiveOn(row, iso)) return 0;
    if (row.allocation_unit === "hours_per_month" && row.allocation_value != null) return row.allocation_value;
    return 0;
  }
  // Detalle agregado (Staffing Director Priority V2, ver HANDOFF) — "--"
  // SOLO cuando los hijos genuinamente DIFIEREN en ese campo, nunca porque
  // el campo "no es agregable en general" (Cliente/Proyecto/Desde/Hasta SÍ
  // se informan a nivel agregado cuando el grupo entero comparte un único
  // valor — incluyendo un único "(sin resolver)" compartido, o fechas
  // iguales aunque Cliente/Proyecto difieran). `undefined` = sin consenso
  // (los valores reales SÍ pueden ser `null`, p. ej. sin fecha — por eso
  // `undefined`, nunca `null`, marca "no agregable").
  function _singleValue(values) {
    const unique = new Set(values);
    return unique.size === 1 ? [...unique][0] : undefined;
  }

  return {
    assignments,
    horizon,
    statusOptions,
    // Concepto (Approved Absence Overlay, ver HANDOFF) — array FIJO (4
    // valores, nunca derivado del dato como statusOptions) expuesto para
    // que el template itere el checkbox-group con `x-for`.
    conceptOptions: CONCEPT_OPTIONS,
    todayMarkerPct,
    // Colapso de grupos de consultor en el timeline — SOLO estado visual de
    // presentación (nunca filtra filteredAssignments). Vacío = todo
    // expandido (default). Keyed por el mismo `key` de timelineGroups.
    collapsedConsultants: {},
    // null | "collapsed" | "expanded" — ver `toggleGrouping`/`collapseAllConsultants`.
    timelineGlobalCollapseIntent: null,
    filters: {
      consultant: "",
      project: "",
      client: "",
      availability: "",
      // Default Director Priority V2: SOLO Activo. Tentativo/Inactivo/"Sin
      // estado" quedan disponibles pero NO seleccionados — decisión
      // explícita y visible, no un descarte silencioso (ver HANDOFF).
      status: defaultStatusFilter(),
      // Concepto (Approved Absence Overlay, ver HANDOFF) — los 4 tildados
      // por default. Filtra ÚNICAMENTE `this.absences` (client-side, sin
      // Graph/refetch) — nunca `assignments`, nunca entra en
      // `_reconcileSelections()`/`facetedAssignments()` (esos son
      // exclusivos del universo de Asignaciones).
      concept: defaultConceptFilter(),
      // Summary Tile Filters corrective (ver HANDOFF — homologación del
      // patrón clickeable de Commercial): "Activos vencidos" no tiene
      // dropdown equivalente (a diferencia de `availability`, que YA es un
      // filtro real reusado tal cual por las tarjetas Full Time/Part
      // Time — ver `activeSummaryTile`) — vive en `filters` igual que
      // cualquier otro filtro real, mismo mecanismo de
      // `facetedAssignments`, nunca un segundo pipeline de filtrado.
      activosVencidos: false,
    },

    // Lazy history (Staffing Director Priority V2, ver HANDOFF): el fetch
    // inicial del server YA excluye Inactivo — `historyLoaded` marca si el
    // delta histórico ya se pidió/mergeó en esta sesión de dashboard
    // (nunca se vuelve a pedir tras la primera vez, togglear Inactivo de
    // ida y vuelta no repite el request). `historyLoading` (contrato
    // CONGELADO pre-PR, ver HANDOFF — antes privado `_historyLoading`,
    // ahora expuesto porque el template necesita mostrar feedback visible
    // mientras carga) es SOLO true durante el fetch en curso.
    // `historyLoadError` es solo presentación (mensaje inline), nunca
    // bloquea el resto del dashboard — y NUNCA marca `historyLoaded=true`
    // tras una falla (fail-soft: un click posterior puede reintentar).
    historyLoaded: false,
    historyLoadError: false,
    historyLoading: false,

    // Ausencias aprobadas (Staffing Approved Absence Overlay, ver HANDOFF
    // — generalización del Vacation Timeline Overlay original) — carga
    // LAZY e INDEPENDIENTE de las asignaciones (arrancan a cargar en init(),
    // sin bloquear el render de asignaciones que ya llegó en el payload
    // inicial). ADITIVO/presentacional puro: una falla acá NUNCA toca
    // filteredAssignments/kpis/timelineGroups de asignaciones — solo omite
    // el overlay y muestra su propio aviso no bloqueante.
    absences: [],
    absencesLoading: false,
    absencesLoadError: false,

    // Grouping switch (Consultor ↔ Proyecto) — mismo `filteredAssignments`,
    // sin refetch. Ver `timelineGroups`/`groupHoursSuffix`/`rowGroupSecondaryLabel`.
    groupBy: "consultant",

    // Proyección de capacidad (+N días) — control numérico, recalcula
    // sobre el dataset ya cargado, sin Graph.
    projectionDays: 35,

    // Ventana de ausencias del Timeline (Dirección corrective, ver
    // HANDOFF) — patrón EQUIVALENTE a `projectionDays` de arriba (control
    // numérico independiente, recalcula sobre `this.absences` ya cargado,
    // sin Graph) pero DELIBERADAMENTE un estado propio, nunca acoplado:
    // `projectionDays` mira hacia ADELANTE para la columna de Asignación
    // proyectada del detalle; este selector mira hacia ATRÁS
    // (lookback) para cuánto historial reciente de ausencias se mantiene
    // visible en el LANE del Timeline — dos preguntas de negocio distintas
    // sobre dos datasets distintos (asignaciones vs. ausencias). Default 10
    // (owner). NO participa de `resetFilters()` — MISMO criterio que
    // `projectionDays`, que tampoco se toca ahí (ver `resetFilters`).
    // OWNER CORRECTIVE (post-smoke, ver HANDOFF): renombrado de
    // `vacationLookbackDays` — el control aplica a los 4 conceptos del
    // lane (Vacaciones/Danwor Day/Cumpleaños/Otros), no sólo Vacaciones;
    // el nombre viejo describía mal la semántica real.
    absenceLookbackDays: 10,
    // Techo DINÁMICO del selector de arriba (Dirección corrective, ver
    // HANDOFF) — arranca en un valor SEGURO antes de hidratar (10, el
    // propio default: nunca promete más historia de la que el default ya
    // necesita) y se actualiza al resolver `_loadAbsences()` con
    // `data.absence_lookback_max_days` (MISMA fuente que decidió qué
    // ausencias se hidrataron server-side — nunca una fórmula propia).
    absenceLookbackMaxDays: 10,

    // Detalle agregado por consultor — expansión de presentación pura
    // (nunca filtra), keyed igual que collapsedConsultants/timelineGroups.
    expandedDetailGroups: {},

    init() {
      // Si el usuario elige un consultor puntual, ese grupo nunca debe
      // quedar oculto por un colapso previo — el filtro nunca debe
      // "pelear" contra el estado de presentación.
      this.$watch("filters.consultant", (value) => {
        if (value) this.collapsedConsultants[value] = false;
      });
      // Filtrado FACETADO (revisión con dato real, ver HANDOFF): cambiar
      // CUALQUIER filtro puede volver imposible la selección vigente de
      // OTRO (p. ej. Estado deja fuera todas las filas del Proyecto
      // elegido) — cada cambio revalida las 4 selecciones "entidad" contra
      // su propio option-set YA recalculado, y resetea a "Todos" la que ya
      // no aparece ahí, en vez de dejar una selección fantasma que
      // produzca un vacío inexplicado.
      this.$watch("filters.consultant", () => this._reconcileSelections());
      this.$watch("filters.project", () => this._reconcileSelections());
      this.$watch("filters.client", () => this._reconcileSelections());
      this.$watch("filters.availability", () => this._reconcileSelections());
      this.$watch("filters.status", () => this._reconcileSelections());

      // Summary Tile Filters (ver HANDOFF) — backstop de mutua exclusión
      // para el camino MANUAL (§5.D del ticket: elegir Full Time/Part Time
      // desde el `<select>` Disponibilidad, vía `x-model`, debe tener el
      // MISMO efecto que clickear la tarjeta — ese camino nunca pasa por
      // `clickFullTimeTile()`/`clickPartTimeTile()`, así que su propia
      // mutua exclusión inline no alcanza acá). El camino de CLIC ya se
      // resuelve solo en los handlers (ver `clickFullTimeTile`/
      // `clickPartTimeTile`/`clickActivosVencidosTile`, arriba) — estos
      // watchers son additivos, no la única fuente de la regla. Simétrico
      // a propósito, sin ciclo (Alpine no re-dispara un watcher si el
      // valor asignado ya es el vigente).
      this.$watch("filters.availability", (value) => {
        if (value === "Full Time" || value === "Part Time") this.filters.activosVencidos = false;
      });
      this.$watch("filters.activosVencidos", (value) => {
        if (value) this.filters.availability = "";
      });
      // Activos vencidos exige Status Activo por definición
      // (`is_activo_vencido`, staffing_service.py) — la tarjeta misma ya
      // está oculta sin Activo tildado (`x-show="filters.status.Activo"`,
      // ver template), pero el FILTRO no debía quedar encendido en
      // silencio detrás de una tarjeta invisible (produciría panel vacío
      // sin explicación si Activo se destilda después de activar la
      // tarjeta) — mismo criterio defensivo que el watcher de Inactivo,
      // abajo.
      this.$watch("filters.status.Activo", (value) => {
        if (!value) this.filters.activosVencidos = false;
      });

      // Lazy history (ver HANDOFF): la PRIMERA vez que el usuario tilda
      // Inactivo, dispara el fetch del delta histórico — togglear de ida y
      // vuelta después nunca vuelve a pedirlo (`historyLoaded`).
      this.$watch("filters.status.Inactivo", (value) => {
        if (value && !this.historyLoaded) this._loadInactiveHistory();
      });

      // Ausencias (ver HANDOFF): arranca sola, independiente de cualquier
      // interacción del usuario — a diferencia del historial de Inactivo
      // (que espera el primer toggle), el overlay de ausencias es
      // contextual y se carga apenas el dashboard existe.
      this._loadAbsences();
    },

    async _loadAbsences() {
      this.absencesLoading = true;
      this.absencesLoadError = false;
      try {
        const token = await dwGraphToken();
        // owner ticket "STAFFING — MSAL TOKEN FAILURE TELEMETRY": SOLO se
        // adjunta cuando el token vino vacío -- con token válido no hay nada
        // que explicar (MISMO criterio que el hook htmx de hub_auth.js).
        const body = new URLSearchParams({ graph_token: token });
        if (!token && window.dwLastTokenFailureDiagnostic) {
          const diagnostic = window.dwLastTokenFailureDiagnostic();
          if (diagnostic) body.set("token_diagnostic", JSON.stringify(diagnostic));
        }
        const response = await fetch("/operaciones/staffing/asignaciones/ausencias", { method: "POST", body });
        const data = await response.json();
        if (data.state === "ok") {
          this.absences = data.absences || [];
          // Techo dinámico del lookback (Dirección corrective, ver
          // HANDOFF) — `data.absence_lookback_max_days` es la MISMA fuente
          // que ya decidió qué `absences` se hidrataron (nunca una
          // fórmula propia acá). "Si el máximo cambia entre
          // hidrataciones" (owner): conservar `absenceLookbackDays` si
          // sigue siendo válido bajo el nuevo máximo, clampear sólo si lo
          // excede — nunca resetear a 10 porque llegó data nueva.
          const max = Number(data.absence_lookback_max_days);
          if (Number.isFinite(max) && max > 0) {
            this.absenceLookbackMaxDays = max;
            this.absenceLookbackDays = Math.min(max, Math.max(1, this.absenceLookbackDays));
          }
        } else {
          // Fail-soft (ver HANDOFF): las asignaciones siguen 100% usables,
          // solo se omite el overlay + aviso propio no bloqueante.
          this.absencesLoadError = true;
        }
      } catch (e) {
        this.absencesLoadError = true;
      } finally {
        this.absencesLoading = false;
      }
    },

    async _loadInactiveHistory() {
      if (this.historyLoaded || this.historyLoading) return;
      this.historyLoading = true;
      this.historyLoadError = false;
      try {
        const token = await dwGraphToken();
        // MISMO criterio que _loadAbsences (ver ahí) — owner ticket
        // "STAFFING — MSAL TOKEN FAILURE TELEMETRY".
        const body = new URLSearchParams({ graph_token: token });
        if (!token && window.dwLastTokenFailureDiagnostic) {
          const diagnostic = window.dwLastTokenFailureDiagnostic();
          if (diagnostic) body.set("token_diagnostic", JSON.stringify(diagnostic));
        }
        const response = await fetch("/operaciones/staffing/asignaciones/historial", { method: "POST", body });
        const data = await response.json();
        if (data.state === "ok") {
          // Merge por assignment_id — nunca reemplaza el array completo,
          // nunca duplica una fila ya presente (dedup determinístico).
          const seen = new Set(this.assignments.map(a => a.assignment_id));
          const fresh = (data.assignments || []).filter(a => !seen.has(a.assignment_id));
          this.assignments = [...this.assignments, ...fresh];
          this.historyLoaded = true;
        } else {
          // NUNCA marca historyLoaded=true tras una falla (fail-soft, ver
          // HANDOFF) — el próximo toggle de Inactivo puede reintentar.
          this.historyLoadError = true;
        }
      } catch (e) {
        this.historyLoadError = true;
      } finally {
        this.historyLoading = false;
      }
    },

    _reconcileSelections() {
      if (this.filters.consultant && !this.consultantOptions.some(o => o.id === this.filters.consultant)) {
        this.filters.consultant = "";
      }
      if (this.filters.project && !this.projectOptions.some(o => o.id === this.filters.project)) {
        this.filters.project = "";
      }
      if (this.filters.client && !this.clientOptions.some(o => o.id === this.filters.client)) {
        this.filters.client = "";
      }
      if (this.filters.availability && !this.availabilityOptions.includes(this.filters.availability)) {
        this.filters.availability = "";
      }
    },

    resetFilters() {
      this.filters.consultant = "";
      this.filters.project = "";
      this.filters.client = "";
      this.filters.availability = "";
      this.filters.activosVencidos = false;
      this.filters.status = defaultStatusFilter();
      this.filters.concept = defaultConceptFilter();
    },

    // ── Summary Tile Filters (ver HANDOFF — homologación del patrón ──────
    // clickeable de Commercial: tarjeta de resumen → filtra el panel →
    // "Filtrado por…" → descartable). `filters.availability` YA ES la
    // única fuente de verdad del dropdown "Disponibilidad de asignación"
    // (ver template) — las tarjetas Full Time/Part Time la reusan TAL
    // CUAL, nunca un segundo estado paralelo que pudiera desincronizarse
    // (§5 del ticket: "Use one authoritative availability-filter state").
    // "Activos vencidos" no tiene dropdown — vive en `filters.activosVencidos`
    // (ver arriba), mismo nivel que cualquier otro filtro real.
    //
    // Mutua exclusión (§6 del ticket — sólo un filtro de tarjeta activo a
    // la vez): el camino de CLIC la resuelve inline en cada handler (ver
    // `clickFullTimeTile`/`clickPartTimeTile`/`clickActivosVencidosTile`,
    // abajo); el camino MANUAL del `<select>` Disponibilidad la resuelve
    // vía `$watch` en `init()` (nunca pasa por esos handlers). `activeSummaryTile`
    // en sí es una lectura PURA de `filters` — nunca decide nada, sólo
    // deriva qué tarjeta debe verse `.is-active` ahora mismo.
    get activeSummaryTile() {
      if (this.filters.activosVencidos) return "activos_vencidos";
      if (this.filters.availability === "Full Time") return "full_time";
      if (this.filters.availability === "Part Time") return "part_time";
      return null;
    },
    get summaryTileFilterLabel() {
      const tile = this.activeSummaryTile;
      if (tile === "full_time") return "Full Time";
      if (tile === "part_time") return "Part Time";
      if (tile === "activos_vencidos") return "Activos vencidos";
      return "";
    },
    // SAME-TILE CLICK (§9 del ticket): audit de Commercial encontró DOS
    // convenciones activas — `initEventBreakdown` ([data-event], los NUEVE
    // tiles de Resumen del landing legacy) SÍ togglea a apagado al
    // reclickear la fila activa; `bar-row--clickable`/`initModuleFilter`
    // (ranking Vendedor/Cliente DENTRO de un panel ya abierto) documentan
    // EXPLÍCITAMENTE lo contrario ("SIN toggle-to-clear… limpiar es una
    // acción explícita y separada"). Estas tarjetas son estructuralmente
    // el mismo rol que los tiles de Resumen de Commercial (KPI de nivel
    // superior en la grilla `.activity-kpi-grid`, no una fila de ranking
    // dentro de un drilldown ya abierto) — se sigue esa convención:
    // togglea a apagado.
    //
    // Mutua exclusión ESCRITA ACÁ TAMBIÉN (no sólo en los `$watch` de
    // `init()`, ver abajo) — a propósito, dos caminos disparan el cambio
    // de `filters.availability`/`filters.activosVencidos` con
    // comportamiento distinto: un clic en la tarjeta (este handler,
    // determinístico, sin depender de reactividad) y un cambio MANUAL del
    // `<select>` Disponibilidad (`x-model`, nunca pasa por acá — §5.D del
    // ticket exige que tenga el MISMO efecto, así que ESE camino sí
    // necesita el watcher). Ninguna de las dos rutas es redundante con la
    // otra, cubren triggers distintos.
    clickFullTimeTile() {
      if (this.filters.availability === "Full Time") {
        this.filters.availability = "";
      } else {
        this.filters.availability = "Full Time";
        this.filters.activosVencidos = false;
      }
    },
    clickPartTimeTile() {
      if (this.filters.availability === "Part Time") {
        this.filters.availability = "";
      } else {
        this.filters.availability = "Part Time";
        this.filters.activosVencidos = false;
      }
    },
    clickActivosVencidosTile() {
      if (this.filters.activosVencidos) {
        this.filters.activosVencidos = false;
      } else {
        this.filters.activosVencidos = true;
        this.filters.availability = "";
      }
    },
    // Dismiss del chip "Filtrado por…" (§8.A del ticket) — SOLO el filtro
    // de tarjeta, nunca Consultor/Proyecto/Cliente/Estado/Concepto (§7:
    // "Do NOT silently wipe unrelated filters"). El botón "✕ Limpiar"
    // global (§8.B — la ÚNICA "X" que el audit encontró en esta pantalla,
    // ver HANDOFF) sigue apuntando a `resetFilters()`, cuyo contrato
    // PROBADO de siempre ya es "limpiar TODO" — se preserva sin cambios
    // (ahora también apaga `activosVencidos`, arriba), nunca se angosta
    // retroactivamente para esta feature.
    clearSummaryTileFilter() {
      this.filters.availability = "";
      this.filters.activosVencidos = false;
    },

    statusSlug(status) {
      const known = { Activo: "activo", Tentativo: "tentativo", Inactivo: "inactivo" };
      return known[status] || "otro";
    },

    // Variante de color del primitivo Hub compartido `.dashboard-status-badge`
    // (Phase S, ver HANDOFF — MISMO badge que Reporting, tools.css). Mapeo
    // presentacional aparte de `statusSlug` (ese sigue alimentando
    // `.staffing-timeline__row-status.is-status-*`, sin tocar el timeline).
    statusColor(status) {
      if (status === "Activo") return "green";
      if (status === "Tentativo") return "amber";
      return "grey";
    },

    paletteClass(row) {
      return "staffing-palette-" + paletteIndex(row);
    },

    // Densidad de información restaurada vs. el Power BI legado (revisión
    // PBI, ver HANDOFF): "Estado (Horas)" + "Cliente - Proyecto" como DOS
    // segmentos separados (nunca un string opaco concatenado) para permitir
    // estilos distintos por segmento. `client_name` es `None` cuando no
    // resuelve (contrato confirmado contra Graph real, ver HANDOFF) —
    // degrada con gracia a solo Proyecto, sin separador feo.
    rowStatusHoursLabel(row) {
      const status = row.status || "Sin estado";
      if (row.allocation_unit === "hours_per_month" && row.allocation_value != null) {
        return `${status} (${row.allocation_label})`;
      }
      return status;
    },
    rowClientProjectLabel(row) {
      return row.client_name ? `${row.client_name} - ${row.project_name}` : row.project_name;
    },
    rowLabelTitle(row) {
      return `${this.rowStatusHoursLabel(row)} · ${this.rowGroupSecondaryLabel(row)}`;
    },

    // Tooltip de la barra — mismos hechos ya presentes en el objeto
    // canónico de la asignación, sin pedir nada nuevo a Graph. Fechas con
    // la misma convención ya usada en el título nativo previo de la barra
    // (nunca se inventa una fecha). `barAriaLabel` es la MISMA información
    // en texto plano, para foco por teclado / lectores de pantalla — el
    // tooltip visual queda aria-hidden para no duplicar el anuncio.
    barAriaLabel(row) {
      const parts = [row.consultant_name || "(sin resolver)", row.status || "Sin estado"];
      if (row.allocation_unit === "hours_per_month" && row.allocation_value != null) {
        parts.push(row.allocation_label);
      }
      if (row.client_name) parts.push(row.client_name);
      parts.push(row.project_name);
      parts.push((this.formatDateDMY(row.start_date) || "¿inicio?") + " → " + (this.formatDateDMY(row.end_date) || "en curso"));
      return parts.join(" — ");
    },

    get todayMarker() {
      return { visible: todayMarkerPct != null, pct: todayMarkerPct };
    },

    isConsultantCollapsed(key) {
      return !!this.collapsedConsultants[key];
    },
    // `timelineGlobalCollapseIntent` (pre-PR corrective, ver HANDOFF):
    // colapso global es una dimensión INDEPENDIENTE del `groupBy` — antes
    // "Contraer todo" en Consultor + cambiar a Proyecto volvía a mostrar
    // todo expandido, porque las keys de `collapsedConsultants` son por
    // entidad (consultant_id vs project_id) y no coinciden entre modos.
    // Un toggle MANUAL de un grupo puntual limpia el intent (deja de ser
    // "todo colapsado/expandido" de forma limpia — pasa a mixto) para que
    // el próximo cambio de agrupación use el default de la agrupación
    // nueva, nunca intente mapear identidades cruzadas consultor↔proyecto.
    toggleConsultant(key) {
      this.timelineGlobalCollapseIntent = null;
      this.collapsedConsultants[key] = !this.collapsedConsultants[key];
    },
    collapseAllConsultants() {
      this.timelineGlobalCollapseIntent = "collapsed";
      const next = {};
      this.timelineGroups.forEach(g => { next[g.key] = true; });
      this.collapsedConsultants = next;
    },
    expandAllConsultants() {
      this.timelineGlobalCollapseIntent = "expanded";
      this.collapsedConsultants = {};
    },

    // Filtrado FACETADO (revisión con dato real, ver HANDOFF): cada
    // dropdown de entidad se calcula sobre el universo de asignaciones
    // filtrado por TODOS los OTROS filtros activos (nunca por sí mismo) —
    // así nunca ofrece un valor que produciría cero filas bajo el resto de
    // la selección vigente (p. ej. un proyecto cuyas únicas asignaciones
    // están en un Estado ya destildado). Estado NO se excluye de acá aparte
    // de cuando se calcula su propio universo — sus opciones son la lista
    // fija de estados conocidos, no se recalculan dinámicamente.
    // `excludeKey` es el ÚNICO filtro que no se aplica en esta pasada.
    facetedAssignments(excludeKey) {
      return this.assignments.filter(a => {
        if (excludeKey !== "consultant" && this.filters.consultant && a.consultant_id !== this.filters.consultant) return false;
        if (excludeKey !== "project" && this.filters.project && a.project_id !== this.filters.project) return false;
        if (excludeKey !== "client" && this.filters.client && a.client_id !== this.filters.client) return false;
        if (excludeKey !== "availability" && this.filters.availability && a.availability !== this.filters.availability) return false;
        // Activos vencidos (Summary Tile Filters corrective, ver HANDOFF) —
        // `a.activo_vencido` es EXACTAMENTE el mismo predicado que ya
        // alimenta el KPI `kpis.activosVencidos` (`is_activo_vencido`,
        // staffing_service.py — server-side, única regla canónica, nunca
        // reimplementada acá). Sin dropdown propio (ver `filters`) — se
        // aplica siempre, igual criterio que `status`, más abajo.
        if (this.filters.activosVencidos && !a.activo_vencido) return false;
        const statusKey = a.status || "Sin estado";
        if (this.filters.status[statusKey] === false) return false;
        return true;
      });
    },

    // Opciones derivadas del universo facetado — nunca hardcodeadas. Value
    // = id estable (sobrevive lookups sin resolver en el DATO, pero ver
    // exclusión de no-resueltos abajo: un id sin resolver NUNCA se ofrece
    // como OPCIÓN de filtro, aunque la fila sí siga visible en Todos).
    get consultantOptions() {
      const seen = new Map();
      this.facetedAssignments("consultant").forEach(a => {
        if (a.consultant_id && !a.consultant_unresolved && !seen.has(a.consultant_id)) {
          seen.set(a.consultant_id, a.consultant_name);
        }
      });
      return [...seen.entries()]
        .map(([id, label]) => ({ id, label }))
        .sort((a, b) => a.label.localeCompare(b.label));
    },
    get projectOptions() {
      const seen = new Map();
      this.facetedAssignments("project").forEach(a => {
        if (a.project_id && !a.project_unresolved && !seen.has(a.project_id)) {
          seen.set(a.project_id, a.project_name);
        }
      });
      return [...seen.entries()]
        .map(([id, label]) => ({ id, label }))
        .sort((a, b) => a.label.localeCompare(b.label));
    },
    // Cliente (revisión PBI, ver HANDOFF) — a diferencia de
    // consultant/project, SOLO valores RESUELTOS (client_id Y client_name
    // presentes). Nunca se sintetiza una opción "(sin resolver)": una
    // asignación sin cliente resuelto simplemente no aporta una opción,
    // pero sigue visible con Cliente = Todos (ver filteredAssignments).
    get clientOptions() {
      const seen = new Map();
      this.facetedAssignments("client").forEach(a => {
        if (a.client_id && a.client_name && !seen.has(a.client_id)) seen.set(a.client_id, a.client_name);
      });
      return [...seen.entries()]
        .map(([id, label]) => ({ id, label }))
        .sort((a, b) => a.label.localeCompare(b.label));
    },
    get availabilityOptions() {
      return [...new Set(this.facetedAssignments("availability").map(a => a.availability))].sort();
    },

    // ÚNICA colección filtrada — KPIs, timeline, disponibilidad y detalle
    // leen TODOS de acá, nunca de una copia independiente (evita que los
    // componentes queden en desacuerdo sobre el filtro vigente). Aplica
    // TODOS los filtros — mismo mecanismo que facetedAssignments, sin
    // excluir ninguno.
    get filteredAssignments() {
      return this.facetedAssignments(null);
    },

    get kpis() {
      const rows = this.filteredAssignments;
      const consultants = new Set(rows.map(r => r.consultant_id).filter(Boolean));
      const projects = new Set(rows.map(r => r.project_id).filter(Boolean));
      const active = rows.filter(r => r.status === "Activo");
      const tentative = rows.filter(r => r.status === "Tentativo");
      const activeHours = active
        .filter(r => r.allocation_unit === "hours_per_month" && r.allocation_value != null)
        .reduce((sum, r) => sum + r.allocation_value, 0);
      // Redondeo a 2 decimales solo para evitar basura de punto flotante
      // (50.1 + 20.2) — nunca una regla de negocio nueva. El valor grande
      // del KPI es SOLO el número (con separador de miles) — la unidad
      // "h/mes" ya está en la etiqueta de la tarjeta, duplicarla ahí rompe
      // la alineación vertical con el resto de los KPIs (revisión visual,
      // ver HANDOFF).
      const roundedActiveHours = Math.round(activeHours * 100) / 100;
      // Activos vencidos (Staffing Director Priority V2, ver HANDOFF):
      // cuenta ASIGNACIONES (no consultores distintos) — `activo_vencido`
      // ya viene calculado por el server (`is_activo_vencido`, ÚNICA regla
      // canónica), acá solo se cuenta sobre el universo YA filtrado.
      const activosVencidos = rows.filter(r => r.activo_vencido).length;
      return {
        consultants: consultants.size,
        projects: projects.size,
        active: active.length,
        tentative: tentative.length,
        activeHoursLabel: roundedActiveHours.toLocaleString("en-US"),
        activosVencidos,
      };
    },

    // Grouping switch (Consultor ↔ Proyecto) — mismo dataset filtrado, sin
    // Graph. Hub Dashboard Contextual View Control corrective (ver
    // HANDOFF): el control visible pasó de un botón de acción única
    // ("Agrupar por proyecto"/su inverso) a un segmented switch
    // (.dashboard-view-switch, compartido con Reporting) que muestra el
    // modo ACTUAL y la alternativa a la vez — `setGrouping(mode)` es la
    // primitiva real (no-op si ya está en ese modo); `toggleGrouping()`
    // queda como wrapper fino sobre ella para no romper otros callers.
    setGrouping(mode) {
      if (this.groupBy === mode) return;
      this.groupBy = mode;
      // Colapso global (pedido del owner, ver HANDOFF): si había un intent
      // limpio de "todo colapsado", el NUEVO groupBy arranca con TODOS sus
      // grupos colapsados (recalculado con las keys de la agrupación
      // nueva — nunca mapeando identidades consultor↔proyecto). "Expandido"
      // o sin intent (mixto) usa el default de la agrupación nueva, que ya
      // ES expandido (`{}`).
      if (this.timelineGlobalCollapseIntent === "collapsed") {
        const next = {};
        this.timelineGroups.forEach(g => { next[g.key] = true; });
        this.collapsedConsultants = next;
      } else {
        this.collapsedConsultants = {};
      }
    },
    toggleGrouping() {
      this.setGrouping(this.groupBy === "consultant" ? "project" : "consultant");
    },
    // Etiqueta secundaria de la fila del timeline — depende de qué está
    // agrupado: agrupado por Consultor, la fila muestra Cliente-Proyecto
    // (como siempre); agrupado por Proyecto, la fila muestra el Consultor
    // de esa asignación (mismo dato, ángulo inverso). Una sola
    // implementación de fila, nunca dos timelines paralelos.
    rowGroupSecondaryLabel(row) {
      return this.groupBy === "project" ? (row.consultant_name || "(sin resolver)") : this.rowClientProjectLabel(row);
    },
    // Solo el SUFIJO ("· 60 h/mes") — nunca el nombre repetido. El template
    // renderiza nombre y sufijo en dos <span> separados a propósito (ajuste
    // cosmético pedido por el usuario): el mismo negro/blanco en bold para
    // ambos competía visualmente — separarlos permite que el sufijo use un
    // gris más suave sin tocar el peso/color del nombre.
    //
    // Semántica CONGELADA (pre-PR final check, ver HANDOFF): el total es
    // horas EFECTIVAS HOY, NUNCA la suma de toda barra que exista en el
    // dataset filtrado/timeline — eso incluiría asignaciones futuras y
    // Activo-vencidas, que no representan carga vigente. Reusa el MISMO
    // helper canónico `_hoursIfEffective` que "Asignación hoy"/"Asignación
    // +N días" — una sola definición de "efectivo en una fecha", nunca dos.
    groupHoursSuffix(group) {
      const todayIso = _todayISO();
      const total = group.rows.reduce((sum, r) => sum + _hoursIfEffective(r, todayIso), 0);
      const rounded = Math.round(total * 100) / 100;
      return `· ${rounded.toLocaleString("en-US")} h/mes`;
    },

    // Fechas Argentina (dd/mm/yyyy completo, dd/mm corto) — parseo puro de
    // string, SIN `new Date(iso)` (evita el corrimiento de un día por UTC
    // que castigaría justo a los valores date-only que maneja Staffing).
    formatDateDMY(iso, short) {
      if (!iso) return null;
      const [y, m, d] = iso.split("-");
      return short ? `${d}/${m}` : `${d}/${m}/${y}`;
    },

    // Proyección de capacidad (+N días) — mismo dataset filtrado, sin
    // Graph. `todayISOValue`/`targetISOValue` son getters (no closures
    // fijas) para que cambiar `projectionDays` recalcule solo.
    get todayISOValue() { return _todayISO(); },
    get targetISOValue() { return _addDaysISO(_todayISO(), this.projectionDays); },
    // "días" es redundante en el header — el control vecino ya dice
    // "Proyección a [N] día(s)" (ajuste presentacional, ver HANDOFF: la
    // versión larga hacía que "Disponibilidad" wrappeara a dos líneas en
    // la tabla de layout fijo).
    get projectionColumnLabel() { return `Asignación +${this.projectionDays}`; },
    setProjectionDays(value) {
      const n = Math.round(Number(value));
      this.projectionDays = Number.isFinite(n) ? Math.min(365, Math.max(1, n)) : this.projectionDays;
    },

    // Ventana de ausencias del Timeline (ver `absenceLookbackDays` arriba)
    // — cutoff = hoy - N días. `_addDaysISO` (MISMA función que
    // `targetISOValue`, nunca una segunda implementación) acepta N negativo
    // sin drama: construye un `Date` LOCAL puro (día/mes/año, sin horas) y
    // vuelve a ISO — cero corrimiento de huso horario, cero off-by-one.
    // Comparación de fechas SIEMPRE como string ISO (`>=`/`<`, lexicográfico
    // — ordena igual que temporal para "YYYY-MM-DD"), MISMO criterio que
    // `_isEffectiveOn` — nunca `new Date(iso)` para comparar.
    get absenceCutoffISO() { return _addDaysISO(_todayISO(), -this.absenceLookbackDays); },
    // Clamp contra el techo DINÁMICO (`absenceLookbackMaxDays`, MISMA
    // fuente runtime que el `:max` del `<input>` — ver template) — nunca
    // un número fijo acá.
    setAbsenceLookbackDays(value) {
      const n = Math.round(Number(value));
      this.absenceLookbackDays = Number.isFinite(n)
        ? Math.min(this.absenceLookbackMaxDays, Math.max(1, n)) : this.absenceLookbackDays;
    },
    // Etiqueta por-asignación en el detalle expandido: "—" si esa asignación
    // puntual no está vigente en la fecha pedida, nunca un cero engañoso
    // (una asignación no-efectiva no tiene una carga "de cero horas", no
    // existe en ese punto del tiempo).
    rowEffectiveLabel(row, iso) {
      if (!_isEffectiveOn(row, iso)) return "—";
      if (row.allocation_unit === "hours_per_month" && row.allocation_value != null) return row.allocation_label;
      return "—";
    },

    // Detalle agregado por consultor (default V2, ver HANDOFF) — Cliente/
    // Proyecto/Desde/Hasta NUNCA se agregan (el template los renderiza
    // "--" para la fila agregada); Estado agregado es determinístico:
    // el único status compartido, o "Mixto" si los hijos difieren — nunca
    // se elige uno arbitrario. Misma key de agrupación que `timelineGroups`
    // en modo consultor (consultant_id, o "unresolved-"+assignment_id para
    // no fusionar personas distintas sin id).
    get aggregatedDetailRows() {
      const todayIso = this.todayISOValue;
      const targetIso = this.targetISOValue;
      const groups = new Map();
      this.filteredAssignments.forEach(a => {
        const key = a.consultant_id || ("unresolved-" + a.assignment_id);
        if (!groups.has(key)) groups.set(key, { key, consultant_name: a.consultant_name, rows: [] });
        groups.get(key).rows.push(a);
      });
      return [...groups.values()].map(g => {
        const statuses = new Set(g.rows.map(r => r.status || "Sin estado"));
        const hoy = g.rows.reduce((sum, r) => sum + _hoursIfEffective(r, todayIso), 0);
        const proyectada = g.rows.reduce((sum, r) => sum + _hoursIfEffective(r, targetIso), 0);
        // Cliente/Proyecto/Desde/Hasta: "--" SOLO si los hijos difieren en
        // ESE campo puntual — cada uno se evalúa de forma INDEPENDIENTE
        // (fechas iguales con clientes distintos igual informan la fecha).
        const clienteValue = _singleValue(g.rows.map(r => r.client_name || null));
        const proyectoValue = _singleValue(g.rows.map(r => r.project_name));
        const desdeValue = _singleValue(g.rows.map(r => r.start_date || null));
        const hastaValue = _singleValue(g.rows.map(r => r.end_date || null));
        return {
          key: g.key,
          consultant_name: g.consultant_name,
          rows: g.rows,
          estado: statuses.size === 1 ? [...statuses][0] : "Mixto",
          disponibilidad: g.rows[0].availability,
          asignacion_hoy_label: (Math.round(hoy * 100) / 100).toLocaleString("en-US") + " h/mes",
          asignacion_proyectada_label: (Math.round(proyectada * 100) / 100).toLocaleString("en-US") + " h/mes",
          cliente_label: clienteValue === undefined ? "--" : (clienteValue || "—"),
          proyecto_label: proyectoValue === undefined ? "--" : proyectoValue,
          // Mismo `project_name` compartido ⇒ mismo estado de resolución
          // (un proyecto real jamás se llama literalmente "(sin resolver)").
          proyecto_unresolved: proyectoValue !== undefined && !!g.rows[0].project_unresolved,
          desde_label: desdeValue === undefined ? "--" : (this.formatDateDMY(desdeValue) || "—"),
          hasta_label: hastaValue === undefined ? "--" : (this.formatDateDMY(hastaValue) || "—"),
        };
      }).sort((x, y) => x.consultant_name.localeCompare(y.consultant_name));
    },
    isDetailGroupExpanded(key) {
      return !!this.expandedDetailGroups[key];
    },
    toggleDetailGroup(key) {
      this.expandedDetailGroups[key] = !this.expandedDetailGroups[key];
    },
    // Vista PLANA (aggregate row + sus hijos, en orden, intercalados) — un
    // solo `x-for` en el template, sin anidar dos <template x-for> sobre la
    // misma lista (eso los renderiza como dos bloques separados, no
    // intercalados: todas las filas agregadas primero y TODOS los hijos
    // expandidos al final de la tabla, un bug real de layout, no solo
    // estético). Todas las filas comparten exactamente las mismas 10
    // columnas — la fila hija solo difiere en QUÉ contenido pone en cada
    // celda, nunca en la forma.
    get detailRows() {
      const todayIso = this.todayISOValue;
      const targetIso = this.targetISOValue;
      const flat = [];
      this.aggregatedDetailRows.forEach(group => {
        const expanded = this.isDetailGroupExpanded(group.key);
        flat.push({
          is_aggregate: true,
          key: "agg-" + group.key,
          toggle_key: group.key,
          expanded,
          consultant_label: group.consultant_name,
          client_label: group.cliente_label,
          project_label: group.proyecto_label,
          project_unresolved: group.proyecto_unresolved,
          hoy_label: group.asignacion_hoy_label,
          proyectada_label: group.asignacion_proyectada_label,
          estado_label: group.estado,
          estado_slug: this.statusSlug(group.estado),
          estado_color: this.statusColor(group.estado),
          desde_label: group.desde_label,
          hasta_label: group.hasta_label,
          disponibilidad_label: group.disponibilidad,
        });
        if (!expanded) return;
        group.rows.forEach(row => {
          flat.push({
            is_aggregate: false,
            key: "row-" + row.assignment_id,
            consultant_label: "",
            client_label: row.client_name || "—",
            project_label: row.project_name,
            project_unresolved: row.project_unresolved,
            hoy_label: this.rowEffectiveLabel(row, todayIso),
            proyectada_label: this.rowEffectiveLabel(row, targetIso),
            estado_label: row.status || "—",
            estado_slug: this.statusSlug(row.status),
            estado_color: this.statusColor(row.status),
            desde_label: this.formatDateDMY(row.start_date) || "—",
            hasta_label: this.formatDateDMY(row.end_date) || "—",
            disponibilidad_label: row.availability,
          });
        });
      });
      return flat;
    },

    // Jerarquía consultor → asignación (una barra por asignación, NUNCA
    // superpuestas en la misma fila) — mismo criterio operativo útil del
    // Power BI legado, sin las barras superpuestas que ese reporte tenía.
    // Key = consultant_id si existe (agrupa asignaciones reales del MISMO
    // consultor, aunque su nombre no resuelva); si el lookup está
    // totalmente vacío, cada asignación es su propio grupo por
    // assignment_id — NUNCA se fusionan dos filas sin id solo porque
    // comparten el mismo texto "(sin resolver)" (podrían ser personas
    // distintas cuyo lookup falló por separado — ver invariante de
    // Disponibilidad más abajo).
    // Grouping switch (Staffing Director Priority V2, ver HANDOFF): UNA sola
    // implementación de agrupación para ambos modos — solo cambia de qué
    // entidad viene la key/label del grupo, nunca duplica el timeline. Modo
    // Proyecto usa el mismo criterio "sin id real = grupo propio" que
    // Consultor (nunca fusiona proyectos sin resolver distintos).
    get timelineGroups() {
      const byProject = this.groupBy === "project";
      const groups = new Map();
      this.filteredAssignments.forEach(a => {
        if (!a.on_timeline) return;
        const key = byProject ? (a.project_id || ("unresolved-" + a.assignment_id)) : (a.consultant_id || ("unresolved-" + a.assignment_id));
        const label = byProject ? a.project_name : a.consultant_name;
        if (!groups.has(key)) groups.set(key, { key, consultant_name: label, rows: [] });
        groups.get(key).rows.push(a);
      });
      // Ausencias (ver HANDOFF): SOLO en agrupación Consultor — son
      // person-specific, nunca se duplican bajo cada proyecto. Mismo dato ya
      // cargado (this.absences), sin refetch — cambiar groupBy solo cambia
      // qué se adjunta acá, nunca dispara un nuevo POST. `absencesByConsultant`
      // ya está filtrado por Concepto (ver ese getter) — un checkbox de
      // Concepto recalcula esta lane sin tocar `filteredAssignments`.
      const absencesByConsultant = byProject ? null : this.absencesByConsultant;
      return [...groups.values()]
        .map(g => ({
          ...g,
          hours_suffix: this.groupHoursSuffix(g),
          absences: absencesByConsultant ? (absencesByConsultant.get(g.key) || []) : [],
        }))
        .sort((x, y) => x.consultant_name.localeCompare(y.consultant_name));
    },

    // Visibilidad del control "Ausencias · últimos N días" (owner
    // corrective, ver HANDOFF) — el selector gobierna el LANE completo (los
    // 4 conceptos), así que se oculta ÚNICAMENTE cuando NINGÚN concepto
    // está tildado (un filtro activo pero invisible confundiría más que
    // sacarlo del todo) — nunca sólo porque "Vacaciones" esté destildado
    // mientras Danwor Day/Cumpleaños/Otros siguen activos (el lookback
    // TAMBIÉN los afecta a ellos). Puro sobre `this.filters.concept`, sin
    // I/O — recalcula solo al tildar/destildar, el template ya usa
    // `x-show` (nunca `x-if`), así que `absenceLookbackDays` sobrevive
    // intacto mientras el control está oculto.
    get anyConceptSelected() {
      return CONCEPT_OPTIONS.some(c => this.filters.concept[c] !== false);
    },

    // Ausencias visibles tras el filtro Concepto Y la ventana de ausencias
    // (client-side puro, ver HANDOFF) — ÚNICA fuente que
    // `absencesByConsultant`/la lane/el total de horas visible leen (grep
    // confirmado: sin otro consumidor — la tabla de Detalle y los KPIs de
    // Resumen nunca tocan `this.absences`, así que este selector no puede
    // filtrarlos aunque quisiera). Nunca llama a Graph, nunca recarga el
    // XLSX, nunca toca `this.assignments`. `filters.concept[a.concept] !==
    // false` (en vez de `=== true`) para que un concepto que por alguna
    // razón no esté en el objeto de filtros (nunca debería pasar con los 4
    // fijos) quede visible por default, nunca oculto por ausencia de key.
    //
    // Ventana de ausencias (Dirección corrective, ver `absenceCutoffISO`)
    // — OWNER CORRECTIVE (revocando una decisión previa, ver HANDOFF): el
    // cutoff aplica a los CUATRO conceptos del lane (Vacaciones/Danwor
    // Day/Cumpleaños/Otros) — el owner detectó en su propio smoke que un
    // registro viejo de Danwor Day seguía visible cuando sólo debía
    // filtrarse "Vacaciones", y aclaró que la intención real siempre fue
    // "cuánto histórico del LANE veo", ortogonal a "qué conceptos veo"
    // (los checkboxes de Concepto). Compone AND con el filtro de Concepto
    // existente (un período puede estar destildado por Concepto Y además
    // caer antes del cutoff — cualquiera de los dos lo excluye igual).
    //
    // Excluye ÚNICAMENTE períodos que terminan COMPLETAMENTE antes del
    // cutoff (`end_date < cutoff`). Un período que arrancó antes del
    // cutoff pero todavía lo atraviesa (`end_date >= cutoff`) se CONSERVA,
    // pero su BARRA se recorta visualmente a `[cutoff, end_date real]` —
    // "Ausencias · últimos N días" es una VENTANA DE VISUALIZACIÓN con
    // clipping, no un simple adentro/afuera. `visible_start_pct` se deriva
    // algebraicamente del `start_pct` real (ver `_visibleAbsenceStartPct`
    // arriba) — NUNCA se toca `end_pct` (el final real de la barra no
    // cambia) ni las fechas (`start_date`/`end_date`, que el tooltip sigue
    // mostrando REALES — ver el template, lee esos campos directo). El
    // objeto original de `this.absences` NUNCA se muta: acá se arma una
    // COPIA (`{...a, start_pct: ...}`) sólo para este render — `map`
    // siempre devuelve un objeto nuevo, incluso cuando no hace falta clip
    // (spread trivial), así que el consumidor nunca puede confundir cuál
    // es cuál por identidad de referencia. Sin límite futuro adicional: el
    // horizonte del Timeline (`_overlaps_horizon`, staffing_service.py) ya
    // es el único techo hacia adelante.
    get filteredAbsences() {
      const cutoff = this.absenceCutoffISO;
      const totalMonths = this.horizon.length;
      return this.absences
        .filter(a => {
          if (this.filters.concept[a.concept] === false) return false;
          if (a.end_date < cutoff) return false;
          return true;
        })
        .map(a => {
          if (a.start_date >= cutoff) return { ...a };
          return { ...a, start_pct: _visibleAbsenceStartPct(a, cutoff, totalMonths) };
        });
    },

    // Ausencias agrupadas por consultant_id — puro sobre filteredAbsences,
    // sin I/O (el fetch ya ocurrió en _loadAbsences). Un consultor sin
    // ausencias visibles simplemente no aparece en el Map (grupo sin lane
    // extra) — si el usuario destilda los 4 conceptos, TODOS los grupos
    // quedan sin lane (el template ya oculta la lane vacía vía
    // `x-if="group.absences.length > 0"`, sin código nuevo).
    get absencesByConsultant() {
      const byConsultant = new Map();
      this.filteredAbsences.forEach(a => {
        if (!byConsultant.has(a.consultant_id)) byConsultant.set(a.consultant_id, []);
        byConsultant.get(a.consultant_id).push(a);
      });
      return byConsultant;
    },

    // Etiqueta VISIBLE de la lane de ausencias (izquierda) — la lane es
    // COMPARTIDA por todos los períodos VISIBLES del consultor (ya
    // filtrados por Concepto), así que resume la SUMA de las horas
    // conocidas (`hours`, ya calculadas Días×8 en el repositorio). Solo
    // suma los períodos que traen un número usable; si ninguno lo trae, cae
    // a "Ausencias" pelado (NUNCA inventa horas desde las fechas).
    // Display-only: no toca allocation/KPIs/geometría. El tooltip sigue
    // mostrando las horas POR período (ver absenceBarLabel).
    absenceLaneLabel(periods) {
      const total = (periods || [])
        .map(p => p.hours)
        .filter(h => typeof h === "number")
        .reduce((sum, h) => sum + h, 0);
      const known = (periods || []).some(p => typeof p.hours === "number");
      return known ? `Ausencias (${total}hs)` : "Ausencias";
    },

    // Etiqueta del período de ausencia — el CONCEPTO ya clasificado
    // server-side (`Vacaciones`/`Danwor Day`/`Cumpleaños`/`Otros` — nunca el
    // Motivo crudo, que ni siquiera llega al browser, ver HANDOFF), con las
    // horas equivalentes (Días × 8, ya calculadas en el repositorio y
    // provistas como absence.hours) cuando la fuente las trae. Sin horas
    // (Días ausente/malformado → hours null) cae al concepto pelado. NUNCA
    // deriva horas del rango Desde/Hasta (ver HANDOFF): la geometría de la
    // barra usa las fechas; la etiqueta usa Días×8, son dimensiones separadas.
    absenceBarLabel(absence) {
      const hours = absence.hours;
      return (typeof hours === "number") ? `${absence.concept} (${hours}hs)` : absence.concept;
    },

    absenceBarAriaLabel(absence) {
      return this.absenceBarLabel(absence) + " — " + (this.formatDateDMY(absence.start_date) || "?") + " → " + (this.formatDateDMY(absence.end_date) || "?");
    },

    // Asignaciones sin ninguna fecha real — no se posicionan en el
    // timeline (no se puede), pero siguen en KPIs/disponibilidad/detalle.
    // Contador visible para que no queden "perdidas" en silencio.
    get offTimelineCount() {
      return this.filteredAssignments.filter(a => !a.on_timeline).length;
    },

    // Día-exacta (ver HANDOFF — corrective de geometría): `start_pct`/`end_pct`
    // (Python, `compute_timeline_position`) ya están en la MISMA escala 0..100
    // que `todayMarker.pct` (`compute_today_marker`) — la barra se posiciona
    // con left/width absolutos dentro de `.staffing-timeline__track`, nunca
    // con grid-column por mes (eso pegaba toda barra al día 1 de su mes).
    barStyle(row) {
      return `left:${row.start_pct}%; width:${row.end_pct - row.start_pct}%`;
    },

    // Disponibilidad de asignación — SOLO hechos: cuenta consultores
    // DISTINTOS *resueltos* (un consultor con 3 asignaciones cuenta una
    // vez), nunca asignaciones. Cero % / horas libres / capacidad — ver
    // HANDOFF.
    //
    // Invariante congelado: Full Time + Part Time + Sin dato === Consultores
    // (kpis.consultants). Por eso la key es SIEMPRE consultant_id, nunca un
    // fallback al nombre — una fila sin consultant_id (lookup vacío en la
    // fuente) NO tiene una identidad de consultor real que contar; se
    // excluye acá exactamente como `kpis.consultants` ya la excluye
    // (`.filter(Boolean)`). Sigue visible en timeline/detalle como
    // "(sin resolver)" — solo no infla ningún conteo de consultores.
    get availabilityGroups() {
      const byConsultant = new Map();
      this.filteredAssignments.forEach(a => {
        if (!a.consultant_id) return;
        if (!byConsultant.has(a.consultant_id)) byConsultant.set(a.consultant_id, a.availability);
      });
      const counts = { "Full Time": 0, "Part Time": 0, "Sin dato": 0 };
      byConsultant.forEach(av => { counts[av] = (counts[av] || 0) + 1; });
      return counts;
    },
  };
}
document.body.addEventListener("htmx:load", () => initModuleFilter(document));

// USAGE METRICS DAILY TREND corrective (owner ticket): barras de "Tendencia
// diaria" clickeables — MISMO contrato Desde/Hasta que el form de filtros de
// arriba (`.whatsnew-filters`), nunca un segundo estado de filtro oculto.
// Clickear una barra fija Desde=Hasta=ese día y dispara la MISMA request que
// "Actualizar" (`form.requestSubmit()` — no una fetch manual nueva), así que
// cualquier otro filtro ya aplicado (usuario/módulo/ámbito/área) se preserva
// tal cual. `data-day` es el YYYYMMDD canónico (ver activity_analytics.py) —
// el label DD/MM visible es solo presentación, la key de filtro nunca se
// deriva del texto mostrado. MISMO patrón de re-cableo que initModuleFilter
// (htmx:load, scope) — necesario porque `#activity-dashboard` se reemplaza
// entero (`hx-swap="outerHTML"`) en cada filtro aplicado, así que cualquier
// listener atado en la corrida anterior murió con el DOM viejo.
function initActivityChartFilter(scope) {
  const bars = [...scope.querySelectorAll(".activity-chart__col[data-day]")];
  if (!bars.length) return;
  const form = scope.querySelector(".whatsnew-filters");
  const from = form ? form.querySelector('input[name="date_from"]') : null;
  const to = form ? form.querySelector('input[name="date_to"]') : null;
  if (!form || !from || !to) return;
  const filterDay = day => {
    const iso = `${day.slice(0, 4)}-${day.slice(4, 6)}-${day.slice(6, 8)}`;
    from.value = iso;
    to.value = iso;
    if (form.requestSubmit) form.requestSubmit(); else form.submit();
  };
  bars.forEach(bar => {
    bar.addEventListener("click", () => filterDay(bar.dataset.day));
    bar.addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); filterDay(bar.dataset.day); }
    });
  });
}
document.body.addEventListener("htmx:load", () => initActivityChartFilter(document));

// ============================================================================
// HubEstimatedProgress — primitivo COMPARTIDO del Hub (Operations) para
// esperas FOREGROUND causadas por fetch de Graph/SharePoint. Reporting es el
// PRIMER adopter (ver reporting.js), no el dueño — cualquier otra pestaña del
// Hub puede reusarlo sin reimplementar esta lógica.
//
// Contrato de UX (Graph no expone % real de avance):
//   - NUNCA porcentaje real/ETA — es tiempo ESTIMADO contra un `seedMs` que
//     aprende con EWMA (0.70 anterior + 0.30 última duración real) por `key`,
//     persistido en localStorage (sin backend nuevo, sin requests extra).
//   - Nunca llega a 100% solo por tiempo (tope ~94%, ver CHECKPOINTS) — el
//     100%/contenido real lo decide el caller cuando el fetch efectivamente
//     resuelve, jamás la animación.
//   - Si el fetch tarda más que el estimado, el llamador debe mostrar un
//     estado "overrun" (sweep/highlight continuo) en vez de seguir subiendo.
//
// Pull-based (sin su propio setInterval): el caller decide la cadencia de
// re-render llamando `.tick()` cuando le convenga (evita timers duplicados
// por cada consumidor).
const HubEstimatedProgress = (() => {
  const STORAGE_PREFIX = "dw-hub-progress-estimate:";
  const MIN_ESTIMATE_MS = 1200;
  // [ratio_transcurrido, porcentaje] — interpolado linealmente entre puntos
  // consecutivos. Seeds de UX pedidas: inicio ~5-8%, 25% del estimado ~35%,
  // 50% ~60%, 75% ~80%, fin del estimado ~93-95% (nunca 100).
  const CHECKPOINTS = [[0, 0.06], [0.25, 0.35], [0.50, 0.60], [0.75, 0.80], [1, 0.94]];

  function percentForRatio(ratio) {
    const r = Math.max(0, Math.min(1, ratio));
    for (let i = 1; i < CHECKPOINTS.length; i++) {
      const [t0, p0] = CHECKPOINTS[i - 1];
      const [t1, p1] = CHECKPOINTS[i];
      if (r <= t1) return p0 + (p1 - p0) * ((r - t0) / (t1 - t0));
    }
    return CHECKPOINTS[CHECKPOINTS.length - 1][1];
  }

  function readEstimate(key, seedMs) {
    try {
      const v = parseFloat(localStorage.getItem(STORAGE_PREFIX + key));
      return Number.isFinite(v) && v > 0 ? v : seedMs;
    } catch (_e) { return seedMs; }
  }

  function writeEstimate(key, previousMs, actualMs) {
    try {
      localStorage.setItem(STORAGE_PREFIX + key, String(previousMs * 0.70 + actualMs * 0.30));
    } catch (_e) { /* localStorage no disponible (incógnito/cuota) — fail-soft, sigue con el seed */ }
  }

  // `key` identifica la FUENTE de datos (no la pestaña que la dispara — dos
  // pestañas que comparten el mismo fetch deben aprender el MISMO estimate).
  function start(key, { seedMs = 8000 } = {}) {
    const estimateMs = Math.max(readEstimate(key, seedMs), MIN_ESTIMATE_MS);
    const startedAt = Date.now();
    let settled = false;
    return {
      tick() {
        const elapsed = Date.now() - startedAt;
        const overrun = elapsed > estimateMs;
        return { percent: Math.round(percentForRatio(overrun ? 1 : elapsed / estimateMs) * 100), overrun };
      },
      // Fetch resuelto OK: graba la duración real (EWMA) para la próxima vez.
      finishSuccess() {
        if (settled) return;
        settled = true;
        writeEstimate(key, estimateMs, Date.now() - startedAt);
      },
      // Fetch en error: detiene el estimate SIN grabar (una duración hasta un
      // error no es evidencia de cuánto tarda un fetch exitoso).
      finishError() { settled = true; },
    };
  }

  return { start, percentForRatio };
})();

// ============================================================================
// HubForegroundProgress — el BOUNDARY compartido del Hub para toda espera
// FOREGROUND visible causada por Graph/SharePoint, sin importar qué vertical
// la inició (owner corrective: "el loader pertenece al HUB", auditado en
// Copiloto/Reporting/Staffing — Asignaciones usa htmx, Reporting usa fetch()
// + Alpine, y ambos deben terminar en la MISMA barra sin que ninguno sepa
// de su existencia). Los consumidores SOLO notifican lifecycle —
// begin/complete/fail — nunca tocan DOM/CSS/width/sweep: eso es
// responsabilidad EXCLUSIVA de este módulo (pinta sobre
// `.read-progress__fill`, el MISMO nodo que Copiloto alimenta con scroll
// real — ver `copilotLayout().init()` arriba — nunca un segundo elemento).
//
// `key` identifica la FUENTE/operación (p. ej. "cartera", "assignments"),
// NO el vertical que la dispara — dos consumidores que comparten la MISMA
// operación real deberían compartir la MISMA key (ver HubEstimatedProgress:
// EWMA aprende por key). `begin` es idempotente (una operación ya en curso
// no se reinicia); `complete`/`fail` son no-op si esa key no está activa.
//
// Un único timer COMPARTIDO para todas las keys activas (nunca uno por
// key) — arranca en el primer `begin()`, se auto-limpia cuando la última
// key activa se liquida. El tick NUNCA dispara render()/side-effects de
// ningún consumidor: solo re-lee el controller y escribe `style.width`.
// Sin noción de "operación activa/visible" — si dos keys estuvieran
// simultáneamente en curso (raro: cada vertical dispara como máximo una a
// la vez en la práctica), se pinta cualquiera; no se modela más que eso a
// propósito (YAGNI — ver corrective: "elegir la opción mínima").
const HubForegroundProgress = (() => {
  const active = {};  // key -> { controller }
  let timer = null;

  function paint() {
    const fill = document.querySelector(".read-progress__fill");
    if (!fill) return;
    const keys = Object.keys(active);
    fill.style.width = keys.length ? active[keys[0]].controller.tick().percent + "%" : "0%";
  }

  function sweepStep() {
    paint();
    if (!Object.keys(active).length && timer) { clearInterval(timer); timer = null; }
  }

  // Arranca (idempotente) la espera foreground de `key`. `seedMs` es el
  // seed inicial SOLO la primera vez que se ve esta key (después, el
  // estimate aprendido de HubEstimatedProgress la reemplaza — ver ahí).
  function begin(key, { seedMs = 8000 } = {}) {
    if (active[key]) return;
    active[key] = { controller: HubEstimatedProgress.start(key, { seedMs }) };
    if (!timer) timer = setInterval(sweepStep, 150);
    paint();
  }
  // La operación resolvió OK — graba la duración real (EWMA) y limpia.
  function complete(key) {
    const entry = active[key];
    if (!entry) return;
    entry.controller.finishSuccess();
    delete active[key];
    paint();
  }
  // La operación falló — limpia SIN grabar estimate (ver HubEstimatedProgress
  // finishError). Mismo fail-soft de siempre en el consumidor; esto solo
  // apaga la barra.
  function fail(key) {
    const entry = active[key];
    if (!entry) return;
    entry.controller.finishError();
    delete active[key];
    paint();
  }

  return { begin, complete, fail };
})();

// Asignaciones (Staffing) — first load vía htmx (`#staffing-rows`,
// hx-trigger="load", ver staffing.html), NO un fetch()+Alpine como
// Reporting. MISMO boundary (HubForegroundProgress) — el listener global de
// htmx (ya usado arriba para el spinner de Sección/Chat/wizard) es el punto
// de integración natural: detecta la request por el elemento que la
// disparó, nunca por asumir "cualquier fetch Graph es foreground" (background
// prefetch de Reporting/Staffing NUNCA pasa por htmx con este id, así que
// nunca activa esto). Sin seed histórico confiable todavía — 8000ms
// (default de HubForegroundProgress.begin) y que EWMA aprenda.
document.body.addEventListener("htmx:beforeRequest", event => {
  if (event.detail.elt?.closest("#staffing-rows")) HubForegroundProgress.begin("assignments");
});
document.body.addEventListener("htmx:afterRequest", event => {
  if (!event.detail.elt?.closest("#staffing-rows")) return;
  if (event.detail.successful) HubForegroundProgress.complete("assignments");
  else HubForegroundProgress.fail("assignments");
});
