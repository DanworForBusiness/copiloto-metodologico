from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from core.prompts import get_out_of_scope_redirect
from core.routing_scorers import (
    _display_heading,
    _find_cross_module_asset,
    _find_cross_module_section,
    _find_matching_asset,
    _find_matching_assets,
    _find_matching_section,
    _should_pair_section_with_asset,
)
from core.utils import slugify
from webapp.services.content_service import (
    ModuleDocument,
    list_appendices,
    list_modules,
)


@dataclass(frozen=True)
class AssistantRouteAction:
    label: str
    url: str
    external: bool = False


@dataclass(frozen=True)
class AssistantRoute:
    message: str
    actions: tuple[AssistantRouteAction, ...]
    original_question: str
    explain_module: str = ""
    explain_heading: str = ""


def _module_url(stem: str, pack_name: str, heading: str = "") -> str:
    fragment = f"#{quote(slugify(_display_heading(heading)))}" if heading else ""
    return f"/modules/{quote(stem)}?pack={quote(pack_name)}{fragment}"


def _asset_action(asset: dict, pack_name: str) -> AssistantRouteAction | None:
    raw = str(asset.get("asset") or asset.get("url") or "").strip()
    if not raw:
        return None
    if raw.lower().startswith(("http://", "https://")):
        return AssistantRouteAction("Abrir recurso", raw, True)
    normalized = raw.replace("\\", "/").lstrip("./")
    kind, separator, relative = normalized.partition("/")
    if not separator or kind not in {"assets", "templates"}:
        kind, relative = "assets", normalized
    url = f"/pack-resources/{quote(pack_name)}/{kind}/{quote(relative, safe='/')}"
    return AssistantRouteAction("Abrir recurso", url, True)


def _module_stem_for_number(number: str, pack_name: str) -> str:
    target = str(number or "").zfill(2)
    summaries = (*list_modules(pack_name), *list_appendices(pack_name))
    match = next((item for item in summaries if item.number == target), None)
    return match.stem if match else ""


class AssistantRoutingService:
    """Resolve curated navigation before falling back to the LLM."""

    def route(
        self,
        document: ModuleDocument,
        question: str,
        pack_name: str,
        active_section: str = "",
    ) -> AssistantRoute | None:
        question = (question or "").strip()
        if not question:
            return None
        stem = document.summary.stem
        source = document.source

        multiple_assets = _find_matching_assets(
            question, stem, source, pack_name=pack_name,
        )
        if multiple_assets:
            actions = tuple(
                action for asset in multiple_assets
                if (action := _asset_action(asset, pack_name)) is not None
            )
            if actions:
                return AssistantRoute(
                    "Encontré varios recursos curados que pueden ayudarte. Elegí cuál querés abrir.",
                    actions,
                    question,
                )

        local_section = _find_matching_section(question, source)
        local_asset = _find_matching_asset(
            question, str(document.summary.path), source, allow_strong=True,
        )
        cross_asset = None if local_asset else _find_cross_module_asset(
            question, stem, allow_strong=True, pack_name=pack_name,
        )
        asset = local_asset or cross_asset
        cross_section = None
        if asset and not local_section:
            cross_section = _find_cross_module_section(question, stem, pack_name)

        if asset:
            asset_module = str(asset.get("_module") or stem)
            asset_label = str(asset.get("description") or "recurso técnico")
            section = local_section or cross_section
            if section and not _should_pair_section_with_asset(question, section):
                section = None
            actions: list[AssistantRouteAction] = []
            explain_module = asset_module
            explain_heading = ""
            if section:
                section_module = str(section.get("module") or stem)
                section_heading = str(section.get("heading") or "")
                actions.append(AssistantRouteAction(
                    "Ir a la sección",
                    _module_url(section_module, pack_name, section_heading),
                ))
                explain_module = section_module
                explain_heading = section_heading
            if asset_action := _asset_action(asset, pack_name):
                actions.append(asset_action)
            source_label = str(asset.get("_module_label") or "").strip()
            location = f" en **{source_label}**" if source_label else ""
            message = f"Encontré un recurso curado{location}: **{asset_label}**."
            if section:
                message += " También hay una sección metodológica relacionada."
            return AssistantRoute(
                message, tuple(actions), question, explain_module, explain_heading,
            )

        if local_section:
            heading = str(local_section.get("heading") or "")
            if slugify(_display_heading(heading)) == active_section:
                return None
            label = _display_heading(heading)
            return AssistantRoute(
                f"Encontré una sección del módulo que puede ayudarte: **{label}**.",
                (AssistantRouteAction("Ir a la sección", _module_url(stem, pack_name, heading)),),
                question,
                stem,
                heading,
            )

        cross_section = _find_cross_module_section(question, stem, pack_name)
        if cross_section:
            target = str(cross_section.get("module") or "")
            heading = str(cross_section.get("heading") or "")
            module_label = str(cross_section.get("module_label") or target)
            return AssistantRoute(
                f"Este tema está cubierto con más precisión en **{module_label}**, "
                f"sección **{_display_heading(heading)}**.",
                (AssistantRouteAction("Ir a la sección", _module_url(target, pack_name, heading)),),
                question,
                target,
                heading,
            )

        redirect = get_out_of_scope_redirect(question, stem, pack_name)
        if redirect:
            target = _module_stem_for_number(str(redirect.get("module") or ""), pack_name)
            if target:
                return AssistantRoute(
                    str(redirect.get("message") or "Esa consulta corresponde a otro módulo."),
                    (AssistantRouteAction("Ir al módulo", _module_url(target, pack_name)),),
                    question,
                    target,
                )
        return None
