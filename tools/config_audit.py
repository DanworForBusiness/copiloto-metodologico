"""
config_audit.py — Config Governance, read-only preflight auditor.

Compares three things and reports drift/gaps between them:
  1. Variable NAMES actually read by `config.py`, discovered by STATIC AST
     analysis of the file — never a hand-duplicated list. This is the only
     source of truth for "what does the code read"; if config.py changes,
     this discovery changes with it automatically.
  2. Keys declared in `.env.example` (the public, versioned contract).
  3. Keys present in a target `.env` file (default: the canonical local
     `.env`, override with `--env-file`).

Classification (semantics, not values): a SMALL metadata map below
(`VAR_METADATA`) covers only the variables whose local-relevance can't be
inferred from code alone — e.g. Spaces is intentionally disabled in any
local dev environment, several SharePoint/Odoo/Human Capital variables are
optional capabilities that fail closed/soft rather than crash. Every other
variable is classified by a simple, conservative rule from its own code
default (see `classify()`): a non-empty literal default means the
variable always resolves to a working value (informationally REQUIRED_LOCAL,
but never actually "missing"); an empty-string default with no entry in
VAR_METADATA defaults to CAPABILITY_OPTIONAL, so an unclassified variable
never fails preflight by accident.

NEVER imports `webapp/` (this must never boot app machinery to run) and
NEVER prints or logs actual `.env` VALUES — only variable names and
computed statuses. `PROTECTED`-style secret values are looked up only to
ask "is this key present", never displayed.

Usage:
    python tools/config_audit.py [--env-file PATH] [--format text|json]

Exit code:
    0   no REQUIRED_MISSING and no contractual drift (DEPRECATED/UNUSED)
    1   otherwise
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CONFIG_PY = _REPO_ROOT / "config.py"
_ENV_EXAMPLE = _REPO_ROOT / ".env.example"
_DEFAULT_ENV_FILE = Path(r"C:\Users\Joe\ArquitectoDanwor\.env")

_ENV_READING_CALLS = {"_env", "_csv_env", "getenv"}

CAPABILITIES: tuple[str, ...] = (
    "Core Hub",
    "Graph",
    "Spaces",
    "Human Capital",
    "Commercial",
    "Operations",
)

REQUIRED_LOCAL = "REQUIRED_LOCAL"
REQUIRED_DEPLOYMENT = "REQUIRED_DEPLOYMENT"
CAPABILITY_OPTIONAL = "CAPABILITY_OPTIONAL"
INTENTIONALLY_UNAVAILABLE_LOCAL = "INTENTIONALLY_UNAVAILABLE_LOCAL"

STATUS_PRESENT = "PRESENT"
STATUS_REQUIRED_MISSING = "REQUIRED_MISSING"
STATUS_OPTIONAL_MISSING = "OPTIONAL_MISSING"
STATUS_INTENTIONALLY_UNAVAILABLE = "INTENTIONALLY_UNAVAILABLE"
STATUS_DEPRECATED = "DEPRECATED"
STATUS_UNUSED = "UNUSED"

# Variables whose VALUE must never be echoed even incidentally (defensive;
# this tool never prints values for ANY variable, secret or not — this set
# only drives the "# SECRET" marker in reports/.env.example generation).
SECRET_VARS: frozenset[str] = frozenset({
    "SESSION_SECRET", "SPACES_KEY", "SPACES_SECRET", "ODOO_API_KEY",
})

# --- Metadata: (capability, classification) for variables whose local
# relevance can't be inferred from config.py's own default alone. SEMANTICS
# ONLY — never a value. Deliberately small: everything else falls back to
# `classify()`'s inference rule. ------------------------------------------
VAR_METADATA: dict[str, tuple[str, str]] = {
    # Graph / core login — real, working code defaults today, but the
    # CAPABILITY is genuinely required for local login to work at all.
    "AZURE_CLIENT_ID": ("Graph", REQUIRED_LOCAL),
    "AZURE_TENANT_ID": ("Graph", REQUIRED_LOCAL),

    # Core Hub
    "SESSION_SECRET": ("Core Hub", REQUIRED_LOCAL),
    "SESSION_COOKIE_SAMESITE": ("Core Hub", REQUIRED_DEPLOYMENT),
    "SESSION_COOKIE_SECURE": ("Core Hub", REQUIRED_DEPLOYMENT),
    "DESAFIO_CLOSE_URL": ("Core Hub", REQUIRED_DEPLOYMENT),
    "HUB_METRICS_ALLOWED_USERS": ("Core Hub", CAPABILITY_OPTIONAL),
    "HUB_METRICS_ENABLED_VERTICALS": ("Core Hub", REQUIRED_DEPLOYMENT),

    # Spaces persistence — intentionally absent in ANY local dev environment;
    # config.py's own comment documents this explicitly (fail-soft no-op).
    "SPACES_ENDPOINT": ("Spaces", INTENTIONALLY_UNAVAILABLE_LOCAL),
    "SPACES_BUCKET": ("Spaces", INTENTIONALLY_UNAVAILABLE_LOCAL),
    "SPACES_KEY": ("Spaces", INTENTIONALLY_UNAVAILABLE_LOCAL),
    "SPACES_SECRET": ("Spaces", INTENTIONALLY_UNAVAILABLE_LOCAL),
    "SPACES_PREFIX": ("Spaces", INTENTIONALLY_UNAVAILABLE_LOCAL),
    "SPACES_REGION": ("Spaces", REQUIRED_DEPLOYMENT),
    "SPACES_SYNC_INTERVAL": ("Spaces", REQUIRED_DEPLOYMENT),
    "SPACES_PERSIST_PATHS": ("Spaces", REQUIRED_DEPLOYMENT),

    # Human Capital — feature degrades to CONFIG_INCOMPLETE, never crashes.
    "HUMAN_CAPITAL_ALLOWED_USERS": ("Human Capital", CAPABILITY_OPTIONAL),
    "HC_SHAREPOINT_SITE_ID": ("Human Capital", CAPABILITY_OPTIONAL),
    "HC_SHAREPOINT_DRIVE_ID": ("Human Capital", CAPABILITY_OPTIONAL),
    "HC_CV_ROOT_ITEM_ID": ("Human Capital", CAPABILITY_OPTIONAL),
    "HC_CV_ROOT_PATH": ("Human Capital", CAPABILITY_OPTIONAL),
    "HC_CATALOG_ITEM_ID": ("Human Capital", CAPABILITY_OPTIONAL),
    "HC_DOCUMENT_WRITE_SCOPE": ("Human Capital", CAPABILITY_OPTIONAL),

    # Commercial
    "COMMERCIAL_ALLOWED_USERS": ("Commercial", CAPABILITY_OPTIONAL),
    "CONTENT_OWNER": ("Commercial", CAPABILITY_OPTIONAL),
    "ODOO_URL": ("Commercial", CAPABILITY_OPTIONAL),
    "ODOO_DB": ("Commercial", CAPABILITY_OPTIONAL),
    "ODOO_USERNAME": ("Commercial", CAPABILITY_OPTIONAL),
    "ODOO_API_KEY": ("Commercial", CAPABILITY_OPTIONAL),
    "DANWOR_COMMERCIAL_APP_TITLE": ("Commercial", REQUIRED_DEPLOYMENT),
    "DANWOR_COMMERCIAL_APP_SUBTITLE": ("Commercial", REQUIRED_DEPLOYMENT),

    # Operations / Reporting (SharePoint Cases/Catalog/Security/Staffing)
    "OPERATIONS_ALLOWED_USERS": ("Operations", CAPABILITY_OPTIONAL),
    "SHAREPOINT_CASES_ENABLED": ("Operations", REQUIRED_DEPLOYMENT),
    "SHAREPOINT_CASES_SITE_ID": ("Operations", REQUIRED_DEPLOYMENT),
    "SHAREPOINT_CASES_LIST_ID": ("Operations", REQUIRED_DEPLOYMENT),
    "SHAREPOINT_CASES_TOP_K": ("Operations", REQUIRED_DEPLOYMENT),
    "SHAREPOINT_CASES_MIN_SCORE": ("Operations", REQUIRED_DEPLOYMENT),
    "SHAREPOINT_CASES_CACHE_TTL": ("Operations", REQUIRED_DEPLOYMENT),
    "SHAREPOINT_CATALOG_CSN_URL": ("Operations", REQUIRED_DEPLOYMENT),
    "SHAREPOINT_SECURITY_ROLE_MATRIX_URL": ("Operations", REQUIRED_DEPLOYMENT),
    "SHAREPOINT_SECURITY_ROLE_MATRIX_CLIENT": ("Operations", REQUIRED_DEPLOYMENT),
    "SHAREPOINT_GESTION_SITE_ID": ("Operations", CAPABILITY_OPTIONAL),
    "SHAREPOINT_ASIGNACIONES_LIST_ID": ("Operations", REQUIRED_DEPLOYMENT),
    "SHAREPOINT_PROYECTOS_LIST_ID": ("Operations", REQUIRED_DEPLOYMENT),
    "SHAREPOINT_COLABORADORES_LIST_ID": ("Operations", REQUIRED_DEPLOYMENT),
    "SHAREPOINT_CLIENTE_LIST_ID": ("Operations", REQUIRED_DEPLOYMENT),
    "SHAREPOINT_HORAS_PROYECTOS_LIST_ID": ("Operations", REQUIRED_DEPLOYMENT),
    "SHAREPOINT_USER_INFORMATION_LIST_ID": ("Operations", REQUIRED_DEPLOYMENT),
    "SHAREPOINT_ASIGNACIONES_FIELD_COLABORADOR_LOOKUP": ("Operations", CAPABILITY_OPTIONAL),
    "SHAREPOINT_ASIGNACIONES_FIELD_PROYECTO_LOOKUP": ("Operations", CAPABILITY_OPTIONAL),
    "SHAREPOINT_ASIGNACIONES_FIELD_ALLOCATION": ("Operations", CAPABILITY_OPTIONAL),
    "SHAREPOINT_ASIGNACIONES_FIELD_STATUS": ("Operations", CAPABILITY_OPTIONAL),
    "SHAREPOINT_ASIGNACIONES_FIELD_START_DATE": ("Operations", CAPABILITY_OPTIONAL),
    "SHAREPOINT_ASIGNACIONES_FIELD_END_DATE": ("Operations", CAPABILITY_OPTIONAL),
    "SHAREPOINT_PROYECTOS_FIELD_TITLE": ("Operations", CAPABILITY_OPTIONAL),
    "SHAREPOINT_COLABORADORES_FIELD_TITLE": ("Operations", CAPABILITY_OPTIONAL),
    "SHAREPOINT_COLABORADORES_FIELD_DISPONIBILIDAD": ("Operations", CAPABILITY_OPTIONAL),
    "SHAREPOINT_PROYECTOS_FIELD_EMPRESA_LOOKUP": ("Operations", CAPABILITY_OPTIONAL),
    "SHAREPOINT_CLIENTE_FIELD_TITLE": ("Operations", CAPABILITY_OPTIONAL),
    "DANWOR_OPERATIONS_APP_TITLE": ("Operations", REQUIRED_DEPLOYMENT),
    "DANWOR_OPERATIONS_APP_SUBTITLE": ("Operations", REQUIRED_DEPLOYMENT),
}


class _EnvCallVisitor(ast.NodeVisitor):
    """Walks a config-module AST, collecting {VAR_NAME: literal_default_or_None}
    from every `_env(...)`/`_csv_env(...)`/`os.getenv(...)` call whose first
    argument is a string literal. Calls with a non-literal first argument
    (e.g. `os.getenv(name, default)` inside `_env`'s own body, where `name`
    is a parameter reference) are correctly skipped — they don't declare a
    NEW variable name, `_env`'s own callers already do that."""

    def __init__(self) -> None:
        self.found: dict[str, str | None] = {}

    def visit_Call(self, node: ast.Call) -> None:
        func_name = None
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr
        if func_name in _ENV_READING_CALLS and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                name = first.value
                default: str | None = None
                if len(node.args) > 1:
                    second = node.args[1]
                    if isinstance(second, ast.Constant) and isinstance(second.value, str):
                        default = second.value
                self.found.setdefault(name, default)
        self.generic_visit(node)


