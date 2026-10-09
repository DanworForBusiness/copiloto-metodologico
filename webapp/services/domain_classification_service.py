"""Clasificación de dominio — armado de entradas desde el Catálogo local y resolución persistido/fresco.

Capa fina sobre el motor puro ``tools.catalog.domain_classification``: solo REÚNE hechos que ya están en el Catálogo local (dominio
actual, objeto raíz, nombres/etiquetas del cierre del flujo, dominios existentes del cliente). No readquiere Datasphere, no hace red
y no toca el Juicio ni el perfil del Assessment (hermana, nunca insumo de la calidad metodológica).
"""
from __future__ import annotations

import logging
from typing import Any

from tools.catalog.business_classification import BUSINESS_CLASSIFICATION_VERSION, classify_business
from tools.catalog.domain_classification import DOMAIN_CLASSIFICATION_VERSION, classify_domain

log = logging.getLogger(__name__)


def _label_of(entry: Any, name: str) -> str:
    from webapp.services import catalog_reads
    desc, _tech = catalog_reads.artifact_title(entry, name)
    return "" if desc == name else str(desc or "")


def _client_domains(entry: Any, all_entries: Any = None) -> list[str]:
    if all_entries is None:
        from webapp.services import catalog_reads
        try:
            _mod, all_entries = catalog_reads._catalog_entries()
        except Exception:                                   # noqa: BLE001 — sin catálogo global: solo el dominio del propio flujo
            all_entries = []
    cli = str(getattr(entry, "cliente", "")).strip().lower()
    return sorted({str(e.dominio) for e in (all_entries or []) if str(getattr(e, "cliente", "")).strip().lower() == cli and getattr(e, "dominio", "")})


def derive_for_entry(entry: Any, terminal_am: str | None, all_entries: Any = None) -> dict | None:
    """Clasificación de dominio del flujo ``entry`` (raíz = AM terminal). ``None`` si no hay raíz/entrada. Fail-soft: jamás rompe la
    generación ni el informe; sin red; determinista."""
    if entry is None or not terminal_am:
        return None
    try:
        names = [str(n) for n in (getattr(entry, "artefactos", None) or [])]
        return classify_domain(
            current_domain=str(getattr(entry, "dominio", "") or ""),
            root={"name": terminal_am, "label": _label_of(entry, terminal_am)},
            closure=[{"name": n, "label": _label_of(entry, n)} for n in names],
            client_domains=_client_domains(entry, all_entries),
            client=str(getattr(entry, "cliente", "") or ""))
    except Exception:                                       # noqa: BLE001
        log.exception("clasificación de dominio: no se pudo derivar para %s", getattr(entry, "entry_id", "?"))
        return None


def resolve(persisted: dict | None, entry: Any, terminal_am: str | None) -> tuple[dict | None, bool]:
    """(clasificación, ``persisted``). Usa la persistida si su ``input_signature`` coincide con las entradas ACTUALES del Catálogo
    (frescura propia de este derivado); si faltó (sobre histórico) o el Catálogo cambió, deriva en SOLO LECTURA (nunca persiste)."""
    fresh = derive_for_entry(entry, terminal_am)
    if persisted and fresh and persisted.get("version") == DOMAIN_CLASSIFICATION_VERSION and persisted.get("input_signature") == fresh.get("input_signature"):
        return persisted, True
    return fresh, False


def _artifact_auto(entry: Any, name: str) -> dict:
    """`auto` de la vista `vistas/<name>.md` (etiqueta + origen remoto). Fail-soft: sin vista, {}."""
    try:
        from pathlib import Path
        from webapp.services.content_service import split_front_matter
        meta, _body = split_front_matter((Path(entry.path).parent / "vistas" / f"{name}.md").read_text(encoding="utf-8"))
        return (meta or {}).get("auto") or {}
    except Exception:                                       # noqa: BLE001
        return {}


def derive_business_for_entry(entry: Any, terminal_am: str | None, all_entries: Any = None) -> dict | None:
    """Clasificación de NEGOCIO v2 (línea de negocio + dominio) del flujo ``entry`` desde la evidencia local del Catálogo: nombres/etiquetas del cierre y la
    identidad de las hojas fuente (``origen_remoto``). Sin red, sin Datasphere, sin tocar el Juicio. ``None`` si no hay raíz/entrada. Fail-soft."""
    if entry is None or not terminal_am:
        return None
    try:
        closure = []
        for n in [str(x) for x in (getattr(entry, "artefactos", None) or [])]:
            auto = _artifact_auto(entry, n)
            label = str(auto.get("label") or "")
            closure.append({"name": n, "label": "" if label == n else label, "source": str(auto.get("origen_remoto") or "")})
        root_label = next((c["label"] for c in closure if c["name"] == terminal_am), "")
        return classify_business(
            current_domain=str(getattr(entry, "dominio", "") or ""), root={"name": terminal_am, "label": root_label}, closure=closure,
            client_domains=_client_domains(entry, all_entries), client=str(getattr(entry, "cliente", "") or ""))
    except Exception:                                       # noqa: BLE001
        log.exception("clasificación de negocio: no se pudo derivar para %s", getattr(entry, "entry_id", "?"))
        return None


def resolve_business(persisted: dict | None, entry: Any, terminal_am: str | None) -> tuple[dict | None, bool]:
    """(clasificación v2, persistida?). Usa la persistida si su ``input_signature`` coincide con las entradas ACTUALES; si falta (sobre histórico anterior a v2) o
    el Catálogo cambió, deriva en SOLO LECTURA (nunca persiste). Una v1 jamás se presenta como v2: viven en claves y versiones distintas."""
    fresh = derive_business_for_entry(entry, terminal_am)
    if persisted and fresh and persisted.get("contract_version") == BUSINESS_CLASSIFICATION_VERSION and persisted.get("input_signature") == fresh.get("input_signature"):
        return persisted, True
    return fresh, False


def manual_lob(entry: Any) -> str | None:
    """Asignación MANUAL explícita de línea de negocio del flujo: ``manual.linea_de_negocio`` del front-matter del ``flujo_*.md`` (bloque ``manual:`` que la
    regeneración del Catálogo preserva; clave en español snake_case como el resto de ese bloque: ``dominio``, ``intent_negocio``…). Solo LECTURA: jamás se escribe
    metadata del cliente desde el resultado automático. ``None`` si no hay asignación o no es un ID de la taxonomía."""
    try:
        from pathlib import Path
        from tools.catalog.business_classification import LOB_BY_ID
        from webapp.services.content_service import split_front_matter
        meta, _body = split_front_matter(Path(entry.path).read_text(encoding="utf-8"))
        value = str(((meta or {}).get("manual") or {}).get("linea_de_negocio") or "").strip().upper()
        return value if value in LOB_BY_ID else None
    except Exception:                                       # noqa: BLE001
        return None
