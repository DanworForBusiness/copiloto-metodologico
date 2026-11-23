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

FINGERPRINT_SCHEMA_V1 = "governed-evidence-fingerprint/v1"         # CONGELADA: interpreta huellas históricas tal como se emitieron
FINGERPRINT_SCHEMA_V2 = "governed-evidence-fingerprint/v2"         # equivalencia material (ver ``equivalence_v2``)
FINGERPRINT_SCHEMA = FINGERPRINT_SCHEMA_V2                          # esquema VIGENTE para huellas nuevas
SUPPORTED_SCHEMAS = (FINGERPRINT_SCHEMA_V1, FINGERPRINT_SCHEMA_V2)
EQUIVALENCE_V2 = "equivalence/v2"
_TIME_DIMENSION_SETTING = "TIME_DIMENSION_SETTING"                  # única base explícita de plataforma hoy acreditada (platform-boundary/v1)
_LOCAL_UNDETERMINED_KINDS = frozenset({"LOCAL_PROVENANCE_NOT_ASSESSED"})
_CORE_CATEGORIES = ("analytic-models", "views", "tables")
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


def fingerprint_v1(profile: Any) -> dict:
    """v1 CONGELADA: ``{schema, identity, identity_digest, equivalence_digest}`` con ``equivalence`` (estricta, sin normalización)."""
    ident, eq = identity(profile), equivalence(profile)
    return {"schema": FINGERPRINT_SCHEMA_V1, "identity": ident, "identity_digest": digest(ident), "equivalence_digest": digest(eq)}


# ── equivalencia v2: material, con fronteras de plataforma acreditadas y abstención ante incertidumbre ───────────────────────────────────────
def _platform_collapse(closure: set[str], pb: Any) -> tuple[set[str], set[str], set[str]]:
    """(descendientes colapsables, fronteras acreditadas consumidas por el cliente, fronteras que absorbieron descendientes). SOLO con evidencia POSITIVA del sidecar ``platform_boundary``:
    · base explícita ``TIME_DIMENSION_SETTING`` en frontera Y descendiente (``NAMESPACE_ONLY`` nunca alcanza);
    · el descendiente no tiene NINGÚN consumidor directo fuera de objetos acreditados (el modelo analítico cuenta como cliente);
    · existe un camino frontera → … → descendiente por aristas acreditadas, con TODOS los nodos acreditados;
    · la frontera tiene al menos un consumidor directo del cliente. Sin sidecar (perfiles históricos): no se colapsa nada."""
    if not isinstance(pb, dict) or not str(pb.get("version") or "").startswith("platform-boundary/"):
        return set(), set(), set()
    accredited = {o.get("name") for o in pb.get("objects") or [] if isinstance(o, dict) and o.get("basis") == _TIME_DIMENSION_SETTING}
    consumers = {k: set(v or []) for k, v in (pb.get("direct_consumers") or {}).items()}
    edges: dict[str, set[str]] = {}
    for r in pb.get("references") or []:
        if isinstance(r, dict) and r.get("from") in accredited and r.get("to") in accredited:
            edges.setdefault(r["from"], set()).add(r["to"])
    frontiers = {g for g in accredited if g in closure and (consumers.get(g, set()) - accredited)}
    collapsed, used = set(), set()
    for g in sorted(frontiers):
        seen, stack = set(), [g]
        while stack:
            n = stack.pop()
            for m in edges.get(n, ()):
                if m not in seen and m != g:
                    seen.add(m)
                    stack.append(m)
        for x in seen:
            if x in closure and x not in frontiers and not (consumers.get(x, set()) - accredited):
                collapsed.add(x)
                used.add(g)
    return collapsed, frontiers, used


