#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextvars
import contextlib
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    sys.stdout.reconfigure(line_buffering=True, write_through=True)
except Exception:
    pass
try:
    sys.stderr.reconfigure(line_buffering=True, write_through=True)
except Exception:
    pass


# Routable console output. CLI writes exactly as print(); programmatic callers
# can capture/stream each line without redirecting global stdout.
_SINK: contextvars.ContextVar = contextvars.ContextVar("dsp_export_sink", default=None)


def _say(*args: Any, file: Any = None, **kwargs: Any) -> None:
    sink = _SINK.get()
    if sink is None:
        print(*args, file=file, **kwargs)
        return
    sink.append(" ".join(str(a) for a in args))


class _LineSink(list):
    def __init__(self, on_line: Any = None) -> None:
        super().__init__()
        self._on_line = on_line

    def append(self, line: str) -> None:
        super().append(line)
        if self._on_line is not None:
            self._on_line(line)


class capture_output:
    """Capture export console lines per context, optionally streaming each one."""

    def __init__(self, on_line: Any = None) -> None:
        self.lines = _LineSink(on_line)
        self._token = None

    def __enter__(self) -> "capture_output":
        self._token = _SINK.set(self.lines)
        return self

    def __exit__(self, *exc: Any) -> None:
        _SINK.reset(self._token)

EXTRACTOR_VERSION = "2.2"

# Regla CANÓNICA, a nivel de nombre, de "¿el target de un flow produce la hoja local?". Es un ESPEJO DELIBERADO de
# ``tools.catalog.modeling_assessment.readers.canonical_target_match`` (la regla del resolver de procedencia): el paquete
# ``modeling_assessment`` está aislado del legacy por invariantes de frontera (R-I2/R-I3), así que el extractor NO puede
# importarlo. La equivalencia NO se garantiza por import compartido sino por un test de contrato
# (``tests/test_dsp_cli_export_lineage_producer_matching.py::test_g_resolver_and_acquisition_matcher_agree``), que falla si
# ambas reglas divergen. Mantener este cuerpo idéntico al del resolver.
_DELTA_SHADOW_SUFFIX = "_Delta"


def _canonical_target_match(target: str, leaf: str, known_entities=None) -> str | None:
    """``"EXACT"`` si ``target == leaf``; ``"DELTA_SHADOW_SUFFIX"`` si ``target`` es la sombra de captura de cambios
    ``<leaf>_Delta`` y ``<leaf>_Delta`` NO es a su vez una entidad real del universo adquirido (guarda ``known_entities``);
    ``None`` en cualquier otro caso. Sin matching difuso ni sufijos generales."""
    if target == leaf:
        return "EXACT"
    if target.endswith(_DELTA_SHADOW_SUFFIX) and target[: -len(_DELTA_SHADOW_SUFFIX)] == leaf:
        if known_entities is not None and target in known_entities:
            return None
        return "DELTA_SHADOW_SUFFIX"
    return None


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
ROOT_OBJECT_TYPES = ["analytic-models", "views", "local-tables", "remote-tables"]
PRODUCER_TYPES = ["replication-flows", "data-flows", "transformation-flows"]
FEATURES = [
    "analytic-models",
    "views",
    "tables",
    "replication-flows",
    "data-flows",
    "transformation-flows",
    "task-chains",
]
ALL_FEATURES = frozenset(FEATURES)
ROOT_TYPE_TO_OBJECT_TYPE = {
    "analytic-model": "analytic-models",
    "view": "views",
    "local-table": "local-tables",
    "remote-table": "remote-tables",
}
SUPPORTED_ROOT_TYPES = frozenset(ROOT_TYPE_TO_OBJECT_TYPE)
SPACE_LIST_SENTINEL = "--list-spaces"
DIAGNOSTIC_PHASES = (
    "PRECHECK",
    "ROOT / INVENTORY",
    "LINEAGE",
    "PRODUCER DISCOVERY",
    "TASK CHAINS",
    "WRITE / EXPORT",
)
OBJECT_TYPE_TO_ROOT_TYPE = {v: k for k, v in ROOT_TYPE_TO_OBJECT_TYPE.items()}
FEATURE_TO_OBJECT_TYPES = {
    "analytic-models": ("analytic-models",),
    "views": ("views",),
    "tables": ("local-tables", "remote-tables"),
    "replication-flows": ("replication-flows",),
    "data-flows": ("data-flows",),
    "transformation-flows": ("transformation-flows",),
    "task-chains": ("task-chains",),
}
FEATURE_LABELS = {
    "analytic-models": "Modelo Analítico",
    "views": "Vistas",
    "tables": "Tablas",
    "replication-flows": "Replication Flows",
    "data-flows": "Data Flows",
    "transformation-flows": "Transformation Flows",
    "task-chains": "Task Chains",
}
ROOT_LABELS = {
    "analytic-model": "Analytic Model",
    "view": "View",
    "local-table": "Local Table",
    "remote-table": "Remote Table",
}


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


class ExtractionIncompleteError(DatasphereCliError):
    """La extracción tuvo fallos de lectura o de listado (o violó un invariante de completitud): jamás se publica. ``failures``: ``[{kind, object_type, technical_name, code}]``."""

    def __init__(self, failures: list[dict]):
        super().__init__(f"EXTRACTION_INCOMPLETE - {len(failures)} fallo(s) de lectura/listado/consistencia.")
        self.failures = list(failures)


class RootTypeConstraintError(DatasphereCliError):
    pass


class ExtractionDiagnostics:
    """Opt-in, in-memory forensic counters. Never persists credentials/tokens."""

    def __init__(self, enabled: bool = False) -> None:
        self.enabled = bool(enabled)
        self.current_phase = "PRECHECK"
        self.total_cli_process_launches = 0
        self.total_network_bearing_commands = 0
        self.family: dict[str, dict[str, float | int]] = {}
        self.phase_stats: dict[str, dict[str, float | int]] = {}
        self._logical_seen: set[tuple[str, str]] = set()
        self._physical_seen: set[tuple[str, str]] = set()

    def _bucket(self, mapping: dict[str, dict[str, float | int]], key: str) -> dict[str, float | int]:
        return mapping.setdefault(key, {})

    @staticmethod
    def _inc(bucket: dict[str, float | int], key: str, amount: float | int = 1) -> None:
        bucket[key] = bucket.get(key, 0) + amount

    @contextlib.contextmanager
    def phase(self, name: str):
        if not self.enabled:
            yield
            return
        previous = self.current_phase
        self.current_phase = name
        started = time.perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            self._inc(self._bucket(self.phase_stats, name), "elapsed_ms", elapsed_ms)
            self.current_phase = previous

    def record_inventory_request(self, family: str, *, cache_hit: bool = False) -> None:
        if not self.enabled:
            return
        bucket = self._bucket(self.family, family)
        self._inc(bucket, "inventory_requests")
        if cache_hit:
            self._inc(bucket, "inventory_cache_hits")

    def record_logical_read(self, family: str, technical_name: str, *, cache_hit: bool, reused: bool) -> None:
        if not self.enabled:
            return
        bucket = self._bucket(self.family, family)
        self._inc(bucket, "logical_reads")
        key = (family, technical_name)
        if key in self._logical_seen:
            self._inc(bucket, "duplicate_logical_reads")
        else:
            self._logical_seen.add(key)
        if cache_hit:
            self._inc(bucket, "cache_hits")
        if reused:
            self._inc(bucket, "reused_parsed_objects")

    def record_cli_call(
        self,
        *,
        family: str | None,
        operation: str,
        technical_name: str | None,
        elapsed_ms: float,
        parse_ms: float = 0.0,
        network_bearing: bool = True,
    ) -> None:
        if not self.enabled:
            return
        self.total_cli_process_launches += 1
        if network_bearing:
            self.total_network_bearing_commands += 1
        phase = self._bucket(self.phase_stats, self.current_phase)
        self._inc(phase, "cli_process_launches")
        if network_bearing:
            self._inc(phase, "network_bearing_commands")
        self._inc(phase, "cli_elapsed_ms", elapsed_ms)
        self._inc(phase, "local_parse_ms", parse_ms)
        if family:
            bucket = self._bucket(self.family, family)
            self._inc(bucket, "cli_process_launches")
            self._inc(bucket, "cli_elapsed_ms", elapsed_ms)
            self._inc(bucket, "local_parse_ms", parse_ms)
            if operation == "list":
                self._inc(bucket, "inventory_cli_calls")
            elif operation == "read":
                self._inc(bucket, "physical_reads")
                key = (family, technical_name or "")
                if key in self._physical_seen:
                    self._inc(bucket, "duplicate_physical_reads")
                else:
                    self._physical_seen.add(key)

    def record_local_work(self, elapsed_ms: float) -> None:
        if not self.enabled:
            return
        self._inc(self._bucket(self.phase_stats, self.current_phase), "local_work_ms", elapsed_ms)

    def snapshot(self) -> dict[str, Any]:
        def clean(mapping: dict[str, dict[str, float | int]]) -> dict[str, dict[str, float | int]]:
            out: dict[str, dict[str, float | int]] = {}
            for key, values in mapping.items():
                out[key] = {
                    k: round(v, 3) if isinstance(v, float) else v
                    for k, v in values.items()
                }
            return out

        return {
            "enabled": self.enabled,
            "total_cli_process_launches": self.total_cli_process_launches,
            "total_network_bearing_commands": self.total_network_bearing_commands,
            "by_family": clean(self.family),
            "by_phase": clean(self.phase_stats),
        }


@dataclass(frozen=True)
class DatasphereCliLauncher:
    command: tuple[str, ...]
    mode: str
    fallback_command: tuple[str, ...] | None = None


def resolve_datasphere_cli_launcher(project_root: Path) -> DatasphereCliLauncher:
    """Resolve the local Datasphere CLI once, preferring direct Node execution."""
    project_root = Path(project_root).resolve()
    node = shutil.which("node")
    local_entrypoint = (
        project_root / "node_modules" / "@sap" / "datasphere-cli" / "terminal.js"
    )
    npx_fallback = (
        ("npx", "--no-install", "datasphere")
        if shutil.which("npx") is not None
        else None
    )

    if node and local_entrypoint.is_file():
        return DatasphereCliLauncher(
            command=(node, str(local_entrypoint)),
            mode="direct",
            fallback_command=npx_fallback,
        )

    if npx_fallback is not None:
        return DatasphereCliLauncher(
            command=npx_fallback,
            mode="npx",
            fallback_command=None,
        )

    raise DatasphereCliError(
        "DATASPHERE_CLI_UNAVAILABLE - No se encontró el CLI local de Datasphere "
        "ni npx disponible como fallback."
    )


class DatasphereCli:
    def __init__(
        self, host: str, client_id: str, client_secret: str, token_url: str, work_dir: Path,
        diagnostics: ExtractionDiagnostics | None = None,
        launcher: DatasphereCliLauncher | None = None,
    ) -> None:
        self.host = host.rstrip("/") + "/"
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_url = token_url
        self.work_dir = work_dir
        self.diagnostics = diagnostics or ExtractionDiagnostics(False)
        self.project_root = Path(__file__).resolve().parents[2]
        self.env = os.environ.copy()
        self.home_dir = self.work_dir / "home"
        self.home_dir.mkdir(parents=True, exist_ok=True)
        self.env["HOME"] = str(self.home_dir)
        self.launcher = launcher or resolve_datasphere_cli_launcher(self.project_root)

    def _invoke_cli(self, args: list[str]) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        cmd = [*self.launcher.command, *args]
        try:
            proc = subprocess.run(
                cmd,
                cwd=self.project_root,
                env=self.env,
                capture_output=True,
                text=True,
            )
            return proc, cmd
        except OSError as exc:
            fallback = self.launcher.fallback_command
            if self.launcher.mode != "direct" or fallback is None:
                raise DatasphereCliError(
                    f"DATASPHERE_CLI_UNAVAILABLE - No se pudo iniciar el launcher de Datasphere: {exc}"
                ) from exc

            fallback_cmd = [*fallback, *args]
            try:
                proc = subprocess.run(
                    fallback_cmd,
                    cwd=self.project_root,
                    env=self.env,
                    capture_output=True,
                    text=True,
                )
                return proc, fallback_cmd
            except OSError as fallback_exc:
                raise DatasphereCliError(
                    "DATASPHERE_CLI_UNAVAILABLE - No se pudo iniciar el CLI local "
                    f"ni el fallback npx: {fallback_exc}"
                ) from fallback_exc

    def _run(self, args: list[str], *, expect_json: bool = False) -> Any:
        family: str | None = None
        operation = args[0] if args else "unknown"
        technical_name: str | None = None
        if len(args) >= 3 and args[0] == "objects":
            family = args[1]
            operation = args[2]
            if operation == "read" and "--technical-name" in args:
                idx = args.index("--technical-name")
                if idx + 1 < len(args):
                    technical_name = args[idx + 1]

        started = time.perf_counter()
        proc, cmd = self._invoke_cli(args)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        parse_ms = 0.0
        parsed: Any = None
        if proc.returncode == 0 and expect_json:
            raw = proc.stdout.strip()
            if not raw:
                self.diagnostics.record_cli_call(
                    family=family, operation=operation, technical_name=technical_name,
                    elapsed_ms=elapsed_ms, network_bearing=True,
                )
                raise DatasphereCliError(f"Command returned empty output: {' '.join(cmd)}")
            parse_started = time.perf_counter()
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                parse_ms = (time.perf_counter() - parse_started) * 1000.0
                self.diagnostics.record_cli_call(
                    family=family, operation=operation, technical_name=technical_name,
                    elapsed_ms=elapsed_ms, parse_ms=parse_ms, network_bearing=True,
                )
                raise DatasphereCliError(
                    f"Expected JSON but could not parse CLI output for: {' '.join(cmd)}"
                ) from exc
            parse_ms = (time.perf_counter() - parse_started) * 1000.0

        self.diagnostics.record_cli_call(
            family=family, operation=operation, technical_name=technical_name,
            elapsed_ms=elapsed_ms, parse_ms=parse_ms, network_bearing=True,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.strip()
            stdout = proc.stdout.strip()
            raise DatasphereCliError(stderr or stdout or f"datasphere CLI failed with exit code {proc.returncode}")
        if expect_json:
            return parsed
        return proc.stdout

    def oauth_preflight(self) -> None:
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
            raise DatasphereCliError(f"PRECHECK_OAUTH_ERROR - Error inesperado validando OAuth: {exc}") from exc

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
        _say(f"Preflight autorización: validando acceso al Space {space}...")
        try:
            self._run(
                ["objects", "analytic-models", "list", "--space", space, "--top", "1"],
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
                "--client-id", self.client_id,
                "--client-secret", self.client_secret,
                "--token-url", self.token_url,
                "--host", self.host,
                "--authorization-flow", "client_credentials",
                "--force",
            ]
        )

    def list_spaces(self) -> list[str]:
        data = self._run(
            ["spaces", "list"],
            expect_json=True,
        )
        if not isinstance(data, list):
            raise DatasphereCliError(
                "Unexpected non-list JSON for spaces list"
            )
        return [
            space.strip()
            for space in data
            if isinstance(space, str) and space.strip()
        ]

    def list_objects(self, space: str, object_type: str) -> list[dict[str, Any]]:
        page_size = 200
        skip = 0
        rows: list[dict[str, Any]] = []
        self.last_list_incomplete = False
        while True:
            data = self._run(
                [
                    "objects", object_type, "list",
                    "--space", space,
                    "--top", str(page_size),
                    "--skip", str(skip),
                ],
                expect_json=True,
            )
            if not isinstance(data, list):
                self.last_list_incomplete = True            # respuesta inesperada: el listado puede estar truncado (lo registra el exportador)
                break
            rows.extend(row for row in data if isinstance(row, dict))
            if len(data) < page_size:
                break
            skip += page_size
        return rows

    def read_object(self, space: str, object_type: str, technical_name: str) -> dict[str, Any]:
        data = self._run(
            ["objects", object_type, "read", "--space", space, "--technical-name", technical_name],
            expect_json=True,
        )
        if not isinstance(data, dict):
            raise DatasphereCliError(f"Unexpected non-object JSON for {object_type}/{technical_name}")
        return data


