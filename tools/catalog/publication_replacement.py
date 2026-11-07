"""Reemplazo GOBERNADO de la publicación de un modelo hecha desde OTRO Space (o tenant): alcance exacto y autorización acotada.

La guarda de identidad del pool (``POOL_IDENTITY_CONFLICT``) sigue igual: un objeto ya publicado desde otro Space/tenant con contenido distinto detiene la
publicación sin cambios. Este módulo NO la apaga ni agrega un interruptor global. Calcula, con la misma evidencia que la guarda y bajo el mismo candado:

  * qué objetos chocan (los mismos que detendrían la publicación) y qué publicación los reclama;
  * si cada uno es EXCLUSIVO del modelo o una DEPENDENCIA COMPARTIDA. Es compartido si lo reclama otro manifiesto (otro modelo), si aparece en los artefactos
    de otra ficha del Catálogo del cliente (cubre los modelos sin manifiesto: exportes previos y paquetes legacy) o si un paquete legacy de la raíz del pool
    lo define embebido;
  * si el reemplazo es DEMOSTRABLEMENTE seguro. Lo es solo si todos los conflictos los reclama UNA única publicación anterior del MISMO modelo (mismo cliente,
    tipo y objeto raíz, otro Space/tenant), todos son exclusivos y la evidencia es legible (manifiestos utilizables y Catálogo del cliente con la ficha del modelo).

La autorización (``token``) es la huella de ESE plan: cliente, modelo, Space destino, el manifiesto desplazado (sha256 exacto) y el contenido planificado de cada
objeto en conflicto. Se recalcula bajo el candado al ejecutar; si algo cambió (otra publicación, el modelo en Datasphere, el Catálogo), no coincide y no se
reemplaza nada. No hay forma de «ignorar» la guarda: solo se acepta el reemplazo exacto que el usuario confirmó y que sigue siendo seguro."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

Key = tuple[str, str]

# Motivos por los que el reemplazo NO es demostrablemente seguro (la UI los explica; ninguno se puede saltear).
R_SHARED = "SHARED_DEPENDENCY"                    # algún objeto en conflicto lo usa otro modelo
R_OTHER_ROOT = "CLAIMED_BY_OTHER_MODEL"           # el conflicto lo reclama la publicación de OTRO modelo
R_MULTIPLE = "MULTIPLE_PREVIOUS_PUBLICATIONS"     # lo reclaman varias publicaciones anteriores del modelo (más de un Space)
R_UNCLAIMED = "UNCLAIMED_CONFLICT"                # choca con un objeto que ninguna publicación reclama: no hay a quién reemplazar
R_UNUSABLE = "UNUSABLE_MANIFESTS"                 # algún manifiesto ilegible: no se puede afirmar quién usa qué
R_CATALOG = "CATALOG_NOT_VERIFIABLE"              # el Catálogo del cliente no se pudo leer o no muestra el modelo

TOKEN_LEN = 64


@dataclass
class Conflict:
    object_type: str
    technical_name: str
    planned_sha256: str
    claimed_by: list[str] = field(default_factory=list)           # «Space · modelo» de las publicaciones que lo reclaman
    used_by_models: list[str] = field(default_factory=list)       # otras fichas del Catálogo que lo usan (nombre visible, sin repetir)
    used_by_count: int = 0                                         # cuántas fichas (dos fichas pueden compartir dominio)
    legacy_packages: list[str] = field(default_factory=list)      # paquetes legacy que lo definen embebido
    other_manifests: list[str] = field(default_factory=list)      # manifiestos de OTROS modelos que lo reclaman

    @property
    def exclusive(self) -> bool:
        return not (self.used_by_count or self.used_by_models or self.legacy_packages or self.other_manifests)

    def as_dict(self) -> dict[str, Any]:
        return {"object_type": self.object_type, "technical_name": self.technical_name, "exclusive": self.exclusive, "claimed_by": self.claimed_by,
                "used_by_models": self.used_by_models, "used_by_count": self.used_by_count, "legacy_packages": self.legacy_packages, "other_manifests": self.other_manifests}


@dataclass
class Analysis:
    client: str
    root_type: str
    root_object: str
    target_space: str
    target_tenant: str
    conflicts: list[Conflict]
    displaced: dict[str, Any] | None                                # publicación anterior del modelo que se reemplazaría (Space, tenant, manifiesto, sha256)
    displaced_only: list[Key] = field(default_factory=list)          # objetos de la publicación anterior que la nueva ya no incluye
    displaced_only_exclusive: list[Key] = field(default_factory=list)  # … y que ningún otro modelo usa (se limpian con el reemplazo; los demás se conservan)
    reasons: list[str] = field(default_factory=list)

    @property
    def safe(self) -> bool:
        return bool(self.conflicts) and self.displaced is not None and not self.reasons

    @property
    def token(self) -> str:
        """Huella del plan exacto que el usuario confirma (determinista; vacía si no es seguro)."""
        if not self.safe:
            return ""
        body = {"client": self.client, "root_type": self.root_type, "root_object": self.root_object, "target_space": self.target_space,
                "target_tenant": self.target_tenant, "displaced_manifest_sha256": self.displaced["manifest_sha256"],
                "conflicts": sorted([c.object_type, c.technical_name, c.planned_sha256] for c in self.conflicts)}
        return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        exclusive = [c.as_dict() for c in self.conflicts if c.exclusive]
        shared = [c.as_dict() for c in self.conflicts if not c.exclusive]
        return {"safe": self.safe, "token": self.token, "reasons": list(self.reasons), "client": self.client, "root_object": self.root_object,
                "root_type": self.root_type, "target_space": self.target_space,
                "displaced": ({k: v for k, v in self.displaced.items() if k != "path"} if self.displaced else None),
                "exclusive": exclusive, "shared": shared, "displaced_only": [f"{t}/{n}" for t, n in self.displaced_only]}


def valid_token(value: str | None) -> str:
    """Normaliza el token que llega del formulario: 64 hex o vacío (cualquier otra cosa no autoriza nada)."""
    v = str(value or "").strip().lower()
    return v if len(v) == TOKEN_LEN and all(ch in "0123456789abcdef" for ch in v) else ""


def _label(claim: dict) -> str:
    return " · ".join(x for x in (str(claim.get("space") or "?"), str(claim.get("rootObject") or claim.get("analyticModel") or "?")) if x)


def _same_root(claim: dict, root_type: str, root_object: str) -> bool:
    name = str(claim.get("rootObject") or claim.get("analyticModel") or "")
    ctype = str(claim.get("rootType") or ("analytic-model" if claim.get("analyticModel") else ""))
    return name == root_object and (not ctype or ctype == root_type)


def legacy_definitions(client_cache_dir: Path) -> dict[str, list[str]]:
    """Nombre técnico → paquetes legacy (JSON en la RAÍZ del pool del cliente) que lo definen embebido. Un paquete ilegible se informa como ``None``."""
    out: dict[str, list[str]] = {}
    for p in sorted(Path(client_cache_dir).glob("*.json")) if Path(client_cache_dir).exists() else []:
        data = json.loads(p.read_text(encoding="utf-8"))          # ilegible ⇒ lanza: el llamador lo trata como evidencia no verificable
        for name in (data.get("definitions") or {}) if isinstance(data, dict) else {}:
            out.setdefault(str(name), []).append(p.stem)
    return out


def catalog_usage(catalog_root: Path, client_dir: str, root_object: str) -> tuple[list[Any], dict[str, list[str]]]:
    """(fichas propias del modelo, nombre técnico → OTRAS fichas del cliente que lo usan). Lanza si el Catálogo del cliente no se puede leer."""
    from tools.catalog import catalog_service as cs
    folder = Path(catalog_root) / client_dir
    entries = cs.load_catalog(folder) if folder.exists() else []
    own = [e for e in entries if root_object in (e.artefactos or [])]
    used: dict[str, list[str]] = {}
    for e in entries:
        if e in own:
            continue
        for name in e.artefactos or []:
            used.setdefault(str(name), []).append(f"{e.entry_id}	{e.dominio or e.entry_id}")   # identidad de la ficha + nombre visible en el Catálogo
    return own, used


def analyze(*, client: str, client_dir: str, root_type: str, root_object: str, target_space: str, target_tenant: str, conflicts: dict[Key, list[dict]],
            planned_sha: dict[Key, str], claims: dict[Key, list[dict]], claim_paths: dict[int, Path], unusable: Iterable[str], catalog_root: Path,
            client_cache_dir: Path, current_objects: set[Key]) -> Analysis:
    """Alcance del conflicto. ``conflicts``: objeto → publicaciones (manifiestos) de OTRO Space/tenant que lo reclaman y cuyo contenido difiere (lo que hoy
    detiene la guarda). ``claims``: TODO lo que reclama cada objeto en los demás manifiestos del cliente (``id(dict)`` → ruta en ``claim_paths``)."""
    reasons: list[str] = []
    if list(unusable):
        reasons.append(R_UNUSABLE)
    try:
        own_flows, used = catalog_usage(catalog_root, client_dir, root_object)
        legacy = legacy_definitions(client_cache_dir)
        if not own_flows:
            reasons.append(R_CATALOG)
    except Exception:                                    # noqa: BLE001 — sin Catálogo o pool legibles no se afirma que algo sea exclusivo
        used, legacy = {}, {}
        reasons.append(R_CATALOG)

    displaced_claims: dict[int, dict] = {}
    items: list[Conflict] = []
    for key in sorted(conflicts):
        claimers = conflicts[key]
        c = Conflict(key[0], key[1], planned_sha.get(key, ""), claimed_by=sorted({_label(x) for x in claimers}),
                     used_by_models=sorted({u.split("	", 1)[-1] for u in used.get(key[1], [])}), used_by_count=len(set(used.get(key[1], []))), legacy_packages=sorted(set(legacy.get(key[1], []))),
                     other_manifests=sorted({_label(x) for x in claims.get(key, []) if not _same_root(x, root_type, root_object)}))
        items.append(c)
        if not claimers:
            reasons.append(R_UNCLAIMED)
        for x in claimers:
            if _same_root(x, root_type, root_object):
                displaced_claims[id(x)] = x
            else:
                reasons.append(R_OTHER_ROOT)
        if not c.exclusive:
            reasons.append(R_SHARED)

    displaced: dict[str, Any] | None = None
    displaced_only: list[Key] = []
    if len(displaced_claims) > 1:
        reasons.append(R_MULTIPLE)
    elif len(displaced_claims) == 1:
        (cid, claim), = displaced_claims.items()
        path = claim_paths.get(cid)
        if path is None or not Path(path).exists():
            reasons.append(R_UNUSABLE)
        else:
            from tools.catalog import publication_txn as txn
            objects = sorted((str(o.get("object_type")), str(o.get("technical_name"))) for o in claim.get("objects") or [] if isinstance(o, dict))
            displaced = {"space": str(claim.get("space") or ""), "tenant": str(claim.get("tenantId") or ""), "root_object": root_object, "path": Path(path),
                         "manifest_sha256": txn.sha256_text_file(Path(path)), "created_at": str(claim.get("createdAt") or ""), "objects": len(objects)}
            displaced_only = [k for k in objects if k not in current_objects]
    exclusive_only = [k for k in displaced_only if not used.get(k[1]) and not legacy.get(k[1])
                      and not any(not _same_root(x, root_type, root_object) for x in claims.get(k, []))]
    return Analysis(client=client, root_type=root_type, root_object=root_object, target_space=target_space, target_tenant=target_tenant,
                    conflicts=items, displaced=displaced, displaced_only=displaced_only, displaced_only_exclusive=exclusive_only, reasons=sorted(set(reasons)))


def preview(*, catalog_root: Path, csn_cache_root: Path, client_dir: str, root_type: str, root_object: str, target_space: str) -> dict[str, Any] | None:
    """Vista PREVIA (sin extraer de Datasphere): la publicación anterior del modelo desde otro Space y, de sus objetos, cuáles son exclusivos y cuáles
    compartidos. NO decide: qué objetos chocan de verdad lo sabe recién la publicación (con lo extraído) y ahí se vuelve a calcular. ``None`` si no hay
    publicación anterior desde otro Space o la evidencia no es legible."""
    from tools.catalog import publication_txn as txn
    runs = Path(catalog_root) / ".lineage_runs" / client_dir
    previous: list[tuple[Path, dict]] = []
    for mp in sorted(runs.rglob("lineage-manifest.json")) if runs.exists() else []:
        loaded = txn.load_manifest(mp)
        if not loaded.usable:
            return None
        if _same_root(loaded.data, root_type, root_object) and str(loaded.data.get("space") or "") != target_space:
            previous.append((mp, loaded.data))
    if len(previous) != 1:
        return None
    path, data = previous[0]
    try:
        own_flows, used = catalog_usage(catalog_root, client_dir, root_object)
        legacy = legacy_definitions(Path(csn_cache_root) / client_dir)
    except Exception:                                    # noqa: BLE001
        return None
    others: dict[str, list[str]] = {}
    for mp in sorted(runs.rglob("lineage-manifest.json")):
        if mp == path:
            continue
        m = txn.load_manifest(mp).data
        if _same_root(m, root_type, root_object):
            continue
        for o in m.get("objects") or []:
            if isinstance(o, dict):
                others.setdefault(str(o.get("technical_name")), []).append(_label(m))
    objects = sorted((str(o.get("object_type")), str(o.get("technical_name"))) for o in data.get("objects") or [] if isinstance(o, dict))
    shared = [{"object": f"{t}/{n}", "name": n, "used_by_models": sorted({u.split("	", 1)[-1] for u in used.get(n, [])}), "legacy_packages": sorted(set(legacy.get(n, []))),
               "other_manifests": sorted(set(others.get(n, [])))} for t, n in objects if used.get(n) or legacy.get(n) or others.get(n)]
    return {"space": str(data.get("space") or ""), "objects": len(objects), "exclusive": len(objects) - len(shared), "shared": shared,
            "catalog_visible": bool(own_flows)}
