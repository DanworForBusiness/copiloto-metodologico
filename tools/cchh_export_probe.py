#!/usr/bin/env python3
"""cchh_export_probe.py — MEDICIÓN CONTROLADA para la futura descarga de CVs (CCHH → Candidatos, Fase 2).

NO es la exportación: no la implementa, no se importa desde la aplicación y no escribe nada fuera de un directorio temporal que borra siempre. Solo STDLIB y UN archivo (se puede copiar y correr tal cual
en la consola de DigitalOcean, sin desplegar nada). Mide lo que hace falta para fijar un presupuesto de tiempo y límites con evidencia (ver docs/CCHH_EXPORTACION_CVS_MEDICION.md):

  --synthetic-bench   Sin token ni CVs: entorno (memoria, cgroup, disco temporal) y costo de construir un ZIP con datos SINTÉTICOS. No toca la red. No requiere autorización.
  --real              CON AUTORIZACIÓN EXPLÍCITA del owner: descarga 1–3 documentos reales por Graph (token delegado del propio usuario), los mete en un ZIP temporal y reporta tiempos.

Privacidad de lo que se REGISTRA (la salida es un JSON por stdout; no se persiste): solo tiempos, tamaños y códigos de resultado. Ni nombres, ni emails, ni URLs, ni identificadores de documento (los documentos
salen como «documento 1..3», en el orden en que se pasaron), ni el token, ni contenido; los nombres dentro del ZIP son fijos (`doc-N.bin`) y los mensajes de error son códigos, nunca el texto de la excepción.

LECTURA ACOTADA (a diferencia de `core.microsoft365.graph_get_bytes`, que lee todo en memoria sin tope): el tamaño se controla ANTES de pedir (tamaño declarado del registro), al recibir los encabezados
(Content-Length) y DURANTE la transferencia (se corta al superar el tope), con plazo total por documento además del timeout de socket. El contenido va a un archivo temporal por trozos (memoria O(trozo)).
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import signal
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

SCHEMA = 1
DEFAULT_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
TOKEN_ENV = "DW_GRAPH_TOKEN"
CONFIRM_PHRASE = "AUTORIZADO-POR-EL-OWNER"
MAX_DOCS = 3
DEFAULT_CAP_BYTES = 25 * 1024 * 1024
TOTAL_CAP_BYTES = 75 * 1024 * 1024
CHUNK = 64 * 1024
DEFAULT_SOCKET_TIMEOUT_S = 30.0
DEFAULT_DOC_DEADLINE_S = 60.0
DEFAULT_TOTAL_DEADLINE_S = 120.0
PREFLIGHT_MARGIN_MB = 200                      # colchón de disco que NUNCA se toca (la aplicación comparte el disco efímero de la instancia)
_MEMORY_FS = {"tmpfs", "ramfs"}
RETRY_AFTER_CAP_S = 120
DOC_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
GRAPH_ID_RE = re.compile(r"^[A-Za-z0-9!_,.\-]{1,256}$")
_LOOPBACK = {"127.0.0.1", "localhost", "[::1]"}


class ProbeInputError(Exception):
    """Argumento o precondición inválidos (se informa el motivo en claro; nunca incluye datos del documento)."""


# ───────────────────────── tiempos y entorno ─────────────────────────
def _ms(seconds: float) -> int:
    return int(round(seconds * 1000))


def _read_text(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="ascii", errors="replace").strip()
    except OSError:
        return None


def _read_int(path: str) -> int | None:
    raw = _read_text(path)
    if raw is None or raw in ("", "max"):
        return None
    try:
        return int(raw.split()[0])
    except ValueError:
        return None


def rss_peak_kb() -> int | None:
    """Pico de memoria residente del proceso (KB): `VmHWM` en Linux, `getrusage` si existe; None si no se puede."""
    status = _read_text("/proc/self/status")
    if status:
        match = re.search(r"VmHWM:\s+(\d+)\s+kB", status)
        if match:
            return int(match.group(1))
    try:
        import resource  # noqa: PLC0415 (no existe en Windows)

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(peak // 1024) if sys.platform == "darwin" else int(peak)
    except Exception:  # noqa: BLE001
        return None


def tmp_write_benchmark(directory: str, megabytes: int) -> dict:
    """Escritura secuencial con fsync en el directorio temporal: cuánto tarda el disco que usaría el ZIP."""
    block = os.urandom(1024 * 1024)
    started = time.perf_counter()
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix="dwprobe-", delete=True) as handle:
            for _ in range(megabytes):
                handle.write(block)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        return {"ok": False, "error": type(exc).__name__}
    elapsed = time.perf_counter() - started
    return {"ok": True, "megabytes": megabytes, "ms": _ms(elapsed), "mb_per_s": round(megabytes / elapsed, 1) if elapsed > 0 else None}


def tmp_fstype(directory: str) -> str | None:
    """Tipo de sistema de archivos que respalda `directory` (Linux, /proc/mounts); None si no se puede saber."""
    mounts = _read_text("/proc/mounts")
    if not mounts:
        return None
    target = os.path.realpath(directory)
    best_mount, best_type = "", None
    for line in mounts.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        mount = parts[1].replace("\\040", " ")
        if (target == mount or target.startswith(mount.rstrip("/") + "/")) and len(mount) > len(best_mount):
            best_mount, best_type = mount, parts[2]
    return best_type


def tmp_preflight(need_mb: int, *, allow_tmpfs: bool = False) -> None:
    """Antes de escribir NADA: el temporal no debe estar en memoria (usaría la RAM de la instancia) ni quedarse sin disco para la aplicación. Falla con un motivo claro y sin crear archivos."""
    tmp = tempfile.gettempdir()
    if tmp_fstype(tmp) in _MEMORY_FS and not allow_tmpfs:
        raise ProbeInputError("el directorio temporal está en memoria (tmpfs): la prueba usaría RAM de la instancia (--allow-tmpfs para forzarlo bajo tu responsabilidad)")
    try:
        free_mb = shutil.disk_usage(tmp).free // (1024 * 1024)
    except OSError:
        raise ProbeInputError("no se pudo medir el espacio libre del directorio temporal") from None
    required = need_mb + PREFLIGHT_MARGIN_MB
    if free_mb < required:
        raise ProbeInputError(f"espacio temporal insuficiente: libres {free_mb} MB, se requieren {required} MB (incluye {PREFLIGHT_MARGIN_MB} MB de colchón para la aplicación)")


def environment_snapshot(*, disk_bench_mb: int = 32) -> dict:
    tmp = tempfile.gettempdir()
    try:
        usage = shutil.disk_usage(tmp)
        disk = {"free_mb": usage.free // (1024 * 1024), "total_mb": usage.total // (1024 * 1024)}
    except OSError as exc:
        disk = {"error": type(exc).__name__}
    cpu_max = _read_text("/sys/fs/cgroup/cpu.max")
    snapshot = {
        "python": platform.python_version(),
        "platform": platform.system(),
        "cpu_count": os.cpu_count(),
        "cgroup": {
            "memory_max_mb": _mb(_read_int("/sys/fs/cgroup/memory.max") or _read_int("/sys/fs/cgroup/memory/memory.limit_in_bytes")),
            "memory_current_mb": _mb(_read_int("/sys/fs/cgroup/memory.current") or _read_int("/sys/fs/cgroup/memory/memory.usage_in_bytes")),
            "memory_peak_mb": _mb(_read_int("/sys/fs/cgroup/memory.peak")),
            "cpu_max": cpu_max,
        },
        "rss_peak_kb": rss_peak_kb(),
        "tmp": {"dir_is_default": tmp == tempfile.gettempdir(), **disk},
    }
    if disk_bench_mb > 0 and "free_mb" in disk and disk["free_mb"] > disk_bench_mb * 4:
        snapshot["tmp"]["write_benchmark"] = tmp_write_benchmark(tmp, disk_bench_mb)
    return snapshot


def _mb(value: int | None) -> int | None:
    # Un límite de cgroup «sin límite» aparece como un número enorme: se informa como None.
    if value is None or value >= 1 << 60:
        return None
    return value // (1024 * 1024)


# ───────────────────────── lectura acotada ─────────────────────────
def read_capped(url: str, token: str, sink, *, cap: int, declared_size: int | None = None, socket_timeout: float = DEFAULT_SOCKET_TIMEOUT_S,
                deadline_s: float = DEFAULT_DOC_DEADLINE_S, chunk: int = CHUNK) -> dict:
    """Descarga `url` hacia `sink` (archivo abierto en binario) con tope de bytes y plazo total. Devuelve SOLO códigos y números (jamás texto de excepciones ni URLs).

    `outcome`: ok · oversize_declared (ni se pide) · oversize_header (Content-Length > tope: se corta sin leer el cuerpo) · oversize_stream (se superó el tope leyendo) · deadline (plazo total) ·
    short_read (cierre antes de completar) · timeout · url_error · http_<código> (con `retry_after_s` si es 429/503 y viene el encabezado)."""
    result = {"outcome": "", "http_status": None, "bytes": 0, "ttfb_ms": None, "transfer_ms": None, "total_ms": None, "retry_after_s": None, "content_length": None}
    if declared_size is not None and declared_size > cap:
        result["outcome"] = "oversize_declared"
        result["total_ms"] = 0
        return result
    started = time.perf_counter()
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/octet-stream"})
    response = None
    try:
        response = urllib.request.urlopen(request, timeout=socket_timeout)  # noqa: S310 (esquema validado por el llamador)
        headers_at = time.perf_counter()
        result["ttfb_ms"] = _ms(headers_at - started)
        result["http_status"] = getattr(response, "status", None) or response.getcode()
        length = response.headers.get("Content-Length")
        if length is not None and length.isdigit():
            result["content_length"] = int(length)
            if int(length) > cap:
                result["outcome"] = "oversize_header"
                return result
        received = 0
        reader = getattr(response, "read1", None) or response.read
        while True:
            if time.perf_counter() - started > deadline_s:
                result["outcome"] = "deadline"
                return result
            want = min(chunk, cap - received + 1)
            block = reader(want)                                                          # `read1`: devuelve lo disponible (un `read(n)` bloquea hasta juntar n bytes y no dejaría respetar el plazo)
            if not block:
                break
            received += len(block)
            if received > cap:
                result["outcome"] = "oversize_stream"
                result["bytes"] = received
                return result
            sink.write(block)
        result["bytes"] = received
        expected = result["content_length"]
        result["outcome"] = "short_read" if expected is not None and received != expected else "ok"
        return result
    except urllib.error.HTTPError as exc:
        result["http_status"] = exc.code
        result["outcome"] = f"http_{exc.code}"
        try:
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            if retry_after is not None:
                result["retry_after_s"] = min(int(float(retry_after)), RETRY_AFTER_CAP_S)
        except (TypeError, ValueError):
            pass
        return result
    except (socket.timeout, TimeoutError):
        result["outcome"] = "timeout"
        return result
    except urllib.error.URLError as exc:
        result["outcome"] = "timeout" if isinstance(getattr(exc, "reason", None), (socket.timeout, TimeoutError)) else "url_error"
        return result
    except Exception as exc:  # noqa: BLE001 — IncompleteRead, ConnectionReset, etc.: solo el nombre de la clase
        name = type(exc).__name__
        result["outcome"] = "short_read" if name == "IncompleteRead" else f"error_{name}"
        return result
    finally:
        result["transfer_ms"] = _ms(time.perf_counter() - started) - (result["ttfb_ms"] or 0)
        result["total_ms"] = _ms(time.perf_counter() - started)
        if response is not None:
            try:
                response.close()
            except Exception:  # noqa: BLE001
                pass


# ───────────────────────── ZIP ─────────────────────────
def _zip_mode(name: str) -> int:
    return zipfile.ZIP_STORED if name == "stored" else zipfile.ZIP_DEFLATED


def synthetic_bench(*, files: int, megabytes: int) -> dict:
    """Costo de armar un ZIP con datos incompresibles (como PDF/DOCX) en el directorio temporal: ALMACENADO vs DEFLATE. No usa red ni CVs."""
    out = {"files": files, "mb_per_file": megabytes, "runs": {}}
    with tempfile.TemporaryDirectory(prefix="dwprobe-bench-") as work:
        for mode in ("stored", "deflated"):
            zpath = Path(work) / f"bench-{mode}.zip"
            rss_before = rss_peak_kb()
            started = time.perf_counter()
            with zipfile.ZipFile(zpath, "w", _zip_mode(mode)) as archive:
                for number in range(1, files + 1):
                    src = Path(work) / f"src-{number}.bin"
                    with open(src, "wb") as handle:
                        for _ in range(megabytes):
                            handle.write(os.urandom(1024 * 1024))
                    t0 = time.perf_counter()
                    archive.write(src, f"doc-{number}.bin")
                    out.setdefault("write_ms_" + mode, []).append(_ms(time.perf_counter() - t0))
                    src.unlink()
            out["runs"][mode] = {"build_total_ms_including_generation": _ms(time.perf_counter() - started), "zip_mb": round(zpath.stat().st_size / (1024 * 1024), 1),
                                 "rss_peak_kb_before": rss_before, "rss_peak_kb_after": rss_peak_kb()}
            zpath.unlink()
    return out


# ───────────────────────── descarga real (autorizada) ─────────────────────────
def _load_document(directory: Path, document_id: str) -> dict:
    if not DOC_ID_RE.match(document_id):
        raise ProbeInputError("identificador de documento inválido")
    path = directory / f"{document_id}.json"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ProbeInputError("registro de documento ilegible o inexistente") from None
    drive_id, item_id = str(record.get("drive_id") or ""), str(record.get("item_id") or "")
    if not GRAPH_ID_RE.match(drive_id) or not GRAPH_ID_RE.match(item_id):
        raise ProbeInputError("el registro no tiene drive_id/item_id válidos")
    size = record.get("size_bytes")
    return {"drive_id": drive_id, "item_id": item_id, "declared_size": int(size) if isinstance(size, int) and size >= 0 else None}


def _validate_graph_base(base: str) -> str:
    parsed = urllib.parse.urlparse(base)
    if base == DEFAULT_GRAPH_BASE:
        return base
    if parsed.scheme == "http" and parsed.hostname in {h.strip("[]") for h in _LOOPBACK}:
        return base.rstrip("/")                                                   # SOLO bucle local (pruebas con Graph simulado): el token nunca viaja a otro host
    raise ProbeInputError("graph-base no permitido (solo Graph oficial o un servidor simulado en localhost)")


def content_url(base: str, drive_id: str, item_id: str) -> str:
    return f"{base}/drives/{urllib.parse.quote(drive_id, safe='!,')}/items/{urllib.parse.quote(item_id, safe='')}/content"


def real_run(*, document_ids: list[str], documents_dir: Path, token: str, graph_base: str, cap: int, compression: str, socket_timeout: float, deadline_s: float,
             total_deadline_s: float = DEFAULT_TOTAL_DEADLINE_S) -> dict:
    started = time.perf_counter()
    docs = [_load_document(documents_dir, document_id) for document_id in document_ids]
    base = _validate_graph_base(graph_base)
    report = {"documents": [], "zip": {}}
    total_bytes = 0
    with tempfile.TemporaryDirectory(prefix="dwprobe-") as work:
        zpath = Path(work) / "probe.zip"
        zip_ms = 0
        with zipfile.ZipFile(zpath, "w", _zip_mode(compression)) as archive:
            for number, doc in enumerate(docs, start=1):
                remaining_total = TOTAL_CAP_BYTES - total_bytes
                time_left = total_deadline_s - (time.perf_counter() - started)
                entry = {"n": number, "declared_size": doc["declared_size"]}
                if remaining_total <= 0 or time_left <= 0:
                    entry.update(outcome="total_cap_reached" if remaining_total <= 0 else "total_deadline", bytes=0)
                    report["documents"].append(entry)
                    continue
                part = Path(work) / f"part-{number}.bin"
                with open(part, "wb") as sink:
                    result = read_capped(content_url(base, doc["drive_id"], doc["item_id"]), token, sink, cap=min(cap, remaining_total), declared_size=doc["declared_size"],
                                         socket_timeout=socket_timeout, deadline_s=min(deadline_s, time_left))
                entry.update(result)
                if result["outcome"] == "ok":
                    total_bytes += result["bytes"]
                    t0 = time.perf_counter()
                    archive.write(part, f"doc-{number}.bin")
                    entry["zip_add_ms"] = _ms(time.perf_counter() - t0)
                    zip_ms += entry["zip_add_ms"]
                    entry["mb_per_s"] = round(result["bytes"] / (1024 * 1024) / (max(result["transfer_ms"] or 0, 1) / 1000), 2)       # piso de 1 ms: en red local rapidísima el tiempo medido puede ser 0
                part.unlink(missing_ok=True)
                report["documents"].append(entry)
        t0 = time.perf_counter()
        zip_size = zpath.stat().st_size if zpath.exists() else 0
        report["zip"] = {"compression": compression, "files": sum(1 for d in report["documents"] if d.get("outcome") == "ok"), "input_bytes": total_bytes, "output_bytes": zip_size,
                         "build_ms": zip_ms + _ms(time.perf_counter() - t0)}
    report["total_elapsed_ms"] = _ms(time.perf_counter() - started)
    report["rss_peak_kb"] = rss_peak_kb()
    report["projection"] = projection(report["documents"])
    return report


def projection(documents: list[dict]) -> dict:
    """Números para el presupuesto: sobrecosto por archivo (ttfb) y rendimiento mínimo observado → cuántos MB entran en 60 s en serie."""
    ok = [d for d in documents if d.get("outcome") == "ok"]
    if not ok:
        return {"basis_documents": 0}
    rates = [d["bytes"] / (1024 * 1024) / (max(d.get("transfer_ms") or 0, 1) / 1000) for d in ok]
    overhead_max = max(d.get("ttfb_ms") or 0 for d in ok)
    slowest = min(rates) if rates else None
    mb_in_60s = None
    if slowest:
        usable = max(60.0 - (overhead_max / 1000) * len(ok), 0.0)
        mb_in_60s = round(slowest * usable, 1)
    return {"basis_documents": len(ok), "ttfb_ms_max": overhead_max, "mb_per_s_min": round(slowest, 2) if slowest else None,
            "mb_per_s_max": round(max(rates), 2) if rates else None, "mb_in_60s_serial_at_min_rate_after_overhead": mb_in_60s, "note": "extrapolación con 1–3 muestras: indicativa, no garantía"}


# ───────────────────────── CLI ─────────────────────────
class _QuietParser(argparse.ArgumentParser):
    """argparse repite en el error los argumentos recibidos («unrecognized arguments: --token abc»): acá nunca, para que un secreto mal pasado no llegue a stderr ni al historial."""

    def error(self, message):
        sys.stderr.write("cchh_export_probe: argumentos inválidos (ver --help)\n")
        raise SystemExit(3)


def build_parser() -> argparse.ArgumentParser:
    parser = _QuietParser(prog="cchh_export_probe", description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--synthetic-bench", action="store_true", help="entorno + ZIP sintético; sin red, sin token, sin CVs")
    mode.add_argument("--real", action="store_true", help="descarga 1-3 CVs reales (requiere autorización explícita del owner)")
    parser.add_argument("--bench-files", type=int, default=4)
    parser.add_argument("--bench-mb", type=int, default=25)
    parser.add_argument("--disk-bench-mb", type=int, default=32)
    parser.add_argument("--allow-tmpfs", action="store_true", help="permite un temporal en memoria (no recomendado en la instancia de producción)")
    parser.add_argument("--document-id", action="append", default=[], help="document_id interno (repetible, máx. 3); el registro se lee de --documents-dir")
    parser.add_argument("--documents-dir", default="human-capital/profile-documents")
    parser.add_argument("--confirm", default="", help=f"frase de autorización: {CONFIRM_PHRASE}")
    parser.add_argument("--graph-base", default=DEFAULT_GRAPH_BASE, help="solo para pruebas con un Graph simulado en localhost")
    parser.add_argument("--cap-mb", type=float, default=DEFAULT_CAP_BYTES / (1024 * 1024))
    parser.add_argument("--compression", choices=("stored", "deflated"), default="deflated")
    parser.add_argument("--socket-timeout", type=float, default=DEFAULT_SOCKET_TIMEOUT_S)
    parser.add_argument("--doc-deadline", type=float, default=DEFAULT_DOC_DEADLINE_S)
    parser.add_argument("--total-deadline", type=float, default=DEFAULT_TOTAL_DEADLINE_S)
    return parser


class _Terminated(BaseException):
    """SIGTERM: por defecto Python muere sin ejecutar `finally`/`with` (quedaría el temporal con CVs). Se convierte en una excepción para que el directorio temporal se borre."""


def _on_sigterm(_signum, _frame):
    raise _Terminated()


def _emit(report: dict) -> None:
    print(json.dumps(report, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report: dict = {"schema": SCHEMA, "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "mode": "synthetic" if args.synthetic_bench else "real"}
    previous = None
    try:
        previous = signal.signal(signal.SIGTERM, _on_sigterm)
    except (ValueError, OSError, AttributeError):              # fuera del hilo principal o plataforma sin SIGTERM: sigue sin el manejador
        previous = None
    try:
        if args.synthetic_bench:
            if not (1 <= args.bench_files <= 8 and 1 <= args.bench_mb <= 50):
                raise ProbeInputError("bench fuera de rango (1-8 archivos, 1-50 MB)")
            tmp_preflight((args.bench_files + 1) * args.bench_mb, allow_tmpfs=args.allow_tmpfs)      # pico: ZIP acumulado + un archivo de origen
            report["environment"] = environment_snapshot(disk_bench_mb=args.disk_bench_mb)
            report["zip_bench"] = synthetic_bench(files=args.bench_files, megabytes=args.bench_mb)
        else:
            if args.confirm != CONFIRM_PHRASE:
                raise ProbeInputError("falta la autorización explícita del owner (--confirm)")
            if not 1 <= len(args.document_id) <= MAX_DOCS:
                raise ProbeInputError(f"se requieren entre 1 y {MAX_DOCS} documentos")
            token = os.environ.get(TOKEN_ENV, "")
            if not token:
                raise ProbeInputError(f"falta el token delegado en la variable de entorno {TOKEN_ENV} (nunca por argumento)")
            tmp_preflight(TOTAL_CAP_BYTES // (1024 * 1024) + DEFAULT_CAP_BYTES // (1024 * 1024), allow_tmpfs=args.allow_tmpfs)   # pico: ZIP (≤ 75 MB) + una parte (≤ 25 MB)
            report["environment"] = environment_snapshot(disk_bench_mb=args.disk_bench_mb)
            report["run"] = real_run(document_ids=args.document_id, documents_dir=Path(args.documents_dir), token=token, graph_base=args.graph_base, cap=int(args.cap_mb * 1024 * 1024),
                                     compression=args.compression, socket_timeout=args.socket_timeout, deadline_s=args.doc_deadline, total_deadline_s=args.total_deadline)
            report["all_ok"] = all(d.get("outcome") == "ok" for d in report["run"]["documents"])
    except ProbeInputError as exc:
        report["error"] = str(exc)
        _emit(report)
        return 3
    except (KeyboardInterrupt, _Terminated) as exc:           # el `with` del directorio temporal ya se desenrolló: no queda nada en disco
        report.pop("run", None)
        report["error"] = "interrumpido"
        report["signal"] = "SIGTERM" if isinstance(exc, _Terminated) else "SIGINT"
        _emit(report)
        return 143 if isinstance(exc, _Terminated) else 130
    except Exception as exc:  # noqa: BLE001 — sin traza: podría incluir rutas, URLs o ids; solo el nombre de la clase
        report.pop("run", None)
        report["error"] = "inesperado"
        report["type"] = type(exc).__name__
        _emit(report)
        return 4
    finally:
        if previous is not None:
            try:
                signal.signal(signal.SIGTERM, previous)
            except (ValueError, OSError):
                pass
    _emit(report)
    return 0 if report.get("all_ok", True) else 2


if __name__ == "__main__":
    sys.exit(main())
