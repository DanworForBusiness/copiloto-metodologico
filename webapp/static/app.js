function copilotLayout(isAppendix = false) {
  return {
    navOpen: false,
    panelOpen: true,
    gestionOpen: false,
    toolTab: "section",
    activeSection: "",
    // Grupos colapsables del nav izquierdo. Abrimos el grupo del documento
    // activo y dejamos el otro colapsado. El grupo activo no se puede colapsar
    // mientras se lo navega: si queda cerrado, un $watch lo reabre (ver init()).
    activeNavGroup: isAppendix ? "appendix" : "core",
    navCore: !isAppendix,
    navAppendix: isAppendix,
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
      // El grupo del doc activo se puede colapsar, pero apenas el contenido
      // central scrollea se reabre solo (no se "navega" con el grupo cerrado).
      const reopenActiveGroup = () => {
        if (this.activeNavGroup === "appendix") this.navAppendix = true;
        else this.navCore = true;
      };
      const contentEl = shell.querySelector(".module-content");
      contentEl?.addEventListener("scroll", reopenActiveGroup, { passive: true });
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
// selector de marco y la pestaña Analítica según corresponda).
window.dwToggleDiagnostic = function (on) {
  document.cookie = "dw-diagnostic=" + (on ? "on" : "off") + ";path=/;max-age=31536000;samesite=lax";
  window.location.reload();
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
});
document.body.addEventListener("htmx:load", syncAssistantFeedbackState);