def _parse_features(raw: str | Iterable[str] | None) -> set[str]:
    if raw is None:
        return set(ALL_FEATURES)
    if isinstance(raw, str):
        values = [part.strip() for part in raw.split(",") if part.strip()]
    else:
        values = [str(part).strip() for part in raw if str(part).strip()]
    unknown = sorted(set(values) - ALL_FEATURES)
    if unknown:
        raise DatasphereCliError(f"INVALID_FEATURE_SCOPE - Features no reconocidas: {', '.join(unknown)}")
    return set(values)


def _parse_allowed_root_types(raw: str | Iterable[str] | None) -> frozenset[str] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        values = [part.strip() for part in raw.split(",") if part.strip()]
    else:
        values = [str(part).strip() for part in raw if str(part).strip()]
    normalized = frozenset(values)
    unknown = sorted(normalized - SUPPORTED_ROOT_TYPES)
    if unknown:
        raise DatasphereCliError(
            "INVALID_ROOT_TYPE_CONSTRAINT - Tipos de raíz no reconocidos: " + ", ".join(unknown)
        )
    if not normalized:
        raise DatasphereCliError(
            "INVALID_ROOT_TYPE_CONSTRAINT - allowed_root_types no puede estar vacío; use None para no restringir."
        )
    return normalized


def _resolve_effective_features(requested: set[str], root_type: str) -> set[str]:
    effective = set(requested)
    flow_features = set(PRODUCER_TYPES)
    if "task-chains" in effective and not (effective & flow_features):
        raise DatasphereCliError(
            "INVALID_FEATURE_SCOPE - Task Chains requiere al menos una familia de flows habilitada "
            "(replication-flows, data-flows o transformation-flows)."
        )
    if effective & flow_features:
        effective.add("tables")
    # To reach tables from an AM/View root we must traverse Views even if the user
    # only requested Tables or a producer family.
    if "tables" in effective and root_type in {"analytic-model", "view"}:
        effective.add("views")
    return effective


def _feature_for_object_type(object_type: str) -> str | None:
    if object_type == "analytic-models":
        return "analytic-models"
    if object_type == "views":
        return "views"
    if object_type in {"local-tables", "remote-tables"}:
        return "tables"
    if object_type in PRODUCER_TYPES or object_type == "task-chains":
        return object_type
    return None


