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
            if (overscroll > 320) { navigating = true; window.location.href = continueEl.getAttribute("href"); }
          } else {
            overscroll = 0;
            continueEl.classList.remove("is-armed");
          }
        }, { passive: true });
      }
      // El hero (#__module__) también es target del scroll-spy: arriba de todo el
      // selector marca "Módulo (general)"; al entrar a una sección, marca la sección.
      const sections = [...document.querySelectorAll(".module-hero, .content-section")];
      this.activeSection = sections[0]?.id || "";
      if ("IntersectionObserver" in window) {
        // Mantenemos un Set persistente de secciones que cruzan la banda y la
        // sección activa = la primera en orden de documento. Así re-identifica
        // tanto bajando como SUBIENDO (antes solo usaba las entries del callback
        // actual, que al subir traían la sección que SALÍA, sin reemplazo).
        const intersecting = new Set();
        const observer = new IntersectionObserver(entries => {
          for (const entry of entries) {
            if (entry.isIntersecting) intersecting.add(entry.target);
            else intersecting.delete(entry.target);
          }
          const top = sections.find(section => intersecting.has(section));
          if (top) this.activeSection = top.id;
          // Banda apenas debajo del topbar (≈ donde aterrizan los anchors): la
          // sección cambia cuando la anterior sale por arriba de los 80px.
        }, { rootMargin: "-80px 0px -75% 0px", threshold: 0 });
        sections.forEach(section => observer.observe(section));
      }
      initContentReveals(shell);
    },
    toggleTheme() {
      this.contentTheme = this.contentTheme === "light" ? "dark" : "light";
      localStorage.setItem("dw-content-theme", this.contentTheme);
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
    });
  }, {
    root: container,
    threshold: 0.05,
    rootMargin: "0px 0px -4% 0px",
  });
  elements.forEach(element => observer.observe(element));
}

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
// (índice de secciones = linaje, y las referencias entre artefactos) scrollean a
// su sección con scrollIntoView. El salto nativo por #hash es poco fiable dentro
// de un fragmento swapeado con contenedor de scroll ambiguo.
document.addEventListener("click", event => {
  const content = document.getElementById("content");
  if (!content || !content.classList.contains("catalog-detail-open")) return;
  const a = event.target.closest('a[href^="#"]');
  if (!a || !content.contains(a)) return;
  const el = document.getElementById(a.getAttribute("href").slice(1));
  if (!el) return;
  event.preventDefault();
  el.scrollIntoView({ behavior: "smooth", block: "start" });
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
