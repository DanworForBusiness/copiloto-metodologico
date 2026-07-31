"""Generador piloto del catálogo de Data Products Danwor.

Lee carpetas bajo Data Products/<PRODUCT_ID>/ con exports JSON de BDC/Data Product
(productCsn, serviceDefinitionObject, serviceEntities) y emite un markdown por
producto en catalog_local/data_products/. Es deliberadamente liviano: estructura
base para navegar, iterar scoring y enriquecer cumplimiento sin mezclarlo con el
catálogo de dominios Datasphere.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from tools.catalog.generate_catalog import load_folder as _load_domain_folder
    from tools.catalog.generate_catalog import _sync_apis as _sync_domain_apis
    # Contrato de metadata COMPARTIDO con Dominios: mismo orden/labels/columnas. DP solo
    # inyecta lo suyo (capa HANA/HDLF, origen vía Replication Flow). Ver artifact_metadata_rows.
    from tools.catalog.generate_catalog import artifact_metadata_rows as _artifact_metadata_rows
    from tools.catalog.generate_catalog import parse_name as _parse_name, CAPA as _CAPA
except Exception:  # pragma: no cover - keeps the standalone generator tolerant
    _load_domain_folder = None
    _sync_domain_apis = None
    _artifact_metadata_rows = None
    _parse_name = None
    _CAPA = {}

GENERATOR_VERSION = "data-products-v14"

DATA_PRODUCT_SUPPORT_DOCUMENT_URLS = {
    "additionalDocuments.pptx": "https://danwor.sharepoint.com/:p:/s/PrcticaAnalytics/IQAuQ_sKiXWKSoy_pXz3Fe5yAUZKhdR-NjSKMCRcu_M5F5Q?e=5yWev3",
}


def _safe_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("_") or "data_product"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig", errors="ignore"))
    except Exception:
        return None


def _walk_values(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _walk_values(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _walk_values(value)


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _label_from_dict(obj: dict[str, Any]) -> str:
    return _first_text(
        obj.get("@EndUserText.label"),
        obj.get("label"),
        obj.get("title"),
        obj.get("name"),
        obj.get("id"),
    )


def _extract_product_title(product_id: str, payloads: dict[str, Any]) -> str:
    for payload in payloads.values():
        for item in _walk_values(payload):
            label = _label_from_dict(item)
            if label and label != product_id:
                return label
    return product_id.replace("_", " ")


def _extract_business_name(product_id: str, payloads: dict[str, Any]) -> str:
    for payload in payloads.values():
        for item in _walk_values(payload):
            if not isinstance(item, dict):
                continue
            value = _first_text(item.get("businessName"), item.get("business_name"))
            if value and value != product_id:
                return value
    return ""


def _list_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (int, float, bool)):
        return [str(value)]
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_list_text(item))
        return out
    if isinstance(value, dict):
        label = _first_text(
            value.get("label"),
            value.get("name"),
            value.get("title"),
            value.get("id"),
            value.get("code"),
        )
        return [label] if label else []
    return []


def _extract_support_documents(payloads: dict[str, Any]) -> list[dict[str, str]]:
    docs: list[dict[str, str]] = []
    seen: set[str] = set()
    has_additional = False
    for payload in payloads.values():
        for item in _walk_values(payload):
            if not isinstance(item, dict):
                continue
            value = item.get("additionalDocuments")
            if isinstance(value, (list, tuple)) and value:
                has_additional = True
            elif isinstance(value, str) and value.strip():
                has_additional = True
    if has_additional:
        filename = "additionalDocuments.pptx"
        url = DATA_PRODUCT_SUPPORT_DOCUMENT_URLS.get(filename, "")
        if url and url not in seen:
            seen.add(url)
            docs.append({"label": "Data Provider Page", "filename": filename, "url": url})
    return docs


def _extract_product_details(payloads: dict[str, Any]) -> dict[str, Any]:
    details = {"data_categories": [], "industries": [], "delivery_mode": ""}
    for payload in payloads.values():
        for item in _walk_values(payload):
            if not isinstance(item, dict):
                continue
            if not details["data_categories"] and "dataCategories" in item:
                details["data_categories"] = _list_text(item.get("dataCategories"))
            if not details["industries"] and "industries" in item:
                details["industries"] = _list_text(item.get("industries"))
            if not details["delivery_mode"] and "deliveryMode" in item:
                details["delivery_mode"] = _first_text(item.get("deliveryMode"))
            if details["data_categories"] and details["industries"] and details["delivery_mode"]:
                return details
    return details


def _extract_entities(payloads: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(name: str, label: str = "", kind: str = "", source: str = "") -> None:
        name = str(name or "").strip()
        if not name or name in seen:
            return
        seen.add(name)
        out.append({"name": name, "label": label or name, "kind": kind or "", "source": source or ""})

    for filename, payload in payloads.items():
        for obj in _walk_values(payload):
            definitions = obj.get("definitions")
            if isinstance(definitions, dict):
                for name, spec in definitions.items():
                    if isinstance(spec, dict):
                        kind = _first_text(spec.get("kind"), spec.get("type"))
                        label = _label_from_dict(spec)
                        if kind or label:
                            add(name, label, kind, filename)

            for key in ("entities", "serviceEntities", "items", "value"):
                value = obj.get(key)
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            add(
                                _first_text(item.get("name"), item.get("technicalName"), item.get("id")),
                                _label_from_dict(item),
                                _first_text(item.get("kind"), item.get("type")),
                                filename,
                            )
                elif isinstance(value, dict):
                    for name, item in value.items():
                        if isinstance(item, dict):
                            add(name, _label_from_dict(item), _first_text(item.get("kind"), item.get("type")), filename)

            direct_name = _first_text(obj.get("name"), obj.get("technicalName"), obj.get("id"))
            if direct_name:
                add(direct_name, _label_from_dict(obj), _first_text(obj.get("kind"), obj.get("type")), filename)
    return out


def _extract_fields(payloads: dict[str, Any], limit: int = 1000) -> list[dict[str, str]]:
    fields: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for payload in payloads.values():
        for obj in _walk_values(payload):
            elements = obj.get("elements")
            if not isinstance(elements, dict):
                continue
            owner = _first_text(obj.get("name"), obj.get("@EndUserText.label"))
            for name, spec in elements.items():
                if not isinstance(spec, dict):
                    continue
                key = (owner, str(name))
                if key in seen:
                    continue
                seen.add(key)
                fields.append(
                    {
                        "entity": owner,
                        "name": str(name),
                        "label": _first_text(spec.get("@EndUserText.label"), spec.get("label")),
                        "type": _field_type(spec),
                        "length": _field_length(spec),
                        "key": bool(spec.get("key")),
                    }
                )
                if len(fields) >= limit:
                    return fields
    return fields


def _field_length(spec: dict[str, Any]) -> str:
    precision = spec.get("precision")
    scale = spec.get("scale")
    length = spec.get("length")
    if precision is not None:
        return f"{precision},{scale}" if scale is not None else str(precision)
    if length is not None:
        return str(length)
    return ""


def _field_type(spec: dict[str, Any]) -> str:
    value = _first_text(spec.get("type"), spec.get("cdsType"))
    return value[4:] if value.startswith("cds.") else value


def _artifact_display_name(name: str) -> str:
    tail = str(name or "").strip()
    tail = tail.split(":", 1)[1] if ":" in tail else tail
    tail = tail.split(".", 1)[1] if "." in tail else tail
    return tail.strip() or str(name or "").strip()




def _lineage_display_name(name: str) -> str:
    return _artifact_display_name(name)


def _lineage_section_slug(display: str, role: str = "") -> str:
    base = f"{role} · {display}" if role else display
    return re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-") or "artefacto"


def _runtime_id(runtime: str, name: str) -> str:
    return f"{runtime}::{name}"


def _delta_base(name: str) -> str:
    raw = str(name or "").strip()
    return raw[:-6] if raw.lower().endswith("_delta") else raw


def _is_replication_flow(payload: Any) -> bool:
    return isinstance(payload, dict) and isinstance(payload.get("replicationflows"), dict)


def _replication_sources(folder: Path) -> dict[str, str]:
    """Mapa {nombre_tecnico_tabla_local (lower) -> CDS de extracción} leído de los
    Replication Flow objects (`RF_*.json` con top-level `replicationflows`).

    Es el análogo del `@DataWarehouse.remote.entity` de Dominios, pero para tablas
    locales con delta capture: la tabla local no referencia a su CDS en su propio CSN
    (lee de su `_Delta`); el vínculo vive en el RF, en cada
    `replicationTasks[].sourceObject.name` (CDS) → `targetObject.name` (target). El
    target suele venir con sufijo `_Delta` (delta capture) → se recorta a la tabla base.
    """
    out: dict[str, str] = {}
    for jf in sorted(folder.glob("*.json")):
        payload = _read_json(jf)
        if not _is_replication_flow(payload):
            continue
        for rf in (payload.get("replicationflows") or {}).values():
            tasks = ((rf or {}).get("contents") or {}).get("replicationTasks") or []
            if not isinstance(tasks, list):
                continue
            for task in tasks:
                if not isinstance(task, dict):
                    continue
                src = str((task.get("sourceObject") or {}).get("name") or "").strip()
                tgt = str((task.get("targetObject") or {}).get("name") or "").strip()
                if src and tgt:
                    out[_delta_base(tgt).lower()] = src
    return out


def _entity_runtime_from_payload(payload: Any, filename: str) -> dict[str, str]:
    """Clasifica cada definición como HANA u Object Store.

    En Data Products, los exports del Object Store se bajan con *_PUB_* para evitar
    colisión de archivo, pero la definición interna conserva el nombre técnico final.
    El tag HDLF es la fuente real; el nombre del archivo queda como respaldo.
    """
    default_runtime = "object_store" if "_PUB_" in filename.upper() else "hana"
    out: dict[str, str] = {}
    if not isinstance(payload, dict):
        return out
    definitions = payload.get("definitions") or {}
    if not isinstance(definitions, dict):
        return out
    for name, spec in definitions.items():
        if not isinstance(spec, dict):
            continue
        runtime = "object_store" if "@DataWarehouse.persistence.hdlf.tableFormat" in spec else default_runtime
        out[str(name)] = runtime
    return out


def _parse_domain_json_file(path: Path) -> dict[str, Any]:
    """Reusa load_folder sobre un solo JSON para preservar duplicados HANA/Object Store."""
    if _load_domain_folder is None:
        return {}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            shutil.copy2(path, tmp_path / path.name)
            parsed, _sql = _load_domain_folder(tmp_path)
            return parsed or {}
    except Exception:
        return {}


def _layer_num(name: str) -> int:
    raw = _artifact_display_name(name).upper()
    m = re.match(r"^(\d+)", raw)
    return int(m.group(1)) if m else 0


def _load_lineage_entities(folder: Path, published: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Serializa linaje de Data Product reutilizando el parser del catálogo de dominios.

    Diferencias clave vs. dominios:
    - HANA y Object Store pueden tener el mismo nombre técnico; usamos IDs internos
      `hana::<name>` / `object_store::<name>` y mantenemos `display` limpio.
    - Los JSON *_PUB_* representan la publicación en Object Store; se conectan con
      su contraparte HANA quitando la noción PUB del archivo.
    - Las tablas *_Delta no se muestran como nodos; se colapsan como subtexto
      `delta capable` sobre la entidad base.
    """
    if _load_domain_folder is None:
        return []

    published_display: set[str] = set()
    for ent in published:
        raw = str(ent.get("name") or "").strip()
        disp = str(ent.get("display") or _artifact_display_name(raw)).strip()
        if raw:
            published_display.add(_artifact_display_name(raw))
            if ":" in raw:
                published_display.add(_artifact_display_name(raw.split(":", 1)[1]))
            if "." in raw:
                published_display.add(_artifact_display_name(raw.rsplit(".", 1)[-1]))
        if disp:
            published_display.add(disp)
    published_display_norm = {x.lower() for x in published_display if x}

    contract_names = {"productcsn.json", "servicedefinitionobject.json", "serviceentities.json"}
    rf_sources = _replication_sources(folder)   # {tabla_local(lower) -> CDS de extracción}
    records: dict[str, dict[str, Any]] = {}
    delta_capable: set[str] = set()
    per_file_entities: dict[str, list[tuple[str, str]]] = {}

    def ensure_node(runtime: str, name: str, ent: Any | None = None) -> dict[str, Any]:
        node_id = _runtime_id(runtime, name)
        display = _lineage_display_name(name)
        node = records.setdefault(node_id, {
            "name": node_id,
            "technical_name": name,
            "display": display,
            "label": display,
            "role": "Artefacto de linaje",
            "kind": "",
            "kind_label": "",
            "data_category": "",
            "modeling_pattern": "",
            "remote_connection": "",
            "remote_entity": "",
            "upstreams": [],
            "assoc_upstreams": [],
            "fields": [],
            "published": False,
            "runtime": runtime,
            "delta_capable": False,
            "slug": _lineage_section_slug(display, "Artefacto de linaje"),
        })
        if ent is not None:
            kind_label = str(getattr(ent, "kind_label", "") or "")
            node.update({
                "label": str(getattr(ent, "label", "") or node["label"] or display),
                "kind": str(getattr(ent, "kind", "") or node.get("kind") or ""),
                "kind_label": kind_label or node.get("kind_label") or "",
                "data_category": str(getattr(ent, "data_category", "") or node.get("data_category") or ""),
                "modeling_pattern": str(getattr(ent, "modeling_pattern", "") or node.get("modeling_pattern") or ""),
                "remote_connection": str(getattr(ent, "remote_connection", "") or node.get("remote_connection") or ""),
                "remote_entity": str(getattr(ent, "remote_entity", "") or node.get("remote_entity") or ""),
                "fields": list(getattr(ent, "fields", []) or node.get("fields") or [])[:120],
            })
            node["role"] = kind_label or node["role"]
        return node

    for jf in sorted(folder.glob("*.json")):
        if jf.name.lower() in contract_names:
            continue
        payload = _read_json(jf)
        if _is_replication_flow(payload):
            continue   # los RF aportan el vínculo CDS (rf_sources), no son artefactos del linaje
        runtime_by_def = _entity_runtime_from_payload(payload, jf.name)
        parsed = _parse_domain_json_file(jf)
        file_nodes: list[tuple[str, str]] = []
        for name, ent in sorted(parsed.items()):
            if str(getattr(ent, "kind", "") or "").strip().lower() != "entity":
                continue
            if name == "DSDEV" or name.startswith("DSDEV."):
                continue
            runtime = runtime_by_def.get(name) or runtime_by_def.get(_delta_base(name)) or ("object_store" if "_PUB_" in jf.name.upper() else "hana")
            base_name = _delta_base(name)
            if base_name != name:
                delta_capable.add(_runtime_id(runtime, base_name))
                continue
            node = ensure_node(runtime, name, ent)
            file_nodes.append((runtime, name))

            for up in list(getattr(ent, "upstreams", []) or []):
                if up == name or up.startswith("DSDEV"):
                    continue
                up_runtime = runtime_by_def.get(up) or runtime_by_def.get(_delta_base(up)) or runtime
                up_base = _delta_base(up)
                if up_base != up:
                    delta_capable.add(_runtime_id(up_runtime, up_base))
                    continue
                up_id = _runtime_id(up_runtime, up_base)
                if up_id != node["name"] and up_id not in node["upstreams"]:
                    node["upstreams"].append(up_id)
            for up in list(getattr(ent, "assoc_upstreams", []) or []):
                if up == name or up.startswith("DSDEV"):
                    continue
                up_runtime = runtime_by_def.get(up) or runtime_by_def.get(_delta_base(up)) or runtime
                up_base = _delta_base(up)
                if up_base != up:
                    delta_capable.add(_runtime_id(up_runtime, up_base))
                    continue
                up_id = _runtime_id(up_runtime, up_base)
                if up_id != node["name"] and up_id not in node["assoc_upstreams"]:
                    node["assoc_upstreams"].append(up_id)
        per_file_entities[jf.name] = file_nodes

    # Inferencia liviana de transformation-flow por archivo FT_*: conecta la capa
    # menor con la mayor cuando el CSN no expone el edge de negocio explícito.
    for fname, file_nodes in per_file_entities.items():
        if not fname.upper().startswith("FT_"):
            continue
        by_runtime: dict[str, list[str]] = {}
        for runtime, name in file_nodes:
            by_runtime.setdefault(runtime, []).append(name)
        for runtime, names in by_runtime.items():
            hana_names = [n for n in names if not n.startswith("DSDEV")]
            if len(hana_names) < 2:
                continue
            layers = sorted({_layer_num(n) for n in hana_names})
            if len(layers) < 2:
                continue
            src_layer, tgt_layer = layers[0], layers[-1]
            sources = [n for n in hana_names if _layer_num(n) == src_layer]
            targets = [n for n in hana_names if _layer_num(n) == tgt_layer]
            for tgt in targets:
                node = records.get(_runtime_id(runtime, tgt))
                if not node:
                    continue
                for src in sources:
                    src_id = _runtime_id(runtime, src)
                    if src_id != node["name"] and src_id in records and src_id not in node["upstreams"]:
                        node["upstreams"].append(src_id)

    # Publicación Object Store: la entidad publicada depende de su homónima HANA.
    for node_id, node in list(records.items()):
        if node.get("runtime") != "object_store":
            continue
        tech = str(node.get("technical_name") or "")
        hana_id = _runtime_id("hana", tech)
        if hana_id in records and hana_id not in node["upstreams"]:
            node["upstreams"].insert(0, hana_id)

    for node_id in delta_capable:
        if node_id in records:
            records[node_id]["delta_capable"] = True

    # Las entidades publicadas son las del Object Store cuando existen; si no, se
    # tolera HANA para no dejar sin salida contratos incompletos.
    object_store_displays = {str(n.get("display") or "").lower() for n in records.values() if n.get("runtime") == "object_store"}
    anchors: list[str] = []
    for node in records.values():
        disp_key = str(node.get("display") or "").lower()
        if disp_key not in published_display_norm:
            continue
        if node.get("runtime") == "object_store" or disp_key not in object_store_displays:
            node["published"] = True
            node["role"] = "Entidad publicada"
            node["slug"] = _lineage_section_slug(str(node.get("display") or ""), "Entidad publicada")
            anchors.append(str(node["name"]))

    if not anchors:
        return []

    reachable: set[str] = set()
    temp_seen: set[str] = set()
    ordered: list[str] = []

    def visit(node_id: str) -> None:
        if node_id in temp_seen or node_id not in records:
            return
        temp_seen.add(node_id)
        node = records[node_id]
        for up in list(node.get("upstreams") or []) + list(node.get("assoc_upstreams") or []):
            visit(str(up))
        reachable.add(node_id)
        ordered.append(node_id)

    for node_id in sorted(anchors):
        visit(node_id)

    nodes: list[dict[str, Any]] = []
    for node_id in ordered:
        node = records[node_id]
        nodes.append({
            "name": node["name"],
            "technical_name": node.get("technical_name") or "",
            "display": node.get("display") or "",
            "label": node.get("label") or node.get("display") or "",
            "role": node.get("role") or "Artefacto de linaje",
            "kind": node.get("kind") or "",
            "kind_label": node.get("kind_label") or "",
            "data_category": node.get("data_category") or "",
            "modeling_pattern": node.get("modeling_pattern") or "",
            "remote_connection": node.get("remote_connection") or "",
            "remote_entity": node.get("remote_entity") or "",
            "upstreams": [x for x in node.get("upstreams") or [] if x in reachable],
            "assoc_upstreams": [x for x in node.get("assoc_upstreams") or [] if x in reachable],
            "fields": node.get("fields") or [],
            "published": bool(node.get("published")),
            "runtime": node.get("runtime") or "hana",
            "delta_capable": bool(node.get("delta_capable")),
            # CDS de extracción que alimenta esta tabla local (vía Replication Flow).
            # Análogo del origen_remoto de Dominios; el runtime lo usa para el botón
            # "Abrir CDS View" si existe APIs/<origen_cds>.md.
            "origen_cds": rf_sources.get(str(node.get("technical_name") or "").lower(), ""),
            # Publicado: conserva el slug de "Entidad publicada" (que el runtime realinea
            # con el rol real de la entidad). No publicado: recomputá SIEMPRE desde el rol
            # FINAL (que ya absorbió el kind_label), porque el heading del markdown usa ese
            # mismo rol; si dejáramos el slug inicial ("Artefacto de linaje") no matchearía
            # el id de su sección y la cajita del SVG no navegaría.
            "slug": (node.get("slug") if node.get("published")
                     else _lineage_section_slug(str(node.get("display") or ""),
                                                str(node.get("role") or "Artefacto de linaje"))),
        })
    return nodes