def discover_config_vars(config_path: Path = _CONFIG_PY) -> dict[str, str | None]:
    """Static AST discovery of every env-backed variable `config.py` reads,
    mapped to its literal default value (or `None` if the call has no
    literal-string default we can read statically). This is the single
    source of truth for "what config.py supports" — never hand-duplicated."""
    tree = ast.parse(config_path.read_text(encoding="utf-8"), filename=str(config_path))
    visitor = _EnvCallVisitor()
    visitor.visit(tree)
    return visitor.found


def read_env_keys(env_path: Path) -> tuple[set[str], bool]:
    """Keys declared in a `.env`-style file — NEVER returns/reads values.
    Returns (keys, file_existed). A missing file is treated as "zero keys
    present", not an error — the audit still runs, just reports everything
    plausible as absent."""
    if not env_path.exists():
        return set(), False
    from dotenv import dotenv_values
    return set(dotenv_values(str(env_path)).keys()), True


def classify(name: str, default: str | None) -> tuple[str, str]:
    """(capability, classification) for one variable. Metadata map wins;
    otherwise inferred conservatively from the code default alone (see
    module docstring)."""
    if name in VAR_METADATA:
        return VAR_METADATA[name]
    if default:
        return ("Core Hub", REQUIRED_LOCAL)
    return ("Core Hub", CAPABILITY_OPTIONAL)