class LineageExporter:
    """Reusable Datasphere upstream lineage acquisition engine.

    Backward compatible constructor:
        LineageExporter(cli, space, analytic_model, output_dir)

    New canonical form:
        LineageExporter(cli, space, output_dir=..., root_object=..., requested_features=...)
    """

    def __init__(
        self,
        cli: DatasphereCli,
        space: str,
        analytic_model: str | None = None,
        output_dir: Path | None = None,
        *,
        root_object: str | None = None,
        requested_features: str | Iterable[str] | None = None,
        root_type: str | None = None,
        mode: str = "assessment",
        allowed_root_types: str | Iterable[str] | None = None,
        diagnostics: ExtractionDiagnostics | None = None,
    ) -> None:
        if analytic_model and root_object:
            raise DatasphereCliError("ROOT_ARGUMENT_CONFLICT - Use analytic_model o root_object, no ambos.")
        normalized_root = (root_object or analytic_model or "").strip()
        if not normalized_root:
            raise DatasphereCliError("ROOT_OBJECT_REQUIRED - Debe informarse root_object/analytic_model.")
        if output_dir is None:
            raise DatasphereCliError("OUTPUT_DIR_REQUIRED - Debe informarse output_dir.")
        if root_type is not None and root_type not in ROOT_TYPE_TO_OBJECT_TYPE:
            raise DatasphereCliError(f"INVALID_ROOT_TYPE - Tipo de raíz no soportado: {root_type}")
        if mode not in {"assessment", "publish"}:
            raise DatasphereCliError(f"INVALID_MODE - Modo no soportado: {mode}")

        self.cli = cli
        self.space = space
        self.root_object = normalized_root
        self.analytic_model = normalized_root if analytic_model else None  # compatibility attribute
        self.output_dir = Path(output_dir)
        self.root_type_override = root_type
        self.root_type: str | None = root_type
        self.mode = mode
        self.requested_features = _parse_features(requested_features)
        self.allowed_root_types = _parse_allowed_root_types(allowed_root_types)
        self.effective_features: set[str] = set()
        self.diagnostics = diagnostics or getattr(cli, "diagnostics", ExtractionDiagnostics(False))

        self.inventory: dict[str, set[str]] = {}
        self.inventory_rows: dict[str, list[dict[str, Any]]] = {}
        self.nodes: dict[tuple[str, str], Node] = {}
        self.edges: set[Edge] = set()
        self.external_sources: set[str] = set()
        self.read_cache: dict[tuple[str, str], dict[str, Any] | None] = {}
        self.failures: list[dict[str, Any]] = []     # fallos de lectura/listado (códigos estables, sin texto crudo del CLI)

    def ensure_inventory(self, object_type: str) -> set[str]:
        if object_type in self.inventory:
            self.diagnostics.record_inventory_request(object_type, cache_hit=True)
            return self.inventory[object_type]
        self.diagnostics.record_inventory_request(object_type, cache_hit=False)
        try:
            rows = self.cli.list_objects(self.space, object_type)
            if getattr(self.cli, "last_list_incomplete", False):
                self.failures.append({"kind": "inventory", "object_type": object_type, "technical_name": None, "code": "INVENTORY_INCOMPLETE"})
        except DatasphereCliError as exc:
            _say(f"WARN inventory {object_type}: {exc}", file=sys.stderr)
            self.failures.append({"kind": "inventory", "object_type": object_type, "technical_name": None, "code": "INVENTORY_FAILED"})
            rows = []
        self.inventory_rows[object_type] = rows
        names = {
            row.get("technicalName")
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("technicalName"), str)
        }
        self.inventory[object_type] = {str(n) for n in names if n}
        _say(f"Inventory {object_type}: {len(self.inventory[object_type])}")
        return self.inventory[object_type]

    def build_inventory(self) -> None:
        """Compatibility method: acquire only inventory needed by effective scope.

        Root detection always needs the four eligible root families. Expensive flow/
        orchestrator inventories are acquired only when enabled.
        """
        for object_type in ROOT_OBJECT_TYPES:
            self.ensure_inventory(object_type)
        for feature in sorted(self.effective_features):
            for object_type in FEATURE_TO_OBJECT_TYPES.get(feature, ()): 
                self.ensure_inventory(object_type)

    def detect_root_type(self) -> str:
        # Root detection intentionally queries only eligible root families.
        matches: list[str] = []
        for object_type in ROOT_OBJECT_TYPES:
            if self.root_object in self.ensure_inventory(object_type):
                matches.append(object_type)

        if self.root_type_override:
            expected = ROOT_TYPE_TO_OBJECT_TYPE[self.root_type_override]
            if expected not in matches:
                raise DatasphereCliError(
                    f"ROOT_OBJECT_NOT_FOUND - '{self.root_object}' no existe como {self.root_type_override} "
                    f"en el Space '{self.space}'."
                )
            self.root_type = self.root_type_override
            return self.root_type

        if not matches:
            raise DatasphereCliError(
                f"ROOT_OBJECT_NOT_FOUND - No se encontró '{self.root_object}' en el Space '{self.space}' "
                "como Analytic Model, View, Local Table o Remote Table."
            )
        if len(matches) > 1:
            semantic = [OBJECT_TYPE_TO_ROOT_TYPE[m] for m in matches]
            raise DatasphereCliError(
                "AMBIGUOUS_ROOT_OBJECT_TYPE - El technical name "
                f"'{self.root_object}' existe en múltiples tipos: {', '.join(semantic)}."
            )
        self.root_type = OBJECT_TYPE_TO_ROOT_TYPE[matches[0]]
        return self.root_type

    def normalize_scope(self) -> None:
        if not self.root_type:
            raise DatasphereCliError("ROOT_TYPE_NOT_RESOLVED - No se resolvió el tipo de raíz.")
        self.effective_features = _resolve_effective_features(self.requested_features, self.root_type)

    def validate_root_constraint(self) -> None:
        if self.allowed_root_types is None:
            return
        if not self.root_type:
            raise DatasphereCliError("ROOT_TYPE_NOT_RESOLVED - No se resolvió el tipo de raíz.")
        if self.root_type not in self.allowed_root_types:
            allowed = ", ".join(sorted(self.allowed_root_types))
            raise RootTypeConstraintError(
                "ROOT_TYPE_CONSTRAINT_FAILED - "
                f"Detected root type '{self.root_type}' is not allowed by this caller constraint "
                f"(allowed: {allowed})."
            )

    def print_scope(self) -> None:
        assert self.root_type
        _say("\n==========================================")
        _say("CONTEXTO DE EXTRACCION")
        _say("==========================================")
        _say(f"Objeto raíz: {self.root_object}")
        _say(f"Tipo detectado: {ROOT_LABELS[self.root_type]}")
        _say(f"Modo: {self.mode.upper()}")
        _say("\nAlcance efectivo:")
        for feature in FEATURES:
            _say(f"  {FEATURE_LABELS[feature]:24} {'SI' if feature in self.effective_features else 'NO'}")
        if self.requested_features != self.effective_features:
            added = sorted(self.effective_features - self.requested_features)
            if added:
                _say("Dependencias agregadas por backend: " + ", ".join(added))

    def resolve_type(self, technical_name: str) -> str | None:
        # Dependency resolution is intentionally limited to model families, not
        # operational artefacts, so a FROM/JOIN cannot silently become a Flow.
        candidates: list[str] = []
        for object_type in ROOT_OBJECT_TYPES:
            if technical_name in self.ensure_inventory(object_type):
                candidates.append(object_type)
        if not candidates:
            return None
        if len(candidates) > 1:
            _say(
                f"WARN dependencia ambigua '{technical_name}' encontrada en: {', '.join(candidates)}; "
                "se omite para evitar precedencia silenciosa.",
                file=sys.stderr,
            )
            return None
        return candidates[0]

    def safe_read(self, object_type: str, technical_name: str) -> dict[str, Any] | None:
        key = (object_type, technical_name)
        if key in self.read_cache:
            cached = self.read_cache[key]
            self.diagnostics.record_logical_read(
                object_type, technical_name, cache_hit=True, reused=cached is not None
            )
            return cached
        self.diagnostics.record_logical_read(
            object_type, technical_name, cache_hit=False, reused=False
        )
        try:
            data = self.cli.read_object(self.space, object_type, technical_name)
        except DatasphereCliError as exc:
            _say(f"WARN read {object_type}/{technical_name}: {exc}", file=sys.stderr)
            self.failures.append({"kind": "read", "object_type": object_type, "technical_name": technical_name, "code": "READ_FAILED"})
            self.read_cache[key] = None
            return None
        self.read_cache[key] = data
        return data

    def add_node(self, object_type: str, technical_name: str) -> None:
        self.nodes[(object_type, technical_name)] = Node(object_type, technical_name)

    def add_edge(self, source_type: str, source_name: str, relation: str, target_type: str, target_name: str) -> None:
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
        return set(re.findall(r'(?:FROM|JOIN)\s+"([^"]+)"', sql, flags=re.IGNORECASE))

    @staticmethod
    def classify_view_type(data: dict[str, Any], technical_name: str) -> str:
        """Tipo de vista (GRAPHICAL | SQL | UNKNOWN). FUENTE ÚNICA: ``tools/catalog/dsp_view_type.py`` (la misma función que consume el
        Modeling Assessment); acá solo se delega, sin segunda implementación."""
        project_root = Path(__file__).resolve().parents[2]
        if str(project_root) not in sys.path:                     # el exportador puede correr como script
            sys.path.insert(0, str(project_root))
        from tools.catalog.dsp_view_type import classify_view_type
        return classify_view_type(data, technical_name)

    def parse_view_dependencies(self, data: dict[str, Any], technical_name: str) -> set[str]:
        definitions = data.get("definitions", {})
        if not isinstance(definitions, dict):
            return set()
        definition = definitions.get(technical_name)
        if not isinstance(definition, dict):
            definition = next((v for v in definitions.values() if isinstance(v, dict)), None)
        if not isinstance(definition, dict):
            return set()
        sql = definition.get("@DataWarehouse.sqlEditor.query")
        return self._extract_sql_dependencies(sql) if isinstance(sql, str) else set()

    def parse_association_targets(self, data: dict[str, Any], technical_name: str) -> set[str]:
        """Targets de las asociaciones ``cds.Association`` declaradas en la definición CSN del artefacto (``elements``).
        Fuente canónica estructural compartida con el catálogo (``tools.catalog.csn_associations``): misma regla para
        vistas SQL y graphical y para Analytic Models. Import diferido: el extractor puede correr como script antes del
        bootstrap de ``sys.path`` (ver ``classify_view_type``)."""
        from tools.catalog.csn_associations import association_targets
        definitions = data.get("definitions", {})
        if not isinstance(definitions, dict):
            return set()
        defn = definitions.get(technical_name)
        if not isinstance(defn, dict):
            defn = next((v for v in definitions.values() if isinstance(v, dict)), None)
        return association_targets(defn) if isinstance(defn, dict) else set()

    def parse_replication_flow(self, data: dict[str, Any], technical_name: str) -> dict[str, set[str]]:
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
            return any(k == value or LineageExporter._contains_exact_string(v, value) for k, v in data.items())
        return False

    def _dependency_allowed(self, dep_type: str) -> bool:
        feature = _feature_for_object_type(dep_type)
        return bool(feature and feature in self.effective_features)

    def walk_model_graph(self) -> None:
        assert self.root_type
        root_object_type = ROOT_TYPE_TO_OBJECT_TYPE[self.root_type]
        _say("\n==========================================")
        _say("RESOLVIENDO LINAJE UPSTREAM")
        _say("==========================================")
        started = time.perf_counter()
        queue: list[Node] = [Node(root_object_type, self.root_object)]
        processed: set[tuple[str, str]] = set()
        i = 0

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
                # AM dependencies are useful only if Views/Tables are in effective scope.
                if "views" in self.effective_features or "tables" in self.effective_features:
                    deps = self.parse_analytic_model_dependencies(data)
                    # Cierre semántico: una asociación del AM/vista puede ser la ÚNICA ruta a una hoja (p. ej. una tabla
                    # de textos ``_Text`` o una dimensión reusada) que el FROM/JOIN no referencia. Se unen al mismo set
                    # y atraviesan el MISMO gating (resolve_type + _dependency_allowed + visitados) que el resto.
                    deps |= self.parse_association_targets(data, node.technical_name)
            elif node.object_type == "views":
                if "views" in self.effective_features or "tables" in self.effective_features:
                    deps = self.parse_view_dependencies(data, node.technical_name)
                    deps |= self.parse_association_targets(data, node.technical_name)

            if not deps:
                _say("OK - sin dependencias upstream dentro del alcance")
                continue

            accepted = 0
            for dep_name in sorted(deps):
                dep_type = self.resolve_type(dep_name)
                if not dep_type:
                    _say(f"  EXTERNAL/UNKNOWN: {dep_name}")
                    self.external_sources.add(dep_name)
                    continue
                if not self._dependency_allowed(dep_type):
                    _say(f"  SKIP scope - {dep_type}: {dep_name}")
                    continue
                accepted += 1
                _say(f"  -> {dep_type}: {dep_name}")
                self.add_edge(node.object_type, node.technical_name, "DEPENDS_ON", dep_type, dep_name)
                queue.append(Node(dep_type, dep_name))
            _say(f"Dependencias incluidas: {accepted}/{len(deps)}")

        _say("\n------------------------------------------")
        _say(f"LINAJE UPSTREAM terminado en {time.perf_counter() - started:.1f}s")
        _say(f"Nodos actuales: {len(self.nodes)}")
        _say("------------------------------------------")

    def discover_producers(self) -> None:
        enabled = [feature for feature in PRODUCER_TYPES if feature in self.effective_features]
        if not enabled:
            _say("\nProductores: SKIP - ninguna familia de flows habilitada.")
            return
        _say("\n==========================================")
        _say("BUSCANDO PRODUCTORES")
        _say("==========================================")
        started = time.perf_counter()
        local_tables = [n.technical_name for n in self.nodes.values() if n.object_type == "local-tables"]
        known_names = {n.technical_name for n in self.nodes.values()}
        # Universo de entidades REALES ya adquiridas (mismo criterio que ``entities`` del resolver): guarda de
        # independencia para la normalización ``X_Delta -> X``. Si ``X_Delta`` es una entidad propia del universo
        # adquirido, NO es la sombra de captura de cambios de ``X`` y no se colapsa. (No se usa el inventario vivo
        # del Space: puede listar tablas ``_Delta`` que nunca se materializan como entidad del pool.)
        acquired_universe = known_names

        if "replication-flows" in self.effective_features:
            rf_names = sorted(self.ensure_inventory("replication-flows"))
            rf_ok = 0
            for i, rf_name in enumerate(rf_names, start=1):
                _say(f"\n[RF {i}/{len(rf_names)}] {rf_name}")
                data = self.safe_read("replication-flows", rf_name)
                if data is None:
                    _say("ERROR")
                    continue
                info = self.parse_replication_flow(data, rf_name)
                # Un leaf del linaje se produce si ALGÚN target declarado del RF matchea canónicamente (EXACT o la
                # sombra de captura de cambios ``<leaf>_Delta``). Misma regla que ``readers.target_flows`` (resolver).
                matched_targets = sorted(
                    leaf for leaf in local_tables
                    if any(_canonical_target_match(t, leaf, acquired_universe) for t in info["targets"])
                )
                if not matched_targets:
                    _say("SKIP - sin target del linaje")
                    continue
                rf_ok += 1
                _say(f"OK - target(s): {', '.join(matched_targets)}")
                self.add_node("replication-flows", rf_name)
                for target in matched_targets:
                    self.add_edge("replication-flows", rf_name, "PRODUCES", "local-tables", target)
                for src in sorted(info["sources"]):
                    self.external_sources.add(src)
                    _say(f"  SOURCE: {src}")
                    self.add_edge("external-source", src, "REPLICATED_BY", "replication-flows", rf_name)
                for connection in sorted(info["connections"]):
                    _say(f"  CONNECTION: {connection}")
                    self.add_edge("connection", connection, "USED_BY", "replication-flows", rf_name)
            _say(f"\nReplication Flows relacionados: {rf_ok}/{len(rf_names)}")

        for object_type in ("data-flows", "transformation-flows"):
            if object_type not in self.effective_features:
                continue
            flow_names = sorted(self.ensure_inventory(object_type))
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
        if "task-chains" not in self.effective_features:
            _say("\nTask Chains: SKIP - feature deshabilitada.")
            return
        _say("\n==========================================")
        _say("BUSCANDO TASK CHAINS")
        _say("==========================================")
        started = time.perf_counter()
        operational_names = {
            n.technical_name for n in self.nodes.values() if n.object_type in PRODUCER_TYPES
        }
        if not operational_names:
            _say("SKIP - no hay productores operativos en el linaje")
            return
        tc_names = sorted(self.ensure_inventory("task-chains"))
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
                ref_type = next((t for t in PRODUCER_TYPES if (t, ref) in self.nodes), "unknown")
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
        client_cache_dir = self.output_dir
        client_cache_dir.mkdir(parents=True, exist_ok=True)
        selected_by_type: dict[str, set[str]] = {object_type: set() for object_type in OBJECT_TYPES}
        for node in self.nodes.values():
            if node.object_type in selected_by_type:
                selected_by_type[node.object_type].add(node.technical_name)

        _say("\n==========================================")
        _say("EXPORTANDO CSN AL POOL DE EVIDENCIA")
        _say("==========================================")
        _say(f"Destino: {client_cache_dir}")
        exported = 0
        errors = 0
        for object_type in OBJECT_TYPES:
            names = sorted(selected_by_type.get(object_type, set()))
            if not names:
                continue
            target_dir = client_cache_dir / object_type
            target_dir.mkdir(parents=True, exist_ok=True)
            _say(f"\nProcesando: {object_type} ({len(names)})")
            for i, technical_name in enumerate(names, start=1):
                _say(f"[{i}/{len(names)}] {technical_name}")
                data = self.safe_read(object_type, technical_name)
                if data is None:
                    _say("ERROR")
                    errors += 1
                    continue
                output_file = target_dir / f"{self._safe_filename(technical_name)}.json"
                local_started = time.perf_counter()
                output_data = data
                if object_type == "views":
                    output_data = dict(data)
                    output_data["viewType"] = self.classify_view_type(
                        data,
                        technical_name,
                    )
                output_file.write_text(
                    json.dumps(output_data, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                self.diagnostics.record_local_work((time.perf_counter() - local_started) * 1000.0)
                _say("OK")
                exported += 1
        _say("\n==========================================")
        _say("EXPORT CSN FINALIZADO")
        _say(f"Archivos exportados: {exported}")
        _say(f"Errores:             {errors}")
        _say(f"Pool:                {client_cache_dir}")
        _say("==========================================")
        return client_cache_dir

    def render_outputs(self) -> tuple[dict[tuple[str, str], str], list[tuple[str, str]]]:
        """Texto EXACTO que ``write_outputs`` escribiría por objeto (sin escribir nada), y los objetos sin contenido. Usa lo ya leído (``read_cache``)."""
        texts: dict[tuple[str, str], str] = {}
        missing: list[tuple[str, str]] = []
        for node in sorted(self.nodes.values(), key=lambda n: (n.object_type, n.technical_name)):
            if node.object_type not in OBJECT_TYPES:
                continue
            data = self.safe_read(node.object_type, node.technical_name)
            if data is None:
                missing.append((node.object_type, node.technical_name))
                continue
            output_data = data
            if node.object_type == "views":
                output_data = dict(data)
                output_data["viewType"] = self.classify_view_type(data, node.technical_name)
            texts[(node.object_type, node.technical_name)] = json.dumps(output_data, indent=2, ensure_ascii=False)
        return texts, missing

    def assert_complete(self) -> None:
        """Compuerta de completitud (H1): lanza ``ExtractionIncompleteError`` si hubo CUALQUIER fallo de lectura o listado, si un nodo quedó sin contenido, si una arista
        apunta a un objeto que no está en el plan o si falta la raíz. Se invoca ANTES de escribir nada."""
        problems = list(self.failures)
        keys = set(self.nodes)
        for (otype, name) in sorted(keys):
            if otype in OBJECT_TYPES and self.read_cache.get((otype, name)) is None:
                problems.append({"kind": "read", "object_type": otype, "technical_name": name, "code": "NODE_WITHOUT_CONTENT"})
        for edge in sorted(self.edges, key=lambda e: (e.source_type, e.source_name, e.relation, e.target_type, e.target_name)):
            for t, n in ((edge.source_type, edge.source_name), (edge.target_type, edge.target_name)):
                if t in OBJECT_TYPES and (t, n) not in keys:
                    problems.append({"kind": "edge", "object_type": t, "technical_name": n, "code": "EDGE_TARGET_NOT_IN_PLAN"})
        if not self.root_type or (ROOT_TYPE_TO_OBJECT_TYPE[self.root_type], self.root_object) not in keys:
            problems.append({"kind": "root", "object_type": ROOT_TYPE_TO_OBJECT_TYPE.get(self.root_type or "", ""), "technical_name": self.root_object, "code": "ROOT_MISSING"})
        unique = {(p["kind"], p["object_type"], p["technical_name"], p["code"]): p for p in problems}
        if unique:
            raise ExtractionIncompleteError(list(unique.values()))

    def extract(self) -> Path:
        """Las fases de ADQUISICIÓN (sin escribir nada). ``run()`` = ``extract()`` + exportar al pool (camino de siempre)."""
        with self.diagnostics.phase("ROOT / INVENTORY"):
            self.detect_root_type()
            self.normalize_scope()
            # Caller constraint is deliberately enforced after root resolution and
            # before any recursive traversal, producer/orchestrator discovery or export.
            self.validate_root_constraint()
            self.print_scope()
        # Root inventories are already acquired by detect_root_type; all expensive
        # families remain lazy and are only acquired by producer/orchestrator phases.
        with self.diagnostics.phase("LINEAGE"):
            self.walk_model_graph()
        with self.diagnostics.phase("PRODUCER DISCOVERY"):
            self.discover_producers()
        with self.diagnostics.phase("TASK CHAINS"):
            self.discover_task_chains()
        return self.output_dir

    def run(self) -> Path:
        self.extract()
        with self.diagnostics.phase("WRITE / EXPORT"):
            return self.write_outputs()


def _manifest_object_keys(manifest: dict[str, Any]) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for obj in manifest.get("objects") or []:
        if not isinstance(obj, dict):
            continue
        object_type = str(obj.get("object_type") or "").strip()
        technical_name = str(obj.get("technical_name") or "").strip()
        if object_type and technical_name:
            out.add((object_type, technical_name))
    return out


def _load_json_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _other_manifest_ownership(
    client_runs_root: Path,
    excluded_manifest_paths: Iterable[Path],
) -> set[tuple[str, str]]:
    owned: set[tuple[str, str]] = set()
    if not client_runs_root.exists():
        return owned
    excluded: set[Path] = set()
    for path in excluded_manifest_paths:
        try:
            excluded.add(path.resolve())
        except OSError:
            excluded.add(path)
    for path in client_runs_root.rglob("lineage-manifest.json"):
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved in excluded:
            continue
        owned |= _manifest_object_keys(_load_json_file(path))
    return owned


def _cleanup_stale_owned_objects(
    *,
    client_cache_dir: Path,
    previous_manifest: dict[str, Any],
    current_objects: set[tuple[str, str]],
    other_owned_objects: set[tuple[str, str]],
) -> tuple[int, int]:
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
    _say(f"Objetos previos:       {len(previous_objects)}")
    _say(f"Objetos actuales:      {len(current_objects)}")
    _say(f"Candidatos obsoletos:  {len(stale)}")
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
    for child in sorted(client_cache_dir.iterdir()) if client_cache_dir.exists() else []:
        if child.is_dir():
            try:
                child.rmdir()
            except OSError:
                pass
    _say("------------------------------------------")
    _say(f"Eliminados:             {deleted}")
    _say(f"Retenidos compartidos:  {retained_shared}")
    _say("------------------------------------------")
    return deleted, retained_shared


def _safe_path_component(value: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", value.strip()).replace(" ", "_")


def _canonical_run_dir(
    catalog_root: Path,
    client_dir_name: str,
    space: str,
    root_type: str,
    root_object: str,
) -> Path:
    return (
        catalog_root
        / ".lineage_runs"
        / client_dir_name
        / _safe_path_component(space)
        / _safe_path_component(root_type)
        / _safe_path_component(root_object)
    )


def _v1_space_manifest(catalog_root: Path, client_dir_name: str, space: str, analytic_model: str) -> Path:
    return (
        catalog_root
        / ".lineage_runs"
        / client_dir_name
        / _safe_path_component(space)
        / _safe_path_component(analytic_model)
        / "lineage-manifest.json"
    )


def _legacy_run_manifest(catalog_root: Path, client_dir_name: str, analytic_model: str) -> Path:
    return (
        catalog_root
        / ".lineage_runs"
        / client_dir_name
        / _safe_path_component(analytic_model)
        / "lineage-manifest.json"
    )


def _load_previous_manifest_with_migration_candidates(
    *,
    catalog_root: Path,
    client_dir_name: str,
    space: str,
    root_type: str,
    root_object: str,
    canonical_manifest_path: Path,
) -> tuple[dict[str, Any], Path | None]:
    current = _load_json_file(canonical_manifest_path)
    if current:
        return current, None
    if root_type != "analytic-model":
        return {}, None
    candidates = [
        _v1_space_manifest(catalog_root, client_dir_name, space, root_object),
        _legacy_run_manifest(catalog_root, client_dir_name, root_object),
    ]
    for candidate_path in candidates:
        if not candidate_path.exists():
            continue
        candidate = _load_json_file(candidate_path)
        if (
            str(candidate.get("space") or "") == space
            and str(candidate.get("analyticModel") or candidate.get("rootObject") or "") == root_object
        ):
            _say(f"  Previous ownership: V1 manifest {candidate_path}")
            return candidate, candidate_path
    return {}, None


def _write_manifest_v2(
    *,
    path: Path,
    client: str,
    space: str,
    exporter: LineageExporter,
    mode: str,
    deleted_stale: int,
    retained_shared: int,
) -> None:
    assert exporter.root_type
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "schemaVersion": 2,
        "extractorVersion": EXTRACTOR_VERSION,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "client": client,
        "space": space,
        "rootObject": exporter.root_object,
        "rootType": exporter.root_type,
        "mode": mode.upper(),
        "requestedFeatures": sorted(exporter.requested_features),
        "effectiveFeatures": sorted(exporter.effective_features),
        "objects": [
            asdict(n)
            for n in sorted(exporter.nodes.values(), key=lambda n: (n.object_type, n.technical_name))
        ],
        "edges": [
            asdict(e)
            for e in sorted(
                exporter.edges,
                key=lambda e: (e.source_type, e.source_name, e.relation, e.target_type, e.target_name),
            )
        ],
        "externalSources": sorted(exporter.external_sources),
        "cleanup": {
            "deletedStaleObjects": deleted_stale,
            "retainedSharedObjects": retained_shared,
        },
    }
    # Transitional compatibility for consumers that still inspect analyticModel.
    if exporter.root_type == "analytic-model":
        manifest["analyticModel"] = exporter.root_object
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")



@dataclass
class ExportRequest:
    """Programmatic extraction contract used by CLI and web orchestration.

    ``analytic_model`` remains as a deprecated compatibility input. New callers
    should use ``root_object`` and optionally ``root_type``. Output/persistence
    policy is supplied by the orchestrator, not inferred by traversal logic.
    """

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
    root_object: str = ""
    root_type: str | None = None
    features_requested: str | Iterable[str] | None = None
    mode: str = "assessment"
    allowed_root_types: str | Iterable[str] | None = None
    diagnostics: bool = False
    safe: bool | None = None          # publicación SEGURA (fase 0); None = según DANWOR_PUBLICATION_SAFE. Solo aplica al modo PUBLISH
    stage_hooks: Any = None           # etapas adicionales de la unidad de publicación (P1: el assessment gobernado); ver ``publication_txn.StageHooks``. Solo publicación segura


@dataclass
class ExportResult:
    ok: bool
    error_code: str | None = None
    message: str = ""
    client: str = ""
    space: str = ""
    analytic_model: str = ""
    root_object: str = ""
    root_type: str | None = None
    mode: str = "assessment"
    requested_features: list[str] = field(default_factory=list)
    effective_features: list[str] = field(default_factory=list)
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
    mirrored_files: int | None = None
    diagnostics: dict[str, Any] | None = None
    exit_code: int = 0
    # ── contrato de la publicación segura (vacío en el camino anterior) ──
    status: str = ""                  # COMPLETED | FAILED_NO_CHANGE | ROLLED_BACK | COMMITTED_UNCONFIRMED | COMMITTED_CLEANUP_DEFERRED | RECOVERY_REQUIRED
    operation_id: str = ""
    durability: str = ""
    failures: list[dict[str, Any]] = field(default_factory=list)
    stages: dict[str, str] = field(default_factory=dict)
    applied: dict[str, Any] = field(default_factory=dict)
    observations: dict[str, Any] = field(default_factory=dict)
    deferred_reasons: list[str] = field(default_factory=list)
    client_dir: str = ""              # directorio canónico del cliente con el que se publicó (una sola identidad por cliente)
    outcome: str = ""                 # CHANGED | NO_CHANGE (una republicación idéntica no escribe nada)


_PRECHECK_CODES = (
    ("PRECHECK_OAUTH_NETWORK_ERROR", "OAUTH_ENDPOINT_UNREACHABLE"),
    ("PRECHECK_OAUTH_ERROR", "OAUTH_ENDPOINT_UNREACHABLE"),
    ("PRECHECK_OAUTH_401", "AUTHENTICATION_FAILED"),
    ("PRECHECK_OAUTH_INVALID_RESPONSE", "AUTHENTICATION_FAILED"),
    ("PRECHECK_OAUTH_HTTP_", "AUTHENTICATION_FAILED"),
    ("PRECHECK_AUTH_403", "INSUFFICIENT_DATASPHERE_PRIVILEGES"),
    ("PRECHECK_AUTH_401", "AUTHENTICATION_FAILED"),
    ("PRECHECK_AUTH_ERROR", "SPACE_ACCESS_FAILED"),
    ("ROOT_OBJECT_NOT_FOUND", "ROOT_OBJECT_NOT_FOUND"),
    ("AMBIGUOUS_ROOT_OBJECT_TYPE", "AMBIGUOUS_ROOT_OBJECT_TYPE"),
    ("INVALID_FEATURE_SCOPE", "INVALID_FEATURE_SCOPE"),
    ("ROOT_ARGUMENT_CONFLICT", "ROOT_ARGUMENT_CONFLICT"),
    ("EXTRACTION_INCOMPLETE", "EXTRACTION_INCOMPLETE"),
    ("ROOT_TYPE_CONSTRAINT_FAILED", "ROOT_TYPE_CONSTRAINT_FAILED"),
    ("INVALID_ROOT_TYPE_CONSTRAINT", "INVALID_ROOT_TYPE_CONSTRAINT"),
)


def classify_error(message: str) -> str:
    for prefix, code in _PRECHECK_CODES:
        if str(message).startswith(prefix):
            return code
    return "EXTRACTION_FAILED"


def regenerate_catalog_from_pool(csn_cache_root: Path, catalog_root: Path, only_clients: set[str] | None = None) -> str:
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from tools.catalog.generate_catalog import generate_all

    if only_clients is not None:
        return str(generate_all(csn_cache_root, catalog_root, only_clients=only_clients))
    return str(generate_all(csn_cache_root, catalog_root))


def format_runtime(seconds: float) -> str:
    """Duración legible: < 60 s => ``47.2s``; >= 60 s => ``06m 52s (412.3 s)`` (minutos y segundos + los segundos exactos con un decimal)."""
    total = round(max(0.0, float(seconds)), 1)
    if total < 60:
        return f"{total:.1f}s"
    minutes = int(total // 60)
    return f"{minutes:02d}m {int(total - 60 * minutes):02d}s ({total:.1f} s)"



# ════════════════════════════════════════════════════════════════════════════════════════
# PUBLICACIÓN SEGURA (fase 0: H1/H3/H5/H6) — se usa SOLO con ``DANWOR_PUBLICATION_SAFE`` activo (o ``ExportRequest.safe=True``) y modo PUBLISH.
# Diseño: docs/audit/CATALOG_REPUBLICATION_PHASE0_DESIGN_20261026.md. Con el interruptor apagado ``run_export`` sigue exactamente el camino anterior.
#   EXTRACT (sin candado) → VALIDATE (completitud) → [hold] → candado de ESCRITOR ÚNICO → recuperar pendientes → REVALIDAR manifiestos/precondiciones (bajo candado)
#   → journal + undo → APPLY atómico → VERIFY → COMMIT (journal) → confirmación remota (persist_changed) → resultado con ``status`` explícito.
# ════════════════════════════════════════════════════════════════════════════════════════
_SAFE_LOCK_TIMEOUT_S = 30.0


def _txn():
    """Import diferido: el exportador puede correr como script antes del bootstrap de ``sys.path``."""
    root = str(Path(__file__).resolve().parents[2])
    if root not in sys.path:
        sys.path.insert(0, root)
    from tools.catalog import publication_txn
    return publication_txn


def _txn_call(hooks, name):
    fn = getattr(hooks, name, None) if hooks is not None else None
    return fn() if callable(fn) else None


def _tenant_id(host: str) -> str:
    from urllib.parse import urlparse
    return (urlparse(host if "://" in host else "https://" + host).hostname or "").lower()


def _safe_failure(req: "ExportRequest", base: "ExportResult", finish, status: str, code: str, message: str, *, operation_id: str = "",
                  stages: dict | None = None, failures: list | None = None, exit_code: int = 1) -> "ExportResult":
    _say(f"ERROR: {code} - {message}", file=sys.stderr)
    _say("\nESTADO DE LA PUBLICACIÓN: " + status + " (no se modificó el Catálogo)" if status == "FAILED_NO_CHANGE" else "\nESTADO DE LA PUBLICACIÓN: " + status)
    base.ok = False
    base.error_code = code
    base.message = message
    base.status = status
    base.operation_id = operation_id
    base.failures = list(failures or [])
    base.stages = dict(stages or {})
    base.exit_code = exit_code
    return finish(base)


def _publish_safe(req: "ExportRequest", exporter: "LineageExporter", *, base: "ExportResult", client_dir_name: str, catalog_root: Path,
                  csn_cache_root: Path, client_cache_dir: Path, diagnostics: Any, finish) -> "ExportResult":
    txn = _txn()
    op_id = txn.new_operation_id()
    base.root_type = exporter.root_type
    base.requested_features = sorted(exporter.requested_features)
    base.effective_features = sorted(exporter.effective_features)
    stages: dict[str, str] = {"extraction": "OK"}
    _say("\n==========================================")
    _say("PUBLICACION SEGURA (DANWOR_PUBLICATION_SAFE)")
    _say("==========================================")

    # ── VALIDATE: una extracción incompleta NO llega a tocar nada ──
    try:
        exporter.assert_complete()
        planned, missing = exporter.render_outputs()
        if missing:
            raise ExtractionIncompleteError([{"kind": "render", "object_type": t, "technical_name": n, "code": "NO_CONTENT"} for t, n in missing])
    except ExtractionIncompleteError as exc:
        stages["extraction"] = "INCOMPLETE"
        _say(f"Objetos con lectura/listado incompleto: {len(exc.failures)}")
        for f in exc.failures[:20]:
            _say(f"  - {f.get('kind')}: {f.get('object_type') or '-'}/{f.get('technical_name') or '-'} ({f.get('code')})")
        return _safe_failure(req, base, finish, txn.FAILED_NO_CHANGE, "EXTRACTION_INCOMPLETE",
                             "La extracción quedó incompleta (falló la lectura o el listado de algún objeto): no se modificó el Catálogo.",
                             operation_id=op_id, stages=stages, failures=exc.failures)
    planned_sha = {key: txn.sha256_text(text) for key, text in planned.items()}
    current_objects = set(planned)
    stages["validation"] = "OK"
    txn.fault("after_validate")

    from core import remote_store
    want_remote = bool(req.mirror_up)
    hold_names = ("catalog_local", *(_txn_call(req.stage_hooks, "persisted_names") or ()))
    hold_cm = remote_store.hold_publication(hold_names) if (want_remote and remote_store.write_enabled()) else contextlib.nullcontext(None)
    try:
        with hold_cm as hold:
            if hold is not None and not hold.exclusive:
                return _safe_failure(req, base, finish, txn.FAILED_NO_CHANGE, "CATALOG_BUSY",
                                     "Hay una publicación periódica en curso sobre el Catálogo: no se modificó nada. Reintentá en unos minutos.",
                                     operation_id=op_id, stages=stages)
            with txn.writer_lock(_SAFE_LOCK_TIMEOUT_S):
                return _apply_locked(req, exporter, base=base, txn=txn, op_id=op_id, stages=stages, planned=planned, planned_sha=planned_sha,
                                     current_objects=current_objects, client_dir_name=client_dir_name, catalog_root=catalog_root,
                                     csn_cache_root=csn_cache_root, client_cache_dir=client_cache_dir, remote_store=remote_store, hold=hold, finish=finish,
                                     diagnostics=diagnostics)
    except txn.CatalogBusyError:
        return _safe_failure(req, base, finish, txn.FAILED_NO_CHANGE, "CATALOG_BUSY",
                             "Hay otra operación sobre el Catálogo en curso: no se modificó nada. Reintentá en unos minutos.", operation_id=op_id, stages=stages)


def _pool_path(client_cache_dir: Path, key: tuple[str, str]) -> Path:
    return client_cache_dir / key[0] / f"{LineageExporter._safe_filename(key[1])}.json"


def _remote_dirs(remote_store) -> dict[str, Path]:
    """Directorios persistidos (nombre lógico → ruta local) tal como los resuelve ``remote_store`` (la MISMA fuente de verdad del mapeo local→clave)."""
    return {name: Path(local).resolve() for name, local in remote_store._persist_dirs()}


def _key_of(dirs: dict[str, Path], path: Path) -> str | None:
    """Clave estable ``<dir persistido>/<ruta relativa>`` de una ruta local, o None si no cae bajo un directorio persistido."""
    resolved = Path(path).resolve()
    for name, base in dirs.items():
        try:
            return f"{name}/{resolved.relative_to(base).as_posix()}"
        except ValueError:
            continue
    return None


def _path_of(dirs: dict[str, Path], key: str) -> Path | None:
    name, _, rel = key.partition("/")
    return dirs[name] / rel if name in dirs else None


def _apply_locked(req, exporter, *, base, txn, op_id, stages, planned, planned_sha, current_objects, client_dir_name, catalog_root,
                  csn_cache_root, client_cache_dir, remote_store, hold, finish, diagnostics) -> "ExportResult":
    from core import atomic_io, publication_gate
    from tools.catalog import publication_ledger as ledger_mod
    from tools.catalog import publication_remote as pr
    hooks = req.stage_hooks
    ctx = txn.StageContext(operation_id=op_id, client_dir=client_dir_name, catalog_root=catalog_root, csn_cache_root=csn_cache_root,
                           root_object=exporter.root_object, root_type=exporter.root_type, space=req.space)
    # ── 0. recuperar cualquier operación a medias ANTES de decidir nada ──
    try:
        recovered = txn.recover_pending(catalog_root)
    except txn.RecoveryError as exc:
        return _safe_failure(req, base, finish, txn.RECOVERY_REQUIRED, "RECOVERY_REQUIRED",
                             "Hay una operación anterior a medias que no pudo recuperarse: no se publica hasta resolverla.", operation_id=op_id, stages=stages)
    for item in recovered:
        _say(f"Recuperación previa: operación {item['operation_id']} {item['from']} → {item['action']}")
    if recovered:
        txn.call_hook(hooks, "after_rollback", ctx)                       # el disco pudo cambiar: la memoria del servicio no puede conservar la versión anterior
    if txn.has_pending(catalog_root):
        return _safe_failure(req, base, finish, txn.RECOVERY_REQUIRED, "RECOVERY_REQUIRED",
                             "Una publicación anterior sigue sin confirmarse en Spaces: no se publica hasta resolverla.", operation_id=op_id, stages=stages)
    txn.fault("after_lock")

    # ── 1. REVALIDAR bajo el candado: manifiestos y precondiciones se calculan ACÁ (nada calculado antes de adquirirlo se da por válido) ──
    space = req.space
    diag_dir = _canonical_run_dir(catalog_root, client_dir_name, space, exporter.root_type, exporter.root_object)
    manifest_path = diag_dir / "lineage-manifest.json"
    own = txn.load_manifest(manifest_path)
    migrated_from: Path | None = None
    legacy_problem: str | None = None
    if own.state == txn.M_ABSENT and exporter.root_type == "analytic-model":
        for cand in (_v1_space_manifest(catalog_root, client_dir_name, space, exporter.root_object),
                     _legacy_run_manifest(catalog_root, client_dir_name, exporter.root_object)):
            if not cand.exists():
                continue
            loaded = txn.load_manifest(cand)
            if not loaded.usable:
                legacy_problem = f"legacy_manifest_{loaded.state.lower()}"
                continue
            if str(loaded.data.get("space") or "") == space and str(loaded.data.get("analyticModel") or loaded.data.get("rootObject") or "") == exporter.root_object:
                _say(f"  Previous ownership: V1 manifest {cand}")
                own, migrated_from = loaded, cand
                break
    runs_root = catalog_root / ".lineage_runs" / client_dir_name
    excluded = {manifest_path.resolve()} | ({migrated_from.resolve()} if migrated_from else set())
    other_owned: set[tuple[str, str]] = set()
    claims: dict[tuple[str, str], list[dict]] = {}
    unusable: list[str] = []
    if runs_root.exists():
        for mp in sorted(runs_root.rglob("lineage-manifest.json")):
            try:
                if mp.resolve() in excluded:
                    continue
            except OSError:
                pass
            loaded = txn.load_manifest(mp)
            if not loaded.usable:
                unusable.append(f"{mp.parent.name}:{loaded.state}")
                continue
            for key in _manifest_object_keys(loaded.data):
                other_owned.add(key)
                claims.setdefault(key, []).append(loaded.data)

    reasons: list[str] = []
    if own.state in (txn.M_UNREADABLE, txn.M_INVALID_SCHEMA, txn.M_DIGEST_MISMATCH):
        reasons.append("previous_manifest_" + own.state.lower())
    if legacy_problem:
        reasons.append(legacy_problem)
    if unusable:
        reasons.append("other_manifest_unusable")
    if remote_store.read_enabled() and remote_store.sync_blocked_reason():
        reasons.append("restore_incomplete")
    deletes_allowed = not reasons
    own_unknown = own.state in (txn.M_UNREADABLE, txn.M_INVALID_SCHEMA, txn.M_DIGEST_MISMATCH) or bool(legacy_problem)
    previous_objects = _manifest_object_keys(own.data) if own.usable else set()
    pending_prev = {(o.get("object_type"), o.get("technical_name")) for o in (own.data.get("pendingCleanup") or []) if isinstance(o, dict)} if own.usable else set()
    stale = (previous_objects | pending_prev) - current_objects      # lo que un cleanup DIFERIDO anterior dejó pendiente se retoma acá (no queda huérfano)
    retained_shared = sorted(k for k in stale if k in other_owned)
    deletable = sorted(k for k in stale if k not in other_owned and _pool_path(client_cache_dir, k).exists()) if deletes_allowed else []
    pending_next = sorted(k for k in stale if k not in other_owned and _pool_path(client_cache_dir, k).exists()) if not deletes_allowed else []
    if stale and not deletes_allowed:
        _say(f"\nLimpieza DIFERIDA (no se borra nada): {', '.join(reasons)}")
        if unusable:
            _say("  Manifiestos no utilizables: " + ", ".join(unusable))

    # observabilidad (sin bloqueo): contracción fuerte del conjunto de objetos
    observations: dict[str, Any] = {}
    if len(previous_objects) >= 4 and len(current_objects) * 2 < len(previous_objects):
        observations["contraction"] = {"previous": len(previous_objects), "current": len(current_objects)}
        _say(f"OBSERVACION: el modelo pasó de {len(previous_objects)} a {len(current_objects)} objetos (contracción > 50 %); revisá que sea un cambio real del Space.")

    # guardia de identidad (red de seguridad transitoria): no pisar un objeto de OTRO Space/tenant con contenido distinto
    tenant = _tenant_id(req.host)
    for key in sorted(current_objects):
        path = _pool_path(client_cache_dir, key)
        if not path.exists():
            continue
        existing = txn.sha256_text_file(path)
        if existing == planned_sha[key]:
            continue
        for claim in claims.get(key, []):
            c_space, c_tenant = str(claim.get("space") or ""), str(claim.get("tenantId") or "")
            if c_space != space or (c_tenant and tenant and c_tenant != tenant):
                return _safe_failure(req, base, finish, txn.FAILED_NO_CHANGE, "POOL_IDENTITY_CONFLICT",
                                     f"El objeto {key[0]}/{key[1]} ya está publicado desde otro Space/tenant con contenido distinto: no se modificó nada.",
                                     operation_id=op_id, stages=stages)

    writes = [(key, _pool_path(client_cache_dir, key), planned[key]) for key in sorted(current_objects)
              if not (_pool_path(client_cache_dir, key).exists() and txn.sha256_text_file(_pool_path(client_cache_dir, key)) == planned_sha[key])]
    new_manifest = txn.seal_manifest({
        "schemaVersion": 3, "extractorVersion": EXTRACTOR_VERSION, "createdAt": txn.now_iso(), "operationId": op_id, "hostId": txn.host_id(),
        "client": req.client, "space": space, "tenantId": tenant, "rootObject": exporter.root_object, "rootType": exporter.root_type, "mode": req.mode.upper(),
        "requestedFeatures": sorted(exporter.requested_features), "effectiveFeatures": sorted(exporter.effective_features),
        "objects": [{**asdict(n), "sha256": planned_sha[(n.object_type, n.technical_name)]}
                    for n in sorted(exporter.nodes.values(), key=lambda n: (n.object_type, n.technical_name)) if (n.object_type, n.technical_name) in planned_sha],
        "edges": [asdict(e) for e in sorted(exporter.edges, key=lambda e: (e.source_type, e.source_name, e.relation, e.target_type, e.target_name))],
        "externalSources": sorted(exporter.external_sources),
        "cleanup": {"deletedStaleObjects": len(deletable), "retainedSharedObjects": len(retained_shared)},
        **({"pendingCleanup": [{"object_type": t, "technical_name": n} for t, n in pending_next]} if pending_next else {}),
    })
    if exporter.root_type == "analytic-model":
        new_manifest = txn.seal_manifest({**{k: v for k, v in new_manifest.items() if k != "integrity"}, "analyticModel": exporter.root_object})
    same_manifest = own.usable and migrated_from is None and own.state == txn.M_OK and txn.semantic_signature(new_manifest) == txn.semantic_signature(own.data) and "integrity" in own.data
    write_manifest = not same_manifest
    pool_changed = bool(writes or deletable or write_manifest)
    ctx.pool_changed = pool_changed

    # ── 1b. persistencia remota: ¿hay que confirmarla? Antes de tocar nada se verifica que se PUEDA (un fallo de lectura/listado es un resultado explícito, no «sin huérfanos») ──
    client_catalog_dir = catalog_root / client_dir_name
    catalog_manifest = catalog_root / ".catalog_manifest.json"
    ledger_file = ledger_mod.ledger_path(catalog_root, client_dir_name)
    stage_names = tuple(txn.call_hook(hooks, "persisted_names", default=()) or ())
    stage_dir = txn.call_hook(hooks, "governed_client_dir", ctx)
    persisted = ("catalog_local", *stage_names)
    want_remote = bool(req.mirror_up and remote_store.write_enabled())
    dirs = _remote_dirs(remote_store) if want_remote else {}
    markers = (catalog_manifest.name, ledger_file.name)
    prefix_keys: list[str] = []
    listing: dict[str, dict] = {}
    if want_remote and stage_dir is not None and not stage_names:        # el almacén gobernado existe pero NO figura en las rutas persistidas: sus assessments no sobreviven a un redeploy
        observations["governed_assessments_not_persisted"] = True
        _say("ADVERTENCIA: el almacén gobernado de assessments no figura en SPACES_PERSIST_PATHS; los assessments no quedan confirmados en Spaces (sumá 'governed-assessments').")
    if want_remote:
        prefix_keys = _client_prefix_keys(dirs, catalog_root, csn_cache_root, client_dir_name, stage_dir)
        pre = pr.preflight(remote_store, persisted, prefix_keys, probe=ledger_file)
        if not pre.ok:
            stages["remote"] = "FAILED"
            return _safe_failure(req, base, finish, txn.FAILED_NO_CHANGE, pre.code,
                                 "No se puede confirmar la publicación en Spaces (" + pre.code + "): no se modificó nada.", operation_id=op_id, stages=stages)
        listing = pre.listing
    elif req.mirror_up and not remote_store.local_only():
        stages["remote"] = "FAILED"
        return _safe_failure(req, base, finish, txn.FAILED_NO_CHANGE, "PERSISTENCE_NOT_AVAILABLE",
                             "La persistencia en Spaces no está disponible en este modo (solo lectura o sin autorizar): no se modificó nada.", operation_id=op_id, stages=stages)

    # ── 1c. ¿hay algo que hacer? Una republicación idéntica NO escribe nada (ni pool, ni Catálogo, ni assessments, ni bitácora) ──
    ledger_state, ledger = ledger_mod.load(catalog_root, client_dir_name)
    stage_work = True if pool_changed else bool(txn.call_hook(hooks, "needs_work", ctx, default=False))
    unit_now = ledger_mod.unit_files(catalog_root, csn_cache_root, client_dir_name, stage_dir) if (not pool_changed and not stage_work) else {}
    ledger_problems = (["ledger " + ledger_state] if ledger_state != ledger_mod.L_OK else ledger_mod.verify(ledger, unit_now)) if (not pool_changed and not stage_work) else ["pendiente"]
    verify_now = _verify_applied(txn, planned_sha, client_cache_dir, manifest_path, client_catalog_dir, require_flows=bool(req.generate_catalog and planned)) if (not pool_changed and not stage_work and not ledger_problems) else ["pendiente"]
    if not pool_changed and not stage_work and not ledger_problems and not verify_now:
        return _finish_no_change(req, exporter, base=base, txn=txn, op_id=op_id, stages=stages, planned=planned, client_cache_dir=client_cache_dir, manifest_path=manifest_path,
                                 client_dir_name=client_dir_name, ledger_file=ledger_file, deferred=(list(reasons) if ((stale and not deletes_allowed) or own_unknown) else []), observations=observations,
                                 want_remote=want_remote, remote_store=remote_store, pr=pr, markers=markers, unit_now=unit_now, listing=listing, dirs=dirs, persisted=persisted,
                                 diagnostics=diagnostics, finish=finish, retained_shared=retained_shared, space=space)
    changed = True

    # ── 2. journal + undo (la compuerta de lectores se cierra ANTES de tocar nada y se abre sólo con la versión confirmada o deshecha) ──
    journal = txn.Journal(catalog_root, op_id, {
        "operation_id": op_id, "mode": "publish", "client_dir": client_dir_name, "space": space, "root_object": exporter.root_object,
        "root_type": exporter.root_type, "catalog_root": str(catalog_root), "csn_cache_root": str(csn_cache_root), "host_id": txn.host_id(),
        "started_at": txn.now_iso(), "planned_writes": [f"{k[0]}/{k[1]}" for k, _, _ in writes], "planned_deletes": [f"{k[0]}/{k[1]}" for k in deletable],
        "touched_dirs": [str(client_cache_dir), str(client_catalog_dir), str(diag_dir), *([str(stage_dir)] if stage_dir else [])]})
    journal.update(txn.J_PLANNED)
    publication_gate.begin(op_id, client_dir_name, catalog_root)
    keep_closed = {"on": False}
    try:
        return _apply_unit(req, exporter, base=base, txn=txn, op_id=op_id, stages=stages, planned=planned, planned_sha=planned_sha, client_dir_name=client_dir_name,
                           catalog_root=catalog_root, csn_cache_root=csn_cache_root, client_cache_dir=client_cache_dir, remote_store=remote_store, hold=hold, finish=finish,
                           diagnostics=diagnostics, hooks=hooks, ctx=ctx, journal=journal, writes=writes, deletable=deletable, retained_shared=retained_shared, reasons=reasons,
                           stale=stale, deletes_allowed=deletes_allowed, own_unknown=own_unknown, previous_objects=previous_objects, observations=observations,
                           manifest_path=manifest_path, new_manifest=new_manifest, write_manifest=write_manifest, migrated_from=migrated_from, diag_dir=diag_dir,
                           client_catalog_dir=client_catalog_dir, catalog_manifest=catalog_manifest, ledger_file=ledger_file, ledger=ledger, stage_dir=stage_dir, persisted=persisted,
                           want_remote=want_remote, dirs=dirs, markers=markers, prefix_keys=prefix_keys, listing=listing, keep_closed=keep_closed, atomic_io=atomic_io,
                           ledger_mod=ledger_mod, pr=pr, space=space, changed=changed, publication_gate=publication_gate)
    except BaseException as exc:                  # noqa: BLE001 — una caída (no una ``Exception`` controlada) deja la unidad a medias: la compuerta SIGUE cerrada hasta que la recuperación la resuelva
        if not isinstance(exc, Exception):
            keep_closed["on"] = True
        raise
    finally:
        if not keep_closed["on"]:
            publication_gate.end(op_id, catalog_root)


def _client_prefix_keys(dirs: dict[str, Path], catalog_root: Path, csn_cache_root: Path, client_dir: str, stage_dir: Path | None) -> list[str]:
    """Prefijos de claves remotas del cliente (pool, linaje, Catálogo, ledger, y la carpeta del cliente de la etapa)."""
    out: list[str] = []
    for folder in (csn_cache_root / client_dir, catalog_root / ".lineage_runs" / client_dir, catalog_root / client_dir):
        key = _key_of(dirs, folder)
        if key:
            out.append(key + "/")
    key = _key_of(dirs, catalog_root / ".publication_state" / f"{client_dir}.publication.json")
    if key:
        out.append(key)
    if stage_dir is not None:
        key = _key_of(dirs, Path(stage_dir))
        if key:
            out.append(key + "/")
    return out


def _finish_no_change(req, exporter, *, base, txn, op_id, stages, planned, client_cache_dir, manifest_path, client_dir_name, ledger_file, deferred, observations, want_remote, remote_store, pr,
                      markers, unit_now, listing, dirs, persisted, diagnostics, finish, retained_shared, space) -> "ExportResult":
    """Republicación idéntica: no se escribió NADA. Si hay persistencia remota se verifica (solo lectura salvo que Spaces estuviera atrasado) y se informa sin cambios."""
    durability = txn.DURABILITY_NOT_REQUESTED
    stages.update({"apply": "SKIPPED", "catalog": "SKIPPED", "assessment": "SKIPPED"})
    if want_remote:
        keyed = dict(unit_now)
        ledger_key = _key_of(dirs, ledger_file)
        if ledger_key and ledger_file.exists():
            keyed[ledger_key] = ledger_file
        outcome = pr.converge(remote_store, keyed, last=[k for k, p in keyed.items() if p.name in markers])
        if outcome.status != pr.CONFIRMED:
            stages["remote"] = "FAILED" if outcome.status == pr.FAILED else "PARTIAL"
            return _safe_failure(req, base, finish, txn.FAILED_NO_CHANGE, "PERSISTENCE_UNCONFIRMED",
                                 "La versión local ya es la publicada, pero no se pudo confirmar en Spaces. Reintentá (es idempotente).", operation_id=op_id, stages=stages)
        durability = txn.DURABILITY_REMOTE
        stages["remote"] = "OK"
    else:
        stages["remote"] = "SKIPPED"
        durability = txn.DURABILITY_LOCAL_BY_CONFIG if req.mirror_up else txn.DURABILITY_NOT_REQUESTED
    files_by_type = {t: sum(1 for _ in (client_cache_dir / t).glob("*.json")) if (client_cache_dir / t).is_dir() else 0 for t in OBJECT_TYPES}
    base.ok, base.status, base.exit_code, base.outcome = True, txn.COMPLETED, 0, "NO_CHANGE"
    base.client, base.space, base.analytic_model = req.client, space, req.analytic_model
    base.root_object, base.root_type = exporter.root_object, exporter.root_type
    base.objects, base.edges, base.external_sources = len(exporter.nodes), len(exporter.edges), len(exporter.external_sources)
    base.exported_files, base.files_by_type = sum(files_by_type.values()), {k: v for k, v in files_by_type.items() if v}
    base.retained_shared = len(retained_shared)
    base.pool_dir, base.manifest_path, base.catalog_generated = client_cache_dir, manifest_path, False
    base.operation_id, base.durability, base.stages, base.observations, base.client_dir = op_id, durability, stages, observations, client_dir_name
    base.deferred_reasons = list(deferred)
    base.applied = {"added": [], "modified": [], "removed": [], "retained_shared": [f"{k[0]}/{k[1]}" for k in retained_shared], "changed": False}
    base.diagnostics = diagnostics.snapshot() if diagnostics.enabled else None
    if base.deferred_reasons:
        base.status, base.ok, base.error_code, base.exit_code = txn.COMMITTED_CLEANUP_DEFERRED, False, "CLEANUP_DEFERRED", 3
        base.message = "Sin cambios; quedan objetos obsoletos sin limpiar (no se pudo verificar la propiedad): no se borró nada."
    _say("\nREPUBLICACION SIN CAMBIOS: el Catálogo y los assessments ya reflejan exactamente esta extracción; no se escribió nada.")
    _say(f"ESTADO DE LA PUBLICACIÓN: {base.status}")
    _say("STATUS: SUCCESS" if base.status == txn.COMPLETED else f"STATUS: {base.status}")
    return finish(base)


def _apply_unit(req, exporter, *, base, txn, op_id, stages, planned, planned_sha, client_dir_name, catalog_root, csn_cache_root, client_cache_dir, remote_store, hold, finish,
                diagnostics, hooks, ctx, journal, writes, deletable, retained_shared, reasons, stale, deletes_allowed, own_unknown, previous_objects, observations, manifest_path,
                new_manifest, write_manifest, migrated_from, diag_dir, client_catalog_dir, catalog_manifest, ledger_file, ledger, stage_dir, persisted, want_remote, dirs, markers,
                prefix_keys, listing, keep_closed, atomic_io, ledger_mod, pr, space, changed, publication_gate) -> "ExportResult":
    undo = txn.UndoLog(txn.undo_dir(catalog_root, op_id))
    stage = "snapshot"
    catalog_generated = False
    stage_summary: dict[str, Any] = {}
    failures: list[dict] = []
    try:
        txn.fault("after_journal_planned")
        txn.call_hook(hooks, "before_apply", ctx)                                  # estado PREVIO del assessment (nada escrito todavía)
        for _key, path, _text in writes:
            undo.capture(path)
        for key in deletable:
            undo.capture(_pool_path(client_cache_dir, key))
        undo.capture(manifest_path)
        if migrated_from is not None:
            undo.capture(migrated_from)
        undo.capture(client_catalog_dir)
        undo.capture(catalog_manifest)
        undo.capture(ledger_file)
        for extra in txn.call_hook(hooks, "undo_paths", ctx, default=[]) or []:
            undo.capture(extra)
        undo.save()
        journal.update(txn.J_APPLYING)
        txn.fault("after_undo_snapshot")
        # ── 3. APPLY (atómico por archivo) → ETAPAS → VERIFY ──
        stage = "apply"
        for index, (key, path, text) in enumerate(writes):
            txn.fault("mid_pool_write", index=index, key=key)
            atomic_io.write_text(path, text)
        txn.fault("after_pool")
        for key in deletable:
            _pool_path(client_cache_dir, key).unlink(missing_ok=True)
        for child in sorted(client_cache_dir.iterdir()) if client_cache_dir.exists() else []:
            if child.is_dir():
                try:
                    child.rmdir()
                except OSError:
                    pass
        txn.fault("after_cleanup")
        txn.fault("before_manifest")
        if write_manifest:
            atomic_io.write_text(manifest_path, json.dumps(new_manifest, indent=2, ensure_ascii=False))
        txn.fault("after_manifest")
        if migrated_from is not None and migrated_from != manifest_path:
            migrated_from.unlink(missing_ok=True)
            try:
                migrated_from.parent.rmdir()
            except OSError:
                pass
        if req.generate_catalog:
            stage = "catalog"
            _say("\n==========================================")
            _say("REGENERANDO CATALOGO (acotado al cliente)")
            _say("==========================================")
            _say(regenerate_catalog_from_pool(csn_cache_root, catalog_root, only_clients={client_dir_name}))
            catalog_generated = True
        txn.fault("after_catalog")
        if hooks is not None:
            stage = "assessment"
            _say("\n==========================================")
            _say("ASSESSMENT DEL CLIENTE (preparar → validar → escribir en local)")
            _say("==========================================")
            txn.call_hook(hooks, "after_catalog", ctx)                              # PREPARA en memoria; falla ⇒ se deshace la unidad entera
            txn.fault("after_assessment_prepare")
            stage_summary = txn.call_hook(hooks, "commit", ctx, default={}) or {}
            txn.fault("after_assessment_commit")
        stage = "verify"
        txn.fault("before_verify")
        problems = _verify_applied(txn, planned_sha, client_cache_dir, manifest_path, client_catalog_dir, require_flows=bool(req.generate_catalog and planned))
        problems += list(txn.call_hook(hooks, "verify", ctx, default=[]) or [])
        if problems:
            raise RuntimeError("VERIFY_FAILED: " + "; ".join(problems[:5]))
        stage = "ledger"
        unit = ledger_mod.unit_files(catalog_root, csn_cache_root, client_dir_name, stage_dir)
        new_ledger = ledger_mod.build(
            ledger, operation_id=op_id, activated_at=txn.now_iso(), client_dir=client_dir_name, space=space, root_object=exporter.root_object, root_type=exporter.root_type,
            extra={"manifest_sha256": txn.sha256_text_file(manifest_path), "objects": {f"{t}/{n}": planned_sha[(t, n)] for (t, n) in sorted(planned_sha)}, **stage_summary},
            file_digests=ledger_mod.digests(unit),
            counts={"written": len(writes), "removed": len(deletable), "assessment": {k: len(v) for k, v in (stage_summary.get("assessment") or {}).items()}})
        ledger_mod.write(catalog_root, client_dir_name, new_ledger)
        txn.fault("after_ledger")
    except Exception as exc:                     # noqa: BLE001 — una caída real (BaseException) NO pasa por acá: la recupera el journal
        code = {"catalog": "CATALOG_GENERATION_FAILED", "verify": "VERIFY_FAILED", "assessment": "ASSESSMENT_FAILED", "ledger": "APPLY_FAILED"}.get(stage, "APPLY_FAILED")
        if stage == "assessment":
            code = str(getattr(exc, "code", "") or code)
            failures = list(getattr(exc, "failures", []) or [])
        _say(f"ERROR: {code} - {type(exc).__name__}", file=sys.stderr)
        stages.update({"apply": "FAILED", "catalog": "FAILED" if stage == "catalog" else "SKIPPED", "assessment": "FAILED" if stage == "assessment" else "SKIPPED"})
        residue = undo.restore()
        txn.call_hook(hooks, "after_rollback", ctx)
        if residue:
            keep_closed["on"] = True
            publication_gate.mark_uncertain(op_id, client_dir_name, catalog_root)
            return _safe_failure(req, base, finish, txn.RECOVERY_REQUIRED, "RECOVERY_REQUIRED",
                                 "La publicación falló y la restauración no pudo verificarse por completo: no se publica hasta resolverlo.", operation_id=op_id, stages=stages)
        journal.update(txn.J_ROLLED_BACK, error_code=code)
        shutil.rmtree(txn.undo_dir(catalog_root, op_id), ignore_errors=True)
        _say("Estado anterior RESTAURADO y verificado.")
        message = {"CATALOG_GENERATION_FAILED": "La extracción terminó, pero falló la regeneración del catálogo: se restauró el estado anterior.",
                   "VERIFY_FAILED": "La publicación falló y se restauró el estado anterior del Catálogo."}.get(code)
        return _safe_failure(req, base, finish, txn.ROLLED_BACK, code, message or "La publicación falló y se restauró el estado anterior del Catálogo.",
                             operation_id=op_id, stages=stages, failures=failures)
    stages.update({"apply": "OK", "catalog": "OK" if catalog_generated else "SKIPPED", "assessment": "OK" if hooks is not None else "SKIPPED"})

    # ── 4. confirmación DURABLE: la versión nueva NO es vigente hasta que Spaces la confirma (cuerpo + marcador al final). Hasta entonces la compuerta sigue cerrada ──
    unit = ledger_mod.unit_files(catalog_root, csn_cache_root, client_dir_name, stage_dir)
    removed_keys: dict[str, Path] = {}
    if want_remote:
        for gone in undo.removed_paths():
            k = _key_of(dirs, gone)
            if k:
                removed_keys[k] = gone
        for k in pr.orphans(listing, prefix_keys, unit):
            p = _path_of(dirs, k)
            if p is not None and k not in removed_keys and len(removed_keys) < _MAX_REMOTE_DELETES:
                removed_keys[k] = p
        unit_keyed = {k: p for k, p in unit.items() if k.split("/", 1)[0] in persisted}
        ledger_key = _key_of(dirs, ledger_file)
        if ledger_key:
            unit_keyed[ledger_key] = ledger_file                                    # el marcador de activación: se confirma el ÚLTIMO
    else:
        unit_keyed = {}
    journal.update(txn.J_COMMIT_UNCERTAIN if want_remote else txn.J_LOCAL_COMMITTED, changed=changed,
                   unit={"keyed": {k: str(p) for k, p in {**unit_keyed, **removed_keys}.items()}, "markers": list(markers), "persisted": list(persisted)})
    txn.fault("after_local_commit")
    durability = txn.DURABILITY_NOT_REQUESTED
    remote_note = ""
    if want_remote:
        txn.fault("before_remote")
        _say("\n==========================================")
        _say("PERSISTENCIA EN DIGITALOCEAN SPACES (confirmación durable)")
        _say("==========================================")
        base.storage_enabled = True
        outcome = pr.confirm(remote_store, persisted, markers, unit_keyed, removed_keys, ledger_file)
        txn.fault("after_remote")
        if outcome.status == pr.CONFIRMED:
            durability = txn.DURABILITY_REMOTE
            stages["remote"] = "OK"
            journal.update(txn.J_REMOTE_CONFIRMED, durability=durability, reconciled=outcome.reconciled)
        elif outcome.status == pr.FAILED:
            # Se leyó el remoto y se verificó que NO refleja la versión: se mantiene la última versión confirmada (local y remoto vuelven a la previa).
            stages["remote"] = "FAILED"
            if hold is not None:
                hold.fail()
            residue = undo.restore()
            txn.call_hook(hooks, "after_rollback", ctx)
            if residue:
                keep_closed["on"] = True
                journal.update(txn.J_COMMIT_UNCERTAIN)
                publication_gate.mark_uncertain(op_id, client_dir_name, catalog_root)
                return _safe_failure(req, base, finish, txn.RECOVERY_REQUIRED, "RECOVERY_REQUIRED", "No se pudo restaurar la versión previa: no se publica hasta resolverlo.",
                                     operation_id=op_id, stages=stages)
            journal.update(txn.J_REMOTE_DIVERGED, local_restored=True)
            back = pr.converge(remote_store, {**unit_keyed, **removed_keys}, last=[k for k, p in unit_keyed.items() if p.name in markers])
            if back.status == pr.CONFIRMED:
                journal.update(txn.J_ROLLED_BACK, error_code="PERSISTENCE_FAILED")
                shutil.rmtree(txn.undo_dir(catalog_root, op_id), ignore_errors=True)
                return _safe_failure(req, base, finish, txn.ROLLED_BACK, "PERSISTENCE_FAILED",
                                     "No se pudo confirmar la publicación en Spaces: se mantuvo la última versión confirmada.", operation_id=op_id, stages=stages)
            return _safe_failure(req, base, finish, txn.RECOVERY_REQUIRED, "PERSISTENCE_DIVERGED",
                                 "La versión previa quedó activa en local, pero Spaces todavía no la refleja: se reintenta; no se publica hasta resolverlo.", operation_id=op_id, stages=stages)
        else:
            # No se pudo leer/verificar el remoto: ni se confirma ni se deshace. Los lectores NO ven la versión nueva: la compuerta queda cerrada hasta resolverlo.
            stages["remote"] = "UNCERTAIN"
            keep_closed["on"] = True
            if hold is not None:
                hold.fail()
            journal.update(txn.J_COMMIT_UNCERTAIN, durability=txn.DURABILITY_UNCONFIRMED)
            publication_gate.mark_uncertain(op_id, client_dir_name, catalog_root)
            return _safe_failure(req, base, finish, txn.RECOVERY_REQUIRED, "PERSISTENCE_UNCERTAIN",
                                 "No se pudo verificar en Spaces si la publicación quedó guardada: la lectura queda bloqueada hasta resolverlo.", operation_id=op_id, stages=stages)
    else:
        stages["remote"] = "SKIPPED"
        durability = txn.DURABILITY_LOCAL_BY_CONFIG if req.mirror_up else txn.DURABILITY_NOT_REQUESTED
        journal.update(txn.J_LOCAL_COMMITTED, durability=durability)
    shutil.rmtree(txn.undo_dir(catalog_root, op_id), ignore_errors=True)
    txn.call_hook(hooks, "finished", ctx)                                           # cachés fuera ANTES de abrir la compuerta: los lectores ven SOLO la versión nueva

    # ── 5. resultado: SUCCESS ⇔ COMPLETED ──
    files_by_type = {t: sum(1 for _ in (client_cache_dir / t).glob("*.json")) if (client_cache_dir / t).is_dir() else 0 for t in OBJECT_TYPES}
    base.ok = False
    base.client, base.space, base.analytic_model = req.client, space, req.analytic_model
    base.root_object, base.root_type = exporter.root_object, exporter.root_type
    base.objects, base.edges, base.external_sources = len(exporter.nodes), len(exporter.edges), len(exporter.external_sources)
    base.exported_files, base.files_by_type = sum(files_by_type.values()), {k: v for k, v in files_by_type.items() if v}
    base.deleted_stale, base.retained_shared = len(deletable), len(retained_shared)
    base.pool_dir, base.manifest_path, base.catalog_generated = client_cache_dir, manifest_path, catalog_generated
    base.operation_id, base.durability, base.stages, base.observations, base.client_dir, base.outcome = op_id, durability, stages, observations, client_dir_name, "CHANGED"
    base.deferred_reasons = list(reasons) if ((stale and not deletes_allowed) or own_unknown) else []
    base.applied = {"added": [f"{k[0]}/{k[1]}" for k, _, _ in writes if k not in previous_objects],
                    "modified": [f"{k[0]}/{k[1]}" for k, _, _ in writes if k in previous_objects],
                    "removed": [f"{k[0]}/{k[1]}" for k in deletable], "retained_shared": [f"{k[0]}/{k[1]}" for k in retained_shared], "changed": changed,
                    "remote_removed": sorted(removed_keys), **stage_summary}
    base.diagnostics = diagnostics.snapshot() if diagnostics.enabled else None
    if base.deferred_reasons:
        base.status, base.error_code, base.exit_code = txn.COMMITTED_CLEANUP_DEFERRED, "CLEANUP_DEFERRED", 3
        base.message = "Publicado, pero no se limpiaron objetos obsoletos (no se pudo verificar la propiedad): no se borró nada."
    else:
        base.status, base.ok, base.exit_code = txn.COMPLETED, True, 0
    _say("\nEXTRACCION DE LINAJE FINALIZADA")
    _say(f"Cliente: {req.client}")
    _say(f"Space: {space}")
    _say(f"Objeto raíz: {exporter.root_object}")
    _say(f"Tipo raíz: {exporter.root_type}")
    _say(f"Objetos incluidos: {len(exporter.nodes)}")
    _say(f"Obsoletos eliminados: {len(deletable)}")
    _say(f"Compartidos retenidos: {len(retained_shared)}")
    _say(f"Catálogo materializado: {'SI' if catalog_generated else 'NO'}")
    _say(f"Persistencia canónica: {'OK' if durability == txn.DURABILITY_REMOTE else 'NO CONFIRMADA (' + durability + ')'}")
    _say(f"ESTADO DE LA PUBLICACIÓN: {base.status}")
    _say("STATUS: SUCCESS" if base.status == txn.COMPLETED else f"STATUS: {base.status}")
    return finish(base)


_MAX_REMOTE_DELETES = 500                  # tope de borrados remotos por publicación (red de seguridad: un error de cálculo no puede vaciar el bucket)


def _verify_applied(txn, planned_sha: dict, client_cache_dir: Path, manifest_path: Path, client_catalog_dir: Path, *, require_flows: bool) -> list[str]:
    problems: list[str] = []
    for key, sha in planned_sha.items():
        path = _pool_path(client_cache_dir, key)
        if not path.is_file() or txn.sha256_text_file(path) != sha:
            problems.append(f"objeto {key[0]}/{key[1]}")
    loaded = txn.load_manifest(manifest_path)
    if not loaded.usable:
        problems.append(f"manifiesto {loaded.state}")
    else:
        for obj in loaded.data.get("objects") or []:
            sha = obj.get("sha256")
            key = (obj.get("object_type"), obj.get("technical_name"))
            if sha and key in planned_sha and planned_sha[key] != sha:
                problems.append(f"manifiesto≠pool {key[0]}/{key[1]}")
    if require_flows and not any(client_catalog_dir.rglob("flujo_*.md")):
        problems.append("catálogo sin flujos")
    return problems



def run_export(req: ExportRequest) -> ExportResult:
    """Run one extraction without reading argv or exiting the process.

    The caller owns business orchestration. In particular, alternate output roots
    can be used for ephemeral assessments, while canonical roots can be used for
    governed publication. ``generate_catalog`` and ``mirror_up`` are explicit
    orchestration decisions and are not inferred by lineage traversal.
    """
    started_at = time.perf_counter()
    safe_flag = {"on": False}

    def finish(result: ExportResult) -> ExportResult:
        result.duration_s = round(time.perf_counter() - started_at, 1)
        if safe_flag["on"] and not result.ok and not result.status:
            result.status = "FAILED_NO_CHANGE"          # publicación segura: una falla previa a APPLY no modificó nada
        return result

    list_spaces_only = req.space.strip().lower() == SPACE_LIST_SENTINEL
    root_object = (
        ""
        if list_spaces_only
        else (req.root_object or req.analytic_model or "").strip()
    )

    if not list_spaces_only and req.root_object and req.analytic_model:
        msg = "ROOT_ARGUMENT_CONFLICT - Use root_object o analytic_model, no ambos."
        _say(f"ERROR: {msg}")
        return finish(ExportResult(ok=False, error_code="ROOT_ARGUMENT_CONFLICT", message=msg, client=req.client, space=req.space, exit_code=2))
    if not list_spaces_only and not root_object:
        msg = "ROOT_OBJECT_REQUIRED - Debe informarse root_object/analytic_model."
        _say(f"ERROR: {msg}")
        return finish(ExportResult(ok=False, error_code="INVALID_PARAMETER", message=msg, client=req.client, space=req.space, exit_code=2))
    if req.mode not in {"assessment", "publish"}:
        msg = f"INVALID_MODE - Modo no soportado: {req.mode}"
        _say(f"ERROR: {msg}")
        return finish(ExportResult(ok=False, error_code="INVALID_PARAMETER", message=msg, client=req.client, space=req.space, exit_code=2))

    legacy_root = bool(req.analytic_model)
    base = ExportResult(
        ok=False, client=req.client, space=req.space,
        analytic_model=req.analytic_model, root_object=root_object,
        root_type=req.root_type or ("analytic-model" if legacy_root else None),
        mode=req.mode,
    )

    project_root = Path(__file__).resolve().parents[2]
    try:
        launcher = resolve_datasphere_cli_launcher(project_root)
    except DatasphereCliError as exc:
        _say(f"ERROR: {exc}", file=sys.stderr)
        base.error_code = "DATASPHERE_CLI_UNAVAILABLE"
        base.message = str(exc)
        base.exit_code = 2
        return finish(base)

    diagnostics = ExtractionDiagnostics(req.diagnostics)

    if list_spaces_only:
        with tempfile.TemporaryDirectory(prefix="dsp-space-discovery-") as tmp:
            cli = DatasphereCli(
                host=req.host,
                client_id=req.client_id,
                client_secret=req.client_secret,
                token_url=req.token_url,
                work_dir=Path(tmp),
                diagnostics=diagnostics,
                launcher=launcher,
            )
            try:
                _say("\n==========================================")
                _say("PRECHECK DE CONEXION Y AUTORIZACION")
                _say("==========================================")
                cli.oauth_preflight()

                _say("\nLogging in to SAP Datasphere...")
                cli.login()
                _say("Login OK")

                spaces = cli.list_spaces()

                _say("\n==========================================")
                _say("SPACE IDs DISPONIBLES")
                _say("==========================================")
                for space_id in spaces:
                    _say(f"  {space_id}")
                _say("------------------------------------------")
                _say(f"Total: {len(spaces)}")
                _say("==========================================")

                base.ok = True
                base.message = f"{len(spaces)} Space IDs disponibles."
                base.exit_code = 0
                if diagnostics.enabled:
                    base.diagnostics = diagnostics.snapshot()
                return finish(base)

            except DatasphereCliError as exc:
                _say(f"ERROR: {exc}")
                base.error_code = classify_error(str(exc))
                base.message = str(exc)
                base.exit_code = 1
                if diagnostics.enabled:
                    base.diagnostics = diagnostics.snapshot()
                return finish(base)

    catalog_root = Path(
        req.catalog_root or os.getenv("DANWOR_CATALOG_DIR") or (project_root / "catalog_local")
    ).resolve()
    csn_cache_root = Path(
        req.csn_cache_dir or os.getenv("DANWOR_CATALOG_CSN_CACHE") or (catalog_root / ".csn_cache")
    ).resolve()

    client_dir_name = _safe_path_component(req.client)
    if not client_dir_name:
        _say("ERROR: --client cannot be empty", file=sys.stderr)
        base.error_code = "INVALID_PARAMETER"
        base.message = "client cannot be empty"
        base.exit_code = 2
        return finish(base)

    safe = bool(req.mode == "publish" and (req.safe if req.safe is not None else _txn().safe_mode_enabled()))
    safe_flag["on"] = safe
    if safe:                                          # un cliente es UNA identidad aunque se escriba con otra capitalización (P1): se reusa el directorio existente o se rechaza el conflicto
        txn = _txn()
        try:
            client_dir_name = txn.canonical_client_dir(client_dir_name, txn.existing_client_dirs(catalog_root, csn_cache_root))
        except txn.ClientKeyConflict as exc:
            _say("ERROR: CLIENT_KEY_CONFLICT - el Catálogo tiene más de una variante del cliente: " + ", ".join(exc.variants), file=sys.stderr)
            base.error_code, base.message, base.exit_code = "CLIENT_KEY_CONFLICT", "CLIENT_KEY_CONFLICT", 1
            base.failures = [{"kind": "client_key", "object_type": "", "technical_name": v, "code": "CASE_VARIANT"} for v in exc.variants]
            return finish(base)
    base.client_dir = client_dir_name

    client_cache_dir = csn_cache_root / client_dir_name
    _say("Extraction integration:")
    _say(f"  Client:       {req.client}")
    _say(f"  Mode:         {req.mode.upper()}")
    _say(f"  Catalog root: {catalog_root}")
    _say(f"  CSN cache:    {csn_cache_root}")
    _say(f"  Client pool:  {client_cache_dir}")

    try:
        requested_features = _parse_features(req.features_requested)
    except DatasphereCliError as exc:
        _say(f"ERROR: {exc}")
        base.error_code = classify_error(str(exc))
        base.message = str(exc)
        base.exit_code = 2
        return finish(base)

    with tempfile.TemporaryDirectory(prefix="dsp-lineage-") as tmp:
        cli = DatasphereCli(
            host=req.host, client_id=req.client_id, client_secret=req.client_secret,
            token_url=req.token_url, work_dir=Path(tmp), diagnostics=diagnostics,
            launcher=launcher,
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
                analytic_model=root_object if legacy_root else None,
                root_object=None if legacy_root else root_object,
                output_dir=client_cache_dir,
                requested_features=requested_features,
                root_type=req.root_type or ("analytic-model" if legacy_root else None),
                mode=req.mode,
                allowed_root_types=req.allowed_root_types,
                diagnostics=diagnostics,
            )
            exported_pool = exporter.extract() if safe else exporter.run()
        except RootTypeConstraintError as exc:
            _say(f"ERROR: {exc}")
            base.error_code = "ROOT_TYPE_CONSTRAINT_FAILED"
            base.message = str(exc)
            base.exit_code = 1
            if 'exporter' in locals():
                base.root_type = exporter.root_type
                base.requested_features = sorted(exporter.requested_features)
                base.effective_features = sorted(exporter.effective_features)
            if diagnostics.enabled:
                base.diagnostics = diagnostics.snapshot()
            return finish(base)
        except DatasphereCliError as exc:
            _say(f"ERROR: {exc}")
            base.error_code = classify_error(str(exc))
            if safe and base.error_code in ("ROOT_OBJECT_NOT_FOUND", "AMBIGUOUS_ROOT_OBJECT_TYPE") and "exporter" in locals() and exporter.failures:
                base.error_code = "EXTRACTION_INCOMPLETE"             # el «no existe» es consecuencia de un listado fallido, no del Space
                base.failures = list(exporter.failures)
            base.message = str(exc)
            base.exit_code = 1
            if diagnostics.enabled:
                base.diagnostics = diagnostics.snapshot()
            return finish(base)

    if safe:
        return _publish_safe(req, exporter, base=base, client_dir_name=client_dir_name, catalog_root=catalog_root, csn_cache_root=csn_cache_root,
                             client_cache_dir=client_cache_dir, diagnostics=diagnostics, finish=finish)

    assert exporter.root_type
    base.root_type = exporter.root_type
    base.requested_features = sorted(exporter.requested_features)
    base.effective_features = sorted(exporter.effective_features)

    diagnostics_dir = _canonical_run_dir(
        catalog_root, client_dir_name, req.space, exporter.root_type, exporter.root_object
    )
    manifest_path = diagnostics_dir / "lineage-manifest.json"
    previous_manifest, migrated_from = _load_previous_manifest_with_migration_candidates(
        catalog_root=catalog_root,
        client_dir_name=client_dir_name,
        space=req.space,
        root_type=exporter.root_type,
        root_object=exporter.root_object,
        canonical_manifest_path=manifest_path,
    )

    current_objects = {
        (node.object_type, node.technical_name)
        for node in exporter.nodes.values()
        if node.object_type in OBJECT_TYPES
    }
    client_runs_root = catalog_root / ".lineage_runs" / client_dir_name
    excluded = [manifest_path]
    if migrated_from:
        excluded.append(migrated_from)
    other_owned_objects = _other_manifest_ownership(client_runs_root, excluded)
    deleted_stale, retained_shared = _cleanup_stale_owned_objects(
        client_cache_dir=client_cache_dir,
        previous_manifest=previous_manifest,
        current_objects=current_objects,
        other_owned_objects=other_owned_objects,
    )

    _write_manifest_v2(
        path=manifest_path,
        client=req.client,
        space=req.space,
        exporter=exporter,
        mode=req.mode,
        deleted_stale=deleted_stale,
        retained_shared=retained_shared,
    )

    if migrated_from and migrated_from != manifest_path:
        try:
            migrated_from.unlink()
            try:
                migrated_from.parent.rmdir()
            except OSError:
                pass
            _say(f"Manifest V1 migrado: {migrated_from}")
        except OSError as exc:
            _say(f"WARN no se pudo retirar manifest V1: {exc}")

    files_by_type = {
        t: sum(1 for _ in (client_cache_dir / t).glob("*.json"))
        if (client_cache_dir / t).is_dir() else 0
        for t in OBJECT_TYPES
    }
    result = ExportResult(
        ok=True, client=req.client, space=req.space, analytic_model=req.analytic_model,
        root_object=exporter.root_object, root_type=exporter.root_type, mode=req.mode,
        requested_features=sorted(exporter.requested_features),
        effective_features=sorted(exporter.effective_features),
        objects=len(exporter.nodes), edges=len(exporter.edges),
        external_sources=len(exporter.external_sources),
        exported_files=sum(files_by_type.values()),
        files_by_type={k: v for k, v in files_by_type.items() if v},
        deleted_stale=deleted_stale, retained_shared=retained_shared,
        pool_dir=exported_pool, manifest_path=manifest_path,
        diagnostics=diagnostics.snapshot() if diagnostics.enabled else None,
    )

    if req.generate_catalog:
        _say("\n==========================================")
        _say("REGENERANDO CATALOGO EXISTENTE")
        _say("==========================================")
        try:
            summary = regenerate_catalog_from_pool(csn_cache_root, catalog_root)
            _say(summary)
            result.catalog_generated = True
        except Exception as exc:
            _say(f"ERROR: CATALOG_GENERATION_FAILED - {exc}", file=sys.stderr)
            result.ok = False
            result.error_code = "CATALOG_GENERATION_FAILED"
            result.message = f"{type(exc).__name__}: {exc}"
            result.exit_code = 1
            return finish(result)
    elif req.mode == "assessment":
        _say("\nCatálogo: SKIP - modo ASSESSMENT.")
    else:
        _say("\nCatálogo: SKIP - deshabilitado por el orquestador.")

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
                _say("WARN: remote_store is disabled; no se confirmó persistencia remota.")
        except Exception as exc:
            _say(f"ERROR: PERSISTENCE_FAILED - {exc}", file=sys.stderr)
            result.ok = False
            result.error_code = "STORAGE_UPLOAD_FAILED"
            result.message = f"{type(exc).__name__}: {exc}"
            result.exit_code = 1
            return finish(result)

    _say("\nEXTRACCION DE LINAJE FINALIZADA")
    _say(f"Cliente: {req.client}")
    _say(f"Space: {req.space}")
    _say(f"Objeto raíz: {exporter.root_object}")
    _say(f"Tipo raíz: {exporter.root_type}")
    _say(f"Objetos incluidos: {len(exporter.nodes)}")
    _say(f"Relaciones: {len(exporter.edges)}")
    _say(f"Fuentes externas: {len(exporter.external_sources)}")
    _say(f"Obsoletos eliminados: {deleted_stale}")
    _say(f"Compartidos retenidos: {retained_shared}")
    _say(f"Pool CSN: {exported_pool}")
    _say(f"Manifest diagnóstico: {manifest_path}")
    if req.mode == "publish":
        _say(f"Catálogo materializado: {'SI' if result.catalog_generated else 'NO'}")
        if req.mirror_up:
            _say(f"Persistencia canónica: {'OK' if result.storage_enabled else 'NO CONFIRMADA'}")
    else:
        _say("Publicación en catálogo: NO")
    _say(f"Total Runtime: {format_runtime(time.perf_counter() - started_at)}")      # duración MEDIDA por el backend (reloj monotónico), solo la extracción
    _say("STATUS: SUCCESS")
    return finish(result)

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export SAP Datasphere upstream lineage into the Danwor evidence/catalog pool."
    )
    parser.add_argument("--host", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--client-secret", required=True)
    parser.add_argument("--token-url", required=True)
    parser.add_argument("--space", required=True)
    roots = parser.add_mutually_exclusive_group(required=False)
    roots.add_argument("--root-object", help="Technical name of AM, View, Local Table or Remote Table.")
    roots.add_argument("--analytic-model", help="DEPRECATED alias for --root-object (Analytic Model).")
    parser.add_argument(
        "--features",
        default=",".join(FEATURES),
        help="Comma-separated acquisition families. Default: all.",
    )
    parser.add_argument(
        "--mode",
        choices=("assessment", "publish"),
        default="assessment",
        help="Orchestration intent. Default: assessment.",
    )
    parser.add_argument("--client", required=True, help="Cliente de catálogo/evidencia (ej. Pampa).")
    parser.add_argument("--catalog-root", default=None)
    parser.add_argument("--csn-cache-dir", default=None)
    parser.add_argument(
        "--no-generate-catalog",
        action="store_true",
        help="Compatibility override: never run generate_catalog even in publish mode.",
    )
    parser.add_argument(
        "--mirror-up",
        action="store_true",
        help="Force core.remote_store.mirror_up() after a successful publish.",
    )
    return parser.parse_args(argv)


def _resolve_cli_paths(args: argparse.Namespace, project_root: Path) -> tuple[Path, Path, bool]:
    """Return (catalog_root, csn_cache_root, ephemeral_destination)."""
    if args.mode == "assessment" and not args.catalog_root and not args.csn_cache_dir:
        # Standalone safe default. The web assessment orchestrator can (and should)
        # pass its own alternate roots. We intentionally do not use TemporaryDirectory
        # because downstream assessment may need the files after this process returns.
        base = Path(tempfile.mkdtemp(prefix="dsp-assessment-evidence-"))
        return base / "catalog_local", base / "catalog_local" / ".csn_cache", True

    catalog_root = Path(
        args.catalog_root
        or os.getenv("DANWOR_CATALOG_DIR")
        or (project_root / "catalog_local")
    ).resolve()
    csn_cache_root = Path(
        args.csn_cache_dir
        or os.getenv("DANWOR_CATALOG_CSN_CACHE")
        or (catalog_root / ".csn_cache")
    ).resolve()
    return catalog_root, csn_cache_root, args.mode == "assessment"


def main(argv: list[str] | None = None) -> int:
    """CLI adapter over the same run_export() contract used by the web app."""
    args = parse_args(argv)
    root_object = (args.root_object or args.analytic_model or "").strip()
    legacy_root = bool(args.analytic_model)
    project_root = Path(__file__).resolve().parents[2]
    catalog_root, csn_cache_root, ephemeral_destination = _resolve_cli_paths(args, project_root)

    if legacy_root:
        _say("WARN: --analytic-model está deprecated; use --root-object en nuevas integraciones.")
    _say(f"Destination policy: {'EPHEMERAL' if ephemeral_destination else 'CANONICAL'}")
    if args.mode == "assessment" and args.mirror_up:
        _say("WARN: --mirror-up ignorado en modo ASSESSMENT.")

    result = run_export(ExportRequest(
        host=args.host,
        client_id=args.client_id,
        client_secret=args.client_secret,
        token_url=args.token_url,
        space=args.space,
        analytic_model=root_object if legacy_root else "",
        root_object="" if legacy_root else root_object,
        client=args.client,
        catalog_root=catalog_root,
        csn_cache_dir=csn_cache_root,
        generate_catalog=(args.mode == "publish" and not args.no_generate_catalog),
        mirror_up=bool(args.mirror_up and args.mode == "publish"),
        features_requested=args.features,
        mode=args.mode,
    ))
    return 0 if result.ok else (result.exit_code or 1)


if __name__ == "__main__":
    raise SystemExit(main())
