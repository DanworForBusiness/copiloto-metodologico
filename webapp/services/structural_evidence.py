"""Sidecar GOBERNADO `structural-evidence/v1`: persiste en el sobre la evidencia estructural `join-key-profile/v1` del MISMO ciclo de evaluación,
ligada INEQUÍVOCAMENTE al perfil (firma del artefacto) y al modelo terminal. No es una segunda fuente de verdad: describe el CSN del que salió ESE perfil
y solo vale mientras el perfil sea exactamente ese.

Contenido: versión · modelo terminal · procedencia (CSN del pool + firma del pool) · bloque `join-key-profile/v1` · digest del bloque ·
vínculo (`sha256:canonical-assessment-json` = firma del artefacto del perfil).

Consumo (`for_engine`): SOLO si todo verifica — versión conocida, MISMO modelo, vínculo == firma del artefacto del perfil (y del sobre), digest del bloque
recalculado == registrado, versión del bloque conocida y artefactos descritos ⊆ cierre del perfil. Cualquier falla (ausente, alterado, obsoleto, de otro
modelo) ⇒ ``None`` + motivo: el Juicio v7 se ABSTIENE (E3/E5 no evaluables). Nunca se usa para v2–v6 (lo garantiza el llamador y el motor).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

SIDECAR_VERSION = "structural-evidence/v1"
BLOCK_VERSION = "join-key-profile/v1"
BINDING_METHOD = "sha256:canonical-assessment-json"
ENVELOPE_KEY = "structural_evidence"


def block_digest(block: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(block, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _artifact_signature(profile: Mapping[str, Any]) -> str:
    from webapp.services.assessment_service import _artifact_signature as sig          # la MISMA firma canónica del artefacto que valida el sobre
    return sig(dict(profile))


def build_sidecar(meta: Mapping[str, Any] | None, profile: Mapping[str, Any] | None, terminal_am: str, pool_signature: str | None) -> dict | None:
    """Sidecar a persistir para ESTE perfil, desde el `evidence_extensions` del MISMO ciclo. ``None`` si no hay bloque o si el sidecar del ciclo no está
    ligado a este perfil (nunca se persiste evidencia de otra evaluación)."""
    ext = (meta or {}).get("evidence_extensions") if isinstance(meta, Mapping) else None
    block = (ext or {}).get("join_key_profile") if isinstance(ext, Mapping) else None
    if not isinstance(block, Mapping) or block.get("version") != BLOCK_VERSION or not isinstance(profile, Mapping):
        return None
    sig = _artifact_signature(profile)
    b = (ext or {}).get("profile_binding") or {}
    if b.get("method") != BINDING_METHOD or b.get("digest") != sig:
        return None
    return {"version": SIDECAR_VERSION, "terminal_am": terminal_am, "provenance": {"source": "CSN_POOL", "pool_signature": pool_signature},
            "join_key_profile": dict(block), "block_digest": block_digest(block), "binding": {"method": BINDING_METHOD, "artifact_signature": sig}}


def verify(sidecar: Any, profile: Mapping[str, Any] | None, terminal_am: str, envelope_signature: str | None = None) -> tuple[dict | None, str]:
    """(bloque verificado | None, motivo)."""
    if sidecar is None:
        return None, "ABSENT"
    if not isinstance(sidecar, Mapping) or sidecar.get("version") != SIDECAR_VERSION:
        return None, "VERSION_UNKNOWN"
    if sidecar.get("terminal_am") != terminal_am:
        return None, "OTHER_MODEL"
    if not isinstance(profile, Mapping):
        return None, "NO_PROFILE"
    sig = _artifact_signature(profile)
    b = sidecar.get("binding") or {}
    if b.get("method") != BINDING_METHOD or b.get("artifact_signature") != sig or (envelope_signature is not None and envelope_signature != sig):
        return None, "PROFILE_MISMATCH"                                          # obsoleto: el perfil cambió después (o es de otra evaluación)
    block = sidecar.get("join_key_profile")
    if not isinstance(block, Mapping) or block.get("version") != BLOCK_VERSION:
        return None, "BLOCK_VERSION_UNKNOWN"
    if block_digest(block) != sidecar.get("block_digest"):
        return None, "DIGEST_MISMATCH"                                           # alterado
    closure = set(((profile.get("scope") or {}).get("closure_artifacts")) or ())
    described = set((block.get("views") or {})) | set((block.get("keys") or {}))
    if not described <= closure:
        return None, "OUTSIDE_CLOSURE"
    return dict(block), "VERIFIED"


def for_engine(env: Mapping[str, Any] | None) -> dict | None:
    """Entrada `structural_evidence` del Juicio v7 para un sobre gobernado, o ``None`` (abstención) si no verifica."""
    if not isinstance(env, Mapping):
        return None
    block, _why = verify(env.get(ENVELOPE_KEY), env.get("profile_json"), str(env.get("terminal_am") or ""), env.get("assessment_artifact_signature"))
    return {"join_key_profile": block} if block is not None else None
