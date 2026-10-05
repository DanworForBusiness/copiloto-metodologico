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

from .derivation import DerivedColumn, extract_derived_columns, select_branches

READERS_VERSION = "supplementary-readers/v2"

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
    load_types: tuple[str, ...] = ()                  # solo Replication Flows: ``loadType`` de cada tarea (INITIAL | REPLICATE | ...), tal cual


LOCAL_DECLARED_DATASET = "DECLARED_DATASET"
LOCAL_TABLE_FUNCTION = "TABLE_FUNCTION"
_DATASOURCE_ENTITY = "@DataWarehouse.dataSource.entity"
_DATASOURCE_SCHEMA = "@DataWarehouse.dataSource.schema"
_TABLE_FUNCTION_SCRIPT = "@DataWarehouse.tableFunction.script"


@dataclass(frozen=True)
class LocalDeclaration:
    """Origen que el PROPIO artefacto local declara en su definicion (sin flows): un dataset externo o una table function. Es evidencia declarada:
    no se infiere de nombres ni de la carpeta."""
    entity: str
    source_file: str
    kind: str                                         # DECLARED_DATASET | TABLE_FUNCTION
    data_source_schema: str | None
    data_source_entity: str | None


@dataclass(frozen=True)
class DerivationEvidence:
    """Columnas derivadas (AST compilado) de una definicion con ``query``. ``has_query`` distingue "sin derivadas" de "sin evidencia de query"."""
    entity: str
    source_file: str
    has_query: bool
    branches: int
    columns: tuple[DerivedColumn, ...]


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


def _read_local_declaration(name: str, defn: dict, rel: str) -> LocalDeclaration | None:
    if _REMOTE_CONNECTION in defn or _REMOTE_ENTITY in defn:
        return None
    if _DATASOURCE_ENTITY in defn:
        return LocalDeclaration(name, rel, LOCAL_DECLARED_DATASET, _str_or_none(defn.get(_DATASOURCE_SCHEMA)), _str_or_none(defn.get(_DATASOURCE_ENTITY)))
    if _TABLE_FUNCTION_SCRIPT in defn:
        return LocalDeclaration(name, rel, LOCAL_TABLE_FUNCTION, None, None)
    return None


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
    load_types: list[str] = []
    for task in (contents.get("replicationTasks") or []):
        if isinstance(task, dict):
            if _str_or_none(task.get("loadType")):
                load_types.append(task["loadType"])
            so, to = task.get("sourceObject"), task.get("targetObject")
            if isinstance(so, dict) and _str_or_none(so.get("name")):
                s_names.add(so["name"])
            if isinstance(to, dict) and _str_or_none(to.get("name")):
                t_names.add(to["name"])
    return FlowEvidence(REPLICATION_FLOW, name, rel, tuple(sorted(s_names)), tuple(sorted(t_names)), systems, tuple(sorted(set(load_types))))


def _read_data_flow(name: str, body: dict, rel: str) -> FlowEvidence:
    srcs = body.get("sources") if isinstance(body.get("sources"), dict) else {}
    tgts = body.get("targets") if isinstance(body.get("targets"), dict) else {}
    return FlowEvidence(DATA_FLOW, name, rel, tuple(sorted(srcs)), tuple(sorted(tgts)), ())


