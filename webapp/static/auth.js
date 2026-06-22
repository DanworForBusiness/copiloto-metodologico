/* auth.js — Login Microsoft (Entra ID) OAuth2 + PKCE del lado del navegador.
   Portado del login de Odoo (C:\Users\Joe\Odoo) a flujo de página completa:
   authorize → vuelve con ?code → intercambia por id_token → POST a
   /api/auth/microsoft/token (el backend valida, allow-list y setea cookie) →
   entra a la app. El registro de Azure es SPA (mismo que usa el Streamlit). */
(function () {
  const API = "/api/auth";
  const STATE_KEY = "dw_ms_state";
  const VERIFIER_PREFIX = "dw_ms_verifier_";

  function b64url(buffer) {
    const bytes = new Uint8Array(buffer);
    let bin = "";
    bytes.forEach((b) => (bin += String.fromCharCode(b)));
    return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
  }
  function randomB64(len = 32) {
    const v = new Uint8Array(len);
    crypto.getRandomValues(v);
    return b64url(v);
  }
  async function sha256(value) {
    return crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  }
  // Debe coincidir EXACTO con el redirect URI registrado en Azure (con barra).
  function redirectUri() {
    return window.location.origin + "/";
  }
  function homeUrl() {
    const btn = document.getElementById("microsoft-login-btn");
    return (btn && btn.getAttribute("data-home")) || "/";
  }

  function showError(message, detail) {
    const box = document.getElementById("login-message");
    if (!box) return;
    if (detail && detail.code === "microsoft_user_not_authorized") {
      box.className = "login-error";
      box.innerHTML =
        "<strong>Acceso pendiente de habilitación.</strong> El usuario <b>" +
        String(detail.denied_user || "").replace(/[<>&]/g, "") +
        "</b> autenticó correctamente, pero todavía no está autorizado para ingresar.";
    } else {
      box.className = "login-error";
      box.textContent = message || "No se pudo iniciar sesión";
    }
    box.classList.remove("hidden");
    document.getElementById("acceso")?.scrollIntoView({ behavior: "smooth" });
  }

  function setOverlay(on) {
    document.body.classList.toggle("auth-completing", on);
  }
  function cleanUrl() {
    window.history.replaceState({}, document.title, window.location.pathname);
  }

  async function getConfig() {
    const r = await fetch(`${API}/microsoft/config`);
    const c = await r.json();
    if (!r.ok || !c.enabled || !c.tenant_id || !c.client_id) {
      throw new Error("Microsoft no está configurado para esta app");
    }
    return c;
  }

  async function startLogin() {
    const c = await getConfig();
    const verifier = randomB64(64);
    const challenge = b64url(await sha256(verifier));
    const state = randomB64(32);
    localStorage.setItem(STATE_KEY, state);
    localStorage.setItem(VERIFIER_PREFIX + state, verifier);
    const p = new URLSearchParams({
      client_id: c.client_id,
      response_type: "code",
      redirect_uri: redirectUri(),
      response_mode: "query",
      scope: "openid profile email User.Read",
      state,
      code_challenge: challenge,
      code_challenge_method: "S256",
      prompt: "select_account",
    });
    window.location.href =
      `https://login.microsoftonline.com/${encodeURIComponent(c.tenant_id)}/oauth2/v2.0/authorize?${p}`;
  }

  async function exchangeCode(code, verifier) {
    const c = await getConfig();
    const body = new URLSearchParams({
      client_id: c.client_id,
      scope: "openid profile email User.Read",
      code,
      redirect_uri: redirectUri(),
      grant_type: "authorization_code",
      code_verifier: verifier,
    });
    const r = await fetch(
      `https://login.microsoftonline.com/${encodeURIComponent(c.tenant_id)}/oauth2/v2.0/token`,
      { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body }
    );
    const d = await r.json().catch(() => ({}));
    if (!r.ok || !d.id_token) throw new Error(d.error_description || "Microsoft no devolvió un token válido");
    return d.id_token;
  }

  async function createSession(idToken) {
    const r = await fetch(`${API}/microsoft/token`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id_token: idToken }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) {
      const det = d.detail;
      const err = new Error((det && det.message) || "No se pudo crear la sesión");
      err.detail = det;
      throw err;
    }
    return d;
  }

  async function handleRedirect() {
    const params = new URLSearchParams(window.location.search);
    const error = params.get("error");
    const code = params.get("code");
    const state = params.get("state");

    if (error) {
      cleanUrl();
      showError(params.get("error_description") || "Microsoft canceló el inicio de sesión");
      return true;
    }
    if (!code) return false;

    setOverlay(true);
    try {
      const expected = localStorage.getItem(STATE_KEY);
      const verifier = state ? localStorage.getItem(VERIFIER_PREFIX + state) : null;
      localStorage.removeItem(STATE_KEY);
      if (state) localStorage.removeItem(VERIFIER_PREFIX + state);
      if (!state || state !== expected || !verifier) {
        throw new Error("No se pudo validar el estado del login (reintentá desde el inicio)");
      }
      const idToken = await exchangeCode(code, verifier);
      await createSession(idToken);
      window.location.replace(homeUrl());
    } catch (err) {
      setOverlay(false);
      cleanUrl();
      showError(err.message, err.detail);
    }
    return true;
  }

  window.loginWithMicrosoft = function (event) {
    if (event) event.preventDefault();
    const btn = document.getElementById("microsoft-login-btn");
    if (btn) { btn.disabled = true; btn.textContent = "Abriendo Microsoft…"; }
    startLogin().catch((err) => {
      if (btn) { btn.disabled = false; btn.textContent = "Ingresar con tu cuenta Danwor"; }
      showError(err.message);
    });
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", handleRedirect);
  else handleRedirect();
})();
