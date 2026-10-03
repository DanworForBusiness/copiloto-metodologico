#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import time
import contextvars
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Iterable


# Streaming console contract: every _say() must be immediately consumable by
# the caller (e.g. the web app) even when stdout/stderr are piped.
try:
    sys.stdout.reconfigure(line_buffering=True, write_through=True)
except Exception:
    pass

try:
    sys.stderr.reconfigure(line_buffering=True, write_through=True)
except Exception:
    pass


# Salida de consola enrutable. Por defecto (CLI) escribe EXACTAMENTE como print(); un llamador programático (la UI web)
# fija un sumidero por contexto con `capture_output()` para recoger la traza sin redirigir el stdout GLOBAL del proceso.
_SINK: contextvars.ContextVar = contextvars.ContextVar("dsp_export_sink", default=None)


def _say(*args: Any, file: Any = None, **kwargs: Any) -> None:
    sink = _SINK.get()
    if sink is None:
        print(*args, file=file, **kwargs)
        return
    sink.append(" ".join(str(a) for a in args))


class _LineSink(list):
    """Lista de líneas que además avisa cada una al llegar (streaming en tiempo real hacia la UI web)."""
    def __init__(self, on_line: Any = None) -> None:
        super().__init__()
        self._on_line = on_line

    def append(self, line: str) -> None:
        super().append(line)
        if self._on_line is not None:
            self._on_line(line)


class capture_output:
    """Context manager: recoge en ``lines`` lo que el export "imprime" mientras dura el bloque (por contexto/hilo).
    ``on_line`` (opcional) se invoca con cada línea en el momento en que se emite, en el mismo orden y con el mismo texto."""
    def __init__(self, on_line: Any = None) -> None:
        self.lines = _LineSink(on_line)
        self._token = None

    def __enter__(self) -> "capture_output":
        self._token = _SINK.set(self.lines)
        return self

    def __exit__(self, *exc: Any) -> None:
        _SINK.reset(self._token)


OBJECT_TYPES = [
    "analytic-models",
    "views",
    "local-tables",
    "remote-tables",
    "replication-flows",
    "data-flows",
    "transformation-flows",
    "task-chains",
    "data-access-controls",
]

PRODUCER_TYPES = [
    "replication-flows",
    "data-flows",
    "transformation-flows",
]

ORCHESTRATOR_TYPES = ["task-chains"]


@dataclass(frozen=True)
class Node:
    object_type: str
    technical_name: str


@dataclass(frozen=True)
class Edge:
    source_type: str
    source_name: str
    relation: str
    target_type: str
    target_name: str


class DatasphereCliError(RuntimeError):
    pass


