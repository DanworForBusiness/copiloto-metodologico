#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable


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
        data = self._run(
            ["objects", object_type, "list", "--space", space],
            expect_json=True,
        )
        return data if isinstance(data, list) else []

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
        self.nodes: dict[tuple[str, str], Node] = {}
        self.edges: set[Edge] = set()
        self.external_sources: set[str] = set()
        self.read_cache: dict[tuple[str, str], dict[str, Any] | None] = {}

    def build_inventory(self) -> None:
        for object_type in OBJECT_TYPES:
            try:
                rows = self.cli.list_objects(self.space, object_type)
            except DatasphereCliError as exc:
                print(f"WARN inventory {object_type}: {exc}", file=sys.stderr)
                self.inventory[object_type] = set()
                continue
            names = {
                row.get("technicalName")
                for row in rows
                if isinstance(row, dict) and row.get("technicalName")
            }
            self.inventory[object_type] = {n for n in names if isinstance(n, str)}
            print(f"Inventory {object_type}: {len(self.inventory[object_type])}")

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
            print(f"WARN read {object_type}/{technical_name}: {exc}", file=sys.stderr)
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
        queue: list[Node] = [Node("analytic-models", self.analytic_model)]
        processed: set[tuple[str, str]] = set()

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
            self.add_node(node.object_type, node.technical_name)

            data = self.safe_read(node.object_type, node.technical_name)
            if data is None:
                continue

            deps: set[str] = set()
            if node.object_type == "analytic-models":
                deps = self.parse_analytic_model_dependencies(data)
            elif node.object_type == "views":
                deps = self.parse_view_dependencies(data, node.technical_name)

            for dep_name in sorted(deps):
                dep_type = self.resolve_type(dep_name)
                if not dep_type:
                    # Keep unknown refs visible but do not invent their object type.
                    self.external_sources.add(dep_name)
                    continue
                self.add_edge(
                    node.object_type,
                    node.technical_name,
                    "DEPENDS_ON",
                    dep_type,
                    dep_name,
                )
                queue.append(Node(dep_type, dep_name))

    def discover_producers(self) -> None:
        # Replication Flows: proven structure, so inspect targets explicitly.
        local_tables = [
            n.technical_name for n in self.nodes.values() if n.object_type == "local-tables"
        ]
        for rf_name in sorted(self.inventory.get("replication-flows", set())):
            data = self.safe_read("replication-flows", rf_name)
            if data is None:
                continue
            info = self.parse_replication_flow(data, rf_name)
            matched_targets = sorted(set(local_tables) & info["targets"])
            if not matched_targets:
                continue

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
                self.add_edge(
                    "external-source",
                    src,
                    "REPLICATED_BY",
                    "replication-flows",
                    rf_name,
                )
            for connection in sorted(info["connections"]):
                self.add_edge(
                    "connection",
                    connection,
                    "USED_BY",
                    "replication-flows",
                    rf_name,
                )

        # For Data/Transformation Flows we have not yet validated the payload schema.
        # Use exact-reference fallback only, avoiding broad substring matches.
        known_names = {n.technical_name for n in self.nodes.values()}
        for object_type in ("data-flows", "transformation-flows"):
            for flow_name in sorted(self.inventory.get(object_type, set())):
                data = self.safe_read(object_type, flow_name)
                if data is None:
                    continue
                refs = [name for name in known_names if self._contains_exact_string(data, name)]
                if not refs:
                    continue
                self.add_node(object_type, flow_name)
                for ref in refs:
                    ref_type = self.resolve_type(ref) or "unknown"
                    self.add_edge(object_type, flow_name, "REFERENCES", ref_type, ref)

    def discover_task_chains(self) -> None:
        operational_names = {
            n.technical_name
            for n in self.nodes.values()
            if n.object_type in PRODUCER_TYPES
        }
        if not operational_names:
            return
        for tc_name in sorted(self.inventory.get("task-chains", set())):
            data = self.safe_read("task-chains", tc_name)
            if data is None:
                continue
            refs = [name for name in operational_names if self._contains_exact_string(data, name)]
            if not refs:
                continue
            self.add_node("task-chains", tc_name)
            for ref in refs:
                ref_type = self.resolve_type(ref) or "unknown"
                self.add_edge("task-chains", tc_name, "ORCHESTRATES", ref_type, ref)

    def write_outputs(self) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Persist all definitions for lineage nodes.
        for node in sorted(self.nodes.values(), key=lambda n: (n.object_type, n.technical_name)):
            if node.object_type in ("external-source", "connection"):
                continue
            data = self.safe_read(node.object_type, node.technical_name)
            if data is None:
                continue
            target_dir = self.output_dir / node.object_type
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / f"{node.technical_name}.json").write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )

        manifest = {
            "space": self.space,
            "analyticModel": self.analytic_model,
            "objects": [asdict(n) for n in sorted(self.nodes.values(), key=lambda n: (n.object_type, n.technical_name))],
            "edges": [asdict(e) for e in sorted(self.edges, key=lambda e: (e.source_type, e.source_name, e.relation, e.target_type, e.target_name))],
            "externalSources": sorted(self.external_sources),
        }
        manifest_path = self.output_dir / "lineage-manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        return manifest_path

    def run(self) -> Path:
        self.build_inventory()
        self.walk_model_graph()
        self.discover_producers()
        self.discover_task_chains()
        return self.write_outputs()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export SAP Datasphere lineage for an Analytic Model using the Datasphere CLI."
    )
    parser.add_argument("--host", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--client-secret", required=True)
    parser.add_argument("--token-url", required=True)
    parser.add_argument("--space", required=True)
    parser.add_argument("--analytic-model", required=True)
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Optional output directory. Defaults to ./Datasphere_LINEAGE_<SPACE>/<ANALYTIC_MODEL>",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if shutil.which("npx") is None:
        print("ERROR: npx not found in PATH", file=sys.stderr)
        return 2

    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else Path.cwd() / f"Datasphere_LINEAGE_{args.space}" / args.analytic_model
    )

    # Work area is ephemeral and isolated from the final export.
    with tempfile.TemporaryDirectory(prefix="dsp-lineage-") as tmp:
        work_dir = Path(tmp)
        cli = DatasphereCli(
            host=args.host,
            client_id=args.client_id,
            client_secret=args.client_secret,
            token_url=args.token_url,
            work_dir=work_dir,
        )
        try:
            print("Logging in to SAP Datasphere...")
            cli.login()
            print("Login OK")

            exporter = LineageExporter(
                cli=cli,
                space=args.space,
                analytic_model=args.analytic_model,
                output_dir=output_dir,
            )
            manifest_path = exporter.run()
        except DatasphereCliError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    print("\nEXPORT DE LINAJE FINALIZADO")
    print(f"Space: {args.space}")
    print(f"Analytic Model: {args.analytic_model}")
    print(f"Objetos incluidos: {len(exporter.nodes)}")
    print(f"Relaciones: {len(exporter.edges)}")
    print(f"Fuentes externas: {len(exporter.external_sources)}")
    print(f"Manifest: {manifest_path}")
    print(f"Destino: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
