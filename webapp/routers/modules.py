"""
modules.py — Module page HTTP (feature COPILOT).

Router de la única página de módulo (`GET /modules/{stem}`): resuelve
autenticación/pack/tab inicial, arma el nav combinado (módulos + apéndices),
el layout inmersivo (storytelling embebido), el contexto de Trees/Wizard y
de Assistant que se muestran en el panel derecho, y el contexto de Catálogo
que consume el tab correspondiente. Extraído de ``webapp/main.py`` (Fase
2J-D) como relocación pura — sin rediseño de auth/pack/tabs/layout/Catálogo.

Feature COPILOT que consume capacidades compartidas desde sus dueños
explícitos (`core.content_registry`, `core.prompts`, `config`,
`webapp.access`, `webapp.nav`, `webapp.presentation.invite`,
`webapp.services.auth_service`, `webapp.services.content_service`,
`webapp.services.activity_service`, `webapp.services.section_analysis`,
`webapp.services.whats_new_service`, `webapp.services.catalog_reads`,
`webapp.templating`) y el estado de proceso del Copiloto directo de
`webapp.copilot_runtime` (`store`, `assistant_store`) — SIN importar
``webapp.main``.

Las lecturas de Catálogo (`catalog_domains`, `catalog_connection_types`,
`catalog_security_families`, `catalog_data_products`) se consumen DIRECTO
de `webapp.services.catalog_reads` (dueño resuelto en Fase 2I) — no de los
alias transicionales `_catalog_*` de `webapp.main`, que siguen ahí solo
para las rutas HTTP de Catálogo residuales.
"""
from __future__ import annotations

import os
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from core.content_registry import (
    DEFAULT_PACK,
    available_content_packs,
    content_pack_label,
    content_pack_short_label,
    resolve_pack,
)
from core.prompts import (
    ALL_ACTIONS,
    MODULE_ACTIONS,
    MODULE_ACTION_DESCRIPTIONS,
    get_loading_message,
)
from config import ia_enabled_for
from webapp import access
from webapp import nav
from webapp import workspaces
from webapp.copilot_runtime import assistant_store, store
from webapp.presentation import invite
from webapp.services import auth_service
from webapp.services import catalog_reads
from webapp.services.activity_service import log_activity
from webapp.services.content_service import (
    academy_asset_for,
    list_academy,
    list_appendices,
    list_modules,
    load_module,
    split_front_matter,
)
from webapp.services.section_analysis import ACTION_DESCRIPTIONS
from webapp.services import whats_new_service as whatsnew
from webapp.templating import templates

router = APIRouter()

_MODULE_TABS = {"section", "tree", "catalogo", "assistant"}


