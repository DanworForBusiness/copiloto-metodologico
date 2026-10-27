"""publication_gate.py — protección de LECTORES durante la activación y la recuperación de una publicación (P1).

Una publicación segura cambia pool, Catálogo materializado y assessments gobernados como UNA unidad. Mientras la unidad se aplica, se verifica y se confirma en Spaces, el contenido de
disco es transitorio: un lector que lo viera mostraría una versión que todavía puede revertirse. Esta compuerta lo impide con un archivo de estado FUERA de los directorios persistidos
(hermano de ``catalog_local``, ``.publication_runtime/gate.json``), que ven todos los procesos que comparten el disco:

  * ``ACTIVATING``  la publicación está en curso: los lectores ESPERAN (acotado) y, vencida la espera, reciben ``PublicationInProgress`` (la web responde 503 con ``Retry-After``).
  * ``UNCERTAIN``   la publicación quedó aplicada pero NO se pudo confirmar ni deshacer de forma verificable en Spaces: los lectores reciben ``PublicationBlocked`` al instante hasta que
                    la recuperación lo resuelva (``set_resolver``). No se muestra como vigente una versión sin confirmación durable.

El hilo que ejecuta la publicación (``owner``) no se bloquea a sí mismo. Sin archivo de estado no cuesta nada (un ``stat``). El archivo lo escribe/retira SOLO el módulo de publicación.
"""
from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Callable

import config
from core import atomic_io

GATE_FILE = "gate.json"
ACTIVATING, UNCERTAIN = "ACTIVATING", "UNCERTAIN"
DEFAULT_WAIT_S = 15.0                         # un lector bloqueado ocupa un hilo del servidor: la espera es corta y, vencida, el cliente reintenta (503 + Retry-After)
_POLL_S = 0.05
_STALE_AFTER_S = 3600.0                       # un ACTIVATING más viejo que esto sin proceso vivo se considera huérfano (lo limpia la recuperación)
_RESOLVE_EVERY_S = 30.0


class PublicationInProgress(RuntimeError):
    """Hay una publicación activándose; se esperó el máximo y sigue en curso. ``retry_after`` en segundos."""

    def __init__(self, retry_after: int = 15):
        super().__init__("PUBLICATION_IN_PROGRESS")
        self.retry_after = retry_after


class PublicationBlocked(RuntimeError):
    """La última publicación no está confirmada ni deshecha: la lectura queda bloqueada hasta resolverlo."""

    def __init__(self, operation_id: str = "", client_dir: str = ""):
        super().__init__("PUBLICATION_BLOCKED")
        self.operation_id, self.client_dir = operation_id, client_dir


_owner = threading.local()
_resolver: Callable[[Path], None] | None = None
_last_resolve = 0.0
_resolve_lock = threading.Lock()


def catalog_root() -> Path:
    return Path(os.getenv("DANWOR_CATALOG_DIR", "").strip() or str(config.BASE_DIR / "catalog_local")).resolve()


def runtime_dir(root: str | Path | None = None) -> Path:
    """Mismo directorio de runtime que el journal (hermano de ``catalog_local``, fuera de lo persistido)."""
    return Path(root or catalog_root()).resolve().parent / ".publication_runtime"


def gate_path(root: str | Path | None = None) -> Path:
    return runtime_dir(root) / GATE_FILE


def read_state(root: str | Path | None = None) -> dict | None:
    """Estado vigente de la compuerta, o None si no hay. Un archivo ilegible cuenta como BLOQUEO (nunca como «libre»)."""
    path = gate_path(root)
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and data.get("state") in (ACTIVATING, UNCERTAIN) else {"state": UNCERTAIN, "operation_id": "", "client_dir": ""}
    except (OSError, ValueError):
        return {"state": UNCERTAIN, "operation_id": "", "client_dir": ""}