def _published_entities(entities: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for ent in entities:
        if str(ent.get("kind") or "").strip().lower() != "entity":
            continue
        display = _artifact_display_name(ent.get("name") or "")
        key = display.lower()
        if not display or key in seen:
            continue
        item = dict(ent)
        item["display"] = display
        out.append(item)
        seen.add(key)
    return out


def _field_rows_for_entity(entity: dict[str, str], fields: list[dict[str, str]], index: int, total: int) -> list[dict[str, str]]:
    name = str(entity.get("name") or "")
    display = str(entity.get("display") or _artifact_display_name(name))
    name_keys = {name.lower(), display.lower()}
    matched = [f for f in fields if str(f.get("entity") or "").strip().lower() in name_keys]
    if not matched:
        display_norm = re.sub(r"[^a-z0-9]+", "", display.lower())
        owner_matches: list[dict[str, str]] = []
        for field in fields:
            owner = str(field.get("entity") or "").strip()
            owner_norm = re.sub(r"[^a-z0-9]+", "", owner.lower())
            if owner_norm and (owner_norm in display_norm or display_norm in owner_norm):
                owner_matches.append(field)
            elif owner.lower().startswith("snd:") and "snd" in display_norm:
                owner_matches.append(field)
            elif owner.lower().startswith("md:") and "md" in display_norm:
                owner_matches.append(field)
        matched = owner_matches
    # En algunos exports BDC los campos vienen bajo una descripción funcional del contrato
    # y no bajo el nombre técnico de la entidad publicada. Para no inventar relaciones,
    # asignamos ese contrato a la primera entidad publicada y dejamos constancia en la tabla.
    owners = {str(f.get("entity") or "").strip() for f in fields if str(f.get("entity") or "").strip()}
    if not matched and index == 0 and len(owners) == 1:
        return fields
    return matched


def _md_value(value: Any, code: bool = False) -> str:
    value = str(value or "").strip()
    if not value:
        return "-"
    value = value.replace("`", "")
    value = value.replace("|", "\\|")
    return f"`{value}`" if code else value




def _sha_folder(folder: Path) -> str:
    h = hashlib.sha256()
    h.update(GENERATOR_VERSION.encode("utf-8"))
    for path in sorted(folder.glob("*.json")):
        h.update(path.name.encode("utf-8"))
        h.update(path.read_bytes())
    return h.hexdigest()


def _fm(auto: dict, manual: dict | None = None) -> str:
    return "---\n" + yaml.safe_dump({"auto": auto, "manual": manual or {}}, allow_unicode=True, sort_keys=False) + "---\n"


def _read_manual(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8", errors="ignore")
    m = re.match(r"^---\n(.*?)\n---", text, re.S)
    if not m:
        return {}
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except Exception:
        return {}
    return fm.get("manual") or {}


def _dp_heading(name: str, label: str) -> tuple[str, str]:
    """Header estándar Danwor, MISMO criterio que Dominios (`_heading`): descripción
    (negro) · ID técnico (dorado). Sin descripción real (label vacío o == nombre) se
    repite el ID. Devuelve (heading_con_tonos, texto_plano) — el plano define el slug."""
    name = str(name or "").strip()
    label = str(label or "").strip()
    desc = label if (label and label != name) else name
    return f"{{dw-black}}{desc} · {{dw-gold}}{name}", f"{desc} · {name}"


def _dp_capa(display: str, runtime: str) -> str:
    """Capa del artefacto en Data Products. Lo ESPECÍFICO de DP (el cruce de universos
    que Dominios no tiene): si vive en HDLF/Object Store, esa ES su capa; si es HANA, se
    usa la capa §28 del nombre — la misma que Dominios."""
    if runtime == "object_store":
        return "HDLF · Object Store"
    pn = _parse_name(display) if _parse_name else {}
    return _CAPA.get(pn.get("capa"), "Sin definir")


def _dp_artefacto(runtime: str, kind_label: str) -> str:
    # local/remota es una distinción del espacio HANA; en Object Store el detalle de
    # persistencia lo da la Capa (HDLF), así que el artefacto queda genérico ("Tabla").
    if runtime == "object_store":
        return "Tabla"
    return kind_label or "Tabla"


def _dp_property_table(*, display: str, runtime: str, kind_label: str, deriva_de: list,
                       asociaciones: list, modeling_pattern: str, origen_cds: str) -> list[str]:
    """Tabla de propiedades de un artefacto DP con el MISMO contrato que Dominios
    (`artifact_metadata_rows`: orden/labels compartidos). El nombre técnico va en el
    heading, no como fila. DP solo inyecta lo suyo: capa HDLF/Object Store y el origen
    vía Replication Flow."""
    if _artifact_metadata_rows is None:
        return ["| Propiedad | Valor |", "|---|---|"]
    pn = _parse_name(display) if _parse_name else {}
    origen = f"`{origen_cds}` · vía Replication Flow" if origen_cds else None
    capa = _dp_capa(display, runtime)
    if capa == "Sin definir" and origen_cds:
        capa = "Ingesta"   # fuente de extracción (RF) sin prefijo §28 → capa de ingesta (simil Dominios)
    # HDLF/Object Store se publica desde su homónima HANA vía un Transformation Flow;
    # lo anotamos en "Deriva de" (simétrico al "Origen · vía Replication Flow").
    deriva_via = "Transformation Flow" if runtime == "object_store" else None
    rows = _artifact_metadata_rows(
        capa=capa,
        artefacto=_dp_artefacto(runtime, kind_label),
        uso=pn.get("uso"),
        origen=origen,
        deriva_de=deriva_de,
        deriva_via=deriva_via,
        asociaciones=asociaciones,
        modeling_pattern=modeling_pattern or None,
    )
    return ["| Propiedad | Valor |", "|---|---|"] + [f"| {k} | {v} |" for k, v in rows]


def _render_product(folder: Path, out_root: Path) -> tuple[str, str]:
    product_id = folder.name
    payloads = {p.name: _read_json(p) for p in sorted(folder.glob("*.json"))}
    payloads = {name: payload for name, payload in payloads.items() if payload is not None}
    contract_names = {"productcsn.json", "servicedefinitionobject.json", "serviceentities.json"}
    contract_payloads = {
        name: payload
        for name, payload in payloads.items()
        if name.lower() in contract_names
    } or payloads
    title = _extract_product_title(product_id, contract_payloads)
    business_name = _extract_business_name(product_id, contract_payloads) or title
    details = _extract_product_details(contract_payloads)
    support_documents = _extract_support_documents(contract_payloads)
    entities = _extract_entities(contract_payloads)
    fields = _extract_fields(contract_payloads)
    files = sorted(payloads)
    published = _published_entities(entities)
    lineage_nodes = _load_lineage_entities(folder, published)

    out_dir = out_root / "data_products"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{_safe_slug(product_id)}.md"
    auto = {
        "tipo": "data_product",
        "product_id": product_id,
        "titulo": title,
        "business_name": business_name,
        "data_categories": details["data_categories"],
        "industries": details["industries"],
        "delivery_mode": details["delivery_mode"],
        "veredicto": "plena",
        "apto": "Referencia Danwor — reutilizable",
        "archivos": files,
        "support_documents": support_documents,
        "entidades": entities,
        "campos": fields,
        "lineage_nodes": lineage_nodes,
        "source_signature": _sha_folder(folder),
    }

    md: list[str] = [
        _fm(auto, _read_manual(out)),
        f"# {{dw-black}}Data Product · {{dw-gold}}{title}",
        "",
        "> Producto de datos gobernado y reutilizable publicado bajo el marco BDC. Esta primera lectura consolida los artefactos técnicos disponibles para documentar alcance, contrato y consumo.",
        "",
        "## Resumen",
        "",
        "| Propiedad | Valor |",
        "|---|---|",
        f"| Business Name | {business_name} |",
        f"| Technical Name | `{product_id}` |",
        f"| Categoría | {', '.join(details['data_categories']) if details['data_categories'] else '-'} |",
        f"| Industria | {', '.join(details['industries']) if details['industries'] else '-'} |",
        f"| Delivery Mode | {details['delivery_mode'] or '-'} |",
        f"| Archivos fuente | {len(files)} |",
        f"| Entidades publicadas | {len(published) or len(entities)} |",
        f"| Campos detectados | {len(fields)} |",
        "",
    ]

    if support_documents:
        doc = support_documents[0]
        try:
            idx = md.index("## Resumen") + 1
        except ValueError:
            idx = len(md)
        md.insert(idx, "")
        md.insert(idx + 1, f'{{{{support: {doc["label"]}="{doc["url"]}"}}}}')


    published_displays = {str(ent.get("display") or _artifact_display_name(ent.get("name") or "")).strip().lower() for ent in published}
    lineage_display_by_id = {
        str(n.get("name") or ""): str(n.get("display") or n.get("technical_name") or n.get("name") or "")
        for n in lineage_nodes
    }
    node_by_display = {str(n.get("display") or "").strip().lower(): n for n in lineage_nodes}

    def _visible_lineage_ref_list(refs: list) -> list[str]:
        """IDs internos (hana::X / object_store::X) → nombres técnicos visibles (lista)."""
        out: list[str] = []
        for ref in refs or []:
            ref_s = str(ref or "")
            val = lineage_display_by_id.get(ref_s) or ref_s.split("::", 1)[-1]
            if val:
                out.append(val)
        return out

    def _refs_from_node(node: dict, key: str) -> list[str]:
        return _visible_lineage_ref_list((node or {}).get(key) or [])

    for i, ent in enumerate(published):
        display = ent.get("display") or _artifact_display_name(ent.get("name") or "")
        rows = _field_rows_for_entity(ent, fields, i, len(published))
        node = node_by_display.get(display.strip().lower()) or {}
        # Descripción del header: la del nodo de linaje (misma fuente que el ancla del
        # SVG en runtime) para que el slug del heading y el del nodo coincidan.
        heading, _plain = _dp_heading(display, node.get("label") or ent.get("label") or "")
        md += [
            f"## {heading}",
            "",
            f"> Entidad publicada por el Data Product `{product_id}`.",
            "",
            *_dp_property_table(
                display=display,
                runtime=str(node.get("runtime") or "object_store"),
                kind_label=str(node.get("kind_label") or ""),
                deriva_de=_refs_from_node(node, "upstreams"),
                asociaciones=_refs_from_node(node, "assoc_upstreams"),
                modeling_pattern=str(node.get("modeling_pattern") or ""),
                origen_cds=str(node.get("origen_cds") or ""),
            ),
            "",
        ]
        if rows:
            md += [f"#### Campos ({len(rows)})", "", "| Campo | Descripción | Tipo | Long. | Clave | Relación |", "|---|---|---|---|:--:|---|"]
            for field in rows:
                md.append(
                    f"| {_md_value(field.get('name'), True)} | "
                    f"{_md_value(field.get('label'))} | {_md_value(field.get('type'))} | "
                    f"{_md_value(field.get('length'))} | {'🔑' if field.get('key') else ''} | |"
                )
            md.append("")

    for node in lineage_nodes:
        display = str(node.get("display") or "").strip()
        if not display or str(node.get("published") or "").lower() == "true" or display.lower() in published_displays:
            continue
        heading, _plain = _dp_heading(display, node.get("label") or "")
        md += [
            f"## {heading}",
            "",
            "> Artefacto técnico detectado en el linaje del Data Product.",
            "",
            *_dp_property_table(
                display=display,
                runtime=str(node.get("runtime") or "hana"),
                kind_label=str(node.get("kind_label") or ""),
                deriva_de=_refs_from_node(node, "upstreams"),
                asociaciones=_refs_from_node(node, "assoc_upstreams"),
                modeling_pattern=str(node.get("modeling_pattern") or ""),
                origen_cds=str(node.get("origen_cds") or ""),
            ),
            "",
        ]
        fields_extra = node.get("fields") or []
        if fields_extra:
            md += [f"#### Campos ({len(fields_extra[:80])})", "", "| Campo | Descripción | Tipo | Long. | Clave | Relación |", "|---|---|---|---|:--:|---|"]
            for field in fields_extra[:80]:
                md.append(
                    f"| {_md_value(field.get('campo'), True)} | "
                    f"{_md_value(field.get('label'))} | {_md_value(field.get('tipo'))} | "
                    f"{_md_value(field.get('longitud'))} | {'\U0001f511' if field.get('key') else ''} | {_md_value(field.get('asociacion'), True)} |"
                )
            md.append("")

    # Cumplimiento en el MISMO formato de bullets que Dominios (`## Hallazgos`): el
    # runtime lo renderiza con el semáforo compartido (_catalog_compliance_table_html)
    # + el intro por veredicto (_catalog_compliance_intro_md). El estado va como marcador
    # inicial del bullet para que el semáforo lo clasifique.
    def _bullet(criterio: str, estado: str, evidencia: str) -> str:
        return f"- {estado} **{criterio}** _Evidencia:_ {evidencia}"

    md += [
        "## Cumplimiento",
        "",
        _bullet("Producto identificado", "Cumple", f"`{product_id}`"),
        _bullet("Entidades publicadas", "Cumple" if published else "Pendiente",
                f"{len(published)} entidad(es) publicada(s) detectada(s)"),
        _bullet("Contrato técnico parseable", "Cumple" if files else "Pendiente",
                f"{len(files)} archivo(s) JSON disponible(s)"),
        _bullet("Campos del contrato", "Cumple" if fields else "Pendiente",
                f"{len(fields)} campo(s) detectado(s)"),
    ]
    out.write_text("\n".join(md) + "\n", encoding="utf-8")
    return product_id, title



def _product_folders(inbound_root: Path) -> list[Path]:
    folders: dict[str, Path] = {}
    for wrapped in sorted(p for p in inbound_root.iterdir() if inbound_root.is_dir() and p.is_dir()):
        clean_name = re.sub(r"^[^\w\d]+", "", wrapped.name).strip()
        clean_name = re.sub(r"^\s*\d+\s*[\.\-_]\s*", "", clean_name).strip().lower()
        clean_name = clean_name.replace("-", " ").replace("_", " ")
        if clean_name not in {"data products"}:
            continue
        for child in sorted(wrapped.iterdir()):
            if child.is_dir() and any(child.glob("*.json")):
                folders[child.name] = child
    # Layout alternativo: cuando el link compartido apunta directamente al root
    # que contiene los productos, Graph puede descargar DP_* en la raíz.
    for child in sorted(inbound_root.iterdir() if inbound_root.is_dir() else []):
        if child.is_dir() and child.name.upper().startswith("DP_") and any(child.glob("*.json")):
            folders.setdefault(child.name, child)
    return [folders[name] for name in sorted(folders)]


def _sync_data_product_apis(inbound_root: Path, out_root: Path) -> int:
    """Sincroniza las CDS de extracción de Data Products (carpeta '01. APIs' anidada
    bajo el wrapper 'Data Products') al pool compartido <out>/APIs, reutilizando el
    `_sync_apis` del generador de dominios (mismo destino agnóstico de cliente que las
    CDS de Dominios). Así el botón "Abrir CDS View" resuelve por `APIs/<CDS>.md`."""
    if _sync_domain_apis is None or not inbound_root.is_dir():
        return 0
    total = 0
    for wrapped in sorted(p for p in inbound_root.iterdir() if p.is_dir()):
        clean = re.sub(r"^[^\w\d]+", "", wrapped.name).strip()
        clean = re.sub(r"^\s*\d+\s*[\.\-_]\s*", "", clean).strip().lower().replace("-", " ").replace("_", " ")
        if clean == "data products":
            total += _sync_domain_apis(wrapped, out_root)
    return total


def generate_all(inbound_root, out_root) -> str:
    inbound_root = Path(inbound_root)
    out_root = Path(out_root)
    folders = _product_folders(inbound_root)
    if not folders:
        return "Catálogo de Data Products: sin productos para generar."
    lines: list[str] = []
    for folder in folders:
        product_id, title = _render_product(folder, out_root)
        lines.append(f"[OK] {product_id}: {title}")
    synced = _sync_data_product_apis(inbound_root, out_root)
    tail = f"\n[APIs] {synced} CDS de extracción sincronizada(s)." if synced else ""
    return f"Catálogo de Data Products regenerado ({len(lines)} producto(s)):\n" + "\n".join(lines) + tail


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Generador piloto de Data Products Danwor")
    ap.add_argument("--all", dest="inbound", required=True)
    ap.add_argument("--out", dest="out", required=True)
    args = ap.parse_args()
    print(generate_all(args.inbound, args.out))