def compute_status(*, effectively_present: bool, classification: str) -> str:
    """Status for one variable, given whether it resolves to SOME value
    (`.env` override OR non-empty code default) and its classification.
    `REQUIRED_DEPLOYMENT` absent in a LOCAL audit is intentionally treated
    like `CAPABILITY_OPTIONAL` for status/exit-code purposes — it is not a
    local problem — but the distinct classification stays visible in the
    per-variable detail line."""
    if effectively_present:
        return STATUS_PRESENT
    if classification == INTENTIONALLY_UNAVAILABLE_LOCAL:
        return STATUS_INTENTIONALLY_UNAVAILABLE
    if classification == REQUIRED_LOCAL:
        return STATUS_REQUIRED_MISSING
    return STATUS_OPTIONAL_MISSING


def audit(
    *, config_path: Path | None = None, env_path: Path | None = None,
    example_path: Path | None = None,
) -> dict:
    """Runs the full audit. Returns a JSON-serializable report dict — never
    contains any `.env` VALUE, only variable names/statuses/booleans.

    Defaults resolve the CURRENT module-level `_CONFIG_PY`/`_DEFAULT_ENV_FILE`/
    `_ENV_EXAMPLE` at call time (never bound at import time) so tests can
    `monkeypatch.setattr` those module globals and have it take effect —
    a bare `Path = _ENV_EXAMPLE`-style default parameter would freeze the
    value at function-definition time instead."""
    config_path = config_path if config_path is not None else _CONFIG_PY
    env_path = env_path if env_path is not None else _DEFAULT_ENV_FILE
    example_path = example_path if example_path is not None else _ENV_EXAMPLE
    discovered = discover_config_vars(config_path)
    discovered_names = set(discovered)
    env_keys, env_file_existed = read_env_keys(env_path)
    example_keys, example_file_existed = read_env_keys(example_path)

    variables = []
    for name, default in sorted(discovered.items()):
        capability, classification = classify(name, default)
        effectively_present = name in env_keys or bool(default)
        status = compute_status(effectively_present=effectively_present, classification=classification)
        variables.append({
            "name": name,
            "capability": capability,
            "classification": classification,
            "status": status,
            "secret": name in SECRET_VARS,
            "has_code_default": bool(default),
            "in_env_file": name in env_keys,
            "in_env_example": name in example_keys,
        })

    deprecated_in_env = sorted(env_keys - discovered_names)
    deprecated_in_example = sorted(example_keys - discovered_names)
    unused_in_example = deprecated_in_example  # same set: documented but not read by code

    required_missing = [v["name"] for v in variables if v["status"] == STATUS_REQUIRED_MISSING]
    has_drift = bool(deprecated_in_env or unused_in_example)
    exit_ok = not required_missing and not has_drift

    return {
        "env_file": str(env_path),
        "env_file_existed": env_file_existed,
        "example_file": str(example_path),
        "example_file_existed": example_file_existed,
        "variables": variables,
        "deprecated_in_env": deprecated_in_env,
        "unused_in_example": unused_in_example,
        "required_missing": required_missing,
        "exit_ok": exit_ok,
    }


