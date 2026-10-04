"""Lectores SUPLEMENTARIOS de evidencia cruda (M2) — solo lectura, aislados del legacy.

El loader legacy (que NO se modifica) descarta metadata cruda del export (p.ej. `@DataWarehouse.remote.query`
de las remote tables), solo conserva una parte del contrato y mezcla el pool. Estos lectores leen directamente los
JSON del pool y devuelven la evidencia ORIGINAL sin interpretarla:

  * `RemoteTableEvidence` — por definicion remota: conexion, entidad remota, inventario de anotaciones del
    artefacto, `supportedCapabilities`, y el `@DataWarehouse.remote.query` crudo (columnas y `where` verbatim) (L1);
  * `FlowEvidence` — Replication Flows y Data Flows (plano PROCESSING): nombre, entidades fuente/destino y sistema
    de origen declarado. Solo REFERENCIAS: no se evalua la calidad del flujo ni su mecanica (full/delta/etc.);
  * `entity_files` — todos los archivos del pool que definen cada entidad (procedencia; el loader solo conserva el
    primero) (L6);
  * `ReadIssue` — todo archivo ilegible/invalido queda registrado EXPLICITAMENTE (no se traga ni se infiere).

Reglas: no importa codigo legacy; no escribe; no infiere metadata ausente (ausente => `None`/vacio + la causa
la decide quien arma el hecho); orden determinista; las rutas se expresan relativas a `root` cuando se indica.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

READERS_VERSION = "supplementary-readers/v1"

REPLICATION_FLOW = "REPLICATION_FLOW"
DATA_FLOW = "DATA_FLOW"

_REMOTE_CONNECTION = "@DataWarehouse.remote.connection"
_REMOTE_ENTITY = "@DataWarehouse.remote.entity"
_REMOTE_QUERY = "@DataWarehouse.remote.query"
_CAPABILITIES = "@ObjectModel.supportedCapabilities"
_PATTERN = "@ObjectModel.modelingPattern"
# Un target de Replication Flow que mantiene una tabla de captura de cambios lleva este sufijo (observado en
# los exports reales: `X_Delta`). Regla explicita y acotada, aplicada SOLO al resolver el nombre de la tabla local.
DELTA_SHADOW_SUFFIX = "_Delta"
_REPLICATION_KEY_TOKENS = ("replic", "delta", "federat")


@dataclass(frozen=True)
class ReadIssue:
    source_file: str
    reason: str


@dataclass(frozen=True)
class RemoteTableEvidence:
    entity: str
    source_file: str
    remote_connection: str | None
    remote_entity: str | None
    annotation_keys: tuple[str, ...]                 # claves `@...` del artefacto (no de sus elementos)
    supported_capabilities: tuple[str, ...]
    modeling_pattern: str | None
    has_remote_query: bool
    query_where: Any | None                          # `SELECT.where` VERBATIM (JSON) o None
    query_column_count: int | None
    replication_annotation_keys: tuple[str, ...]     # anotaciones cuyo nombre sugiere replicacion/delta/federacion


@dataclass(frozen=True)
class FlowSourceSystem:
    connection_id: str | None
    connection_type: str | None
    container: str | None


@dataclass(frozen=True)
class FlowEvidence:
    kind: str                                         # REPLICATION_FLOW | DATA_FLOW
    name: str
    source_file: str
    source_entities: tuple[str, ...]
    target_entities: tuple[str, ...]
    source_systems: tuple[FlowSourceSystem, ...]      # solo Replication Flows


@dataclass(frozen=True)
class ViewTypeEvidence:
    """Tipo de vista de una definición del pool (GRAPHICAL | SQL | UNKNOWN) y su procedencia. NO decide qué es una vista: eso lo decide quien
    arma el hecho con ``family`` (carpeta-tipo normalizada del archivo, o None en pools planos)."""
    entity: str
    source_file: str
    family: str | None
    view_type: str
    view_type_basis: str                              # STORED (el JSON traía `viewType`) | DERIVED (se derivó de la metadata cruda)


def _rel(path: Path, root: Path | None) -> str:
    if root is not None:
        try:
            return path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            pass
    return path.name


def _str_or_none(v: Any) -> str | None:
    return v if isinstance(v, str) and v else None


def _hash_value(v: Any) -> str | None:
    """Valor de anotaciones tipo `{"#": "X"}` o string."""
    if isinstance(v, dict):
        return _str_or_none(v.get("#"))
    return _str_or_none(v)


def _read_remote_table(name: str, defn: dict, rel: str) -> RemoteTableEvidence | None:
    if _REMOTE_CONNECTION not in defn and _REMOTE_ENTITY not in defn:
        return None
    keys = tuple(sorted(k for k in defn if isinstance(k, str) and k.startswith("@")))
    caps = tuple(sorted(x for x in (_hash_value(c) for c in (defn.get(_CAPABILITIES) or [])) if x))
    rq = defn.get(_REMOTE_QUERY)
    where, ncols, has_q = None, None, False
    if isinstance(rq, dict):
        has_q = True
        select = rq.get("SELECT")
        if isinstance(select, dict):
            where = select.get("where")
            cols = select.get("columns")
            ncols = len(cols) if isinstance(cols, list) else None
    repl = tuple(k for k in keys if any(t in k.lower() for t in _REPLICATION_KEY_TOKENS))
    return RemoteTableEvidence(
        entity=name, source_file=rel,
        remote_connection=_str_or_none(defn.get(_REMOTE_CONNECTION)),
        remote_entity=_str_or_none(defn.get(_REMOTE_ENTITY)),
        annotation_keys=keys, supported_capabilities=caps, modeling_pattern=_hash_value(defn.get(_PATTERN)),
        has_remote_query=has_q, query_where=where, query_column_count=ncols, replication_annotation_keys=repl)


def _read_replication_flow(name: str, body: dict, rel: str) -> FlowEvidence:
    contents = body.get("contents") if isinstance(body.get("contents"), dict) else {}
    systems = tuple(
        FlowSourceSystem(_str_or_none(s.get("connectionId")), _str_or_none(s.get("connectionType")),
                         _str_or_none(s.get("container")))
        for s in (contents.get("sourceSystem") or []) if isinstance(s, dict))
    srcs = body.get("sources") if isinstance(body.get("sources"), dict) else {}
    tgts = body.get("targets") if isinstance(body.get("targets"), dict) else {}
    # Los objetos de las tareas tambien declaran fuente/destino (nombres crudos).
    s_names, t_names = set(srcs), set(tgts)
    for task in (contents.get("replicationTasks") or []):
        if isinstance(task, dict):
            so, to = task.get("sourceObject"), task.get("targetObject")
            if isinstance(so, dict) and _str_or_none(so.get("name")):
                s_names.add(so["name"])
            if isinstance(to, dict) and _str_or_none(to.get("name")):
                t_names.add(to["name"])
    return FlowEvidence(REPLICATION_FLOW, name, rel, tuple(sorted(s_names)), tuple(sorted(t_names)), systems)


def _read_data_flow(name: str, body: dict, rel: str) -> FlowEvidence:
    srcs = body.get("sources") if isinstance(body.get("sources"), dict) else {}
    tgts = body.get("targets") if isinstance(body.get("targets"), dict) else {}
    return FlowEvidence(DATA_FLOW, name, rel, tuple(sorted(srcs)), tuple(sorted(tgts)), ())


# Familias del extractor que alimentan la evidencia de flows del plano PROCESSING (identificadores canonicos del extractor).
# Task Chains y Transformation Flows NO se leen aqui (ver tests/test_modeling_assessment_evidence_scope.py: auditoria de uso).
FLOW_FAMILY_REPLICATION = "replication-flows"
FLOW_FAMILY_DATA = "data-flows"
FLOW_FAMILIES = frozenset({FLOW_FAMILY_REPLICATION, FLOW_FAMILY_DATA})


class SupplementaryEvidence:
    """Evidencia cruda leida del pool. Inmutable tras la construccion.

    ``acquired_flow_families``: que familias de flows FUERON adquiridas por la extraccion. Por defecto (alcance desconocido: Catalogo, CLI,
    pools historicos) se asumen ambas: comportamiento previo. Si una NO fue adquirida, su ausencia en el pool NO es "ausencia evaluada":
    ``flows_conclusive`` es False y los hechos de plano PROCESSING lo declaran (no se afirma "ningun flow referencia X")."""

    def __init__(
        self,
        remote_tables: Mapping[str, RemoteTableEvidence],
        flows: Iterable[FlowEvidence],
        entity_files: Mapping[str, tuple[str, ...]],
        issues: Iterable[ReadIssue],
        files_read: int,
        acquired_flow_families: Iterable[str] | None = None,
        view_types: Mapping[str, ViewTypeEvidence] | None = None,
    ) -> None:
        self._view_types = dict(sorted((view_types or {}).items()))
        self.acquired_flow_families = FLOW_FAMILIES if acquired_flow_families is None else frozenset(acquired_flow_families) & FLOW_FAMILIES
        self._remote = dict(sorted(remote_tables.items()))
        self._flows = tuple(sorted(flows, key=lambda f: (f.kind, f.name, f.source_file)))
        self._files = {k: tuple(v) for k, v in sorted(entity_files.items())}
        self._issues = tuple(sorted(set(issues), key=lambda i: (i.source_file, i.reason)))
        self.files_read = files_read

    @classmethod
    def from_files(cls, json_files: Iterable[str | Path], root: str | Path | None = None,
                   acquired_flow_families: Iterable[str] | None = None, *, view_type_of=None, family_of_dir=None) -> "SupplementaryEvidence":
        """``view_type_of(data, nombre) -> (tipo, base)`` y ``family_of_dir(carpeta) -> familia|None`` los inyecta quien lee (el shadow, vía
        ``legacy_bridge``): este módulo permanece puro. Sin ellos no se recoge evidencia de tipo de vista (comportamiento previo)."""
        rootp = Path(root) if root is not None else None
        remote: dict[str, RemoteTableEvidence] = {}
        flows: list[FlowEvidence] = []
        view_cands: dict[str, list[ViewTypeEvidence]] = {}
        files: dict[str, list[str]] = {}
        issues: list[ReadIssue] = []
        n = 0
        for f in sorted(Path(x) for x in json_files):
            rel = _rel(f, rootp)
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                issues.append(ReadIssue(rel, f"no se pudo leer/parsear: {type(exc).__name__}"))
                continue
            if not isinstance(data, dict):
                issues.append(ReadIssue(rel, f"raiz JSON no es objeto ({type(data).__name__})"))
                continue
            n += 1
            for name, defn in (data.get("definitions") or {}).items():
                if not isinstance(defn, dict):
                    continue
                files.setdefault(name, []).append(rel)
                if view_type_of is not None:
                    family = family_of_dir(Path(rel).parent.name) if family_of_dir is not None and Path(rel).parent.name else None
                    vtype, basis = view_type_of(data, name)
                    if family is not None or vtype != "UNKNOWN":      # en pools planos solo interesa lo que tiene marcador
                        view_cands.setdefault(name, []).append(ViewTypeEvidence(name, rel, family, vtype, basis))
                ev = _read_remote_table(name, defn, rel)
                if ev is not None and name not in remote:
                    remote[name] = ev
            for name, body in (data.get("replicationflows") or {}).items():
                if isinstance(body, dict):
                    flows.append(_read_replication_flow(name, body, rel))
            for name, body in (data.get("dataflows") or {}).items():
                if isinstance(body, dict):
                    flows.append(_read_data_flow(name, body, rel))
        # Un nombre definido en varios archivos: se prefiere el de la familia `views`, luego el de ruta menor (determinista).
        view_types = {name: min(c, key=lambda e: (e.family != "views", e.source_file)) for name, c in view_cands.items()}
        return cls(remote, flows, {k: tuple(sorted(set(v))) for k, v in files.items()}, issues, n, acquired_flow_families, view_types)

    @classmethod
    def empty(cls) -> "SupplementaryEvidence":
        return cls({}, (), {}, (), 0)

    # ── consulta ───────────────────────────────────────────────────────────
    @property
    def remote_tables(self) -> Mapping[str, RemoteTableEvidence]:
        return self._remote

    @property
    def flows(self) -> tuple[FlowEvidence, ...]:
        return self._flows

    @property
    def issues(self) -> tuple[ReadIssue, ...]:
        return self._issues

    def view_type_evidence(self, entity: str) -> ViewTypeEvidence | None:
        """Tipo de vista de la entidad según el JSON del pool (None si el pool no lo define o no se recogió evidencia de tipo)."""
        return self._view_types.get(entity)

    def files_defining(self, entity: str) -> tuple[str, ...]:
        """Todos los archivos del pool que definen la entidad (L6); vacio si ninguno la define."""
        return self._files.get(entity, ())

    def remote_table(self, entity: str) -> RemoteTableEvidence | None:
        return self._remote.get(entity)

    def flows_touching(self, entity: str) -> tuple[tuple[FlowEvidence, str], ...]:
        """Flujos que referencian la entidad de Datasphere, con su rol ("source" | "target").

        * Data Flow: fuentes y destinos SON entidades de Datasphere (se matchean ambos).
        * Replication Flow: solo el DESTINO es una entidad de Datasphere; su `sourceObject` vive en el sistema de
          ORIGEN (p.ej. una CDS de S/4) y puede coincidir por nombre con una entidad local sin ser la misma cosa, asi
          que NO se matchea (queda como evidencia cruda en `source_entities`). Un destino `X_Delta` se resuelve a la
          tabla local `X` (sufijo de captura de cambios); un nombre exacto tambien cuenta."""
        out: list[tuple[FlowEvidence, str]] = []
        for fl in self._flows:
            if fl.kind == DATA_FLOW and entity in fl.source_entities:
                out.append((fl, "source"))
            if entity in fl.target_entities or f"{entity}{DELTA_SHADOW_SUFFIX}" in fl.target_entities:
                out.append((fl, "target"))
        return tuple(out)

    @property
    def flows_conclusive(self) -> bool:
        """¿Se adquirieron TODAS las familias de flows que este lector interpreta? Solo entonces "ningun flow referencia X" es una conclusion."""
        return self.acquired_flow_families >= FLOW_FAMILIES

    @property
    def data_flows_acquired(self) -> bool:
        return FLOW_FAMILY_DATA in self.acquired_flow_families

    @property
    def available(self) -> bool:
        """¿Se leyo al menos un archivo del pool? (si no, ningun hecho derivado de evidencia cruda es evaluable)."""
        return self.files_read > 0
