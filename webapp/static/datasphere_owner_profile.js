// Selector "Cliente" (SOLO dueño de la app) de los formularios Datasphere: "Assessment de tu modelo" y "Publicar modelo".
//
// UN solo comportamiento compartido por ambos formularios (el marcado lo emite la macro `datasphere_form_fields`, que el servidor renderiza
// únicamente si el usuario es APP_OWNER y la fuente SharePoint está configurada).
//
// SEGURIDAD: el navegador solo recibe y maneja METADATOS SEGUROS (profile_id opaco, cliente, host, token URL, client ID). El Client Secret
// NUNCA llega al navegador: con un perfil elegido el campo queda deshabilitado ("Configurado desde SharePoint") y el servidor vuelve a
// resolver el secreto desde SharePoint a partir del profile_id al ejecutar. Para eso el envío agrega, por única vez, el token delegado de
// Graph (`graph_token`, el mismo contrato que el resto del Hub) y lo borra apenas los handlers existentes arman el cuerpo.
//
// FALLBACK: si la lista no se puede cargar, se muestra un aviso breve y el formulario manual sigue funcionando exactamente igual. Todo
// texto externo entra con textContent (nunca como HTML).
(function () {
  "use strict";

  function qs(root, sel) { return root.querySelector(sel); }

  function setField(form, p, name, value) {
    const input = form.querySelector("#" + p + "-" + name);
    if (!input) return;
    input.value = value || "";
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function secretInput(form, p) { return form.querySelector("#" + p + "-client_secret"); }

  function useProfile(form, p, profile) {
    qs(form, "[data-dw-profile-id]").value = profile.profile_id;
    setField(form, p, "host", profile.host);
    setField(form, p, "token_url", profile.token_url);
    setField(form, p, "client_id", profile.client_id);
    setField(form, p, "client", profile.client);
    const secret = secretInput(form, p);
    if (secret) {                                           // el secreto real jamás se muestra ni se carga: solo el estado "configurado"
      if (!secret.dataset.dwPlaceholder) secret.dataset.dwPlaceholder = secret.placeholder || "";
      secret.value = "";
      secret.disabled = true;                               // un campo deshabilitado no se envía ni exige "required"
      secret.placeholder = "Configurado desde SharePoint";
    }
  }

  function clearProfile(form, p) {
    qs(form, "[data-dw-profile-id]").value = "";
    qs(form, "[data-dw-graph-field]").value = "";
    const secret = secretInput(form, p);
    if (secret) {                                           // modo manual otra vez: campo editable y vacío
      secret.disabled = false;
      secret.value = "";
      if (secret.dataset.dwPlaceholder !== undefined) secret.placeholder = secret.dataset.dwPlaceholder;
    }
  }

  function warn(box, message) {
    const node = qs(box, "[data-dw-profile-warning]");
    if (!node) return;
    node.textContent = message || "";
    node.hidden = !message;
  }

  async function load(box, select, state) {
    if (state.loaded || state.loading) return;
    state.loading = true;
    try {
      const token = await (window.dwGraphToken ? window.dwGraphToken() : Promise.resolve("")).catch(() => "");
      const response = await fetch(box.dataset.profilesUrl, {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({ graph_token: token || "" }),
      });
      if (!response.ok) throw new Error("HTTP " + response.status);
      const data = await response.json();
      if (!data.ok) { warn(box, data.message || "No se pudo leer la lista de clientes. Completá la conexión a mano."); state.loaded = true; return; }
      data.profiles.forEach((profile) => {
        const option = document.createElement("option");
        option.value = profile.profile_id;
        option.textContent = profile.client;
        select.appendChild(option);
        state.profiles.set(profile.profile_id, profile);
      });
      state.loaded = true;
    } catch (error) {
      warn(box, "No se pudo leer la lista de clientes. Completá la conexión a mano.");   // el formulario manual no se ve afectado
      state.loaded = true;
    } finally {
      state.loading = false;
    }
  }

  function init(box) {
    if (box.dataset.dwProfileReady) return;
    box.dataset.dwProfileReady = "1";
    const form = box.closest("form");
    const select = qs(box, "[data-dw-profile-select]");
    if (!form || !select) return;
    const p = select.id.replace(/-profile$/, "");
    const state = { loaded: false, loading: false, profiles: new Map() };

    const start = () => load(box, select, state);
    if ("IntersectionObserver" in window) {                 // se carga una sola vez, cuando el selector se hace visible (no al abrir la página)
      const io = new IntersectionObserver((entries) => {
        if (entries.some((e) => e.isIntersecting)) { io.disconnect(); start(); }
      });
      io.observe(box);
    } else {
      select.addEventListener("focus", start, { once: true });
    }
    select.addEventListener("focus", start);
    select.addEventListener("change", () => {
      warn(box, "");
      const profile = state.profiles.get(select.value);
      if (profile) useProfile(form, p, profile); else clearProfile(form, p);
    });
  }

  function initAll() { document.querySelectorAll("[data-dw-owner-profile]").forEach(init); }

  // Envío con perfil elegido: agrega el token de Graph (una sola vez) y deja pasar a los handlers existentes del formulario.
  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    const idField = form.querySelector("[data-dw-profile-id]");
    const tokenField = form.querySelector("[data-dw-graph-field]");
    if (!idField || !tokenField) return;                    // formulario sin selector (p. ej. no dueño): intacto
    if (!idField.value) { tokenField.value = ""; return; }  // modo manual: exactamente como hoy
    if (form.dataset.dwTokenReady === "1") {                // reenvío con token: los handlers existentes arman el cuerpo AHORA
      delete form.dataset.dwTokenReady;
      setTimeout(() => { tokenField.value = ""; }, 0);
      return;
    }
    event.preventDefault();
    event.stopImmediatePropagation();
    Promise.resolve().then(() => (window.dwGraphToken ? window.dwGraphToken() : "")).catch(() => "").then((token) => {
      tokenField.value = token || "";
      form.dataset.dwTokenReady = "1";
      if (form.requestSubmit) form.requestSubmit(); else form.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
    });
  }, true);

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initAll); else initAll();
  document.addEventListener("htmx:afterSwap", initAll);
})();