# Familias del extractor que alimentan la evidencia de flows del plano PROCESSING (identificadores canonicos del extractor).
# Task Chains y Transformation Flows NO se leen aqui (ver tests/test_modeling_assessment_evidence_scope.py: auditoria de uso).
FLOW_FAMILY_REPLICATION = "replication-flows"
FLOW_FAMILY_DATA = "data-flows"
FLOW_FAMILIES = frozenset({FLOW_FAMILY_REPLICATION, FLOW_FAMILY_DATA})
FLOW_FAMILY_TRANSFORMATION = "transformation-flows"
# Familias de PRODUCTORES de objetos locales. Para afirmar la AUSENCIA de un productor (procedencia NOT_OBSERVABLE concluyente) hay que haber
# ADQUIRIDO las tres; solo RF y DF se INTERPRETAN como procedencia (los Transformation Flows no: no hay evidencia forense de una relacion
# entrada/salida confiable). Los Task Chains orquestan: NUNCA son procedencia.
PRODUCER_FAMILIES = frozenset({FLOW_FAMILY_REPLICATION, FLOW_FAMILY_DATA, FLOW_FAMILY_TRANSFORMATION})
SCOPE_NOT_DECLARED = "SCOPE_NOT_DECLARED"
SCOPE_FAMILY_NOT_ACQUIRED = "PRODUCER_FAMILY_NOT_ACQUIRED"
SCOPE_UNINTERPRETED_PRODUCERS = "UNINTERPRETED_PRODUCER_EVIDENCE"
SCOPE_UNREADABLE_FILES = "UNREADABLE_POOL_FILES"
SCOPE_NO_FILES = "NO_POOL_FILES"


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
        local_declarations: Mapping[str, LocalDeclaration] | None = None,
        derivations: Mapping[str, DerivationEvidence] | None = None,
        effective_features: Iterable[str] | None = None,
        transformation_flow_files: int = 0,
    ) -> None:
        self._local_declarations = dict(sorted((local_declarations or {}).items()))
        self._derivations = dict(sorted((derivations or {}).items()))
        # Alcance EXPLICITO declarado por la extraccion (``effective_features``). None = no declarado: la completitud NUNCA se infiere de carpetas ni nombres.
        self.effective_features = None if effective_features is None else frozenset(effective_features)
        self.transformation_flow_files = transformation_flow_files
        self._view_types = dict(sorted((view_types or {}).items()))
        self.acquired_flow_families = FLOW_FAMILIES if acquired_flow_families is None else frozenset(acquired_flow_families) & FLOW_FAMILIES
        self._remote = dict(sorted(remote_tables.items()))
        self._flows = tuple(sorted(flows, key=lambda f: (f.kind, f.name, f.source_file)))
        self._files = {k: tuple(v) for k, v in sorted(entity_files.items())}
        self._issues = tuple(sorted(set(issues), key=lambda i: (i.source_file, i.reason)))
        self.files_read = files_read

    @classmethod
    def from_files(cls, json_files: Iterable[str | Path], root: str | Path | None = None,
                   acquired_flow_families: Iterable[str] | None = None, *, view_type_of=None, family_of_dir=None,
                   effective_features: Iterable[str] | None = None) -> "SupplementaryEvidence":
        """``view_type_of(data, nombre) -> (tipo, base)`` y ``family_of_dir(carpeta) -> familia|None`` los inyecta quien lee (el shadow, vía
        ``legacy_bridge``): este módulo permanece puro. Sin ellos no se recoge evidencia de tipo de vista (comportamiento previo)."""
        rootp = Path(root) if root is not None else None
        remote: dict[str, RemoteTableEvidence] = {}
        flows: list[FlowEvidence] = []
        view_cands: dict[str, list[ViewTypeEvidence]] = {}
        files: dict[str, list[str]] = {}
        issues: list[ReadIssue] = []
        local_decl: dict[str, LocalDeclaration] = {}
        derivs: dict[str, DerivationEvidence] = {}
        tf_files = 0
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
                ld = _read_local_declaration(name, defn, rel)
                if ld is not None and name not in local_decl:
                    local_decl[name] = ld
                branches = select_branches(defn)
                if branches and (name not in derivs or not derivs[name].has_query):
                    derivs[name] = DerivationEvidence(name, rel, True, len(branches), extract_derived_columns(name, defn))
            for name, body in (data.get("replicationflows") or {}).items():
                if isinstance(body, dict):
                    flows.append(_read_replication_flow(name, body, rel))
            for name, body in (data.get("dataflows") or {}).items():
                if isinstance(body, dict):
                    flows.append(_read_data_flow(name, body, rel))
            if isinstance(data.get("transformationflows"), dict) and data["transformationflows"]:
                tf_files += 1                                   # solo se CUENTA: no se interpreta (no es procedencia aprobada) pero impide afirmar ausencia
        # Un nombre definido en varios archivos: se prefiere el de la familia `views`, luego el de ruta menor (determinista).
        view_types = {name: min(c, key=lambda e: (e.family != "views", e.source_file)) for name, c in view_cands.items()}
        return cls(remote, flows, {k: tuple(sorted(set(v))) for k, v in files.items()}, issues, n, acquired_flow_families, view_types,
                   local_decl, derivs, effective_features, tf_files)

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

    def local_declaration(self, entity: str) -> LocalDeclaration | None:
        """Origen declarado por el propio artefacto local (dataset externo / table function), o None."""
        return self._local_declarations.get(entity)

    def derivation_evidence(self, entity: str) -> DerivationEvidence | None:
        """Derivaciones (AST compilado) de la definicion, o None si el pool no trae ``query`` para la entidad."""
        return self._derivations.get(entity)

    @property
    def provenance_scope(self) -> dict:
        """Completitud del alcance para AFIRMAR ausencia de productores. ``conclusive`` solo si el alcance fue DECLARADO por la extraccion, incluye
        las tres familias de productores, no hay archivos de Transformation Flow sin interpretar y no hubo archivos ilegibles. Nunca se infiere."""
        declared = self.effective_features is not None
        families = sorted(self.effective_features & PRODUCER_FAMILIES) if declared else []
        if not declared:
            limitation = SCOPE_NOT_DECLARED
        elif PRODUCER_FAMILIES - self.effective_features:
            limitation = SCOPE_FAMILY_NOT_ACQUIRED
        elif self.transformation_flow_files:
            limitation = SCOPE_UNINTERPRETED_PRODUCERS
        elif self._issues:
            limitation = SCOPE_UNREADABLE_FILES
        elif not self.available:
            limitation = SCOPE_NO_FILES
        else:
            limitation = None
        return {"declared": declared, "producer_families_acquired": families, "conclusive": limitation is None, "limitation": limitation}

    def files_defining(self, entity: str) -> tuple[str, ...]:
        """Todos los archivos del pool que definen la entidad (L6); vacio si ninguno la define."""
        return self._files.get(entity, ())

    def remote_table(self, entity: str) -> RemoteTableEvidence | None:
        return self._remote.get(entity)

    def target_flows(self, entity: str, known_entities=None) -> tuple[tuple[tuple[FlowEvidence, str], ...], str | None]:
        """``(((flujo, match), ...), ambiguedad)``: flujos que producen la entidad local ``entity`` como DESTINO. Orden de coincidencia (G1, contrato fijo):

          1. coincidencia EXACTA del destino (RF o DF): si existe, gana y el sufijo ni se intenta (``match = "EXACT"``);
          2. solo si no hay exacta: la UNICA normalizacion conocida, el destino ``X_Delta`` de un Replication Flow (tabla de captura de cambios) ->
             ``X`` (``match = "DELTA_SHADOW_SUFFIX"``). Sin matching difuso ni sufijos generales;
          3. si en el pool existe una entidad local REAL llamada ``X_Delta``, ese destino es suyo: NO se mapea a ``X``;
          4. el candidato normalizado debe ser UNICO: con varios flujos candidatos la procedencia NO se establece y se informa la ambiguedad
             (``MULTIPLE_DELTA_CANDIDATES``), nunca se adivina.

        ``known_entities``: nombres de las entidades del pool (para el punto 3); None = no se conoce (no se aplica esa guarda)."""
        exact = tuple((fl, "EXACT") for fl in self._flows if entity in fl.target_entities)
        if exact:
            return exact, None
        shadow = f"{entity}{DELTA_SHADOW_SUFFIX}"
        if known_entities is not None and shadow in known_entities:
            return (), None
        cands = tuple((fl, "DELTA_SHADOW_SUFFIX") for fl in self._flows if fl.kind == REPLICATION_FLOW and shadow in fl.target_entities)
        if len(cands) > 1:
            return (), "MULTIPLE_DELTA_CANDIDATES"
        return cands, None

    def flows_touching(self, entity: str, known_entities=None) -> tuple[tuple[FlowEvidence, str], ...]:
        """Flujos que referencian la entidad de Datasphere, con su rol ("source" | "target").

        * Data Flow: fuentes y destinos SON entidades de Datasphere.
        * Replication Flow: solo el DESTINO es una entidad de Datasphere; su `sourceObject` vive en el sistema de ORIGEN (p.ej. una CDS de S/4) y puede
          coincidir por nombre con una entidad local sin ser la misma cosa, asi que NO se matchea (queda como evidencia cruda en `source_entities`).
        * Los DESTINOS siguen el orden de ``target_flows`` (exacto primero; ``X_Delta`` solo como normalizacion conocida, unica y sin entidad homonima)."""
        out: list[tuple[FlowEvidence, str]] = [(fl, "source") for fl in self._flows if fl.kind == DATA_FLOW and entity in fl.source_entities]
        out += [(fl, "target") for fl, _m in self.target_flows(entity, known_entities)[0]]
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
