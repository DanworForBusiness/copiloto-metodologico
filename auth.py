"""
auth.py - Autenticacion OAuth corporativa via Microsoft Entra ID (Azure AD).

Dependencia: pip install streamlit-msal

Uso en app.py (al inicio, antes de cualquier render):
    from auth import require_login, get_current_user
    require_login()
    user = get_current_user()   # {"name": "...", "username": "...", "token": "..."}
"""

from __future__ import annotations

import html

import streamlit as st
from config import ALLOWED_USERS, AZURE_CLIENT_ID, AZURE_TENANT_ID


def _allowed_users() -> set[str] | None:
    """
    Devuelve None cuando el acceso está abierto a cualquier usuario autenticado.
    Si ALLOWED_USERS contiene una lista CSV, normaliza los mails a minúscula.
    """
    raw = (ALLOWED_USERS or "").strip()
    if not raw or raw.upper() == "ALL":
        return None
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _is_authorized_user(username: str) -> bool:
    allowed = _allowed_users()
    if allowed is None:
        return True
    return (username or "").strip().lower() in allowed


def require_login() -> None:
    """
    Bloquea el acceso a la app hasta que el usuario se autentique.
    Si ya hay sesion activa, no hace nada.
    """
    try:
        from streamlit_msal import Msal
    except ImportError:
        st.error("Dependencia faltante: ejecuta `pip install streamlit-msal`")
        st.stop()

    auth_data = Msal.initialize(
        client_id=AZURE_CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{AZURE_TENANT_ID}",
        scopes=["User.Read"],
    )

    if not auth_data:
        _render_login_screen()
        st.stop()

    account = auth_data.get("account", {})
    full_name = account.get("name", "Usuario")
    first_name = full_name.split()[0] if full_name else "Usuario"
    username = account.get("username", "")
    token = auth_data.get("accessToken", "")

    if not _is_authorized_user(username):
        st.session_state["_auth_denied"] = username or "usuario no identificado"
        Msal.sign_out()
        st.stop()

    # Microsoft Graph expone jobTitle con User.Read. Si falla, el login no debe bloquear.
    job_title = ""
    if token:
        try:
            import json as _json
            import urllib.request

            req = urllib.request.Request(
                "https://graph.microsoft.com/v1.0/me?$select=jobTitle",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = _json.loads(resp.read())
                job_title = data.get("jobTitle", "") or ""
        except Exception:
            pass

    st.session_state["_auth"] = {
        "name": full_name,
        "first_name": first_name,
        "username": username,
        "job_title": job_title,
        "token": token,
    }


def get_current_user() -> dict:
    """
    Retorna los datos del usuario autenticado.
    Requiere haber llamado require_login() antes.
    """
    return st.session_state.get(
        "_auth",
        {
            "name": "Usuario",
            "first_name": "Usuario",
            "username": "",
            "job_title": "",
            "token": "",
        },
    )


def render_logout_button() -> None:
    """
    Renderiza usuario y estado de sesion en el sidebar.
    Apagar el toggle equivale a cerrar sesion.
    """
    try:
        from streamlit_msal import Msal
    except ImportError:
        return

    user = get_current_user()
    display_name = html.escape(user.get("name") or "Usuario")

    st.sidebar.markdown(
        """
<style>
[data-testid="stSidebar"] .dw-sidebar-session-user {
    color: rgba(255,255,255,.94) !important;
    font-size: 0.82rem !important;
    font-weight: 650 !important;
    line-height: 1.25 !important;
    padding: 2px 0 3px 0 !important;
    white-space: nowrap !important;
    overflow: hidden !important;
    text-overflow: ellipsis !important;
}
[data-testid="stSidebar"] .st-key-session_active_toggle {
    display: flex !important;
    justify-content: flex-end !important;
    align-items: center !important;
    margin: 0 !important;
    padding: 0 !important;
}
[data-testid="stSidebar"] .st-key-session_active_toggle [data-testid="stWidgetLabel"] {
    display: none !important;
}
[data-testid="stSidebar"] .st-key-session_active_toggle label {
    margin: 0 !important;
    padding: 0 !important;
}
[data-testid="stSidebar"] .st-key-session_active_toggle div[data-testid="stElementContainer"],
[data-testid="stSidebar"] .st-key-session_active_toggle div[data-testid="stMarkdownContainer"] {
    margin: 0 !important;
    padding: 0 !important;
}
</style>
""",
        unsafe_allow_html=True,
    )

    toggle_key = "session_active_toggle"
    if toggle_key not in st.session_state:
        st.session_state[toggle_key] = True

    def _sign_out_if_disabled() -> None:
        if st.session_state.get(toggle_key, True):
            return
        Msal.sign_out()
        st.session_state.pop("_auth", None)
        st.session_state.pop(toggle_key, None)
        st.rerun()

    col_user, col_toggle = st.sidebar.columns([0.82, 0.18], vertical_alignment="center")
    with col_user:
        st.markdown(
            f"<div class='dw-sidebar-session-user'>Bienvenido {display_name}!</div>",
            unsafe_allow_html=True,
        )
    with col_toggle:
        st.toggle(
            "Sesion activa",
            key=toggle_key,
            label_visibility="collapsed",
            on_change=_sign_out_if_disabled,
        )


def _render_login_screen() -> None:
    """
    Pantalla de login corporativo.

    El layout se resuelve con HTML/CSS fijo para mantener el split visual y permitir
    que el boton MSAL siga siendo un boton Streamlit real, necesario para disparar
    Msal.sign_in() sin depender de JavaScript custom.
    """
    try:
        from streamlit_msal import Msal
    except ImportError:
        return

    from config import APP_SUBTITLE, APP_TITLE

    app_title = html.escape(APP_TITLE)
    app_subtitle = html.escape(APP_SUBTITLE)
    logo_url = "https://danwor.com/logo_dfb.png"

    st.markdown(
        f"""
<style>
:root {{
    --dw-login-black: #101820;
    --dw-login-black-soft: #17222c;
    --dw-login-gold: #EAB308;
    --dw-login-muted: #8a96a3;
    --dw-login-border: #e8e4d9;
    --dw-login-bg: #f7f7f5;
}}

[data-testid="stAppViewContainer"] > .main {{
    padding: 0 !important;
}}
[data-testid="stAppViewBlockContainer"] {{
    padding: 0 !important;
    max-width: 100% !important;
}}
section[data-testid="stMain"] > div {{
    padding: 0 !important;
}}
[data-testid="stSidebar"],
[data-testid="stHeader"],
[data-testid="stToolbar"],
footer {{
    display: none !important;
}}
[data-testid="stAppViewContainer"] {{
    background: var(--dw-login-bg) !important;
}}

.dw-login-left {{
    position: fixed;
    inset: 0 auto 0 0;
    width: 42%;
    min-width: 520px;
    background:
        radial-gradient(ellipse at 18% 18%, rgba(234,179,8,.085) 0%, transparent 58%),
        radial-gradient(ellipse at 86% 82%, rgba(234,179,8,.055) 0%, transparent 52%),
        var(--dw-login-black);
    color: #fff;
    z-index: 20;
    overflow: hidden;
    font-family: "Segoe UI", system-ui, sans-serif;
}}
.dw-login-left::after {{
    content: "";
    position: absolute;
    inset: 0;
    background-image: linear-gradient(rgba(255,255,255,.018) 1px, transparent 1px);
    background-size: 100% 4px;
    pointer-events: none;
    opacity: .45;
}}
.dw-login-left-inner {{
    position: relative;
    z-index: 1;
    height: 100vh;
    padding: 54px 64px 44px;
    display: flex;
    flex-direction: column;
}}
.dw-login-logo {{
    width: 178px;
    height: auto;
    display: block;
}}
.dw-login-copy {{
    flex: 1;
    display: flex;
    flex-direction: column;
    justify-content: center;
    max-width: 560px;
}}
.dw-login-kicker {{
    width: max-content;
    max-width: 100%;
    color: var(--dw-login-gold);
    border: 1px solid rgba(234,179,8,.42);
    border-radius: 999px;
    padding: 5px 12px;
    font-size: 11px;
    font-weight: 750;
    letter-spacing: .08em;
    text-transform: uppercase;
    margin-bottom: 12px;
}}
.dw-login-title {{
    margin: 0;
    font-size: 40px;
    line-height: 1.08;
    letter-spacing: -0.02em;
    font-weight: 800;
}}
.dw-login-title__gold {{
    color: var(--dw-login-gold);
}}
.dw-login-title__white {{
    color: #fff;
}}
.dw-login-desc {{
    margin: 26px 0 0;
    color: rgba(238,242,246,.72);
    font-size: 16px;
    line-height: 1.7;
    max-width: 560px;
}}
.dw-login-features {{
    display: grid;
    gap: 14px;
    margin-top: 34px;
}}
.dw-login-feature {{
    display: flex;
    align-items: center;
    gap: 13px;
    color: rgba(255,255,255,.90);
    font-size: 15px;
}}
.dw-login-check {{
    width: 26px;
    height: 26px;
    border-radius: 999px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    background: var(--dw-login-gold);
    color: var(--dw-login-black);
    font-size: 15px;
    font-weight: 900;
    flex: 0 0 auto;
}}
.dw-login-footer {{
    color: rgba(238,242,246,.36);
    font-size: 12px;
}}

.dw-login-right {{
    position: fixed;
    inset: 0 0 0 auto;
    width: 58%;
    height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--dw-login-bg);
    z-index: 10;
    font-family: "Segoe UI", system-ui, sans-serif;
}}
.dw-card {{
    position: relative;
    width: min(520px, calc(100vw - 48px));
    background: #fff;
    border: 1px solid var(--dw-login-border);
    border-bottom: 4px solid var(--dw-login-gold);
    border-radius: 16px;
    padding: 52px 54px 76px;
    box-shadow: 0 18px 54px rgba(16,24,32,.08);
    text-align: center;
}}
.dw-card-title {{
    font-size: 1.82rem;
    font-weight: 850;
    color: #0d0d0d;
    letter-spacing: -0.03em;
    white-space: nowrap;
    text-align: center;    
}}
.dw-card-sub {{
    margin: 0 0 34px;
    color: #737b84;
    font-size: .92rem;
    line-height: 1.55;
    text-align: center;
}}
.dw-login-slot {{
    height: 68px;
}}

.st-key-login_btn,
div[data-testid="stButton"].st-key-login_btn {{
    position: fixed !important;
    left: 71%;
    top: calc(50% + 68px);
    transform: translateX(-50%);
    width: min(360px, calc(100vw - 108px));
    z-index: 1000;
    margin: 0 !important;
}}
.st-key-login_btn div[data-testid="stButton"] {{
    width: 100% !important;
    margin: 0 !important;
}}
.st-key-login_btn button,
div[data-testid="stButton"].st-key-login_btn > button {{
    width: 100% !important;
    min-height: 46px !important;
    background: #0d0d0d !important;
    color: #fff !important;
    border: 0 !important;
    border-radius: 10px !important;
    padding: 13px 22px !important;
    font-size: .95rem !important;
    font-weight: 700 !important;
    letter-spacing: .01em !important;
    cursor: pointer !important;
    box-shadow: 0 8px 18px rgba(0,0,0,.12) !important;
    transition: background .16s ease, transform .16s ease, box-shadow .16s ease !important;
}}
.st-key-login_btn button:hover,
div[data-testid="stButton"].st-key-login_btn > button:hover {{
    background: #1a1a1a !important;
    border: 0 !important;
    transform: translateY(-1px);
    box-shadow: 0 10px 22px rgba(0,0,0,.15) !important;
}}
.st-key-login_btn button:focus,
div[data-testid="stButton"].st-key-login_btn > button:focus {{
    outline: none !important;
    box-shadow: 0 0 0 3px rgba(234,179,8,.36), 0 8px 18px rgba(0,0,0,.12) !important;
}}
.dw-access-note {{
    position: fixed;
    left: 71%;
    top: calc(50% + 130px);
    transform: translateX(-50%);
    width: min(360px, calc(100vw - 108px));
    z-index: 1000;
    color: #a9b0b8;
    font: 12px/1.45 "Segoe UI", system-ui, sans-serif;
    text-align: center;
}}
.dw-login-denied {{
    position: fixed;
    left: 71%;
    top: calc(50% + 166px);
    transform: translateX(-50%);
    width: min(360px, calc(100vw - 108px));
    z-index: 1000;
    color: #8a4b00;
    background: rgba(234,179,8,.12);
    border: 1px solid rgba(234,179,8,.38);
    border-radius: 10px;
    padding: 10px 12px;
    font: 12px/1.45 "Segoe UI", system-ui, sans-serif;
    text-align: center;
}}

@media (max-width: 1120px) {{
    .dw-login-left {{
        min-width: 430px;
    }}
    .dw-login-left-inner {{
        padding: 42px 44px 36px;
    }}
    .dw-login-title {{
        font-size: 34px;
    }}
}}
@media (max-width: 900px) {{
    .dw-login-left {{
        display: none;
    }}
    .dw-login-right {{
        left: 0;
        width: 100%;
    }}
    .st-key-login_btn,
    div[data-testid="stButton"].st-key-login_btn,
    .dw-access-note,
    .dw-login-denied {{
        left: 50%;
    }}
}}
</style>

<section class="dw-login-left" aria-label="Presentacion Danwor">
  <div class="dw-login-left-inner">
    <img class="dw-login-logo" src="{logo_url}" alt="Danwor">
    <div class="dw-login-copy">
      <h1 class="dw-login-title">
        <span class="dw-login-title__white">Copiloto Metodologico</span><br>
        <span class="dw-login-title__gold">Arquitecto Danwor</span>
      </h1>
      <p class="dw-login-desc">
        Un entorno guiado para recorrer nuestro marco metodologico, consultar decisiones
        tecnicas y acelerar entregables con criterio arquitectonico y mirada Danwor.
      </p>
      <div class="dw-login-features">
        <div class="dw-login-feature"><span class="dw-login-check">&#10003;</span><span>Marco metodologico navegable</span></div>
        <div class="dw-login-feature"><span class="dw-login-check">&#10003;</span><span>Arboles de decision y asistencia IA</span></div>
        <div class="dw-login-feature"><span class="dw-login-check">&#10003;</span><span>Criterios tecnicos para proyectos SAP</span></div>
      </div>
    </div>
    <div class="dw-login-footer">&copy; 2026 Danwor &middot; Data & AI for Business &vert; Danwor Transversal</div>
  </div>
</section>

<section class="dw-login-right" aria-label="Acceso corporativo">
  <div class="dw-card">
    <h2 class="dw-card-title">¡Hola, bienvenido!</h2>
    <p class="dw-card-sub">{app_title}<br>{app_subtitle}</p>
    <div class="dw-login-slot" aria-hidden="true"></div>
  </div>
</section>
""",
        unsafe_allow_html=True,
    )

    denied_user = st.session_state.pop("_auth_denied", "")
    if denied_user:
        st.markdown(
            f"""
<div class="dw-login-denied">
  El usuario {html.escape(denied_user)} no está autorizado para ingresar.
</div>
""",
            unsafe_allow_html=True,
        )

    if st.button("Ingresar con tu cuenta Danwor", use_container_width=True, key="login_btn"):
        Msal.sign_in()

    st.markdown(
        """
<div class="dw-access-note">Autenticacion segura via dominio @danwor.com | Microsoft 365</div>
""",
        unsafe_allow_html=True,
    )
