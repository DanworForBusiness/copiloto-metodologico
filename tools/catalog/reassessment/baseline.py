"""baseline.py — reproducibilidad y vigencia de los assessments PUBLICADOS sobre un snapshot local (contrato ``reassessment-baseline/v1``).

SOLO LECTURA POR DISEÑO: lee un snapshot preparado de antemano (sobres + pool) y escribe UN informe en una carpeta explícita fuera del repo, del
snapshot y de los almacenes del Hub. No importa persistencia, publicación ni servicios web (lo fija un test), no usa red, no regenera ni reescribe
assessments, no aplica reglas candidatas ni migraciones. La publicación selectiva sigue siendo una acción aparte y explícita (``assessment_backfill``).

Ejes INDEPENDIENTES por sobre:
  * integridad   — las firmas persistidas son coherentes entre sí (perfil ↔ artefacto ↔ Juicio);
  * Juicio       — ``evaluate_judgment`` con el ruleset y las ratificaciones que el propio sobre registra reproduce su ``output_signature``;
  * evidencia    — el motor reconstruye el MISMO perfil, solo si el snapshot trae un pool EQUIVALENTE (misma firma) y el código está en las MISMAS versiones;
  * vigencia     — la regla compartida del Hub (``currentness.resolve_currentness``); sin firma de dependencias, un pool cambiado es UNDETERMINED (nunca vigente ni
                   desactualizado por omisión).
``JUDGMENT_ONLY`` NO es un defecto: el Juicio se reproduce y la integridad se verifica, pero no hay evidencia para reconstruir el perfil de forma independiente.

Snapshot: ``<snap>/envelopes/**/*.json`` (sobres ``assessment-cache/v1``), ``<snap>/pool/`` (mismo layout que ``catalog_local/.csn_cache``) y, opcional,
``<snap>/snapshot.json`` ({"prefix_label", "taken_at"}). Uso: ``python -m tools.catalog.reassessment.baseline --snapshot <snap> --out <dir>``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.catalog.methodological_judgment import CURRENT_RULESET_VERSION
from tools.catalog.methodological_judgment.contract import signature as judgment_signature
from tools.catalog.methodological_judgment.engine import evaluate_judgment
from tools.catalog.modeling_assessment import currentness as cur_rule
from tools.catalog.modeling_assessment import dependency_signature as deps
from tools.catalog.modeling_assessment import profile as ma_profile
from tools.catalog.modeling_assessment import shadow

CONTRACT = "reassessment-baseline/v1"
ENVELOPE_VERSION = "assessment-cache/v1"
REPRODUCED, JUDGMENT_ONLY, NOT_REPRODUCED = "REPRODUCED", "JUDGMENT_ONLY", "NOT_REPRODUCED"
VERIFIED, BROKEN = "VERIFIED", "BROKEN"
MATCH, MISMATCH, ERROR, NOT_ATTEMPTED = "MATCH", "MISMATCH", "ERROR", "NOT_ATTEMPTED"
UNDETERMINED = "UNDETERMINED"
REPO_ROOT = Path(__file__).resolve().parents[3]


class SnapshotError(ValueError):
    """El snapshot no es utilizable (falta, mezcla ambientes o no trae sobres)."""


class OutputLocationError(ValueError):
    """La carpeta de salida cae dentro de algo que el simulador jamás puede escribir."""


# ───────────────────────────── entradas ─────────────────────────────
def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load_snapshot(snap: Path) -> tuple[dict, list[tuple[Path, bytes, dict]]]:
    snap = Path(snap)
    env_dir = snap / "envelopes"
    if not env_dir.is_dir():
        raise SnapshotError(f"el snapshot no trae la carpeta 'envelopes': {snap}")
    meta: dict = {}
    if (snap / "snapshot.json").is_file():
        meta = json.loads((snap / "snapshot.json").read_text(encoding="utf-8"))
        prefixes = meta.get("prefix_labels")
        if isinstance(prefixes, list) and len(set(prefixes)) > 1:
            raise SnapshotError(f"el snapshot mezcla ambientes: {sorted(set(prefixes))}")
    envelopes = []
    for p in sorted(env_dir.rglob("*.json")):
        raw = p.read_bytes()
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("envelope_version") == ENVELOPE_VERSION and data.get("client") and data.get("terminal_am"):
            envelopes.append((p, raw, data))
    if not envelopes:
        raise SnapshotError(f"el snapshot no trae sobres {ENVELOPE_VERSION}: {env_dir}")
    keys = Counter((d["client"].lower(), d["terminal_am"]) for _p, _r, d in envelopes)
    dup = sorted(k for k, n in keys.items() if n > 1)
    if dup:
        raise SnapshotError(f"el snapshot trae sobres duplicados para (cliente, AM): {dup}")
    return meta, envelopes


def current_versions() -> dict:
    """Bloque ``versions`` del código actual (misma fuente que el Hub: el constructor INDETERMINATE emite el bloque vigente)."""
    return dict(ma_profile.build_indeterminate_assessment(client="", requested_model=None, message="")["versions"])


def _artifact_signature(profile: dict) -> str:
    return hashlib.sha256(ma_profile.canonical_assessment_json(profile).encode("utf-8")).hexdigest()


class _PoolIndex:
    """Firma del pool, entidades y firmas de dependencias por cliente del snapshot: se calcula UNA vez por cliente."""

    def __init__(self, pool_root: Path | None):
        self.root = pool_root if pool_root and pool_root.is_dir() else None
        self._cache: dict[str, dict] = {}

    def of(self, client: str) -> dict:
        if client.lower() in self._cache:
            return self._cache[client.lower()]
        info: dict = {"available": False, "reason": "POOL_UNAVAILABLE"}
        if self.root is not None:
            try:
                key, group = shadow.select_pool(self.root, client, None)
                files = [*group["jsons"], *group["txts"]]
                info = {"available": True, "client_dir": key[0], "domain_dir": key[1], "signature": shadow.pool_signature(self.root, files), "group": group}
            except shadow.AmbiguousClientDomainError:
                info = {"available": False, "reason": "AMBIGUOUS_POOL_DOMAIN"}
            except shadow.ClientNotFoundError:
                info = {"available": False, "reason": "POOL_UNAVAILABLE"}
        self._cache[client.lower()] = info
        return info

    def dependency_signature(self, client: str, am: str) -> str | None:
        info = self.of(client)
        if not info.get("available"):
            return None
        if "deps" not in info:
            try:
                ents, universe, _sh = shadow.semantic_universe(shadow.load_pool(info["group"]), info["group"])
                info["deps"] = deps.dependency_signatures(self.root, info["group"], ents, universe)
            except Exception:                                   # noqa: BLE001 — dependencias no calculables = INCIERTO (misma regla que el Hub)
                info["deps"] = {}
        return info["deps"].get(am)


# ───────────────────────────── ejes ─────────────────────────────
def check_integrity(env: dict) -> dict:
    """Coherencia de las firmas PERSISTIDAS (sin recalcular nada del motor)."""
    prof, jdg = env.get("profile_json") or {}, env.get("judgment") or {}
    src = jdg.get("source_assessment") or {}
    causes = []
    if env.get("assessment_artifact_signature") and _artifact_signature(prof) != env["assessment_artifact_signature"]:
        causes.append("ARTIFACT_SIGNATURE_MISMATCH")
    if src.get("profile_signature") and judgment_signature(prof) != src["profile_signature"]:
        causes.append("PROFILE_SIGNATURE_MISMATCH")
    if src.get("input_pool_signature") and src["input_pool_signature"] != env.get("input_pool_signature"):
        causes.append("JUDGMENT_POOL_SIGNATURE_DIFFERS")
    if src.get("assessment_versions") and dict(src["assessment_versions"]) != dict(env.get("assessment_versions") or {}):
        causes.append("JUDGMENT_VERSIONS_DIFFER")
    if not jdg:
        causes.append("JUDGMENT_MISSING")
    return {"state": BROKEN if causes else VERIFIED, "causes": causes}


def reproduce_judgment(env: dict) -> dict:
    jdg = env.get("judgment") or {}
    try:
        rc = ((jdg.get("decision_inputs") or {}).get("ratified_contracts") or [])
        out = evaluate_judgment(env["profile_json"], ruleset=jdg.get("ruleset_version"), ratified_contracts=rc)
    except Exception as exc:                                    # noqa: BLE001
        return {"state": ERROR, "output_signature": None, "error": type(exc).__name__}
    same = out.get("output_signature") == jdg.get("output_signature")
    return {"state": MATCH if same else MISMATCH, "output_signature": out.get("output_signature"), "ratified_contracts_used": len(rc)}


def reconstruct_evidence(env: dict, pool: dict, versions: dict, pools: _PoolIndex) -> dict:
    if not pool.get("available"):
        return {"state": NOT_ATTEMPTED, "reason": pool.get("reason")}
    if pool["signature"] != env.get("input_pool_signature"):
        return {"state": NOT_ATTEMPTED, "reason": "POOL_NOT_EQUIVALENT"}
    if cur_rule.version_diff(env.get("assessment_versions"), versions):
        return {"state": NOT_ATTEMPTED, "reason": "CODE_VERSIONS_DIFFER"}
    try:
        doc, _meta = shadow.assess(pools.root, pool["client_dir"], env["terminal_am"], pool["domain_dir"])
    except Exception as exc:                                    # noqa: BLE001
        return {"state": ERROR, "reason": "ENGINE_ERROR", "error": type(exc).__name__}
    art, prof_sig = _artifact_signature(doc), judgment_signature(doc)
    same = art == _artifact_signature(env["profile_json"])
    return {"state": MATCH if same else MISMATCH, "reason": None if same else "PROFILE_DIFFERS", "artifact_signature": art, "profile_signature": prof_sig}


def _currency_core(env: dict, pool: dict, versions: dict, pools: _PoolIndex) -> dict:
    """Vigencia con la regla COMPARTIDA del Hub. Diferencia: un pool cambiado SIN firma de dependencias utilizable es UNDETERMINED (el Hub lo muestra STALE)."""
    if not pool.get("available"):
        return {"state": UNDETERMINED, "basis": pool.get("reason"), "hub_state": None, "version_diff": list(cur_rule.version_diff(env.get("assessment_versions"), versions))}
    stored, rule = env.get("input_closure_signature"), env.get("dependency_rule_version")
    current_dep: dict = {}

    def dependency() -> str:
        current_dep["sig"] = pools.dependency_signature(env["client"], env["terminal_am"]) if stored and rule == deps.DEPENDENCY_RULE_VERSION else None
        return cur_rule.dependency_verdict_from(stored, rule, deps.DEPENDENCY_RULE_VERSION, current_dep["sig"])

    c, drift, verdict = cur_rule.resolve_currentness(env.get("input_pool_signature"), env.get("assessment_versions"), pool["signature"], versions, dependency)
    out = {"hub_state": c.state, "hub_reason": c.reason, "pool_drift": drift, "dependency_verdict": verdict, "version_diff": list(c.version_diff),
           "pending_migration": c.pending_migration, "pool_signature_current": pool["signature"], "closure_signature_current": current_dep.get("sig")}
    if c.state == cur_rule.CURRENT:
        return {"state": cur_rule.CURRENT, "basis": "DEPENDENCIES_INDEPENDENT" if drift else "POOL_IDENTICAL", **out}
    if verdict == cur_rule.DEP_AFFECTED:
        return {"state": cur_rule.STALE, "basis": "DEPENDENCIES_AFFECTED", **out}
    if verdict == cur_rule.DEP_UNCERTAIN:
        basis = "NO_DEPENDENCY_SIGNATURE" if not stored else ("DEPENDENCY_RULE_DIFFERS" if rule != deps.DEPENDENCY_RULE_VERSION else "DEPENDENCIES_UNCERTAIN")
        if c.reason == cur_rule.STALE_INPUT_CHANGED:
            return {"state": UNDETERMINED, "basis": basis, **out}                # el pool cambió y no hay cómo saber si le afecta
    return {"state": cur_rule.STALE, "basis": "VERSIONS_DIFFER", **out}            # versiones distintas (con o sin cambio de pool)


HUB_FAIL_CLOSED = "HUB_FAIL_CLOSED_UNCERTAIN_DEPENDENCIES"
COMPARISON_TEXT = {
    "AGREES": "El Hub y la simulación coinciden.",
    HUB_FAIL_CLOSED: ("Política operativa del Hub, conservadora y documentada (docs/REPUBLICATION_P1.md): si no puede demostrar que lo que usa el modelo sigue igual "
                      "(dependencias inciertas), lo trata como desactualizado: muestra el informe anterior, recomienda regenerarlo o republicarlo y las migraciones "
                      "lo excluyen. La simulación informa la vigencia DEMOSTRABLE: con la evidencia disponible no se puede afirmar ni negar. Ambos resultados son "
                      "correctos para su propósito; no es un error del Hub."),
    "NOT_COMPARABLE": "Sin pool del cliente en el snapshot: el Hub no puede evaluarse desde este snapshot.",
    "DIFFERS": "El Hub y la simulación difieren por una causa no catalogada: revisar.",
}


def hub_comparison(currency: dict) -> dict:
    """Relación entre la vigencia OPERATIVA que muestra el Hub (``hub_state``) y la vigencia DEMOSTRABLE de la simulación (``state``). Se preservan ambas."""
    hub, sim = currency.get("hub_state"), currency.get("state")
    if hub is None:
        code = "NOT_COMPARABLE"
    elif hub == sim:
        code = "AGREES"
    elif sim == UNDETERMINED and hub == cur_rule.STALE and currency.get("dependency_verdict") == cur_rule.DEP_UNCERTAIN:
        code = HUB_FAIL_CLOSED
    else:
        code = "DIFFERS"
    return {"code": code, "hub_state": hub, "simulation_state": sim, "explanation": COMPARISON_TEXT[code]}


def evaluate_currency(env: dict, pool: dict, versions: dict, pools: _PoolIndex) -> dict:
    out = _currency_core(env, pool, versions, pools)
    return {**out, "hub_comparison": hub_comparison(out)}


def _flags(env: dict, versions: dict, evidence: dict, currency: dict) -> list[str]:
    flags = []
    if not env.get("input_closure_signature"):
        flags.append("NO_DEPENDENCY_SIGNATURE")
    if cur_rule.version_diff(env.get("assessment_versions"), versions):
        flags.append("MIXED_EVIDENCE_VERSIONS")
    if (env.get("judgment") or {}).get("ruleset_version") != CURRENT_RULESET_VERSION:
        flags.append("LEGACY_RULESET")
    if evidence.get("reason") == "POOL_NOT_EQUIVALENT":
        flags.append("POOL_NOT_EQUIVALENT")
    if currency.get("pending_migration"):
        flags.append(f"PENDING_MIGRATION:{currency['pending_migration']}")
    if evidence.get("reason") == "AMBIGUOUS_POOL_DOMAIN":
        flags.append("AMBIGUOUS_POOL_DOMAIN")
    return flags


def _published(env: dict) -> dict:
    j = env.get("judgment") or {}
    return {"status": j.get("status"), "level": j.get("level"), "level_rule": j.get("level_rule"), "ruleset_version": j.get("ruleset_version"),
            "output_signature": j.get("output_signature"), "profile_signature": (j.get("source_assessment") or {}).get("profile_signature"),
            "assessment_versions": env.get("assessment_versions"), "input_pool_signature": env.get("input_pool_signature"),
            "input_closure_signature": env.get("input_closure_signature"), "dependency_rule_version": env.get("dependency_rule_version"), "generated_at": env.get("generated_at")}


def evaluate_envelope(raw: bytes, env: dict, pools: _PoolIndex, versions: dict) -> dict:
    pool = pools.of(env["client"])
    integrity = check_integrity(env)
    judgment = reproduce_judgment(env)
    evidence = reconstruct_evidence(env, pool, versions, pools)
    currency = evaluate_currency(env, pool, versions, pools)
    causes = [*integrity["causes"]]
    if judgment["state"] != MATCH:
        causes.append(f"JUDGMENT_{judgment['state']}")
    if evidence["state"] in (MISMATCH, ERROR):
        causes.append(f"EVIDENCE_{evidence['state']}")
    if causes:
        state = NOT_REPRODUCED
    elif evidence["state"] == MATCH:
        state = REPRODUCED
    else:
        state = JUDGMENT_ONLY
    return {"client": env["client"], "terminal_am": env["terminal_am"], "envelope_sha256": _sha256_bytes(raw), "published": _published(env),
            "reproducibility": {"state": state, "integrity": integrity, "judgment": judgment, "evidence": evidence, "causes": causes},
            "currency": currency, "flags": _flags(env, versions, evidence, currency)}


# ───────────────────────────── salida ─────────────────────────────
def _forbidden_roots(snap: Path) -> list[Path]:
    roots = [REPO_ROOT, Path(snap)]
    for var in ("DANWOR_CATALOG_DIR", "DANWOR_GOVERNED_ASSESSMENTS_DIR", "DANWOR_CATALOG_CSN_CACHE", "DANWOR_ASSESSMENT_CACHE_DIR"):
        if os.environ.get(var):
            roots.append(Path(os.environ[var]))
    return [r.resolve() for r in roots]


def validate_output_dir(out: Path, snap: Path) -> Path:
    out = Path(out).resolve()
    for root in _forbidden_roots(snap):
        if out == root or root in out.parents:
            raise OutputLocationError(f"la salida no puede estar dentro de {root}")
    return out


def run(snap: Path, *, generated_at: str | None = None) -> dict:
    meta, envelopes = load_snapshot(snap)
    versions = current_versions()
    pools = _PoolIndex(Path(snap) / "pool")
    rows = [evaluate_envelope(raw, env, pools, versions) for _p, raw, env in envelopes]
    rows.sort(key=lambda r: (r["client"].lower(), r["terminal_am"]))
    summary = {"reproducibility": dict(Counter(r["reproducibility"]["state"] for r in rows)), "currency": dict(Counter(r["currency"]["state"] for r in rows)),
               "matrix": dict(Counter(f"{r['reproducibility']['state']} × {r['currency']['state']}" for r in rows)),
               "flags": dict(Counter(f for r in rows for f in r["flags"])), "envelopes": len(rows),
               "hub_comparison": dict(Counter(r["currency"]["hub_comparison"]["code"] for r in rows))}
    return {"contract": CONTRACT, "generated_at": generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "code": {"git_sha": shadow._git_sha(), "current_ruleset": CURRENT_RULESET_VERSION, "current_versions": versions,
                     "dependency_rule_version": deps.DEPENDENCY_RULE_VERSION},
            "snapshot": {"prefix_label": meta.get("prefix_label"), "taken_at": meta.get("taken_at"), "envelopes": len(rows),
                         "manifest_sha256": _sha256_bytes(json.dumps(sorted(r["envelope_sha256"] for r in rows)).encode())},
            "envelopes": rows, "summary": summary}


def render_markdown(report: dict) -> str:
    s = report["summary"]
    out = [f"# Reproducibilidad y vigencia del baseline ({report['contract']})", "",
           f"Sobres: {s['envelopes']} · código {report['code']['git_sha'] or 'sin git'} · ruleset vigente {report['code']['current_ruleset']}", "",
           "`JUDGMENT_ONLY` no es un defecto: el Juicio se reproduce y la integridad se verifica, pero el snapshot no permite reconstruir el perfil de forma independiente.", "",
           "| Reproducibilidad × vigencia | Sobres |", "|---|---|", *[f"| {k} | {v} |" for k, v in sorted(s["matrix"].items())], "",
           "## Vigencia: Hub vs simulación", "",
           "El Hub muestra la vigencia OPERATIVA; la simulación, la vigencia DEMOSTRABLE. Se preservan ambas.", "",
           "| Relación | Sobres | Explicación |", "|---|---|---|", *[f"| {k} | {v} | {COMPARISON_TEXT[k]} |" for k, v in sorted(s["hub_comparison"].items())], "",
           "| Cliente | AM | Reproducibilidad | Evidencia | Vigencia (base) | Hub | Banderas |", "|---|---|---|---|---|---|---|"]
    for r in report["envelopes"]:
        rep, c = r["reproducibility"], r["currency"]
        ev = rep["evidence"]["state"] + (f" ({rep['evidence'].get('reason')})" if rep["evidence"].get("reason") else "")
        out.append(f"| {r['client']} | {r['terminal_am']} | {rep['state']}{' · ' + ', '.join(rep['causes']) if rep['causes'] else ''} | {ev} | "
                   f"{c['state']} ({c.get('basis')}) | {c.get('hub_state') or '—'} ({c['hub_comparison']['code']}) | {', '.join(r['flags']) or '—'} |")
    return "\n".join(out) + "\n"


def write_report(report: dict, out_dir: Path, snap: Path) -> tuple[Path, Path]:
    out_dir = validate_output_dir(out_dir, snap)
    out_dir.mkdir(parents=True, exist_ok=True)
    j, m = out_dir / "reassessment-baseline.json", out_dir / "reassessment-baseline.md"
    j.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    m.write_text(render_markdown(report), encoding="utf-8")
    return j, m


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Reproducibilidad y vigencia del baseline publicado (solo lectura).")
    ap.add_argument("--snapshot", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args(argv)
    validate_output_dir(a.out, a.snapshot)                                  # falla ANTES de leer nada
    report = run(a.snapshot)
    j, _m = write_report(report, a.out, a.snapshot)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=1))
    print(f"informe: {j}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
