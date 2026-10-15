"""legacy_bridge — UNICO punto de acceso de `modeling_assessment` al codigo legacy.

Contrato (decision del owner):
  * ningun otro modulo del paquete importa nada legacy (lo verifica un test AST);
  * este modulo NO re-exporta el evaluador legacy de cumplimiento ni su vocabulario
    de veredictos: el perfil no puede depender de ellos. Si el informe de validacion
    shadow (hito M8) necesita el veredicto legacy para compararlo lado a lado, se
    agregara AQUI, en una seccion `VALIDATION ONLY` explicita y con su propio test;
  * el legacy NUNCA importa este paquete (dependencia unidireccional);
  * `classify_extraction` es el MISMO objeto que usa el generador (fuente unica del
    criterio, ver tests/test_compliance_rubric.py): se importa, nunca se copia.

Los nombres con guion bajo del legacy (`_is_cube`, `_cube_subgraph`, ...) son privados
de `generate_catalog`; se re-exportan aqui con nombres publicos para que un renombre
en el legacy falle en UN solo test claro (tests/test_modeling_assessment_bridge.py).
"""
from __future__ import annotations

from pathlib import Path

from tools.catalog import generate_catalog as _gc
from tools.catalog.compliance_criteria import classify_extraction
from tools.catalog.dsp_view_type import (  # noqa: F401 — fuente ÚNICA del tipo de vista (la misma función del exportador)
    BASIS_DERIVED, BASIS_STORED, GRAPHICAL, SQL, UNKNOWN, VIEW_TYPES, classify_view_type, view_type_of_export,
)

# ── Modulos NEUTROS compartidos con el Juicio (derivacion de materializacion de hojas locales) ──
# ``tools.catalog.leaf_materialization`` / ``leaf_materialization_evidence`` son hermanos de ``business_classification``: no importan este paquete ni el de Juicio, y son la FUENTE UNICA de la logica
# de materializacion (el Assessment la persiste, el Juicio la lee). Acceso unico por el bridge (R-I3): ningun otro modulo del paquete importa nada fuera de stdlib/intra-paquete.
from tools.catalog import leaf_materialization, leaf_materialization_evidence  # noqa: E402,F401

# ── Loader y modelo de entidad ─────────────────────────────────────────────
load_folder = _gc.load_folder
Entity = _gc.Entity
CatalogCollisionError = _gc.CatalogCollisionError

# ── Grafo / capa / naming ──────────────────────────────────────────────────
is_cube = _gc._is_cube
cube_subgraph = _gc._cube_subgraph
capa_of = _gc._capa_of
parse_name = _gc.parse_name
typed_artifact = _gc._typed_artifact
is_sapi_remote = _gc._is_sapi_remote
CAPA = _gc.CAPA
OTROS = _gc.OTROS

# ── Pool de archivos (export CLI de Datasphere) ────────────────────────────
client_domain_for = _gc._client_domain_for
is_artifact_type_dir = _gc._is_artifact_type_dir
catalog_dir_kind = _gc._catalog_dir_kind
CATALOG_IGNORED_DIRS = _gc.CATALOG_IGNORED_DIRS
ARTIFACT_TYPE_DIRS = _gc.CATALOG_ARTIFACT_TYPE_DIRS


def artifact_family_of_dir(dir_name: str) -> str | None:
    """Familia NORMALIZADA (``views``, ``analytic models``, ``local tables``…) de una carpeta-tipo del export CLI, o None si la carpeta NO
    es una carpeta-tipo (pool plano / dominio). Reusa el contrato de carpetas del legado (`_catalog_dir_kind` + allowlist)."""
    kind = catalog_dir_kind(dir_name)
    return kind if kind in ARTIFACT_TYPE_DIRS else None
CATALOG_SOURCE_EXTENSIONS = _gc.CATALOG_SOURCE_EXTENSIONS
file_sha256 = _gc._catalog_sha256


# ── Persistencia remota (para la guarda de la ruta de salida) ──────────────
def persist_enabled() -> bool:
    """¿Hay espejado a Spaces activo en este entorno? (lectura pura, no sube nada)."""
    import config
    return bool(config.PERSIST_ENABLED)


def persisted_dirs() -> list[Path]:
    """Directorios locales que `core.remote_store.mirror_up()` sube al bucket (recursivo, sin exclusiones)."""
    from core import remote_store
    return [Path(p) for _name, p in remote_store._persist_dirs()]


# ===========================================================================
# LEGACY COMPATIBILITY LOGIC — SNAPSHOT DE `generate_catalog.generate_all`
# ===========================================================================
# `generate_all` agrupa los .json/.txt de un inbound por (cliente, dominio) con una
# funcion de elegibilidad ANIDADA (`_eligible`) que no es importable. Para que el
# shadow lea EXACTAMENTE el mismo pool que el catalogo, su comportamiento se
# reproduce aca — y SOLO aca (un unico borde de compatibilidad legacy; el runner y
# el resto del paquete NO duplican esta logica).
#
# CONTRATO: esta funcion es una copia deliberada de las lineas de agrupacion de
# `generate_all`. Esta protegida por el test de paridad
#   tests/test_modeling_assessment_bridge.py::test_pool_groups_parity_with_generate_all
# Si `generate_all` cambia su criterio, ese test falla y este snapshot se actualiza
# de forma explicita y revisable.
def _eligible(rel_parts: tuple[str, ...]) -> bool:
    return (not any(catalog_dir_kind(part) in CATALOG_IGNORED_DIRS for part in rel_parts[:-1])
            and not (rel_parts and rel_parts[0].upper().startswith("DP_")))


def legacy_pool_groups(inbound_root) -> dict[tuple[str, str], dict[str, list[Path]]]:
    """{(cliente_dir, dominio_dir): {"jsons": [...], "txts": [...]}} con listas ordenadas."""
    inbound_root = Path(inbound_root)
    groups: dict[tuple[str, str], dict[str, list[Path]]] = {}
    for p in inbound_root.rglob("*.json"):
        parts = p.relative_to(inbound_root).parts
        if not _eligible(parts):
            continue
        cd = client_domain_for(parts)
        if cd is None:
            continue
        groups.setdefault(cd, {"jsons": [], "txts": []})["jsons"].append(p)
    for p in inbound_root.rglob("*.txt"):
        parts = p.relative_to(inbound_root).parts
        if not _eligible(parts):
            continue
        cd = client_domain_for(parts)
        if cd is not None and cd in groups:
            groups[cd]["txts"].append(p)
    for g in groups.values():
        g["jsons"].sort()
        g["txts"].sort()
    return groups
