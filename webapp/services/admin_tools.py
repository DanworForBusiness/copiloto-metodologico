"""
admin_tools.py
==============
Capa fina para las "Herramientas de gestión" (Analítica interna) del panel
diagnóstico. Orquesta in-process — sin subprocess y sin BD — las utilidades que
en el Streamlit original vivían en ui/tools_panel.py.

Dos familias:

1. Export runtime — empaqueta los directorios efímeros (feedback_log, activity_log,
   case_library) en un ZIP descargable antes de un redeploy/reinicio en DO. Copia
   fiel de las funciones de legacy:ui/tools_panel.py.

2. Análisis / propuestas — reusa los scripts CLI de la raíz (analyze_cases.py,
   analyze_feedback.py, propose_tree_updates.py, propose_content_updates.py)
   ejecutando su main() in-process y capturando su salida (el mismo texto que el
   legacy mostraba al correrlos por subprocess). No se duplica lógica ni se tocan
   los scripts; los flags se pasan por argv.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import threading
import zipfile
from datetime import datetime
from pathlib import Path

from config import ACTIVITY_LOG_DIR, CASE_LIBRARY_DIR, FEEDBACK_LOG_DIR


# =========================================================
# 1. Export runtime  (copia fiel de legacy:ui/tools_panel.py)
# =========================================================

_RUNTIME_EXPORT_DIRS = (
    ("feedback_log", FEEDBACK_LOG_DIR),
    ("activity_log", ACTIVITY_LOG_DIR),
    ("case_library", CASE_LIBRARY_DIR),
)


def _format_bytes(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{num_bytes} B"


def _iter_runtime_files(root: Path) -> list[Path]:
    if not root.exists() or not root.is_dir():
        return []
    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    )


def runtime_inventory() -> tuple[list[dict], int, int, float | None]:
    """Inventario por carpeta: filas + totales + mtime más reciente."""
    rows: list[dict] = []
    total_files = 0
    total_size = 0
    latest_mtime: float | None = None

    for label, root in _RUNTIME_EXPORT_DIRS:
        files = _iter_runtime_files(root)
        size = sum(p.stat().st_size for p in files)
        mtimes = [p.stat().st_mtime for p in files]
        latest = max(mtimes) if mtimes else None
        rows.append({
            "carpeta": label,
            "path": str(root),
            "archivos": len(files),
            "tamano": _format_bytes(size),
            "ultimo_cambio": (
                datetime.fromtimestamp(latest).strftime("%Y-%m-%d %H:%M:%S")
                if latest else "-"
            ),
        })
        total_files += len(files)
        total_size += size
        if latest is not None:
            latest_mtime = latest if latest_mtime is None else max(latest_mtime, latest)

    return rows, total_files, total_size, latest_mtime


def runtime_export_zip() -> bytes:
    """ZIP en memoria con manifest.json + los archivos de cada carpeta runtime."""
    rows, total_files, total_size, latest_mtime = runtime_inventory()
    generated_at = datetime.now().isoformat(timespec="seconds")
    manifest = {
        "generated_at": generated_at,
        "total_files": total_files,
        "total_size_bytes": total_size,
        "latest_mtime": latest_mtime,
        "folders": rows,
        "note": "Export manual de archivos runtime locales/efimeros antes de redeploy o reinicio.",
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for label, root in _RUNTIME_EXPORT_DIRS:
            for path in _iter_runtime_files(root):
                zf.write(path, arcname=str(Path(label) / path.relative_to(root)))
    return buffer.getvalue()


def runtime_export_filename() -> str:
    return f"danwor-runtime-export-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"


# =========================================================
# 2. Análisis / propuestas  (reusan los scripts CLI in-process)
# =========================================================

# Los scripts parsean sys.argv y escriben por stdout. Parcheamos argv y
# capturamos stdout para correr su main() exacta sin tocarlos ni duplicar lógica.
# Un lock evita pisar sys.argv entre requests concurrentes (uso admin, baja
# concurrencia, pero el lock lo deja determinístico).
_RUN_LOCK = threading.Lock()


def _capture_main(module, argv: list[str]) -> str:
    buffer = io.StringIO()
    with _RUN_LOCK:
        original_argv = sys.argv
        sys.argv = [getattr(module, "__name__", "script"), *argv]
        try:
            with contextlib.redirect_stdout(buffer):
                try:
                    module.main()
                except SystemExit:
                    # Los scripts hacen sys.exit(0) cuando no hay datos.
                    pass
                except Exception as exc:  # noqa: BLE001 — se reporta al panel
                    buffer.write(f"\n[ERROR] {type(exc).__name__}: {exc}\n")
        finally:
            sys.argv = original_argv
    return buffer.getvalue().strip() or "(sin salida)"


def run_cases_analysis(use_llm: bool = False) -> str:
    import analyze_cases
    return _capture_main(analyze_cases, ["--llm"] if use_llm else [])


def run_feedback_analysis(kind: str = "all") -> str:
    import analyze_feedback
    if kind not in {"ia", "trees", "missing", "all"}:
        kind = "all"
    return _capture_main(analyze_feedback, ["--type", kind])


def run_tree_proposals(apply: bool = False) -> str:
    import propose_tree_updates
    return _capture_main(propose_tree_updates, ["--apply"] if apply else [])


def run_content_proposals(use_llm: bool = False) -> str:
    import propose_content_updates
    return _capture_main(propose_content_updates, ["--llm"] if use_llm else [])
