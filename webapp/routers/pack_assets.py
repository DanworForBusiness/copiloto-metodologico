"""
pack_assets.py — Pack asset/resource serving (feature COPILOT).

Router de las 3 rutas que sirven archivos de un content pack: el logo animado
(`GET /brand/dancing/{pack_name}`), assets embebibles/standalone (`GET
/pack-assets/{pack_name}/{asset_path:path}`, storytelling de Academy, imágenes de
módulos, etc.) y recursos genéricos (`GET
/pack-resources/{pack_name}/{kind}/{resource_path:path}`). Extraído de
``webapp/main.py`` (Fase 2F) como relocación pura — sin rediseño de auth/pack/
theme/embed/traversal.

Feature COPILOT que consume capacidades compartidas desde sus dueños explícitos
(`core.content_registry`, `webapp.services.auth_service`, `webapp.nav`,
`webapp.presentation.academy_feedback`) SIN importar ``webapp.main``.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, Response

from core.content_registry import resolve_pack, resolve_pack_file, roots_for_kind
from webapp.services import auth_service
from webapp.nav import login_redirect
from webapp.presentation.academy_feedback import feedback_assets

router = APIRouter()

# Constantes JS hardcodeadas en el asset original (identidad animada I+D +
# título + subtítulo, ver danwor-animated-dancing.html) — se sustituyen por
# los valores config-driven del workspace que embebe el iframe (Copiloto:
# APP_TITLE/APP_SUBTITLE; Operaciones: OPERATIONS_APP_TITLE/SUBTITLE). Sin
# `title`/`subtitle` explícitos, el asset conserva su texto original (no-op).
_TITLE_PATTERN = re.compile(r"const TITLE\s*=\s*'(?:[^'\\]|\\.)*';")
_SUBTITLE_PATTERN = re.compile(r"const SUBTITLE\s*=\s*'(?:[^'\\]|\\.)*';")


def _js_single_quoted(value: str) -> str:
    """Escapa `value` para insertarlo como literal JS de comillas simples
    DENTRO de un `<script>` embebido en HTML — no alcanza con escapar para
    el parser de JS (comillas/backslashes): el parser de HTML tokeniza
    `</script>` de forma literal e insensible a mayúsculas ANTES de que
    exista noción de "string JS", así que un valor con `</script>` cierra
    el bloque real aunque esté sintácticamente dentro de un string válido —
    mismo escape ("</" → "<\\/") que ya usa `pack_asset` más abajo para el
    mismo problema (`payload.replace("</", "<\\/")`), reutilizado acá. Los
    saltos de línea también se escapan: un literal `'...'` de JS no admite
    un salto de línea crudo (rompería el script, aunque sin ejecutar nada
    inyectado)."""
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    escaped = escaped.replace("\r\n", "\\n").replace("\r", "\\n").replace("\n", "\\n")
    escaped = escaped.replace("</", "<\\/")
    return "'" + escaped + "'"


# El logo animado es PÚBLICO y determinista: depende solo de (pack, title, subtitle) y del archivo del pack, nunca de la sesión. Se descarga en CADA página del Hub
# (iframe de identidad, ~125 KB) y sin validador ni vigencia el navegador lo bajaba entero en cada navegación, aun con la caché caliente. ETag por contenido + vigencia
# corta: una navegación posterior no vuelve a pedirlo y, vencida la vigencia, un 304 evita el cuerpo. Vigencia acotada: tras un deploy que cambie el asset, a lo sumo
# una hora con el anterior (no es un asset versionado por URL, por eso NO es `immutable`).
DANCING_LOGO_MAX_AGE = 3600


def _dancing_logo_headers(etag: str) -> dict[str, str]:
    return {"ETag": etag, "Cache-Control": f"public, max-age={DANCING_LOGO_MAX_AGE}"}


@router.get("/brand/dancing/{pack_name}", include_in_schema=False)
def dancing_logo(request: Request, pack_name: str, title: str = Query(""), subtitle: str = Query("")):
    selected = resolve_pack(pack_name)
    path = resolve_pack_file("assets", "danwor-animated-dancing.html", selected)
    if not path.exists():
        raise HTTPException(404, "Logo no encontrado")
    source = path.read_text(encoding="utf-8", errors="ignore")
    # El asset original fue diseñado para fondo claro. Esta variante conserva
    # animaciones y dorados, pero usa la paleta legible del shell oscuro.
    dark_shell_colors = {
        "#363636": "#e4e4ec",
        "#1a1a1a": "#e4e4ec",
        "#5e6166": "#8b8b96",
        "#d0d0d0": "rgba(226,232,245,0.18)",
        "0x36": "0xe4",
    }
    for original, replacement in dark_shell_colors.items():
        source = source.replace(original, replacement)
    if title:
        source = _TITLE_PATTERN.sub(lambda m: f"const TITLE    = {_js_single_quoted(title)};", source, count=1)
    if subtitle:
        source = _SUBTITLE_PATTERN.sub(lambda m: f"const SUBTITLE = {_js_single_quoted(subtitle)};", source, count=1)
    etag = '"' + hashlib.sha1(source.encode("utf-8")).hexdigest() + '"'
    headers = _dancing_logo_headers(etag)
    if etag in [v.strip() for v in request.headers.get("if-none-match", "").split(",")]:
        return Response(status_code=304, headers=headers)
    return HTMLResponse(source, headers=headers)


@router.get("/pack-assets/{pack_name}/{asset_path:path}", include_in_schema=False)
def pack_asset(request: Request, pack_name: str, asset_path: str):
    selected = resolve_pack(pack_name)
    # Este endpoint sirve el contenido real (storytelling de Academy, imágenes de
    # módulos, etc.), no solo un shell — sin este chequeo, la URL del botón
    # "pantalla completa" (o cualquier <iframe src> copiado) queda accesible sin
    # sesión aunque el usuario se haya deslogueado. `next` preserva el destino
    # (mismo mecanismo que el resto de las rutas protegidas).
    if not auth_service.session_user(request):
        return login_redirect(request, selected)
    relative = Path(asset_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise HTTPException(400, "Ruta de asset inválida")
    path = resolve_pack_file("assets", relative, selected).resolve()
    allowed_roots = [root.resolve() for root in roots_for_kind("assets", selected)]
    if not path.exists() or not any(path.is_relative_to(root) for root in allowed_roots):
        raise HTTPException(404, "Asset no encontrado")
    if path.suffix.lower() == ".html":
        theme = request.cookies.get("dw-content-theme", "light")
        props = {"theme": theme if theme in ("light", "dark") else "light"}
        for key, value in request.query_params.items():
            normalized = key.lower().replace("-", "_")
            props[normalized] = value
            bits = [part for part in re.split(r"[_-]+", normalized) if part]
            if bits:
                props[bits[0] + "".join(part.title() for part in bits[1:])] = value
        payload = json.dumps(props, ensure_ascii=False).replace("</", "<\\/")
        # Soporte de tema común a TODOS los embeds: en modo oscuro vuelve
        # transparente el fondo del asset (mata el recuadro blanco; se ve el
        # canvas oscuro detrás) y escucha el cambio en vivo del copiloto.
        # OJO: scopeado a [data-dw-embed] — standalone (sin embed=1, botón
        # "pantalla completa") no tiene canvas del copiloto detrás; si se deja
        # transparente ahí, el body queda sin fondo (blanco del navegador) pese
        # a que las variables de color sí resuelven a la paleta oscura.
        theme_support = (
            '<style>html[data-dw-embed][data-dw-theme="dark"],html[data-dw-embed][data-dw-theme="dark"] body'
            '{background:transparent!important}'
            # Scrollbars sutiles en dark (thumb claro translúcido, track transparente).
            'html[data-dw-theme="dark"]{scrollbar-width:thin;scrollbar-color:rgba(255,255,255,.18) transparent}'
            'html[data-dw-theme="dark"]::-webkit-scrollbar,html[data-dw-theme="dark"] ::-webkit-scrollbar'
            '{width:9px;height:9px}'
            'html[data-dw-theme="dark"]::-webkit-scrollbar-track,html[data-dw-theme="dark"] ::-webkit-scrollbar-track'
            '{background:transparent}'
            'html[data-dw-theme="dark"]::-webkit-scrollbar-thumb,html[data-dw-theme="dark"] ::-webkit-scrollbar-thumb'
            '{background:rgba(255,255,255,.16);border-radius:9px;border:3px solid transparent;background-clip:padding-box}'
            'html[data-dw-theme="dark"]::-webkit-scrollbar-thumb:hover,html[data-dw-theme="dark"] ::-webkit-scrollbar-thumb:hover'
            '{background:rgba(255,255,255,.30);background-clip:padding-box}'
            # Mismo scrollbar sutil en claro (thumb oscuro translúcido, como los paneles).
            'html[data-dw-theme="light"]{scrollbar-width:thin;scrollbar-color:rgba(20,22,28,.18) transparent}'
            'html[data-dw-theme="light"]::-webkit-scrollbar,html[data-dw-theme="light"] ::-webkit-scrollbar'
            '{width:9px;height:9px}'
            'html[data-dw-theme="light"]::-webkit-scrollbar-track,html[data-dw-theme="light"] ::-webkit-scrollbar-track'
            '{background:transparent}'
            'html[data-dw-theme="light"]::-webkit-scrollbar-thumb,html[data-dw-theme="light"] ::-webkit-scrollbar-thumb'
            '{background:rgba(20,22,28,.16);border-radius:9px;border:3px solid transparent;background-clip:padding-box}'
            'html[data-dw-theme="light"]::-webkit-scrollbar-thumb:hover,html[data-dw-theme="light"] ::-webkit-scrollbar-thumb:hover'
            '{background:rgba(20,22,28,.30);background-clip:padding-box}'
            # Embebido a pantalla completa (embed=1): el shell del copiloto ya da
            # contexto, así que se ocultan el topbar/progreso/footer propios del HTML.
            'html[data-dw-embed] .topbar,html[data-dw-embed] .scroll-progress,html[data-dw-embed] footer{display:none!important}'
            'html[data-dw-embed] .hero{padding-top:2.5rem}</style>'
            "<script>(function(){function a(t){document.documentElement"
            ".setAttribute('data-dw-theme',t||'light');}"
            "var p=window.DANWOR_EMBED_PROPS||{};a(p.theme);"
            "if(p.embed){document.documentElement.setAttribute('data-dw-embed','1');"
            # Avisar al copiloto qué página se está viendo (para sincronizar el nav
            # izquierdo al navegar entre páginas DENTRO del iframe, p. ej. paradigmas→datasphere).
            # El propio HTML ya arrastra el query completo (branding+embed) a sus cross-links
            # (data-carry-branding), así que embed=1 persiste al navegar dentro.
            "try{parent.postMessage({type:'danwor-academy-nav',"
            "asset:(location.pathname.split('/').pop()||'')},'*');}catch(_){}}"
            "window.addEventListener('message',function(e){"
            "if(e.data&&e.data.type==='danwor-theme')a(e.data.theme);});})();</script>"
        )
        injection = f"<script>window.DANWOR_EMBED_PROPS = {payload};</script>{theme_support}"
        source = path.read_text(encoding="utf-8", errors="ignore")
        academy_feedback_stem = str(request.query_params.get("academy_feedback") or "").strip()
        academy_feedback = ""
        if academy_feedback_stem and relative.parts and relative.parts[0].lower() == "academy":
            academy_feedback = feedback_assets(academy_feedback_stem, selected)
        if academy_feedback:
            if re.search(r"</body>", source, re.I):
                source = re.sub(r"</body>", academy_feedback + "\n</body>", source, count=1, flags=re.I)
            else:
                source = source + "\n" + academy_feedback
        if re.search(r"</head>", source, re.I):
            source = re.sub(r"</head>", injection + "\n</head>", source, count=1, flags=re.I)
        else:
            source = injection + "\n" + source
        return HTMLResponse(source)
    return FileResponse(path)


@router.get("/pack-resources/{pack_name}/{kind}/{resource_path:path}", include_in_schema=False)
def pack_resource(request: Request, pack_name: str, kind: str, resource_path: str):
    selected = resolve_pack(pack_name)
    if not auth_service.session_user(request):
        return login_redirect(request, selected)
    if kind not in {"assets", "templates"}:
        raise HTTPException(404, "Tipo de recurso no encontrado")
    relative = Path(resource_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise HTTPException(400, "Ruta de recurso inválida")
    path = resolve_pack_file(kind, relative, selected).resolve()
    allowed_roots = [root.resolve() for root in roots_for_kind(kind, selected)]
    if not path.exists() or not any(path.is_relative_to(root) for root in allowed_roots):
        raise HTTPException(404, "Recurso no encontrado")
    return FileResponse(path)