class DatasphereCli:
    def __init__(
        self,
        host: str,
        client_id: str,
        client_secret: str,
        token_url: str,
        work_dir: Path,
    ) -> None:
        self.host = host.rstrip("/") + "/"
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_url = token_url
        self.work_dir = work_dir
        # Script lives in tools/catalog/, so repo root is two levels above.
        self.project_root = Path(__file__).resolve().parents[2]
        self.env = os.environ.copy()

        # Isolate CLI state per execution so simultaneous tenants/jobs do not share secrets.
        self.home_dir = self.work_dir / "home"
        self.home_dir.mkdir(parents=True, exist_ok=True)
        self.env["HOME"] = str(self.home_dir)

    def _run(self, args: list[str], *, expect_json: bool = False) -> Any:
        # Use the Datasphere CLI already installed in this application's node_modules.
        # --no-install prevents npx from trying to download a package from npm.
        cmd = ["npx", "--no-install", "datasphere", *args]
        proc = subprocess.run(
            cmd,
            cwd=self.project_root,
            env=self.env,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.strip()
            stdout = proc.stdout.strip()
            msg = stderr or stdout or f"datasphere CLI failed with exit code {proc.returncode}"
            raise DatasphereCliError(msg)

        if expect_json:
            raw = proc.stdout.strip()
            if not raw:
                raise DatasphereCliError(f"Command returned empty output: {' '.join(cmd)}")
            try:
                return json.loads(raw)
            except json.JSONDecodeError as exc:
                raise DatasphereCliError(
                    f"Expected JSON but could not parse CLI output for: {' '.join(cmd)}"
                ) from exc
        return proc.stdout

    def oauth_preflight(self) -> None:
        """Validate OAuth client_credentials before any Datasphere CLI work."""
        _say("Preflight OAuth: validando client_credentials...")

        raw = f"{self.client_id}:{self.client_secret}".encode("utf-8")
        basic = base64.b64encode(raw).decode("ascii")
        request = urllib.request.Request(
            self.token_url,
            data=b"grant_type=client_credentials",
            method="POST",
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                status = int(getattr(response, "status", 0) or response.getcode() or 0)
                payload = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            payload = exc.read().decode("utf-8", errors="replace")
        except urllib.error.URLError as exc:
            raise DatasphereCliError(
                "PRECHECK_OAUTH_NETWORK_ERROR - No se pudo contactar el endpoint OAuth. "
                f"Revisá Token URL, DNS/conectividad y disponibilidad del tenant. Detalle: {exc.reason}"
            ) from exc
        except Exception as exc:
            raise DatasphereCliError(
                f"PRECHECK_OAUTH_ERROR - Error inesperado validando OAuth: {exc}"
            ) from exc

        if status == 200:
            try:
                token_data = json.loads(payload)
            except Exception:
                token_data = {}
            if not isinstance(token_data, dict) or not token_data.get("access_token"):
                raise DatasphereCliError(
                    "PRECHECK_OAUTH_INVALID_RESPONSE - OAuth devolvió HTTP 200 pero no incluyó access_token."
                )
            _say("Preflight OAuth: OK (HTTP 200)")
            return

        if status == 401:
            raise DatasphereCliError(
                "PRECHECK_OAUTH_401 - OAuth rechazó las credenciales (HTTP 401). "
                "Revisá Client ID, Client Secret, Token URL y que el OAuth/Technical User siga vigente."
            )

        raise DatasphereCliError(
            f"PRECHECK_OAUTH_HTTP_{status} - OAuth devolvió HTTP {status}. "
            "Revisá la configuración del OAuth Technical User y el endpoint de token."
        )

    def authorization_preflight(self, space: str) -> None:
        """Validate minimal Datasphere authorization after successful OAuth/login."""
        _say(f"Preflight autorización: validando acceso al Space {space}...")

        try:
            self._run(
                [
                    "objects",
                    "analytic-models",
                    "list",
                    "--space",
                    space,
                    "--top",
                    "1",
                ],
                expect_json=True,
            )
        except DatasphereCliError as exc:
            msg = str(exc)
            if "403" in msg:
                raise DatasphereCliError(
                    "PRECHECK_AUTH_403 - OAuth válido, pero el Technical User no tiene "
                    f"permisos suficientes sobre el Space '{space}'. Revisá los scoped roles "
                    "y autorizaciones asignadas al Technical User."
                ) from exc
            if "401" in msg:
                raise DatasphereCliError(
                    "PRECHECK_AUTH_401 - El token fue emitido, pero Datasphere rechazó la sesión "
                    "al consultar objetos. Revisá la configuración del OAuth Technical User."
                ) from exc
            raise DatasphereCliError(
                f"PRECHECK_AUTH_ERROR - No se pudo validar acceso al Space '{space}'. Detalle: {msg}"
            ) from exc

        _say(f"Preflight autorización: OK - acceso al Space {space}")

    def login(self) -> None:
        self._run(
            [
                "login",
                "--client-id",
                self.client_id,
                "--client-secret",
                self.client_secret,
                "--token-url",
                self.token_url,
                "--host",
                self.host,
                "--authorization-flow",
                "client_credentials",
                "--force",
            ]
        )

    def list_objects(self, space: str, object_type: str) -> list[dict[str, Any]]:
        """Return the complete inventory for an object type using CLI pagination."""
        page_size = 200
        skip = 0
        rows: list[dict[str, Any]] = []

        while True:
            data = self._run(
                [
                    "objects",
                    object_type,
                    "list",
                    "--space",
                    space,
                    "--top",
                    str(page_size),
                    "--skip",
                    str(skip),
                ],
                expect_json=True,
            )
            if not isinstance(data, list):
                break

            rows.extend(row for row in data if isinstance(row, dict))

            if len(data) < page_size:
                break
            skip += page_size

        return rows

    def read_object(self, space: str, object_type: str, technical_name: str) -> dict[str, Any]:
        data = self._run(
            [
                "objects",
                object_type,
                "read",
                "--space",
                space,
                "--technical-name",
                technical_name,
            ],
            expect_json=True,
        )
        if not isinstance(data, dict):
            raise DatasphereCliError(
                f"Unexpected non-object JSON for {object_type}/{technical_name}"
            )
        return data


class LineageExporter:
    def __init__(
        self,
        cli: DatasphereCli,
        space: str,
        analytic_model: str,
        output_dir: Path,
    ) -> None:
        self.cli = cli
        self.space = space
        self.analytic_model = analytic_model
        self.output_dir = output_dir

        self.inventory: dict[str, set[str]] = {}
        self.inventory_rows: dict[str, list[dict[str, Any]]] = {}
        self.nodes: dict[tuple[str, str], Node] = {}
        self.edges: set[Edge] = set()
        self.external_sources: set[str] = set()
        self.read_cache: dict[tuple[str, str], dict[str, Any] | None] = {}

    def build_inventory(self) -> None:
        for object_type in OBJECT_TYPES:
            try:
                rows = self.cli.list_objects(self.space, object_type)
            except DatasphereCliError as exc:
                _say(f"WARN inventory {object_type}: {exc}", file=sys.stderr)
                self.inventory[object_type] = set()
                continue
            self.inventory_rows[object_type] = rows
            names = {
                row.get("technicalName")
                for row in rows
                if isinstance(row, dict) and row.get("technicalName")
            }
            self.inventory[object_type] = {n for n in names if isinstance(n, str)}
            _say(f"Inventory {object_type}: {len(self.inventory[object_type])}")

    def resolve_type(self, technical_name: str) -> str | None:
        # Prefer model/view/table types over operational artefacts when names collide.
        priority = [
            "analytic-models",
            "views",
            "local-tables",
            "remote-tables",
            "data-access-controls",
            "replication-flows",
            "data-flows",
            "transformation-flows",
            "task-chains",
        ]
        for object_type in priority:
            if technical_name in self.inventory.get(object_type, set()):
                return object_type
        return None

    def safe_read(self, object_type: str, technical_name: str) -> dict[str, Any] | None:
        key = (object_type, technical_name)
        if key in self.read_cache:
            return self.read_cache[key]
        try:
            data = self.cli.read_object(self.space, object_type, technical_name)
        except DatasphereCliError as exc:
            _say(f"WARN read {object_type}/{technical_name}: {exc}", file=sys.stderr)
            self.read_cache[key] = None
            return None
        self.read_cache[key] = data
        return data

    def add_node(self, object_type: str, technical_name: str) -> None:
        self.nodes[(object_type, technical_name)] = Node(object_type, technical_name)

    def add_edge(
        self,
        source_type: str,
        source_name: str,
        relation: str,
        target_type: str,
        target_name: str,
    ) -> None:
        self.edges.add(Edge(source_type, source_name, relation, target_type, target_name))

    def parse_analytic_model_dependencies(self, data: dict[str, Any]) -> set[str]:
        deps: set[str] = set()
        bld = data.get("businessLayerDefinitions", {})
        if not isinstance(bld, dict):
            return deps

        for layer in bld.values():
            if not isinstance(layer, dict):
                continue
            source_model = layer.get("sourceModel", {})
            if not isinstance(source_model, dict):
                continue
            for bucket_name in ("factSources", "dimensionSources"):
                bucket = source_model.get(bucket_name, {})
                if not isinstance(bucket, dict):
                    continue
                for source in bucket.values():
                    if not isinstance(source, dict):
                        continue
                    entity = source.get("dataEntity", {})
                    if isinstance(entity, dict):
                        key = entity.get("key")
                        if isinstance(key, str) and key:
                            deps.add(key)
        return deps

    @staticmethod
    def _extract_sql_dependencies(sql: str) -> set[str]:
        # Handles quoted object names in FROM/JOIN clauses, including nested SELECTs.
        return set(re.findall(r'(?:FROM|JOIN)\s+"([^"]+)"', sql, flags=re.IGNORECASE))

    def parse_view_dependencies(self, data: dict[str, Any], technical_name: str) -> set[str]:
        definitions = data.get("definitions", {})
        if not isinstance(definitions, dict):
            return set()
        definition = definitions.get(technical_name)
        if not isinstance(definition, dict):
            # Fallback for payloads where key differs unexpectedly.
            definition = next((v for v in definitions.values() if isinstance(v, dict)), None)
        if not isinstance(definition, dict):
            return set()
        sql = definition.get("@DataWarehouse.sqlEditor.query")
        if not isinstance(sql, str):
            return set()
        return self._extract_sql_dependencies(sql)

    def parse_replication_flow(self, data: dict[str, Any], technical_name: str) -> dict[str, Any]:
        root = data.get("replicationflows", {})
        if not isinstance(root, dict):
            return {"targets": set(), "connections": set(), "sources": set()}
        flow = root.get(technical_name)
        if not isinstance(flow, dict):
            flow = next((v for v in root.values() if isinstance(v, dict)), None)
        if not isinstance(flow, dict):
            return {"targets": set(), "connections": set(), "sources": set()}

        targets = set(flow.get("targets", {}).keys()) if isinstance(flow.get("targets"), dict) else set()
        connections = set(flow.get("connections", {}).keys()) if isinstance(flow.get("connections"), dict) else set()

        sources: set[str] = set()
        contents = flow.get("contents", {})
        if isinstance(contents, dict):
            tasks = contents.get("replicationTasks", [])
            if isinstance(tasks, list):
                for task in tasks:
                    if not isinstance(task, dict):
                        continue
                    src_obj = task.get("sourceObject", {})
                    if isinstance(src_obj, dict):
                        name = src_obj.get("name")
                        if isinstance(name, str) and name:
                            sources.add(name)

        return {"targets": targets, "connections": connections, "sources": sources}

    @staticmethod
    def _contains_exact_string(data: Any, value: str) -> bool:
        if isinstance(data, str):
            return data == value
        if isinstance(data, list):
            return any(LineageExporter._contains_exact_string(v, value) for v in data)
        if isinstance(data, dict):
            return any(
                k == value or LineageExporter._contains_exact_string(v, value)
                for k, v in data.items()
            )
        return False

    def walk_model_graph(self) -> None:
        _say("\n==========================================")
        _say("RESOLVIENDO LINAJE DE MODELOS")
        _say("==========================================")
        started = time.perf_counter()

        queue: list[Node] = [Node("analytic-models", self.analytic_model)]
        processed: set[tuple[str, str]] = set()
        i = 0

        if self.analytic_model not in self.inventory.get("analytic-models", set()):
            raise DatasphereCliError(
                f"Analytic Model '{self.analytic_model}' not found in space '{self.space}'"
            )

        while queue:
            node = queue.pop(0)
            key = (node.object_type, node.technical_name)
            if key in processed:
                continue

            processed.add(key)
            i += 1
            _say(f"\n[{i}] {node.object_type} / {node.technical_name}")
            self.add_node(node.object_type, node.technical_name)

            data = self.safe_read(node.object_type, node.technical_name)
            if data is None:
                _say("ERROR - no se pudo leer")
                continue

            deps: set[str] = set()
            if node.object_type == "analytic-models":
                deps = self.parse_analytic_model_dependencies(data)
            elif node.object_type == "views":
                deps = self.parse_view_dependencies(data, node.technical_name)

            if not deps:
                _say("OK - sin dependencias descendentes")
                continue

            _say(f"Dependencias: {len(deps)}")
            for dep_name in sorted(deps):
                dep_type = self.resolve_type(dep_name)
                if not dep_type:
                    _say(f"  EXTERNAL/UNKNOWN: {dep_name}")
                    self.external_sources.add(dep_name)
                    continue

                _say(f"  -> {dep_type}: {dep_name}")
                self.add_edge(
                    node.object_type,
                    node.technical_name,
                    "DEPENDS_ON",
                    dep_type,
                    dep_name,
                )
                queue.append(Node(dep_type, dep_name))

        _say("\n------------------------------------------")
        _say(f"LINAJE DE MODELOS terminado en {time.perf_counter() - started:.1f}s")
        _say(f"Nodos actuales: {len(self.nodes)}")
        _say("------------------------------------------")
    def discover_producers(self) -> None:
        _say("\n==========================================")
        _say("BUSCANDO PRODUCTORES")
        _say("==========================================")
        started = time.perf_counter()

        local_tables = [
            n.technical_name for n in self.nodes.values() if n.object_type == "local-tables"
        ]

        rf_names = sorted(self.inventory.get("replication-flows", set()))
        rf_ok = 0
        for i, rf_name in enumerate(rf_names, start=1):
            _say(f"\n[RF {i}/{len(rf_names)}] {rf_name}")
            data = self.safe_read("replication-flows", rf_name)
            if data is None:
                _say("ERROR")
                continue

            info = self.parse_replication_flow(data, rf_name)
            matched_targets = sorted(set(local_tables) & info["targets"])
            if not matched_targets:
                _say("SKIP - sin target del linaje")
                continue

            rf_ok += 1
            _say(f"OK - target(s): {', '.join(matched_targets)}")
            self.add_node("replication-flows", rf_name)

            for target in matched_targets:
                self.add_edge(
                    "replication-flows",
                    rf_name,
                    "PRODUCES",
                    "local-tables",
                    target,
                )

            for src in sorted(info["sources"]):
                self.external_sources.add(src)
                _say(f"  SOURCE: {src}")
                self.add_edge(
                    "external-source",
                    src,
                    "REPLICATED_BY",
                    "replication-flows",
                    rf_name,
                )

            for connection in sorted(info["connections"]):
                _say(f"  CONNECTION: {connection}")
                self.add_edge(
                    "connection",
                    connection,
                    "USED_BY",
                    "replication-flows",
                    rf_name,
                )

        _say(f"\nReplication Flows relacionados: {rf_ok}/{len(rf_names)}")

        known_names = {n.technical_name for n in self.nodes.values()}
        for object_type in ("data-flows", "transformation-flows"):
            flow_names = sorted(self.inventory.get(object_type, set()))
            matches = 0
            for i, flow_name in enumerate(flow_names, start=1):
                _say(f"\n[{object_type} {i}/{len(flow_names)}] {flow_name}")
                data = self.safe_read(object_type, flow_name)
                if data is None:
                    _say("ERROR")
                    continue

                refs = [name for name in known_names if self._contains_exact_string(data, name)]
                if not refs:
                    _say("SKIP - sin referencia al linaje")
                    continue

                matches += 1
                _say(f"OK - referencias: {len(refs)}")
                self.add_node(object_type, flow_name)
                for ref in refs:
                    ref_type = self.resolve_type(ref) or "unknown"
                    _say(f"  -> {ref_type}: {ref}")
                    self.add_edge(object_type, flow_name, "REFERENCES", ref_type, ref)

            _say(f"\n{object_type} relacionados: {matches}/{len(flow_names)}")

        _say("\n------------------------------------------")
        _say(f"PRODUCTORES terminados en {time.perf_counter() - started:.1f}s")
        _say("------------------------------------------")
    def discover_task_chains(self) -> None:
        _say("\n==========================================")
        _say("BUSCANDO TASK CHAINS")
        _say("==========================================")
        started = time.perf_counter()

        operational_names = {
            n.technical_name
            for n in self.nodes.values()
            if n.object_type in PRODUCER_TYPES
        }
        if not operational_names:
            _say("SKIP - no hay productores operativos en el linaje")
            return

        tc_names = sorted(self.inventory.get("task-chains", set()))
        matches = 0

        for i, tc_name in enumerate(tc_names, start=1):
            _say(f"\n[TC {i}/{len(tc_names)}] {tc_name}")
            data = self.safe_read("task-chains", tc_name)
            if data is None:
                _say("ERROR")
                continue

            refs = [name for name in operational_names if self._contains_exact_string(data, name)]
            if not refs:
                _say("SKIP - sin referencia al linaje")
                continue

            matches += 1
            _say(f"OK - referencias: {len(refs)}")
            self.add_node("task-chains", tc_name)
            for ref in refs:
                ref_type = self.resolve_type(ref) or "unknown"
                _say(f"  -> {ref_type}: {ref}")
                self.add_edge("task-chains", tc_name, "ORCHESTRATES", ref_type, ref)

        _say("\n------------------------------------------")
        _say(f"Task Chains relacionadas: {matches}/{len(tc_names)}")
        _say(f"TASK CHAINS terminadas en {time.perf_counter() - started:.1f}s")
        _say("------------------------------------------")
    @staticmethod
    def _safe_filename(technical_name: str) -> str:
        return re.sub(r'[\\/:*?"<>|]', "_", technical_name)

    def write_outputs(self) -> Path:
        """
        Persist the selected lineage into the canonical catalog CSN pool:

            catalog_local/.csn_cache/<CLIENT>/<object-type>/<technicalName>.json

        No inventory JSON and no diagnostic manifest are written inside .csn_cache,
        because generate_catalog.py treats every .json under that tree as CSN input.
        """
        client_cache_dir = self.output_dir
        client_cache_dir.mkdir(parents=True, exist_ok=True)

        selected_by_type: dict[str, set[str]] = {
            object_type: set() for object_type in OBJECT_TYPES
        }
        for node in self.nodes.values():
            if node.object_type in selected_by_type:
                selected_by_type[node.object_type].add(node.technical_name)

        _say("\n==========================================")
        _say("EXPORTANDO CSN AL POOL DEL CATALOGO")
        _say("==========================================")
        _say(f"Destino cliente: {client_cache_dir}")

        exported = 0
        errors = 0

        for object_type in OBJECT_TYPES:
            names = sorted(selected_by_type.get(object_type, set()))
            target_dir = client_cache_dir / object_type
            target_dir.mkdir(parents=True, exist_ok=True)

            _say("")
            _say("==========================================")
            _say(f"Procesando: {object_type}")
            _say(f"Destino:    {target_dir}")
            _say("==========================================")
            _say("")
            _say(f"Objetos encontrados: {len(names)}")

            if not names:
                _say("SKIP - Sin objetos")
                continue

            ok = 0
            failed = 0

            for i, technical_name in enumerate(names, start=1):
                output_file = target_dir / f"{self._safe_filename(technical_name)}.json"

                _say("")
                _say(f"[{i}/{len(names)}] {technical_name}")

                data = self.safe_read(object_type, technical_name)
                if data is None:
                    _say("ERROR")
                    failed += 1
                    errors += 1
                    continue

                output_file.write_text(
                    json.dumps(data, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                _say("OK")
                ok += 1
                exported += 1

            _say("")
            _say("------------------------------------------")
            _say(f"{object_type} terminado")
            _say(f"OK:      {ok}")
            _say(f"Errores: {failed}")
            _say(f"Total:   {len(names)}")
            _say("------------------------------------------")

        _say("")
        _say("==========================================")
        _say("EXPORT CSN FINALIZADO")
        _say(f"Archivos exportados: {exported}")
        _say(f"Errores:             {errors}")
        _say(f"Pool cliente:        {client_cache_dir}")
        _say("==========================================")

        # Return the client cache directory; diagnostics are written later outside
        # .csn_cache so they never become catalog source JSON.
        return client_cache_dir

    def run(self) -> Path:
        self.build_inventory()
        self.walk_model_graph()
        self.discover_producers()
        self.discover_task_chains()
        return self.write_outputs()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export SAP Datasphere lineage for an Analytic Model into the Danwor catalog CSN pool."
    )
    parser.add_argument("--host", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--client-secret", required=True)
    parser.add_argument("--token-url", required=True)
    parser.add_argument("--space", required=True)
    parser.add_argument("--analytic-model", required=True)
    parser.add_argument(
        "--client",
        required=True,
        help="Cliente de catálogo (ej. Pampa). Se usa como carpeta bajo .csn_cache.",
    )
    parser.add_argument(
        "--catalog-root",
        default=None,
        help=(
            "Raíz catalog_local. Default: DANWOR_CATALOG_DIR o <repo>/catalog_local."
        ),
    )
    parser.add_argument(
        "--csn-cache-dir",
        default=None,
        help=(
            "Pool CSN persistido. Default: DANWOR_CATALOG_CSN_CACHE "
            "o <catalog-root>/.csn_cache."
        ),
    )
    parser.add_argument(
        "--no-generate-catalog",
        action="store_true",
        help="Sólo exporta CSN; no ejecuta generate_catalog.generate_all().",
    )
    parser.add_argument(
        "--mirror-up",
        action="store_true",
        help=(
            "Al finalizar, invoca core.remote_store.mirror_up() para persistencia "
            "inmediata. Sin este flag se usa el sync loop normal de la app."
        ),
    )
    return parser.parse_args()


def _manifest_object_keys(manifest: dict) -> set[tuple[str, str]]:
    """Return {(object_type, technical_name)} from one lineage manifest."""
    out: set[tuple[str, str]] = set()
    for obj in manifest.get("objects") or []:
        if not isinstance(obj, dict):
            continue
        object_type = str(obj.get("object_type") or "").strip()
        technical_name = str(obj.get("technical_name") or "").strip()
        if object_type and technical_name:
            out.add((object_type, technical_name))
    return out


def _load_json_file(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _other_manifest_ownership(
    client_runs_root: Path,
    current_manifest_path: Path,
) -> set[tuple[str, str]]:
    """
    Aggregate objects owned by every OTHER lineage manifest for this client.
    This prevents deletion of shared artifacts used by another Analytic Model.
    """
    owned: set[tuple[str, str]] = set()
    if not client_runs_root.exists():
        return owned

    current_resolved = current_manifest_path.resolve()
    for path in client_runs_root.rglob("lineage-manifest.json"):
        try:
            if path.resolve() == current_resolved:
                continue
        except OSError:
            pass
        owned |= _manifest_object_keys(_load_json_file(path))
    return owned


def _cleanup_stale_owned_objects(
    *,
    client_cache_dir: Path,
    previous_manifest: dict,
    current_objects: set[tuple[str, str]],
    other_owned_objects: set[tuple[str, str]],
) -> tuple[int, int]:
    """
    Remove objects that belonged to the previous run of THIS model but no longer
    belong to its current lineage, only when no other model manifest owns them.

    Returns (deleted, retained_shared).
    """
    previous_objects = _manifest_object_keys(previous_manifest)
    stale = previous_objects - current_objects
    deleted = 0
    retained_shared = 0

    if not stale:
        _say("\nLimpieza incremental: sin objetos obsoletos.")
        return deleted, retained_shared

    _say("\n==========================================")
    _say("LIMPIEZA INCREMENTAL DEL POOL")
    _say("==========================================")
    _say(f"Objetos previos:    {len(previous_objects)}")
    _say(f"Objetos actuales:   {len(current_objects)}")
    _say(f"Candidatos obsoletos: {len(stale)}")

    for object_type, technical_name in sorted(stale):
        if (object_type, technical_name) in other_owned_objects:
            retained_shared += 1
            _say(f"KEEP shared - {object_type}/{technical_name}")
            continue

        path = client_cache_dir / object_type / f"{LineageExporter._safe_filename(technical_name)}.json"
        if not path.exists():
            _say(f"SKIP missing - {object_type}/{technical_name}")
            continue

        try:
            path.unlink()
            deleted += 1
            _say(f"DELETE - {object_type}/{technical_name}")
        except OSError as exc:
            _say(f"WARN no se pudo borrar {path}: {exc}")

    # Remove only empty technical-type directories. Never remove client_cache_dir.
    for child in sorted(client_cache_dir.iterdir()) if client_cache_dir.exists() else []:
        if child.is_dir():
            try:
                child.rmdir()
            except OSError:
                pass

    _say("------------------------------------------")
    _say(f"Eliminados:          {deleted}")
    _say(f"Retenidos compartidos: {retained_shared}")
    _say("------------------------------------------")
    return deleted, retained_shared


def _canonical_run_dir(catalog_root: Path, client_dir_name: str, space: str, analytic_model: str) -> Path:
    safe_space = re.sub(r'[\\/:*?"<>|]', "_", space.strip())
    safe_model = re.sub(r'[\\/:*?"<>|]', "_", analytic_model.strip())
    return catalog_root / ".lineage_runs" / client_dir_name / safe_space / safe_model


def _legacy_run_manifest(catalog_root: Path, client_dir_name: str, analytic_model: str) -> Path:
    """Compatibility with the first version, which did not include SPACE in the path."""
    safe_model = re.sub(r'[\\/:*?"<>|]', "_", analytic_model.strip())
    return catalog_root / ".lineage_runs" / client_dir_name / safe_model / "lineage-manifest.json"


@dataclass
class ExportRequest:
    """Parámetros de una exportación. Es el contrato único que consumen los DOS adaptadores (CLI y UI web)."""
    host: str
    client_id: str
    client_secret: str = field(repr=False)
    token_url: str = ""
    space: str = ""
    analytic_model: str = ""
    client: str = ""
    catalog_root: Path | None = None
    csn_cache_dir: Path | None = None
    generate_catalog: bool = True
    mirror_up: bool = False


@dataclass
class ExportResult:
    ok: bool
    error_code: str | None = None
    message: str = ""
    client: str = ""
    space: str = ""
    analytic_model: str = ""
    duration_s: float = 0.0
    objects: int = 0
    edges: int = 0
    external_sources: int = 0
    exported_files: int = 0
    export_errors: int = 0
    files_by_type: dict[str, int] = field(default_factory=dict)
    deleted_stale: int = 0
    retained_shared: int = 0
    pool_dir: Path | None = None
    manifest_path: Path | None = None
    catalog_generated: bool = False
    storage_enabled: bool = False
    mirrored_files: int | None = None       # None = no se pidió subir; N = archivos sincronizados a Spaces por mirror_up()
    exit_code: int = 0


_PRECHECK_CODES = (
    ("PRECHECK_OAUTH_NETWORK_ERROR", "OAUTH_ENDPOINT_UNREACHABLE"),
    ("PRECHECK_OAUTH_ERROR", "OAUTH_ENDPOINT_UNREACHABLE"),
    ("PRECHECK_OAUTH_401", "AUTHENTICATION_FAILED"),
    ("PRECHECK_OAUTH_INVALID_RESPONSE", "AUTHENTICATION_FAILED"),
    ("PRECHECK_OAUTH_HTTP_", "AUTHENTICATION_FAILED"),
    ("PRECHECK_AUTH_403", "INSUFFICIENT_DATASPHERE_PRIVILEGES"),
    ("PRECHECK_AUTH_401", "AUTHENTICATION_FAILED"),
    ("PRECHECK_AUTH_ERROR", "SPACE_ACCESS_FAILED"),
)


def classify_error(message: str) -> str:
    """Código estable a partir del prefijo `PRECHECK_*` de los errores del CLI (los demás son fallas de extracción)."""
    for prefix, code in _PRECHECK_CODES:
        if str(message).startswith(prefix):
            return code
    return "EXTRACTION_FAILED"


def regenerate_catalog_from_pool(csn_cache_root: Path, catalog_root: Path) -> str:
    """Regenera la proyección del catálogo desde el pool CSN (``generate_all``). Paso ÚNICO compartido: lo usa ``run_export`` cuando
    ``generate_catalog=True`` y la acción explícita "Actualizar catálogo" de la UI web; no existe otra copia de esta llamada."""
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from tools.catalog.generate_catalog import generate_all

    return str(generate_all(csn_cache_root, catalog_root))


def run_export(req: ExportRequest) -> ExportResult:
    """Exporta el linaje de un Analytic Model al pool CSN del catálogo. ÚNICA implementación: el CLI (`main`) y la UI web
    (`webapp/services/datasphere_assessment_service.py`) son adaptadores sobre esta función. No lee argv ni llama a sys.exit."""
    started_at = time.perf_counter()

    def finish(result: ExportResult) -> ExportResult:
        result.duration_s = round(time.perf_counter() - started_at, 1)
        return result

    base = ExportResult(ok=False, client=req.client, space=req.space, analytic_model=req.analytic_model)

    if shutil.which("npx") is None:
        _say("ERROR: npx not found in PATH", file=sys.stderr)
        return finish(ExportResult(**{**base.__dict__, "error_code": "DATASPHERE_CLI_UNAVAILABLE", "message": "npx not found in PATH", "exit_code": 2}))

    project_root = Path(__file__).resolve().parents[2]

    catalog_root = Path(
        req.catalog_root
        or os.getenv("DANWOR_CATALOG_DIR")
        or (project_root / "catalog_local")
    ).resolve()

    csn_cache_root = Path(
        req.csn_cache_dir
        or os.getenv("DANWOR_CATALOG_CSN_CACHE")
        or (catalog_root / ".csn_cache")
    ).resolve()

    # Client is intentionally a runtime parameter. Keep path-safe while preserving
    # the visible business name as much as possible.
    client_dir_name = re.sub(r'[\\/:*?"<>|]', "_", req.client.strip()).replace(" ", "_")
    if not client_dir_name:
        _say("ERROR: --client cannot be empty", file=sys.stderr)
        return finish(ExportResult(**{**base.__dict__, "error_code": "INVALID_PARAMETER", "message": "client cannot be empty", "exit_code": 2}))

    client_cache_dir = csn_cache_root / client_dir_name
    diagnostics_dir = _canonical_run_dir(catalog_root, client_dir_name, req.space, req.analytic_model)

    _say("Catalog integration:")
    _say(f"  Client:       {req.client}")
    _say(f"  Catalog root: {catalog_root}")
    _say(f"  CSN cache:    {csn_cache_root}")
    _say(f"  Client pool:  {client_cache_dir}")

    manifest_path = diagnostics_dir / "lineage-manifest.json"
    previous_manifest = _load_json_file(manifest_path)
    legacy_manifest_path = _legacy_run_manifest(catalog_root, client_dir_name, req.analytic_model)
    using_legacy_manifest = False

    # Migration from the first implementation: reuse its previous ownership only
    # when its embedded identity matches this exact space/model.
    if not previous_manifest and legacy_manifest_path.exists():
        candidate = _load_json_file(legacy_manifest_path)
        if (
            str(candidate.get("space") or "") == req.space
            and str(candidate.get("analyticModel") or "") == req.analytic_model
        ):
            previous_manifest = candidate
            using_legacy_manifest = True
            _say(f"  Previous ownership: legacy manifest {legacy_manifest_path}")

    # Work area is ephemeral and isolated from the final export.
    with tempfile.TemporaryDirectory(prefix="dsp-lineage-") as tmp:
        work_dir = Path(tmp)
        cli = DatasphereCli(
            host=req.host,
            client_id=req.client_id,
            client_secret=req.client_secret,
            token_url=req.token_url,
            work_dir=work_dir,
        )
        try:
            _say("\n==========================================")
            _say("PRECHECK DE CONEXION Y AUTORIZACION")
            _say("==========================================")
            cli.oauth_preflight()

            _say("\nLogging in to SAP Datasphere...")
            cli.login()
            _say("Login OK")

            cli.authorization_preflight(req.space)

            _say("\nPrechecks OK. Iniciando export de linaje...")

            exporter = LineageExporter(
                cli=cli,
                space=req.space,
                analytic_model=req.analytic_model,
                output_dir=client_cache_dir,
            )
            exported_pool = exporter.run()
        except DatasphereCliError as exc:
            _say(f"ERROR: {exc}")
            return finish(ExportResult(**{**base.__dict__, "error_code": classify_error(str(exc)), "message": str(exc), "exit_code": 1}))

    # Ownership cleanup happens AFTER the current export but BEFORE generate_catalog:
    # current files have already been refreshed, while stale files can still be
    # removed before the catalog projection is rebuilt.
    current_objects = {
        (node.object_type, node.technical_name)
        for node in exporter.nodes.values()
        if node.object_type in OBJECT_TYPES
    }
    client_runs_root = catalog_root / ".lineage_runs" / client_dir_name
    other_owned_objects = _other_manifest_ownership(client_runs_root, manifest_path)

    deleted_stale, retained_shared = _cleanup_stale_owned_objects(
        client_cache_dir=client_cache_dir,
        previous_manifest=previous_manifest,
        current_objects=current_objects,
        other_owned_objects=other_owned_objects,
    )

    # Diagnostic + ownership manifest OUTSIDE .csn_cache. This is persisted with
    # catalog_local but is never scanned as CSN input by generate_catalog.py.
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "client": req.client,
        "space": req.space,
        "analyticModel": req.analytic_model,
        "objects": [
            asdict(n)
            for n in sorted(
                exporter.nodes.values(),
                key=lambda n: (n.object_type, n.technical_name),
            )
        ],
        "edges": [
            asdict(e)
            for e in sorted(
                exporter.edges,
                key=lambda e: (
                    e.source_type,
                    e.source_name,
                    e.relation,
                    e.target_type,
                    e.target_name,
                ),
            )
        ],
        "externalSources": sorted(exporter.external_sources),
        "cleanup": {
            "deletedStaleObjects": deleted_stale,
            "retainedSharedObjects": retained_shared,
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Once the canonical space-aware manifest exists, remove only the old manifest
    # file used for migration. Leave its directory untouched unless it becomes empty.
    if using_legacy_manifest and legacy_manifest_path != manifest_path:
        try:
            legacy_manifest_path.unlink()
            try:
                legacy_manifest_path.parent.rmdir()
            except OSError:
                pass
            _say(f"Manifest legacy migrado: {legacy_manifest_path}")
        except OSError as exc:
            _say(f"WARN no se pudo retirar manifest legacy: {exc}")

    files_by_type = {
        t: sum(1 for _ in (client_cache_dir / t).glob("*.json")) if (client_cache_dir / t).is_dir() else 0 for t in OBJECT_TYPES
    }
    result = ExportResult(**{
        **base.__dict__, "ok": True, "objects": len(exporter.nodes), "edges": len(exporter.edges),
        "external_sources": len(exporter.external_sources), "exported_files": sum(files_by_type.values()),
        "files_by_type": {k: v for k, v in files_by_type.items() if v}, "deleted_stale": deleted_stale,
        "retained_shared": retained_shared, "pool_dir": exported_pool, "manifest_path": manifest_path,
    })

    if req.generate_catalog:
        _say("\n==========================================")
        _say("REGENERANDO CATALOGO EXISTENTE")
        _say("==========================================")
        try:
            summary = regenerate_catalog_from_pool(csn_cache_root, catalog_root)
            _say(summary)
            result.catalog_generated = True
        except Exception as exc:
            _say(f"ERROR generating catalog: {exc}", file=sys.stderr)
            result.ok, result.error_code, result.message, result.exit_code = False, "CATALOG_GENERATION_FAILED", f"{type(exc).__name__}", 1
            return finish(result)

    if req.mirror_up:
        _say("\n==========================================")
        _say("PERSISTIENDO EN DIGITALOCEAN SPACES")
        _say("==========================================")
        try:
            if str(project_root) not in sys.path:
                sys.path.insert(0, str(project_root))
            from core import remote_store

            result.storage_enabled = bool(remote_store.enabled())
            if result.storage_enabled:
                result.mirrored_files = int(remote_store.mirror_up() or 0)
                _say("mirror_up OK")
            else:
                _say("WARN: remote_store is disabled; local catalog was generated but not mirrored.")
        except Exception as exc:
            _say(f"ERROR mirror_up: {exc}", file=sys.stderr)
            result.ok, result.error_code, result.message, result.exit_code = False, "STORAGE_UPLOAD_FAILED", f"{type(exc).__name__}", 1
            return finish(result)
    else:
        _say(
            "\nPersistencia Spaces: se usará el sync loop normal de remote_store "
            "(use --mirror-up para forzarla ahora)."
        )

    _say("\nEXPORT DE LINAJE FINALIZADO")
    _say(f"Cliente: {req.client}")
    _say(f"Space: {req.space}")
    _say(f"Analytic Model: {req.analytic_model}")
    _say(f"Objetos incluidos: {len(exporter.nodes)}")
    _say(f"Relaciones: {len(exporter.edges)}")
    _say(f"Fuentes externas: {len(exporter.external_sources)}")
    _say(f"Obsoletos eliminados: {deleted_stale}")
    _say(f"Compartidos retenidos: {retained_shared}")
    _say(f"Pool CSN: {exported_pool}")
    _say(f"Manifest diagnóstico: {manifest_path}")
    _say(f"Catálogo: {catalog_root}")
    _say("STATUS: SUCCESS")
    return finish(result)


def main() -> int:
    """Adaptador CLI: argv → ExportRequest → run_export (misma lógica que usa la UI web)."""
    args = parse_args()
    result = run_export(ExportRequest(
        host=args.host, client_id=args.client_id, client_secret=args.client_secret, token_url=args.token_url,
        space=args.space, analytic_model=args.analytic_model, client=args.client,
        catalog_root=Path(args.catalog_root) if args.catalog_root else None,
        csn_cache_dir=Path(args.csn_cache_dir) if args.csn_cache_dir else None,
        generate_catalog=not args.no_generate_catalog, mirror_up=bool(args.mirror_up),
    ))
    return 0 if result.ok else (result.exit_code or 1)


if __name__ == "__main__":
    raise SystemExit(main())