def _format_text(report: dict) -> str:
    lines: list[str] = []
    lines.append(f"config_audit - env file: {report['env_file']}"
                 + ("" if report["env_file_existed"] else "  (not found - treated as empty)"))
    lines.append("")
    by_capability: dict[str, list[dict]] = {c: [] for c in CAPABILITIES}
    for v in report["variables"]:
        by_capability.setdefault(v["capability"], []).append(v)

    for capability in list(CAPABILITIES) + [c for c in by_capability if c not in CAPABILITIES]:
        vars_here = by_capability.get(capability) or []
        if not vars_here:
            continue
        lines.append(f"== {capability} ==")
        for v in vars_here:
            secret_tag = " [SECRET]" if v["secret"] else ""
            source = "" if v["status"] != STATUS_PRESENT else (
                " (.env)" if v["in_env_file"] else " (code default)"
            )
            lines.append(f"  {v['status']:<24} {v['name']}{secret_tag}  [{v['classification']}]{source}")
        lines.append("")

    if report["deprecated_in_env"]:
        lines.append("== DEPRECATED (present in .env, no longer read by config.py) ==")
        for name in report["deprecated_in_env"]:
            lines.append(f"  {STATUS_DEPRECATED:<24} {name}")
        lines.append("")

    if report["unused_in_example"]:
        lines.append("== UNUSED (documented in .env.example, no longer read by config.py) ==")
        for name in report["unused_in_example"]:
            lines.append(f"  {STATUS_UNUSED:<24} {name}")
        lines.append("")

    lines.append(f"REQUIRED_MISSING: {len(report['required_missing'])}"
                 + (f" -> {', '.join(report['required_missing'])}" if report["required_missing"] else ""))
    lines.append(f"Contractual drift (DEPRECATED+UNUSED): "
                 f"{len(report['deprecated_in_env']) + len(report['unused_in_example'])}")
    lines.append("VERDICT: " + ("OK" if report["exit_ok"] else "FAIL"))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env-file", type=Path, default=_DEFAULT_ENV_FILE,
                        help=f"Target .env to audit (default: {_DEFAULT_ENV_FILE})")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)

    report = audit(env_path=args.env_file)
    if args.format == "json":
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(_format_text(report))
    return 0 if report["exit_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