def _write(root: str | Path | None, state: str, operation_id: str, client_dir: str) -> None:
    atomic_io.write_text(gate_path(root), json.dumps({"state": state, "operation_id": operation_id, "client_dir": client_dir, "pid": os.getpid(), "since": time.time()}))


def begin(operation_id: str, client_dir: str, root: str | Path | None = None) -> None:
    """Cierra la compuerta (ACTIVATING) para lectores ajenos y marca al hilo actual como dueño."""
    _write(root, ACTIVATING, operation_id, client_dir)
    _owner.op = operation_id


def mark_uncertain(operation_id: str, client_dir: str, root: str | Path | None = None) -> None:
    """La publicación quedó sin confirmar ni deshacer: la compuerta pasa a UNCERTAIN y NO se abre sola."""
    _write(root, UNCERTAIN, operation_id, client_dir)


def end(operation_id: str, root: str | Path | None = None) -> None:
    """Abre la compuerta SOLO si la cerró esta operación (nunca la de otra)."""
    st = read_state(root)
    if st is not None and st.get("operation_id") in ("", operation_id):
        with contextlib.suppress(OSError):
            gate_path(root).unlink()
    if getattr(_owner, "op", None) == operation_id:
        _owner.op = None


def clear(root: str | Path | None = None) -> None:
    """Retira la compuerta incondicionalmente (la usa la recuperación tras verificar el estado)."""
    with contextlib.suppress(OSError):
        gate_path(root).unlink()
    _owner.op = None


@contextlib.contextmanager
def owner(operation_id: str):
    """Marca al hilo como dueño de la operación (para que sus propias lecturas no esperen a la compuerta que él mismo cerró)."""
    prev = getattr(_owner, "op", None)
    _owner.op = operation_id
    try:
        yield
    finally:
        _owner.op = prev


def set_resolver(fn: Callable[[Path], None] | None) -> None:
    """Registra la función que intenta resolver un estado UNCERTAIN (confirmar o deshacer verificando Spaces)."""
    global _resolver
    _resolver = fn


def _maybe_resolve(root: Path) -> None:
    global _last_resolve
    fn = _resolver
    if fn is None:
        return
    with _resolve_lock:
        now = time.monotonic()
        if now - _last_resolve < _RESOLVE_EVERY_S:
            return
        _last_resolve = now
    with contextlib.suppress(Exception):
        fn(root)


def check(root: str | Path | None = None, *, wait: float | None = None) -> None:
    """Punto de entrada de los LECTORES. Sin compuerta cerrada vuelve de inmediato. ACTIVATING: espera hasta ``wait`` s (``0`` = no espera) y lanza ``PublicationInProgress`` si sigue.
    UNCERTAIN: intenta (como mucho cada 30 s) resolverlo y, si sigue, lanza ``PublicationBlocked``. El dueño de la operación no se bloquea."""
    st = read_state(root)
    if st is None:
        return
    here = getattr(_owner, "op", None)
    if here and st.get("operation_id") == here:
        return
    base = Path(root).resolve() if root else catalog_root()
    limit = DEFAULT_WAIT_S if wait is None else max(0.0, wait)
    deadline = time.monotonic() + limit
    while st is not None and st.get("state") == ACTIVATING:
        if time.time() - float(st.get("since") or 0) > _STALE_AFTER_S:
            break                                                           # huérfano: lo trata como incierto (la recuperación lo limpia)
        if time.monotonic() >= deadline:
            raise PublicationInProgress()
        time.sleep(_POLL_S)
        st = read_state(root)
    if st is None:
        return
    if st.get("state") == UNCERTAIN or st.get("state") == ACTIVATING:
        _maybe_resolve(base)
        st = read_state(root)
        if st is None:
            return
        if here and st.get("operation_id") == here:
            return
        if st.get("state") == ACTIVATING:
            raise PublicationInProgress()
        raise PublicationBlocked(str(st.get("operation_id") or ""), str(st.get("client_dir") or ""))