@router.get("/modules/{stem}", response_class=HTMLResponse)
def module_page(request: Request, stem: str, pack: str = Query(DEFAULT_PACK), tab: str = ""):
    diagnostic = access.is_diagnostic(request)
    # Fuera de modo diagnóstico, el marco es siempre el productivo (Datasphere).
    selected = resolve_pack(pack) if diagnostic else DEFAULT_PACK
    user = auth_service.session_user(request)
    if not user:  # sin sesión válida → al landing/acceso
        return nav.login_redirect(request, selected)
    modules = list_modules(selected)
    all_appendices = list_appendices(selected)
    # Apéndices con nav_hidden (p. ej. A06 en revisión) salen del nav y del pager,
    # pero la página sigue resoluble por URL directa (para trabajarla). is_appendix
    # se calcula sobre la lista completa para que su render (eyebrow "Apéndice")
    # sea correcto si se entra directo.
    appendices = [a for a in all_appendices if not a.nav_hidden]
    academy = [item for item in list_academy(selected) if not item.nav_hidden]
    try:
        document = load_module(stem, selected)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Módulo no encontrado") from exc

    # Pestaña inicial del panel derecho vía URL (?tab=…). Permite que un link (p. ej. de
    # Novedades) abra el módulo YA posicionado en Catálogo/Árbol/etc. Se valida: si la
    # pestaña no aplica a este módulo, se ignora (el default de copilotLayout gana).
    initial_tab = (tab or "").strip().lower()
    if initial_tab not in _MODULE_TABS:
        initial_tab = ""
    elif initial_tab == "catalogo":
        _num = str(document.summary.number)
        if not (catalog_reads._catalog_domains() and _num != "05" and not document.summary.stem.startswith("05_")):
            initial_tab = ""
    elif initial_tab == "section" and access.is_readonly(request):
        initial_tab = ""
    log_activity("module_selected", user=user, module=stem, pack=selected)

    # Nav combinado: módulos principales + apéndices técnicos (módulo final).
    # Danwor Academy queda FUERA de nav_items a propósito: es un área desconectada
    # de la navegación (no entra en el pager prev/next ni en el bucle de scroll).
    nav_items = (*modules, *appendices)
    is_appendix = any(item.stem == stem for item in all_appendices)
    ia_enabled = ia_enabled_for(stem)

    # Layout inmersivo (p. ej. 00 Intro): en vez de renderizar secciones markdown,
    # se embebe un HTML de storytelling (formato Academy) a pantalla completa. El
    # módulo sigue en el nav Core con su pager/loop; solo cambia el cuerpo.
    _meta, _ = split_front_matter(document.source)
    immersive = str(_meta.get("layout") or "").strip().lower() == "immersive"
    embed_asset = str(_meta.get("embed_asset") or "").strip()
    immersive = immersive and bool(embed_asset) and "/" not in embed_asset and ".." not in embed_asset
    current_index = next((i for i, item in enumerate(nav_items) if item.stem == stem), None)
    previous_module = nav_items[current_index - 1] if current_index else None
    next_module = (
        nav_items[current_index + 1]
        if current_index is not None and current_index + 1 < len(nav_items)
        else None
    )
    # Bucle de lectura: en el último documento, la continuación por scroll vuelve
    # al inicio (Intro) en vez de "morir". Solo aplica al cue de scroll continuo,
    # no a las flechas del pager (que siguen deshabilitándose al final).
    loop_home = nav_items[0] if (next_module is None and len(nav_items) > 1) else None

    # En modo inmersivo el scroll ocurre dentro del iframe, así que el cue de
    # "scroll para continuar" vive en el HTML embebido: le pasamos a dónde ir
    # (siguiente módulo, o loop al inicio) para que navegue el top al llegar al final.
    iframe_src = None
    if immersive:
        _cont = next_module or loop_home
        _params = {"branding": "danwor", "embed": "1"}
        if _cont:
            _prefix = _cont.stem.split("_")[0]
            _params["continue_url"] = f"/modules/{_cont.stem}?pack={selected}"
            _params["continue_label"] = (
                f"{_prefix} · {_cont.title}" if next_module else f"↻ {_prefix} · {_cont.title}"
            )
        iframe_src = f"/pack-assets/{selected}/{embed_asset}?{urlencode(_params)}"
    trees = store.trees_for_module(stem, selected, include_assessment=True)
    run = store.start(trees[0], selected) if len(trees) == 1 else None
    conversation = assistant_store.start(stem, selected)
    # Perfil de solo lectura (ACADEMY_USERS): lee todo, pero sin IA. Conserva el
    # panel SOLO si el módulo tiene árboles de decisión (deterministas, sin tokens);
    # las pestañas Sección/Asistente (IA) se ocultan en el template.
    readonly = access.is_readonly(request)
    has_decision_tree = any(t.get("kind") != "assessment" for t in trees)
    packs = [
        {"name": name, "label": content_pack_short_label(name), "description": content_pack_label(name), "home": nav.home_url(name)}
        for name in available_content_packs()
    ]
    return templates.TemplateResponse(
        request=request,
        name="module.html",
        context={
            "modules": modules,
            "appendices": appendices,
            "academy": academy,
            "academy_assets": {a.stem: academy_asset_for(a.stem, selected) for a in academy},
            "active_group": "appendix" if is_appendix else "core",
            "active_stem": stem,
            "ia_enabled": ia_enabled,
            "is_appendix": is_appendix,
            "diagnostic": diagnostic,
            "is_owner": access.is_owner(request),
            "immersive": immersive,
            "iframe_src": iframe_src,
            "readonly": readonly,
            # El panel derecho se muestra si el módulo tiene IA (grupo Decisión) o
            # si está el modo diagnóstico activo (grupo Gestión). Nunca en módulos
            # inmersivos (p. ej. 00 Intro). Para el perfil de solo lectura se muestra
            # si hay asistente (router determinista) o árbol; la salida LLM queda gateada.
            "show_panel": (
                ((ia_enabled or has_decision_tree) and not immersive) if readonly
                else ((ia_enabled or diagnostic) and not immersive)
            ),
            "document": document,
            "trees": trees,
            "run": run,
            "node": run.node if run else None,
            "leaf": run.leaf if run else None,
            "selected_tree_id": run.tree.get("id") if run else "",
            "pack": selected,
            "packs": packs,
            "pack_label": content_pack_short_label(selected),
            "previous_module": previous_module,
            "next_module": next_module,
            "loop_home": loop_home,
            "section_actions": ALL_ACTIONS,
            "module_actions": MODULE_ACTIONS,
            # Descripciones y mensajes de carga combinados (sección + módulo) para el
            # template, que alterna las acciones según el target activo.
            "all_action_descriptions": {**ACTION_DESCRIPTIONS, **MODULE_ACTION_DESCRIPTIONS},
            "all_loading_messages": {
                action: get_loading_message(action, False)
                for action in (*ALL_ACTIONS, *MODULE_ACTIONS)
            },
            "user": user,
            "current_workspace": workspaces.COPILOT,
            "available_workspaces": workspaces.available_workspaces(user),
            "conversation_id": conversation.id,
            "invite_src": invite.invite_src(request, selected),
            "catalog_pilot": os.getenv("DANWOR_CATALOG_PILOT", "").lower() in {"1", "true", "on", "yes"},
            "catalog_domains": catalog_reads._catalog_domains(),
            "catalog_connection_types": catalog_reads._catalog_connection_types(),
            "catalog_security_families": catalog_reads._catalog_security_families(),
            "catalog_data_products": catalog_reads._catalog_data_products_list(),
            "initial_tab": initial_tab,
            "whatsnew_unseen": whatsnew.is_unseen(request, selected),
        },
    )
