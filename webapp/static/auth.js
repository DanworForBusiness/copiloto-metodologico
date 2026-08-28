/* auth.js — la UI del login con Microsoft (Entra ID).

   FASE 1B (owner decision 2026-08-23): este archivo YA NO implementa OAuth.
   Antes tenía el flujo PKCE completo escrito a mano —generación de verifier y
   challenge con `crypto.subtle`, `state` en localStorage, apertura del popup,
   redirect a `authorize`, intercambio del `code` contra el token endpoint, y
   un handshake de `postMessage` para devolverle el resultado al opener—.
   Todo eso lo hace ahora MSAL Browser, detrás de la autoridad compartida del
   Hub (`dwHubLogin` en hub_auth.js).

   Lo que queda acá es lo que siempre fue de esta capa: el botón, el overlay,
   los mensajes de error y la advertencia de storage restringido.

   POR QUÉ IMPORTA que el protocolo se haya ido: el PKCE a mano adquiría el
   access token UNA sola vez y no tenía forma de renovarlo (no pedía
   `offline_access`, no había refresh token). De ahí venía la ventana de horas
   en las que la sesión del Hub seguía viva y la autorización de Graph ya
   estaba muerta. MSAL sí puede renovar, y es dueño completo de esos
   artefactos: ninguna línea de la app los lee, escribe, rota ni exporta.

   LO QUE NO CAMBIÓ, a propósito:
   * el popup sigue siendo popup (la app puede ir embebida en un iframe, y
     Microsoft no autentica dentro de un iframe);
   * la sesión del Hub la sigue creando el BACKEND con el `id_token` validado
     (`POST /api/auth/microsoft/token`), no el cliente;
   * la cookie sigue siendo `SameSite=None; Secure` para viajar en el iframe;
   * los permisos de Graph son los mismos (`User.Read`, `Sites.Read.All`);
   * `prompt: "select_account"` se preserva.

   PENDIENTE PARA EL OWNER (no bloqueante, requiere Azure): el `redirectUri`
   sigue siendo `origin + "/"`, el único registrado como SPA. MSAL lee el
   resultado del popup desde el opener, así que funciona — pero el popup
   renderiza la landing un instante antes de cerrarse. Apuntarlo a una página
   mínima lo evitaría, y eso implica registrar un redirect URI nuevo en Azure. */
(function () {
  const MIN_ERROR = "No se pudo iniciar sesión";

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
      box.textContent = message || MIN_ERROR;
    }
    box.classList.remove("hidden");
    document.getElementById("acceso")?.scrollIntoView({ behavior: "smooth" });
  }

  // Click "Ingresar". El popup, el protocolo y la cache son de MSAL; de acá
  // sale el estado del botón y el destino final.
  window.loginWithMicrosoft = async function (event) {
    if (event) event.preventDefault();
    const btn = document.getElementById("microsoft-login-btn");
    if (btn) { btn.disabled = true; btn.textContent = "Abriendo Microsoft…"; }
    try {
      await window.dwHubLogin();
      setOverlay(true);
      window.location.href = homeUrl();
    } catch (err) {
      setOverlay(false);
      resetButton();
      // El usuario que cierra el popup no cometió un error: MSAL lo reporta
      // como `user_cancelled` y no corresponde mostrarle una alerta roja.
      const code = (err && err.errorCode) || "";
      if (code === "user_cancelled" || code === "popup_window_error") {
        if (code === "popup_window_error") {
          showError("El navegador bloqueó la ventana de Microsoft. Habilitá los pop-ups e intentá de nuevo.");
        }
        return;
      }
      showError((err && err.message) || MIN_ERROR, err && err.detail);
    }
  };

  // Cookies/storage de terceros bloqueadas (incógnito, Safari/Chrome mobile): el
  // popup autentica en un contexto de PRIMERA parte (ondigitalocean.app top-level),
  // pero el iframe embebido bajo danwor.com.ar es TERCERO para ese mismo origen —
  // ahí la cookie de sesión no llega, y el login termina en un loop silencioso (o
  // en un popup huérfano mostrando la app sola). document.hasStorageAccess() es la
  // API real para esto (Safari/Firefox/Chrome recientes): dice si ESTE frame ya
  // tiene acceso a sus cookies sin partición. Solo aplica si estamos embebidos —
  // en acceso directo (sin iframe) nunca hay problema, sea cual sea el navegador.
  //
  // NO se usa para pedir acceso ni para ningún workaround de credenciales: sólo
  // avisa antes de que el usuario intente algo que no va a poder completar.
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

  // Init. Ya no hay que decidir "soy popup o soy la app": MSAL maneja su propia
  // ventana. Sólo queda avisar si el contexto no va a poder completar el login.
  warnIfStorageRestricted();
})();
