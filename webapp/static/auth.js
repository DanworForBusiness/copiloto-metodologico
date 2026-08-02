/* auth.js — Login Microsoft (Entra ID) OAuth2 + PKCE en POPUP.
   La app puede ir embebida en un iframe (danwor.com/copiloto), y Microsoft no
   permite autenticar dentro de un iframe. Por eso el login abre una ventana
   nueva (top-level): authorize → ?code → exchange → /api/auth/microsoft/token
   (setea la cookie de sesión) → postMessage al opener → la app entra.
   La cookie debe ser SameSite=None; Secure para viajar en el iframe. */
(function () {
  const API = "/api/auth";
  const STATE_KEY = "dw_ms_state";
  const VERIFIER_PREFIX = "dw_ms_verifier_";
  const MSG_TYPE = "danwor-auth";

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
  // Debe coincidir EXACTO con el redirect URI (SPA) registrado en Azure (con barra).
  function redirectUri() {
    return window.location.origin + "/";
  }
  function homeUrl() {
    const btn = document.getElementById("microsoft-login-btn");
    return (btn && btn.getAttribute("data-home")) || "/";
  }
  function resetButton() {
    const btn = document.getElementById("microsoft-login-btn");
    if (btn) { btn.disabled = false; btn.textContent = "Ingresar con tu cuenta Danwor"; }
  }
  function setOverlay(on) {
    document.body.classList.toggle("auth-completing", on);
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

  async function getConfig() {
    const r = await fetch(`${API}/microsoft/config`);
    const c = await r.json();
    if (!r.ok || !c.enabled || !c.tenant_id || !c.client_id) {
      throw new Error("Microsoft no está configurado para esta app");
    }
    return c;
  }

  // Corre DENTRO del popup: arranca el redirect a Microsoft.
  async function startAuthInThisWindow() {
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
      scope: "openid profile email User.Read Sites.Read.All",
      state,
      code_challenge: challenge,
      code_challenge_method: "S256",
      prompt: "select_account",
    });
    window.location.replace(
      `https://login.microsoftonline.com/${encodeURIComponent(c.tenant_id)}/oauth2/v2.0/authorize?${p}`
    );
  }

  async function exchangeCode(code, verifier) {
    const c = await getConfig();
    const body = new URLSearchParams({
      client_id: c.client_id,
      scope: "openid profile email User.Read Sites.Read.All",
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
    // Devolvemos también el access_token (Graph): lo usa el seed de casos SAP.
    return { id_token: d.id_token, access_token: d.access_token || "" };
  }

  function rememberGraphToken(token) {
    if (!token) return;
    try { sessionStorage.setItem("dw-graph-token", token); } catch (e) { /* sin storage: se omite */ }
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

  function notifyOpener(payload) {
    const ok = !!(window.opener && !window.opener.closed);
    if (ok) {
      window.opener.postMessage({ type: MSG_TYPE, ...payload }, window.location.origin);
      window.close();
      return true;
    }
    return false;
  }

  // Corre DENTRO del popup cuando Microsoft vuelve con ?code (o ?error).
  async function handleRedirect() {
    const params = new URLSearchParams(window.location.search);
    const error = params.get("error");
    const code = params.get("code");
    const state = params.get("state");

    if (error) {
      const msg = params.get("error_description") || "Microsoft canceló el inicio de sesión";
      if (!notifyOpener({ status: "error", message: msg })) showError(msg);
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
      const tokens = await exchangeCode(code, verifier);
      await createSession(tokens.id_token);
      // Éxito: avisar al opener (la app) para que entre; si no hay opener, navegar acá.
      // El token Graph viaja al opener por postMessage (mismo origen) y se guarda
      // en su sessionStorage; si no hay opener, lo guardamos en esta ventana.
      if (!notifyOpener({ status: "success", graph_token: tokens.access_token })) {
        rememberGraphToken(tokens.access_token);
        window.location.replace(homeUrl());
      }
    } catch (err) {
      setOverlay(false);
      if (!notifyOpener({ status: "error", message: err.message, detail: err.detail })) {
        showError(err.message, err.detail);
      }
    }
    return true;
  }

  // Click "Ingresar": abre el popup (top-level, fuera del iframe).
  window.loginWithMicrosoft = function (event) {
    if (event) event.preventDefault();
    const btn = document.getElementById("microsoft-login-btn");
    if (btn) { btn.disabled = true; btn.textContent = "Abriendo Microsoft…"; }
    const popup = window.open(
      window.location.origin + "/?auth_popup=1",
      "dwMsLogin",
      "width=520,height=720,menubar=no,toolbar=no,location=yes,status=no"
    );
    if (!popup) {
      resetButton();
      showError("El navegador bloqueó la ventana de Microsoft. Habilitá los pop-ups e intentá de nuevo.");
      return;
    }
    const timer = setInterval(() => {
      if (popup.closed) { clearInterval(timer); resetButton(); }
    }, 500);
  };

  // En el OPENER (la app): escucha el resultado del popup.
  window.addEventListener("message", (event) => {
    if (event.origin !== window.location.origin || !event.data || event.data.type !== MSG_TYPE) return;
    if (event.data.status === "success") {
      rememberGraphToken(event.data.graph_token);
      window.location.href = homeUrl();
    } else {
      resetButton();
      showError(event.data.message, event.data.detail);
    }
  });

  // Cookies/storage de terceros bloqueadas (incógnito, Safari/Chrome mobile): el
  // popup autentica en un contexto de PRIMERA parte (ondigitalocean.app top-level),
  // pero el iframe embebido bajo danwor.com.ar es TERCERO para ese mismo origen —
  // ahí la cookie de sesión no llega, y el login termina en un loop silencioso (o
  // en un popup huérfano mostrando la app sola). document.hasStorageAccess() es la
  // API real para esto (Safari/Firefox/Chrome recientes): dice si ESTE frame ya
  // tiene acceso a sus cookies sin partición. Solo aplica si estamos embebidos —
  // en acceso directo (sin iframe) nunca hay problema, sea cual sea el navegador.
  async function warnIfStorageRestricted() {
    if (window.top === window.self) return false;               // no embebido: no aplica
    if (!("hasStorageAccess" in document)) return false;         // API no soportada: no bloqueamos a ciegas
    let hasAccess = true;
    try { hasAccess = await document.hasStorageAccess(); } catch (e) { return false; }
    if (hasAccess) return false;
    const btn = document.getElementById("microsoft-login-btn");
    const warning = document.getElementById("storage-restricted-warning");
    if (btn) btn.classList.add("hidden");
    if (warning) warning.classList.remove("hidden");
    return true;
  }

  // Init: decidir el rol de esta ventana.
  (async function init() {
    const params = new URLSearchParams(window.location.search);
    if (await handleRedirect()) return;                         // popup volviendo de Microsoft
    if (window.opener && params.get("auth_popup") === "1") {    // popup recién abierto
      try { await startAuthInThisWindow(); }
      catch (err) { if (!notifyOpener({ status: "error", message: err.message })) showError(err.message); }
      return;
    }
    // si no, es la app normal (opener): antes de esperar el click, chequeamos si
    // el login va a poder completarse en este contexto.
    await warnIfStorageRestricted();
  })();
})();
