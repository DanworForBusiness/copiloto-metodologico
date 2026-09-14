"""test_content_service_anchor_contract.py — corrective: el fragment que
`AssistantRoutingService` genera para una sección debe resolver a un `id` REAL
en el DOM que `content_service` renderiza. NO alcanza con que el href
"parezca" correcto.

EL DEFECTO (forensic, ver HANDOFF/ticket): `render_sections` sólo asigna `id`
a nivel de heading `##` (h2) — vía `ModuleSection.slug`, estampado por
`module.html` en el `<section id="...">` que envuelve cada h2. Todo lo que
está DENTRO de una sección (incluido cualquier `###`/h3) pasa por
`markdown.markdown(prepared, extensions=[...])` SIN `toc`/`attr_list`, así que
nunca recibe `id`. El router (`_find_matching_section`,
`extract_section_by_heading(..., level=(2,3))`) sí trata un h3 con
`{{section_meta}}` como sección navegable de pleno derecho — de ahí el
"Ir a la sección" que aterriza en el ROOT del módulo en vez de la sección: el
fragment es correcto, el ancla simplemente no existe.

Alcance TRANSVERSAL a propósito (guardrails B/C/D): "Streaming Read" es HOY el
único h3 con `{{section_meta}}` en todo el corpus, pero el contrato que se
protege acá es general — CUALQUIER heading que el router pueda seleccionar
como sección debe tener un `id` real, sea h2 o h3+, en cualquier pack.
"""
from __future__ import annotations

import re

from core.content_metadata import parse_section_meta, split_front_matter
from core.content_registry import available_content_packs
from core.routing_scorers import _display_heading
from core.utils import slugify
from webapp.services.assistant_routing import AssistantRoutingService
from webapp.services.content_service import list_appendices, list_modules, load_module

PACK = "datasphere_core"
MOD_01 = "01_Arquitectura_y_Conectividad"
MOD_03 = "03_Integracion_y_Estrategia_de_Datos"

Q1 = (
    "En una conexión de SAP Datasphere a S/4HANA mediante Remote Tables, "
    "DP Agent y ABAPAdapter, ¿qué significa que Streaming Read esté en OFF?"
)

_docs: dict[tuple[str, str], object] = {}


def _doc(stem: str, pack: str = PACK):
    key = (stem, pack)
    if key not in _docs:
        _docs[key] = load_module(stem, pack)
    return _docs[key]


def _rendered_ids(document) -> set[str]:
    """Todos los `id=` realmente presentes en la página renderizada: los
    wrappers h2 (`ModuleSection.slug`, estampados por module.html) MÁS
    cualquier `id=` que el html de cada sección ya traiga (h3+, si el fix
    los agrega)."""
    ids: set[str] = set()
    for section in document.sections:
        ids.add(section.slug)
        ids.update(re.findall(r'\bid="([^"]+)"', section.html))
    return ids


# --- A: el caso reportado — el fragment DEBE existir en el DOM real --------

def test_reported_query_ir_a_la_seccion_fragment_resolves_to_a_real_dom_id():
    route = AssistantRoutingService().route(_doc(MOD_01), Q1, PACK)
    assert route is not None
    action = next(a for a in route.actions if a.label == "Ir a la sección")
    assert "#" in action.url, "la acción debería incluir un fragment"
    fragment = action.url.split("#", 1)[1]

    rendered_ids = _rendered_ids(_doc(MOD_03))
    assert fragment in rendered_ids, (
        f"'Ir a la sección' apunta a '#{fragment}', que NO existe en el DOM renderizado "
        f"de {MOD_03} — el navegador aterriza en el root del módulo, no en la sección."
    )


# --- B (guardrail A del ticket): un h2 existente sigue resolviendo --------

def test_existing_h2_target_still_resolves_to_a_real_dom_id():
    route = AssistantRoutingService().route(
        _doc(MOD_01), "federacion vs persistencia cual conviene", PACK
    )
    assert route is not None
    action = next(a for a in route.actions if a.label == "Ir a la sección")
    fragment = action.url.split("#", 1)[1]
    assert fragment in _rendered_ids(_doc(MOD_03))


# --- C (guardrail B del ticket): TODO h3+ direccionable, en TODO el corpus -

def _addressable_headings(document, body: str):
    """Todo heading (nivel >=2) con `{{section_meta}}` propio — el mismo
    universo que `_find_matching_section`/`_find_cross_module_section`
    consideran seleccionable — junto con el fragment que el router calcularía
    para él (MISMA fórmula que `_module_url`: slugify(_display_heading(h)))."""
    _, plain_body = split_front_matter(body)
    for entry in parse_section_meta(plain_body):
        heading = entry["heading"]
        yield heading, slugify(_display_heading(heading))


def test_every_addressable_section_meta_heading_has_a_matching_dom_id():
    """Transversal: recorre TODOS los módulos/apéndices de TODOS los packs
    registrados. No hardcodea Streaming Read — si algún heading (h2 o h3+)
    tiene `{{section_meta}}`, el router puede seleccionarlo, así que DEBE
    tener un id real. Falla hoy únicamente para el h3 de Streaming Read."""
    checked = 0
    missing: list[tuple[str, str, str]] = []
    for pack in available_content_packs():
        stems = [m.stem for m in list_modules(pack)] + [a.stem for a in list_appendices(pack)]
        for stem in stems:
            document = load_module(stem, pack)
            rendered_ids = _rendered_ids(document)
            for heading, expected_fragment in _addressable_headings(document, document.source):
                checked += 1
                if expected_fragment not in rendered_ids:
                    missing.append((pack, stem, heading))
    assert checked > 0, "guardrail vacío — no se recorrió ningún heading direccionable"
    assert not missing, (
        f"{len(missing)} heading(s) direccionable(s) sin id real en el DOM: {missing}"
    )


# --- D (guardrail C del ticket): sin colisiones nested-vs-h2 ---------------

def test_no_nested_id_collides_with_an_existing_h2_slug():
    for pack in available_content_packs():
        stems = [m.stem for m in list_modules(pack)] + [a.stem for a in list_appendices(pack)]
        for stem in stems:
            document = load_module(stem, pack)
            h2_ids = {s.slug for s in document.sections}
            for s in document.sections:
                nested_ids = re.findall(r'\bid="([^"]+)"', s.html)
                collisions = [i for i in nested_ids if i in h2_ids and i != s.slug]
                assert not collisions, f"{pack}/{stem}/{s.title}: nested id colisiona con un h2: {collisions}"


# --- E (guardrail D del ticket): sin ids duplicados dentro de una página ---

def test_no_duplicate_ids_within_a_single_rendered_page():
    for pack in available_content_packs():
        stems = [m.stem for m in list_modules(pack)] + [a.stem for a in list_appendices(pack)]
        for stem in stems:
            document = load_module(stem, pack)
            all_ids: list[str] = []
            for s in document.sections:
                all_ids.append(s.slug)
                all_ids.extend(re.findall(r'\bid="([^"]+)"', s.html))
            dupes = {i for i in all_ids if all_ids.count(i) > 1}
            assert not dupes, f"{pack}/{stem}: ids duplicados en la misma página: {dupes}"
