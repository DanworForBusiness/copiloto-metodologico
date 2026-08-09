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
    const body = JSON.stringify({ event, stem, ...payload });
    if (navigator.sendBeacon) {
      navigator.sendBeacon("/telemetry/event", new Blob([body], { type: "application/json" }));
    } else {
      fetch("/telemetry/event", { method: "POST", headers: { "Content-Type": "application/json" }, body, keepalive: true });
    }
  } catch (e) { /* nunca romper la experiencia por telemetría */ }
}

// Interacciones de navegación/consumo (VERBOSE). Un solo listener delegado en
// captura: continuar al módulo, saltar por el índice/roadmap, y links externos
// del contenido (qué activo se llevó al usuario fuera del copiloto).
document.addEventListener("click", event => {
  const el = event.target;
  if (!el || !el.closest) return;
  const cont = el.closest(".module-continue");
  if (cont) { dwBeacon("module_continue", { via: "click", href: cont.getAttribute("href") || "" }); return; }
  const nav = el.closest(".section-index a, .module-roadmap__links a");
  if (nav) { dwBeacon("nav_jump", { href: nav.getAttribute("href") || "" }); return; }
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

// Logout: limpia la cookie de sesión en el backend y vuelve al landing.
window.dwLogout = function () {
  fetch("/api/auth/logout", { method: "POST" }).finally(() => { window.location.href = "/"; });
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
function dwRunRegenerate(source) {
  let token = "";
  try { token = sessionStorage.getItem("dw-graph-token") || ""; } catch (e) {}
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
    if (container.dataset.dashWired) return;
    container.dataset.dashWired = "1";
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
document.body.addEventListener("htmx:load", () => initDashboardSections(document));

// Panel interactivo de "Eventos y catálogo": clic en un evento de la izquierda →
// muestra su desglose (precalculado en summary.event_breakdowns) a la derecha.
// Sin request extra; el swap de htmx recrea el grupo, por eso se re-cablea en
// cada htmx:load igual que initDashboardSections.
function initEventBreakdown(scope) {
  scope.querySelectorAll("[data-breakdown-group]").forEach(group => {
    const rows = [...group.querySelectorAll("[data-event]")];
    const panels = [...group.querySelectorAll("[data-breakdown]")];
    const show = ev => {
      panels.forEach(p => { p.hidden = p.dataset.breakdown !== ev; });
      rows.forEach(r => r.classList.toggle("is-active", r.dataset.event === ev));
    };
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
    if (rows.length) show(rows[0].dataset.event);  // por defecto: el evento más frecuente
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
  const show = mod => {
    panels.forEach(p => { p.hidden = p.dataset.moduleBreakdown !== mod; });
    rows.forEach(r => r.classList.toggle("is-active", !!mod && r.dataset.module === mod));
    if (labelWrap) {
      labelWrap.hidden = !mod;
      if (mod && labelText) labelText.textContent = mod;
    }
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

  const defaultStatusFilter = () =>
    Object.fromEntries(statusOptions.map(s => [s, s === "Activo" || s === "Tentativo"]));

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

  return {
    assignments,
    horizon,
    statusOptions,
    todayMarkerPct,
    // Colapso de grupos de consultor en el timeline — SOLO estado visual de
    // presentación (nunca filtra filteredAssignments). Vacío = todo
    // expandido (default). Keyed por el mismo `key` de timelineGroups.
    collapsedConsultants: {},
    filters: {
      consultant: "",
      project: "",
      client: "",
      availability: "",
      // Default V1 congelado: Activo + Tentativo ÚNICAMENTE seleccionados.
      // Inactivo y "Sin estado" quedan disponibles pero NO seleccionados —
      // decisión explícita y visible, no un descarte silencioso (ver HANDOFF).
      status: defaultStatusFilter(),
    },

    init() {
      // Si el usuario elige un consultor puntual, ese grupo nunca debe
      // quedar oculto por un colapso previo — el filtro nunca debe
      // "pelear" contra el estado de presentación.
      this.$watch("filters.consultant", (value) => {
        if (value) this.collapsedConsultants[value] = false;
      });
    },

    resetFilters() {
      this.filters.consultant = "";
      this.filters.project = "";
      this.filters.client = "";
      this.filters.availability = "";
      this.filters.status = defaultStatusFilter();
    },

    statusSlug(status) {
      const known = { Activo: "activo", Tentativo: "tentativo", Inactivo: "inactivo" };
      return known[status] || "otro";
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
      return `${this.rowStatusHoursLabel(row)} · ${this.rowClientProjectLabel(row)}`;
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
      parts.push((row.start_date || "¿inicio?") + " → " + (row.end_date || "en curso"));
      return parts.join(" — ");
    },

    get todayMarker() {
      return { visible: todayMarkerPct != null, pct: todayMarkerPct };
    },

    isConsultantCollapsed(key) {
      return !!this.collapsedConsultants[key];
    },
    toggleConsultant(key) {
      this.collapsedConsultants[key] = !this.collapsedConsultants[key];
    },
    collapseAllConsultants() {
      const next = {};
      this.timelineGroups.forEach(g => { next[g.key] = true; });
      this.collapsedConsultants = next;
    },
    expandAllConsultants() {
      this.collapsedConsultants = {};
    },

    // Opciones derivadas del dataset cargado — nunca hardcodeadas. Value =
    // id estable (sobrevive lookups sin resolver); label = nombre para
    // mostrar (ya con el fallback "(sin resolver)" si corresponde).
    get consultantOptions() {
      const seen = new Map();
      this.assignments.forEach(a => {
        if (a.consultant_id && !seen.has(a.consultant_id)) seen.set(a.consultant_id, a.consultant_name);
      });
      return [...seen.entries()]
        .map(([id, label]) => ({ id, label }))
        .sort((a, b) => a.label.localeCompare(b.label));
    },
    get projectOptions() {
      const seen = new Map();
      this.assignments.forEach(a => {
        if (a.project_id && !seen.has(a.project_id)) seen.set(a.project_id, a.project_name);
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
      this.assignments.forEach(a => {
        if (a.client_id && a.client_name && !seen.has(a.client_id)) seen.set(a.client_id, a.client_name);
      });
      return [...seen.entries()]
        .map(([id, label]) => ({ id, label }))
        .sort((a, b) => a.label.localeCompare(b.label));
    },
    get availabilityOptions() {
      return [...new Set(this.assignments.map(a => a.availability))].sort();
    },

    // ÚNICA colección filtrada — KPIs, timeline, disponibilidad y detalle
    // leen TODOS de acá, nunca de una copia independiente (evita que los
    // componentes queden en desacuerdo sobre el filtro vigente).
    get filteredAssignments() {
      return this.assignments.filter(a => {
        if (this.filters.consultant && a.consultant_id !== this.filters.consultant) return false;
        if (this.filters.project && a.project_id !== this.filters.project) return false;
        if (this.filters.client && a.client_id !== this.filters.client) return false;
        if (this.filters.availability && a.availability !== this.filters.availability) return false;
        const statusKey = a.status || "Sin estado";
        if (this.filters.status[statusKey] === false) return false;
        return true;
      });
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
      return {
        consultants: consultants.size,
        projects: projects.size,
        active: active.length,
        tentative: tentative.length,
        activeHoursLabel: roundedActiveHours.toLocaleString("en-US"),
      };
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
    get timelineGroups() {
      const groups = new Map();
      this.filteredAssignments.forEach(a => {
        if (!a.on_timeline) return;
        const key = a.consultant_id || ("unresolved-" + a.assignment_id);
        if (!groups.has(key)) groups.set(key, { key, consultant_name: a.consultant_name, rows: [] });
        groups.get(key).rows.push(a);
      });
      return [...groups.values()].sort((x, y) => x.consultant_name.localeCompare(y.consultant_name));
    },

    // Asignaciones sin ninguna fecha real — no se posicionan en el
    // timeline (no se puede), pero siguen en KPIs/disponibilidad/detalle.
    // Contador visible para que no queden "perdidas" en silencio.
    get offTimelineCount() {
      return this.filteredAssignments.filter(a => !a.on_timeline).length;
    },

    barStyle(row) {
      return `grid-column:${row.start_index + 1} / ${row.end_index + 2}`;
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
