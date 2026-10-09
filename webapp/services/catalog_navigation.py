"""catalog_navigation.py — universos de navegación del Catálogo (por modelo, por línea de negocio, por cliente).

Capa fina de LECTURA sobre las entradas que ya carga ``tools/catalog/catalog_service`` (que permanece intacto: es un módulo legacy congelado, ver
``test_legacy_list_tile_template_and_scoring_modules_are_untouched_by_the_assessment_front``). Solo usa su API pública (``flows_for_domain``); no clasifica,
no lee el assessment y no escribe. Criterios de datos puros: sin reglas por cliente ni por nombre de dominio concreto.
"""
from __future__ import annotations

from typing import Any

# Orden de las tarjetas por mirada Danwor (🟢 → 🟡 → 🟠) — el MISMO que ``catalog_service.flows_for_domain`` (un test lo ata a su ``_VERDICT_RANK``).
_VERDICT_RANK = {"plena": 0, "buena": 1, "parcial": 2}
# Rótulo de dominio que el generador asigna cuando NO puede inferir uno (``generate_catalog``: "'General' si no hay match"): no identifica un modelo.
_GENERIC_DOMAIN = "general"


def sort_flows_by_verdict(flows: list) -> list:
    """Orden estable de las tarjetas de flujo por mirada Danwor; compartido por las vistas que listan flujos."""
    return sorted(flows, key=lambda e: _VERDICT_RANK.get(e.veredicto, 9))


def browsable_flows(mod: Any, entries: list) -> list:
    """Universo de flujos navegables: todas las entradas salvo las de dominios RESERVADOS (Seguridad, Conexiones, Espacios…, con generadores/vistas propias).
    Mismo criterio que ``domains``/``flows_for_domain`` (que devuelven [] para un dominio reservado), pero SIN exigir un dominio no vacío: una agrupación
    alternativa (línea de negocio, cliente) debe poder particionar TODO el Catálogo."""
    reserved = {d for d in {e.dominio for e in entries} if d and not mod.flows_for_domain(entries, d)}
    return [e for e in entries if e.dominio not in reserved]


def model_domains(mod: Any, entries: list) -> list[str]:
    """Dominios que identifican un MODELO genuino (desagrupado), para el selector "Modelos": los que tienen EXACTAMENTE un flujo y no son el rótulo genérico
    del generador. Un dominio compartido por varios flujos es una agrupación legacy (no un modelo) y el rótulo genérico no nombra ninguno: ambos quedan fuera
    de ESTA lista (siguen existiendo en ``domains``/``flows_for_domain``, cuyo contrato no cambia, y sus flujos se navegan por línea de negocio o cliente)."""
    flows = browsable_flows(mod, entries)
    counts: dict[str, int] = {}
    for e in flows:
        d = (e.dominio or "").strip()
        if d:
            counts[d.lower()] = counts.get(d.lower(), 0) + 1
    out = {}
    for e in flows:
        d = (e.dominio or "").strip()
        if d and counts[d.lower()] == 1 and d.lower() != _GENERIC_DOMAIN:
            out[d.lower()] = d
    return sorted(out.values())


def clients(mod: Any, entries: list) -> list[str]:
    """Clientes con al menos un flujo navegable, sin repetir (sin distinguir mayúsculas) y en orden alfabético: el selector de la modalidad "Clientes"."""
    seen: dict[str, str] = {}
    for e in browsable_flows(mod, entries):
        c = (e.cliente or "").strip()
        if c:
            seen.setdefault(c.lower(), c)
    return sorted(seen.values(), key=str.lower)


def flows_for_client(mod: Any, entries: list, cliente: str) -> list:
    """TODOS los flujos navegables de un cliente (coincidencia exacta sin distinguir mayúsculas), ordenados por mirada Danwor."""
    c = (cliente or "").strip().lower()
    return sort_flows_by_verdict([e for e in browsable_flows(mod, entries) if c and (e.cliente or "").strip().lower() == c])
