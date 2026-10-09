"""Clasificación de dominio — armado de entradas desde el Catálogo local y resolución persistido/fresco.

Capa fina sobre el motor puro ``tools.catalog.domain_classification``: solo REÚNE hechos que ya están en el Catálogo local (dominio
actual, objeto raíz, nombres/etiquetas del cierre del flujo, dominios existentes del cliente). No readquiere Datasphere, no hace red
y no toca el Juicio ni el perfil del Assessment (hermana, nunca insumo de la calidad metodológica).
"""
from __future__ import annotations

import hashlib
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


def manual_lob(entry: Any, *, strict: bool = False) -> str | None:
    """Asignación MANUAL explícita de línea de negocio del flujo: ``manual.linea_de_negocio`` del front-matter del ``flujo_*.md`` (bloque ``manual:`` que la
    regeneración del Catálogo preserva; clave en español snake_case como el resto de ese bloque: ``dominio``, ``intent_negocio``…). Solo LECTURA: jamás se escribe
    metadata del cliente desde el resultado automático. ``None`` si no hay asignación o no es un ID de la taxonomía.
    ``strict=True`` NO oculta un error real de lectura/parseo (lo propaga): la navegación por línea de negocio no puede inventar "sin asignación manual".
    El archivo se lee SIEMPRE (barato); solo el parseo YAML —lo caro— se memoiza por el HASH del contenido: nunca sirve un valor viejo y evita re-parsear ~35
    archivos en cada navegación por línea de negocio."""
    try:
        from pathlib import Path
        from tools.catalog.business_classification import LOB_BY_ID
        value = _manual_lob_raw(str(Path(entry.path)))
        return value if value in LOB_BY_ID else None
    except Exception:                                       # noqa: BLE001
        if strict:
            raise
        return None


_MANUAL_LOB_MEMO: dict[str, tuple[str, str]] = {}      # ruta → (hash del contenido, valor crudo); acotado por la cantidad de flujos


def _manual_lob_raw(path: str) -> str:
    """Valor crudo (mayúsculas) de ``manual.linea_de_negocio`` del archivo. Errores de lectura/parseo: se propagan y NO se memoizan."""
    from pathlib import Path
    from webapp.services.content_service import split_front_matter
    text = Path(path).read_text(encoding="utf-8")
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
    hit = _MANUAL_LOB_MEMO.get(path)
    if hit and hit[0] == digest:
        return hit[1]
    meta, _body = split_front_matter(text)
    value = str(((meta or {}).get("manual") or {}).get("linea_de_negocio") or "").strip().upper()
    if len(_MANUAL_LOB_MEMO) > 2048:
        _MANUAL_LOB_MEMO.clear()
    _MANUAL_LOB_MEMO[path] = (digest, value)
    return value


# ── Navegación por LÍNEA DE NEGOCIO del Catálogo (lectura de la clasificación v2 PERSISTIDA; sin derivar, sin escribir) ──────────────────────
UNCLASSIFIED, UNAVAILABLE = "UNCLASSIFIED", "UNAVAILABLE"
# Motivos (estables) por los que un flujo queda SIN CLASIFICAR; distinguen "el clasificador se abstuvo" de "no hay nada que clasificar / aún no se clasificó".
WHY_NO_MODEL, WHY_NOT_CLASSIFIED, WHY_UNDETERMINED, WHY_SUGGESTED = "NO_ANALYTIC_MODEL", "NOT_CLASSIFIED", "UNDETERMINED", "SUGGESTED_ONLY"


def lob_options() -> list[dict]:
    """Opciones del selector de Líneas de negocio, en el orden estable de la taxonomía gobernada; "Sin clasificar" siempre al final."""
    from tools.catalog.business_classification import LINES_OF_BUSINESS
    return [{"id": x["id"], "label": x["label"]} for x in LINES_OF_BUSINESS] + [{"id": UNCLASSIFIED, "label": "Sin clasificar"}]


def _persisted_business(status: Any) -> dict | None:
    """SOLO la clasificación v2 PERSISTIDA en el sobre gobernado. Una v1 (u otro contrato) jamás se presenta como v2; sin ella → ``None`` (nunca se deriva)."""
    bc = getattr(status, "business_classification", None)
    return bc if isinstance(bc, dict) and bc.get("contract_version") == BUSINESS_CLASSIFICATION_VERSION else None


def _effective_for(entry: Any, status: Any) -> dict:
    from tools.catalog import business_classification as BC
    manual = manual_lob(entry, strict=True)
    bc = _persisted_business(status)
    eff = BC.effective_classification(bc, manual)
    auto = ((bc or {}).get("line_of_business") or {})
    out = {"entry_id": entry.entry_id, "available": True, "source": eff["source"], "line_of_business": eff["line_of_business"], "label": eff["label"],
           "group": eff["line_of_business"] or UNCLASSIFIED, "alignment": eff.get("automatic_alignment"), "reason": None, "suggestion": None,
           "automatic_label": auto.get("label") if eff["source"] == "MANUAL" and eff.get("automatic_alignment") == "DISAGREES" else None}
    if eff["source"] == "UNCLASSIFIED":
        if getattr(status, "state", None) == "NOT_APPLICABLE":
            out["reason"] = WHY_NO_MODEL                    # sin AM terminal: no hay raíz que el clasificador pudiera evaluar (≠ UNDETERMINED)
        elif bc is None:
            out["reason"] = WHY_NOT_CLASSIFIED              # histórico / sin assessment / contrato ajeno: aún sin clasificación v2 persistida
        elif eff.get("suggestion"):
            out["reason"] = WHY_SUGGESTED
            out["suggestion"] = {**eff["suggestion"], "confidence": bc.get("confidence")}
        else:
            out["reason"] = WHY_UNDETERMINED
    return out


def effective_lob_by_entry(entries: Any, statuses: dict | None = None) -> dict[str, dict]:
    """``entry_id`` → clasificación EFECTIVA (manual > automática HIGH > sin clasificar) leída de la v2 PERSISTIDA, por LOTE: una sola lectura de
    ``statuses_for_entries`` (o la que ya hizo el llamador), sin N+1. NO deriva, NO clasifica, NO escribe y no toca Evidence/Juicio/Dominio v1.
    Un error real al leer UNA entrada la marca ``available=False`` (grupo ``UNAVAILABLE``): jamás se disfraza de "sin clasificar". Un error del lote entero se propaga."""
    from webapp.services import assessment_service as svc
    entries = list(entries)
    statuses = statuses if statuses is not None else svc.statuses_for_entries(entries)
    out: dict[str, dict] = {}
    for e in entries:
        try:
            out[e.entry_id] = _effective_for(e, statuses[e.entry_id])
        except Exception:                                   # noqa: BLE001 — error real de lectura: se informa, no se oculta
            log.exception("línea de negocio: no se pudo leer la clasificación de %s", getattr(e, "entry_id", "?"))
            out[e.entry_id] = {"entry_id": e.entry_id, "available": False, "source": UNAVAILABLE, "line_of_business": None, "label": None, "group": UNAVAILABLE,
                               "alignment": None, "reason": None, "suggestion": None, "automatic_label": None}
    return out


def partition_by_lob(entries: Any, effective: dict[str, dict]) -> dict[str, list]:
    """Partición COMPLETA y EXCLUSIVA: cada entrada cae en exactamente un grupo (una línea de negocio, ``UNCLASSIFIED`` o ``UNAVAILABLE``)."""
    groups: dict[str, list] = {x["id"]: [] for x in lob_options()}
    groups[UNAVAILABLE] = []
    for e in entries:
        groups[effective[e.entry_id]["group"]].append(e)
    return groups
