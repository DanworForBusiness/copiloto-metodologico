"""governed_evidence.py — identidad, equivalencia metodológica y procedencia de la evidencia de una actualización gobernada (PR-2). SOLO LECTURA.

Cuatro componentes SEPARADOS (contrato PR-2, ajuste 2):
  · IDENTIDAD del modelo ............ cliente + modelo terminal + raíz (objeto y tipo).
  · EQUIVALENCIA METODOLÓGICA ...... lo que decide el Juicio: el CONJUNTO de artefactos del cierre semántico, el alcance de evidencia efectivo, la versión
                                      del contrato de evidencia y, por hoja frontera, su clase de contrato de fuente, conexión remota, tipo de conector,
                                      entidades remotas y sistemas fuente. Se calcula desde el PERFIL CANÓNICO (ya normalizado por el motor): no depende de
                                      bytes, orden de serialización ni timestamps del pool. Textos descriptivos excluidos.
  · PROCEDENCIA ..................... ``verify_provenance``: el origen DECLARADO (uno o más Spaces) se acredita por (a) MANIFIESTO de publicación existente
                                      o (b) EVIDENCIA RECONSTRUIDA desde el Space autorizado (la evaluación ad hoc de ese Space es metodológicamente
                                      equivalente a la reconstrucción gobernada restringida a sus conexiones). Si no se acredita: abstención.
  · INTEGRIDAD OPERATIVA ............ plan aprobado, sha256 del sobre vigente y firma de dependencias (``dep-closure/v1``): la verifica la ejecución
                                      contra lo aprobado; vive en el registro de la aprobación, no acá.

No se exige igualdad literal del pool entre el ad hoc y la reconstrucción gobernada: solo la de lo metodológicamente relevante.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

FINGERPRINT_SCHEMA = "governed-evidence-fingerprint/v1"
PROVENANCE_SCHEMA = "governed-provenance/v1"
ROUTE_MANIFEST, ROUTE_RECONSTRUCTED = "MANIFEST", "RECONSTRUCTED"
ACCREDITED, NOT_ACCREDITED = "ACCREDITED", "NOT_ACCREDITED"
_MANIFEST_DIR = {"analytic-model": "analytic-model"}


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def digest(obj: Any) -> str:
    return hashlib.sha256(_canonical(obj)).hexdigest()


def _profile(p: Any) -> dict:
    if isinstance(p, str):
        try:
            p = json.loads(p)
        except ValueError:
            return {}
    return p if isinstance(p, dict) else {}


def identity(profile: Any) -> dict:
    sc = (_profile(profile).get("scope") or {})
    return {"client": sc.get("client"), "terminal_am": sc.get("terminal_analytic_model"), "root_object": sc.get("root_object"),
            "root_type": sc.get("root_type")}


def _leaf_view(leaf: dict) -> dict:
    return {"leaf": leaf.get("leaf"), "leaf_kind": leaf.get("leaf_kind"), "contract_class": leaf.get("contract_class"),
            "remote_connection": leaf.get("remote_connection"), "connection_type": leaf.get("connection_type"),
            "remote_entities": sorted(str(x) for x in leaf.get("remote_entities") or []),
            "source_systems": sorted(str(x) for x in leaf.get("source_systems") or [])}


def equivalence(profile: Any) -> dict:
    """Vista metodológicamente relevante (ver docstring del módulo). Ordenada: el orden incidental no cuenta."""
    p = _profile(profile)
    sc = p.get("scope") or {}
    ce = p.get("canonical_evidence") or {}
    leaves = ((ce.get("source_contract_classes") or {}).get("leaves")) or []
    return {"closure": sorted({str(a) for a in sc.get("closure_artifacts") or []}),
            "effective_features": sorted(str(x) for x in ((sc.get("evidence_scope") or {}).get("effective_features") or [])),
            "canonical_evidence": ce.get("version"),
            "source_contracts": sorted((_leaf_view(lf) for lf in leaves if isinstance(lf, dict)), key=lambda x: str(x["leaf"]))}


def fingerprint(profile: Any) -> dict:
    """``{schema, identity, identity_digest, equivalence_digest}``: identidad y equivalencia con digests SEPARADOS."""
    ident, eq = identity(profile), equivalence(profile)
    return {"schema": FINGERPRINT_SCHEMA, "identity": ident, "identity_digest": digest(ident), "equivalence_digest": digest(eq)}


def equivalence_diff(a: Any, b: Any) -> list[str]:
    """Qué difiere en lo metodológicamente relevante (para explicar una abstención); vacío = equivalentes."""
    ea, eb = equivalence(a), equivalence(b)
    out = []
    ca, cb = set(ea["closure"]), set(eb["closure"])
    if ca != cb:
        out.append(f"cierre semántico: +{len(cb - ca)} / -{len(ca - cb)} artefactos")
    if ea["effective_features"] != eb["effective_features"]:
        out.append("alcance de evidencia")
    if ea["canonical_evidence"] != eb["canonical_evidence"]:
        out.append("versión del contrato de evidencia")
    la = {x["leaf"]: x for x in ea["source_contracts"]}
    lb = {x["leaf"]: x for x in eb["source_contracts"]}
    changed = sorted(k for k in set(la) | set(lb) if la.get(k) != lb.get(k))
    if changed:
        out.append(f"contratos de fuente en {len(changed)} hoja(s): " + ", ".join(changed[:5]) + ("…" if len(changed) > 5 else ""))
    return out


# ── procedencia ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


_POOL_FOLDERS = ("remote-tables", "views", "local-tables", "analytic-models", "data-flows", "replication-flows", "transformation-flows")


def _pool_object_sha(pool_root: Path | None, client_dir: str, name: str) -> str | None:
    """sha256 del archivo del objeto en el pool del Catálogo (la carpeta es el tipo de objeto; el nombre técnico es único por cliente)."""
    if pool_root is None:
        return None
    for folder in _POOL_FOLDERS:
        sha = _sha(Path(pool_root) / client_dir / folder / f"{name}.json")
        if sha:
            return sha
    return None


def _manifest(catalog_dir: Path, client_dir: str, space: str, am: str) -> dict | None:
    path = Path(catalog_dir) / ".lineage_runs" / client_dir / space / "analytic-model" / am / "lineage-manifest.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _manifest_objects(doc: dict) -> dict[str, dict]:
    return {str(o.get("technical_name")): o for o in doc.get("objects") or [] if isinstance(o, dict) and o.get("technical_name")}


def _other_space_claims(catalog_dir: Path, client_dir: str, declared: set[str]) -> dict[str, list[tuple[str, str | None]]]:
    """``{objeto: [(Space, sha256|None)]}`` reclamado por manifiestos de Spaces NO declarados del mismo cliente."""
    base = Path(catalog_dir) / ".lineage_runs" / client_dir
    out: dict[str, list] = {}
    if not base.is_dir():
        return out
    for space_dir in base.iterdir():
        if not space_dir.is_dir() or space_dir.name in declared:
            continue
        for mf in space_dir.rglob("lineage-manifest.json"):
            try:
                doc = json.loads(mf.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for name, obj in _manifest_objects(doc).items():
                out.setdefault(name, []).append((space_dir.name, obj.get("sha256")))
    return out


def verify_provenance(*, client_dir: str, am: str, declared_spaces: Iterable[str], governed_profile: Any, adhoc_fingerprint: dict | None,
                      adhoc_space: str | None, connection_records: list[dict], catalog_dir: Path, pool_root: Path | None) -> dict:
    """¿El origen DECLARADO queda acreditado para esta reconstrucción? Solo lectura; nunca infiere ni completa. Ver docstring del módulo.
    Devuelve ``{schema, state, route, declared, checks, reasons, digest}``; ``state`` = ACCREDITED solo si TODO se cumple."""
    declared = sorted({str(s).strip() for s in declared_spaces if str(s).strip()})
    reasons: list[str] = []
    checks: dict[str, Any] = {}
    known = sorted({r["space"] for r in connection_records if r.get("space")})
    checks["known_spaces"] = known
    if not declared:
        reasons.append("NO_SPACE_DECLARED")
    unknown = [s for s in declared if s not in known]
    if unknown:
        reasons.append("UNKNOWN_SPACE:" + ",".join(unknown))
    # Conexiones: cada conexión remota del cierre debe existir en un Space declarado con el MISMO tipo de conector; nunca se resuelve desde otro Space.
    gp = _profile(governed_profile)
    leaves = [lf for lf in (((gp.get("canonical_evidence") or {}).get("source_contract_classes") or {}).get("leaves") or []) if isinstance(lf, dict)]
    conns = sorted({str(lf.get("remote_connection")) for lf in leaves if lf.get("remote_connection")})
    by_conn: dict[str, list[dict]] = {}
    for r in connection_records:
        by_conn.setdefault(r["name"], []).append(r)
    conn_checks = []
    for c in conns:
        in_declared = [r for r in by_conn.get(c, []) if r.get("space") in declared]
        elsewhere = sorted({r["space"] for r in by_conn.get(c, []) if r.get("space") not in declared and r.get("space")})
        types = {lf.get("connection_type") for lf in leaves if lf.get("remote_connection") == c and lf.get("connection_type")}
        ok = bool(in_declared) and (not types or all(r.get("typeId") in types for r in in_declared))
        conn_checks.append({"connection": c, "declared_spaces": sorted({r["space"] for r in in_declared}), "other_spaces": elsewhere, "ok": ok})
        if not in_declared:
            reasons.append(f"CONNECTION_OUTSIDE_AUTHORIZED:{c}" + (f"@{','.join(elsewhere)}" if elsewhere else ""))
        elif not ok:
            reasons.append(f"CONNECTION_TYPE_MISMATCH:{c}")
    checks["connections"] = conn_checks
    # Equivalencia metodológica ad hoc (del Space declarado) ↔ reconstrucción gobernada restringida: exigida en ambas vías.
    gov_fp = fingerprint(gp)
    checks["governed_fingerprint"] = {k: gov_fp[k] for k in ("identity_digest", "equivalence_digest")}
    if not adhoc_fingerprint or adhoc_fingerprint.get("schema") != FINGERPRINT_SCHEMA:
        reasons.append("NO_ADHOC_FINGERPRINT")
    else:
        if adhoc_fingerprint.get("identity_digest") != gov_fp["identity_digest"]:
            reasons.append("MODEL_IDENTITY_DIFFERS")
        if adhoc_fingerprint.get("equivalence_digest") != gov_fp["equivalence_digest"]:
            reasons.append("EVIDENCE_NOT_EQUIVALENT")
    # Vía (a): manifiesto existente de un Space declarado que cubre el cierre (y, si trae hashes, coincide con el pool).
    closure = equivalence(gp)["closure"]
    manifest_route = None
    for s in declared:
        doc = _manifest(catalog_dir, client_dir, s, am)
        if not doc:
            continue
        objs = _manifest_objects(doc)
        missing = [a for a in closure if a not in objs]
        mismatched = []
        if pool_root is not None:
            for a in closure:
                o = objs.get(a)
                if o and o.get("sha256") and o.get("object_type"):
                    cur = _sha(Path(pool_root) / client_dir / str(o["object_type"]) / f"{a}.json")
                    if cur is not None and cur != o["sha256"]:
                        mismatched.append(a)
        checks.setdefault("manifests", []).append({"space": s, "missing": len(missing), "sha_mismatch": len(mismatched),
                                                   "with_sha": sum(1 for a in closure if (objs.get(a) or {}).get("sha256"))})
        if not missing and not mismatched:
            manifest_route = s
            break
    # Mezcla no autorizada: el CONTENIDO ACTUAL de un objeto del cierre coincide (por sha256) con lo publicado desde un Space NO declarado y con nada
    # publicado desde un Space declarado. Un reclamo sin hash (manifiesto viejo) no prueba origen: se informa, no bloquea (la equivalencia lo cubre).
    others = _other_space_claims(catalog_dir, client_dir, set(declared))
    declared_sha: dict[str, set] = {}
    for s in declared:
        doc = _manifest(catalog_dir, client_dir, s, am)
        for name, o in (_manifest_objects(doc).items() if doc else []):
            if o.get("sha256"):
                declared_sha.setdefault(name, set()).add(o["sha256"])
    mixed, unverifiable = [], []
    for a in closure:
        claims = others.get(a) or []
        if not claims:
            continue
        foreign_sha = {sha for _sp, sha in claims if sha}
        if not foreign_sha:
            unverifiable.append(a)
            continue
        cur = _pool_object_sha(pool_root, client_dir, a)
        if cur and cur in foreign_sha and cur not in declared_sha.get(a, set()):
            mixed.append(a)
    if mixed:
        reasons.append(f"OBJECTS_FROM_OTHER_SPACE:{len(mixed)}")
    checks["other_space_claims"] = {"mixed": sorted(mixed), "unverifiable": sorted(unverifiable)}
    # Vía (b): evidencia reconstruida desde el Space autorizado (la evaluación ad hoc se hizo en un Space declarado).
    reconstructed = adhoc_space in declared if adhoc_space else False
    if manifest_route is None and not reconstructed:
        reasons.append("ORIGIN_NOT_ACCREDITED")
    route = ROUTE_MANIFEST if manifest_route else (ROUTE_RECONSTRUCTED if reconstructed else None)
    state = ACCREDITED if not reasons and route else NOT_ACCREDITED
    body = {"schema": PROVENANCE_SCHEMA, "state": state, "route": route, "declared": declared, "checks": checks, "reasons": reasons}
    return {**body, "digest": digest({k: body[k] for k in ("schema", "state", "route", "declared", "checks")})}