def equivalence_v2(profile: Any, extensions: Any = None) -> dict:
    """Vista material (contrato ``equivalence/v2``). Estrictos: versiones (evidencia, cierre, identidad y sidecars), cierre del cliente, fronteras
    de plataforma acreditadas (su identidad), hojas no colapsadas (clase, conexión, conector, entidades, sistemas) y miembros producidos por
    flujos. NO entra el alcance DECLARADO. ``undetermined`` lista lo que impide acreditar equivalencia (abstención, nunca equivalencia)."""
    p = _profile(profile)
    ext = extensions if isinstance(extensions, dict) else {}
    sc, ce, vs = p.get("scope") or {}, p.get("canonical_evidence") or {}, p.get("versions") or {}
    closure = {str(a) for a in sc.get("closure_artifacts") or []}
    pb, obs = ext.get("platform_boundary"), ext.get("evidence_observability")
    collapsed, frontiers, absorbing = _platform_collapse(closure, pb)
    leaves = [lf for lf in ((ce.get("source_contract_classes") or {}).get("leaves") or []) if isinstance(lf, dict)]
    views = {str(lf.get("leaf")): _leaf_view(lf) for lf in leaves if str(lf.get("leaf")) not in collapsed}
    for g in absorbing:                                                     # la frontera absorbe la profundidad: queda como hoja de plataforma
        views.setdefault(g, {"leaf": g, "leaf_kind": "PLATFORM_OBJECT", "contract_class": "PLATFORM_PROVIDED", "remote_connection": None,
                             "connection_type": None, "remote_entities": [], "source_systems": []})
    pc = ((ce.get("closure") or {}).get("plane_context") or {})
    rr = ((ce.get("closure") or {}).get("reference_resolution") or {})
    undetermined = set()
    counts = rr.get("counts") or {}
    if not rr or counts.get("UNRESOLVED", 0) or counts.get("AMBIGUOUS", 0) or rr.get("members_with_open_references"):
        undetermined.add("CLOSURE_INCOMPLETE")                              # sin cierre completo no se acredita qué produce qué
    if any(lf.get("leaf_kind") in _LOCAL_UNDETERMINED_KINDS for lf in leaves):
        undetermined.add("FLOW_MATERIALITY_UNDETERMINED")                   # tabla local sin procedencia evaluada: un flujo podría producirla
    if isinstance(obs, dict) and obs.get("source") == "EXTRACTOR":
        cats = obs.get("categories") or {}
        if any((cats.get(c) or {}).get("state") != "OBSERVED" for c in _CORE_CATEGORIES):
            undetermined.add("CORE_CATEGORY_NOT_OBSERVED")                  # el ad hoc excluyó evidencia núcleo
    return {"contract": EQUIVALENCE_V2,
            "versions": {"evidence": vs.get("canonical_evidence") or ce.get("version"), "closure_rules": vs.get("closure"), "identity_rules": vs.get("identity"),
                         "platform_boundary": (pb or {}).get("version") if isinstance(pb, dict) else None},
            "closure": sorted(closure - collapsed),
            "platform_frontiers": sorted(frontiers),
            "flow_produced_members": sorted(str((m or {}).get("artifact") if isinstance(m, dict) else m) for m in pc.get("flow_produced_members") or []),
            "source_contracts": [views[k] for k in sorted(views)],
            "undetermined": sorted(undetermined)}


def fingerprint_v2(profile: Any, extensions: Any = None) -> dict:
    ident, eq = identity(profile), equivalence_v2(profile, extensions)
    return {"schema": FINGERPRINT_SCHEMA_V2, "contract": EQUIVALENCE_V2, "identity": ident, "identity_digest": digest(ident),
            "equivalence_digest": digest(eq), "undetermined": eq["undetermined"], "contract_versions": eq["versions"]}


def fingerprint(profile: Any, extensions: Any = None, *, schema: str = FINGERPRINT_SCHEMA) -> dict:
    """Huella bajo ``schema`` (por defecto, la VIGENTE). Una huella se compara SIEMPRE con otra del MISMO esquema."""
    if schema == FINGERPRINT_SCHEMA_V1:
        return fingerprint_v1(profile)
    if schema == FINGERPRINT_SCHEMA_V2:
        return fingerprint_v2(profile, extensions)
    raise ValueError(f"esquema de huella no soportado: {schema}")


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
                      adhoc_space: str | None, connection_records: list[dict], catalog_dir: Path, pool_root: Path | None,
                      governed_extensions: Any = None) -> dict:
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
    # Se interpreta SIEMPRE bajo el esquema de la huella del ad hoc (v1 histórica = v1 estricta; v2 = material). Nunca se comparan esquemas distintos.
    schema = (adhoc_fingerprint or {}).get("schema")
    if not adhoc_fingerprint or not schema:
        reasons.append("NO_ADHOC_FINGERPRINT")
    elif schema not in SUPPORTED_SCHEMAS:
        reasons.append("FINGERPRINT_SCHEMA_UNSUPPORTED")
    else:
        gov_fp = fingerprint(gp, governed_extensions, schema=schema)
        checks["fingerprint_schema"] = schema
        checks["governed_fingerprint"] = {k: gov_fp[k] for k in ("identity_digest", "equivalence_digest")}
        undetermined = sorted(set(adhoc_fingerprint.get("undetermined") or []) | set(gov_fp.get("undetermined") or []))
        if undetermined:
            checks["undetermined"] = undetermined
            reasons.append("EVIDENCE_SCOPE_UNDETERMINED:" + ",".join(undetermined))      # incertidumbre: abstención, nunca equivalencia
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
