"""Excepcion y ayudas de validacion compartidas por los contratos de ``canonical_evidence`` (v1 en ``evidence_contract``, secciones v2 en ``evidence_contract_v2``)."""
from __future__ import annotations

from typing import Any


class CanonicalEvidenceError(ValueError):
    """``canonical_evidence`` no cumple el contrato (subclase de ``ValueError``: ``validate_assessment`` ya lanza esa clase)."""


def _fail(path: str, msg: str) -> None:
    raise CanonicalEvidenceError(f"canonical_evidence{path}: {msg}")


def _expect(cond: bool, path: str, msg: str) -> None:
    if not cond:
        _fail(path, msg)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _keys(d: Any, required: set[str], path: str, *, closed: bool = False, optional: set[str] = frozenset()) -> None:
    _expect(isinstance(d, dict), path, "debe ser un objeto")
    missing = required - set(d)
    _expect(not missing, path, f"faltan claves {sorted(missing)}")
    if closed:
        extra = set(d) - required - set(optional)
        _expect(not extra, path, f"claves fuera del contrato {sorted(extra)}")
