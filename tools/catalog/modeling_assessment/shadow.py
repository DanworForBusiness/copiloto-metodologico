"""Runner del assessment shadow (M7): pool -> hechos -> composicion -> documento publico -> JSON + Markdown + manifiesto.

Lectura SOLO: no escribe en el catalogo, no sube nada, no toca el legacy. La salida va a un directorio EXPLICITO validado por `paths.resolve_output_dir`
(falla CERRADO ante un destino ausente o dentro de un directorio persistido/espejado), y esa validacion ocurre ANTES de leer el pool.

Layout canonico de salida (P9/P6):
    <out>/<cliente>/<AM>.profile.json       documento publico versionado (`profile.build_assessment`)
    <out>/<cliente>/<AM>.profile.md         el MISMO documento renderizado (`render_md.render_markdown`)
    <out>/<cliente>/run_manifest.json       procedencia de las corridas (git, firma del pool, versiones, hashes de salida, marca de tiempo UTC)

JSON y Markdown no llevan marcas de tiempo: son deterministas para la misma entrada; la hora vive solo en el manifiesto.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import legacy_bridge as bridge
from . import paths
from .assessability import compose_assessment
from .facts_am import am_facts
from .facts_closure import closure_facts
from .facts_contract_semantics import published_contract_facts
from .facts_expressions import expression_facts
from .facts_field_lineage import published_field_lineage_facts, source_contract_class_facts
from .facts_harmonization_contract import harmonization_contract_facts
from .facts_processing import processing_property_facts
from .facts_reconvergence import reconvergence_facts
from .facts_view_complexity import view_complexity_facts
from .facts_harmonization import harmonization_facts
from .facts_ingestion import ingestion_facts
from .facts_view_type import view_type_facts
from .facts_naming import naming_facts
from .facts_propagation import propagation_facts
from .facts_source import source_facts
from .identity import classify_universe
from .model import Fact
from .profile import (
    REASON_REGISTRY_VERSION, build_assessment, build_indeterminate_assessment, canonical_assessment_json, validate_assessment,
)
from .readers import SupplementaryEvidence
from .render_md import render_markdown
from .scope import AssessmentUniverse, TerminalAmSelectionRequired, UnknownRootObjectError, UnknownTerminalAmError

MANIFEST_NAME = "run_manifest.json"
MANIFEST_VERSION = "run-manifest/v1"
NO_TERMINAL_AM_LABEL = "NO_TERMINAL_ANALYTIC_MODEL"

_SLUG = re.compile(r"[^A-Za-z0-9._-]+")


class ClientNotFoundError(ValueError):
    """El cliente pedido no tiene un pool en el inbound."""


class AmbiguousClientDomainError(ValueError):
    """El cliente tiene mas de un dominio en el inbound: hay que elegir uno."""


class AssessmentPoolLoadError(RuntimeError):
    """El pool no se pudo cargar (colision de entidades o error de carga); se escribe un documento INDETERMINATE."""


@dataclass(frozen=True)
class RunResult:
    document: dict
    json_text: str
    markdown_text: str
    out_dir: Path
    json_path: Path
    markdown_path: Path
    manifest_path: Path
    exit_code: int


def client_slug(client: str) -> str:
    return _SLUG.sub("_", client).strip("._") or "client"


def select_pool(inbound: Path, client: str, domain: str | None = None) -> tuple[tuple[str, str], dict[str, list[Path]]]:
    """(clave (cliente_dir, dominio_dir), {"jsons","txts"}) del pool del cliente; coincidencia exacta sin distinguir mayusculas."""
    groups = bridge.legacy_pool_groups(inbound)
    found = sorted(k for k in groups if k[0].lower() == client.lower() and (domain is None or k[1].lower() == domain.lower()))
    if not found:
        known = sorted({k[0] for k in groups})
        raise ClientNotFoundError(f"el cliente {client!r} no tiene un conjunto de artefactos en {inbound}" + (f" (dominio {domain!r})" if domain else "") +
                                  f"; clientes disponibles: {known}")
    if len(found) > 1:
        raise AmbiguousClientDomainError(f"el cliente {client!r} tiene varios dominios {[k[1] for k in found]}: indicar --domain")
    return found[0], groups[found[0]]


def pool_signature(inbound: Path, files: list[Path]) -> str:
    """sha256 sobre (ruta relativa, sha256 del contenido) ordenados: identifica el pool exacto leido."""
    h = hashlib.sha256()
    for p in sorted(files, key=lambda x: x.relative_to(inbound).as_posix()):
        h.update(p.relative_to(inbound).as_posix().encode("utf-8"))
        h.update(b"\0")
        h.update(bridge.file_sha256(p).encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()


def load_pool(group: dict[str, list[Path]]):
    """Entidades del pool (el loader legacy imprime en stdout: se silencia)."""
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            entities, _sql = bridge.load_folder(json_files=group["jsons"], txt_files=group["txts"])
    except bridge.CatalogCollisionError as exc:
        raise AssessmentPoolLoadError(f"colisión de entidades en el conjunto de artefactos: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - cualquier fallo de carga se reporta como documento INDETERMINATE, nunca se enmascara
        raise AssessmentPoolLoadError(f"no se pudo cargar el conjunto de artefactos: {type(exc).__name__}: {exc}") from exc
    return entities


def collect_facts(universe: AssessmentUniverse, entities, am: str, evidence: SupplementaryEvidence) -> list[Fact]:
    identity = classify_universe(entities)
    class_facts = source_contract_class_facts(universe, entities, am, identity=identity, evidence=evidence)
    return [*class_facts,
            *published_field_lineage_facts(universe, entities, am, identity=identity, evidence=evidence, leaf_classes={f.subject: f.value for f in class_facts}),
            *source_facts(universe, entities, am, evidence=evidence, identity=identity),
            *ingestion_facts(universe, entities, am, evidence=evidence, identity=identity),
            *am_facts(universe, entities, am, identity=identity),
            *closure_facts(universe, entities, am, evidence=evidence),
            *view_type_facts(universe, entities, am, evidence=evidence),
            *propagation_facts(universe, entities, am, identity=identity, evidence=evidence),
            *harmonization_facts(universe, entities, am, identity=identity, evidence=evidence),
            *naming_facts(universe, entities, am, identity=identity),
            *published_contract_facts(universe, entities, am, identity=identity, evidence=evidence),
            *harmonization_contract_facts(universe, entities, am, identity=identity, evidence=evidence),
            *expression_facts(universe, entities, am, identity=identity, evidence=evidence),
            *reconvergence_facts(universe, entities, am, identity=identity, evidence=evidence),
            *view_complexity_facts(universe, entities, am, identity=identity, evidence=evidence),
            *processing_property_facts(universe, entities, am, identity=identity, evidence=evidence)]


def list_terminal_models(inbound: Path, client: str, domain: str | None = None) -> tuple[str, ...]:
    _key, group = select_pool(inbound, client, domain)
    return AssessmentUniverse.from_entities(load_pool(group)).terminal_ams


def _business_label(entities, terminal_am: str | None) -> str | None:
    """Nombre de negocio del AM terminal segun la EVIDENCIA (@EndUserText.label ya resuelto por el loader, incl. i18n). El loader cae al
    nombre tecnico cuando la evidencia no declara etiqueta: eso NO es un nombre de negocio (no se deriva un titulo del nombre tecnico)."""
    if not terminal_am:
        return None
    ent = entities.get(terminal_am)
    label = str(getattr(ent, "label", "") or "").strip()
    return label if label and label != terminal_am else None


def _acquired_flow_families(evidence_scope: dict | None):
    """Familias de flows adquiridas segun el alcance EFECTIVO de la extraccion (None => alcance desconocido => comportamiento previo)."""
    if not evidence_scope:
        return None
    effective = set(evidence_scope.get("effective_features") or ())
    return {"replication-flows", "data-flows"} & effective


def assess(inbound: Path, client: str, am: str | None, domain: str | None = None, *, evidence_scope: dict | None = None,
           generic_root: bool = False, root_type: str | None = None) -> tuple[dict, dict]:
    """(documento publico, metadatos de pool para el manifiesto). Lanza ClientNotFoundError/AmbiguousClientDomainError/UnknownTerminalAmError/
    TerminalAmSelectionRequired; un fallo de carga NO lanza: devuelve el documento INDETERMINATE.
    ``generic_root=True`` (producto "Assessment de tu modelo"): ``am`` es el OBJETO RAIZ y puede ser un AM, una vista o una tabla; se evalua su
    clausura semantica upstream (``root_type`` = tipo informado por el extractor). Sin ``generic_root`` (Catalogo) el comportamiento es el de siempre:
    solo un AM terminal es una unidad valida."""
    key, group = select_pool(inbound, client, domain)
    files = [*group["jsons"], *group["txts"]]
    signature = pool_signature(inbound, files)
    meta = {"client_dir": key[0], "domain_dir": key[1], "files": len(files), "json_files": len(group["jsons"]), "pool_signature": signature, "errors": []}
    try:
        entities = load_pool(group)
    except AssessmentPoolLoadError as exc:
        meta["errors"].append(str(exc))
        return build_indeterminate_assessment(client=key[0], requested_model=am, message=str(exc), pool_signature=signature), meta
    universe = AssessmentUniverse.from_entities(entities)
    if generic_root and am is not None:
        universe, scope = universe.resolve_root(entities, am, root_type, pool_signature=signature)   # puede lanzar UnknownRootObjectError
    else:
        scope = universe.scope_for(am, pool_signature=signature)           # puede lanzar UnknownTerminalAmError / TerminalAmSelectionRequired
    facts = [] if scope.root is None else collect_facts(universe, entities, scope.root, SupplementaryEvidence.from_files(group["jsons"], root=inbound, acquired_flow_families=_acquired_flow_families(evidence_scope),
                                                                                                  view_type_of=bridge.view_type_of_export, family_of_dir=bridge.artifact_family_of_dir,
                                                                                                  effective_features=(evidence_scope or {}).get("effective_features") if evidence_scope else None))
    comp = compose_assessment(scope, facts)
    doc = build_assessment(comp, facts, client=key[0], terminal_label=_business_label(entities, scope.root), evidence_scope=evidence_scope)
    validate_assessment(doc)
    meta["terminal_analytic_models_in_pool"] = len(universe.terminal_ams)
    return doc, meta


# ───────────────────────────── salida ─────────────────────────────
def _git_sha() -> str | None:
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=paths.repo_root(), capture_output=True, text=True, timeout=10, check=False)
        sha = r.stdout.strip()
        return sha if r.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", sha) else None
    except (OSError, subprocess.SubprocessError):
        return None


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def write_outputs(doc: dict, meta: dict, out_dir: Path, *, am: str | None, inbound: Path, error: bool) -> RunResult:
    """Escribe JSON + Markdown + manifiesto en `out_dir/<cliente>/`. `out_dir` YA fue validado por `paths.resolve_output_dir`."""
    target = out_dir / client_slug(doc["scope"]["client"])
    target.mkdir(parents=True, exist_ok=True)
    label = am or doc["scope"]["terminal_analytic_model"] or NO_TERMINAL_AM_LABEL
    json_text = canonical_assessment_json(doc)
    md_text = render_markdown(doc)
    json_path = target / paths.profile_filename(label, "json")
    md_path = target / paths.profile_filename(label, "md")
    _write_text(json_path, json_text)
    _write_text(md_path, md_text)
    manifest_path = target / MANIFEST_NAME
    runs: dict = {}
    if manifest_path.exists():
        try:
            prev = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(prev, dict) and isinstance(prev.get("runs"), dict):
                runs = prev["runs"]
        except (OSError, ValueError):
            runs = {}
    runs[label] = {
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "assessment_state": doc["assessment_state"],
        "git_sha": _git_sha(),
        "inbound": str(inbound), "client": doc["scope"]["client"], "analytic_model": doc["scope"]["terminal_analytic_model"],
        "pool": {k: meta[k] for k in ("client_dir", "domain_dir", "files", "json_files", "pool_signature")},
        "versions": {**doc["versions"], "assessment_schema": doc["schema_version"], "reason_codes": REASON_REGISTRY_VERSION},
        "outputs": {"json": {"file": json_path.name, "sha256": _sha256_text(json_text)}, "markdown": {"file": md_path.name, "sha256": _sha256_text(md_text)}},
        "errors": list(meta["errors"]),
    }
    _write_text(manifest_path, json.dumps({"manifest_version": MANIFEST_VERSION, "runs": dict(sorted(runs.items()))}, sort_keys=True, ensure_ascii=False,
                                          indent=2, separators=(",", ": ")) + "\n")
    return RunResult(doc, json_text, md_text, target, json_path, md_path, manifest_path, 3 if error else 0)


def run(*, inbound: str | Path, client: str, am: str | None, out: str | Path | None, domain: str | None = None) -> RunResult:
    """Corrida completa. El destino se valida PRIMERO (fail-closed); despues se lee el pool y se escribe."""
    out_dir = paths.resolve_output_dir(out)                        # sin allow_persisted: no se expone la excepcion
    inbound_path = Path(inbound)
    if not inbound_path.is_dir():
        raise FileNotFoundError(f"--inbound no es un directorio: {inbound_path}")
    doc, meta = assess(inbound_path, client, am, domain)
    return write_outputs(doc, meta, out_dir, am=am, inbound=inbound_path, error=doc["assessment_state"] == "INDETERMINATE")


__all__ = ["AmbiguousClientDomainError", "AssessmentPoolLoadError", "ClientNotFoundError", "RunResult", "TerminalAmSelectionRequired",
           "UnknownTerminalAmError", "UnknownRootObjectError", "assess", "collect_facts", "list_terminal_models", "pool_signature", "run", "select_pool", "write_outputs"]
