"""OPERACIÓN EXCEPCIONAL — carga controlada de la EVIDENCIA de la Fase 1 (extracción de solo lectura de los 25 modelos de Biogenesis desde CALIDAD) a un prefijo PRIVADO de Spaces.

Se ejecuta A MANO desde el equipo del operador (no desde el Hub), con las credenciales de Spaces del entorno (SPACES_ENDPOINT/REGION/BUCKET/KEY/SECRET, SPACES_PREFIX opcional).
DRY-RUN por defecto. La evidencia NO está en el repositorio: se indica con --evidence-dir (la carpeta OUT de la extracción: pool/, roots/, extraction_summary.json).

  · destino: <SPACES_PREFIX>/quarantine/biogenesis-20261108-calidad-evidence/… — FUERA de SPACES_PERSIST_PATHS: el restore del Hub nunca lo baja y el sincronizador nunca lo toca;
  · el manifiesto de la carpeta debe tener EXACTAMENTE la huella fija ``migrate_biogenesis.EVIDENCE_SHA256``; cada archivo se verifica después de subirlo (sha256 leído);
  · los objetos quedan PRIVADOS (sin ACL pública: se verifica con get_object_acl); el manifiesto se sube AL FINAL (sin manifiesto, la migración no encuentra evidencia);
  · idempotente: un objeto presente con el hash correcto no se vuelve a subir; uno presente con otro contenido ABORTA (nunca se sobrescribe evidencia).

Uso:
  python ops/migration/biogenesis_calidad_20261108/upload_evidence.py --evidence-dir <OUT> --print-digest      # huella de la carpeta (para fijar EVIDENCE_SHA256)
  python ops/migration/biogenesis_calidad_20261108/upload_evidence.py --evidence-dir <OUT>                     # dry-run
  python ops/migration/biogenesis_calidad_20261108/upload_evidence.py --evidence-dir <OUT> --apply --confirm SUBIR-EVIDENCIA-BIOGENESIS-CALIDAD-20261108
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import migrate_biogenesis as mb  # noqa: E402

CONFIRM = "SUBIR-EVIDENCIA-BIOGENESIS-CALIDAD-20261108"
_PUBLIC_GRANTEES = ("http://acs.amazonaws.com/groups/global/AllUsers", "http://acs.amazonaws.com/groups/global/AuthenticatedUsers")


def _sha(b: bytes | None) -> str | None:
    return None if b is None else hashlib.sha256(b).hexdigest()


def _client():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    import boto3
    for v in ("SPACES_ENDPOINT", "SPACES_BUCKET", "SPACES_KEY", "SPACES_SECRET"):
        if not os.getenv(v):
            raise SystemExit(f"ABORT: falta {v} en el entorno")
    c = boto3.client("s3", endpoint_url=os.environ["SPACES_ENDPOINT"], region_name=os.getenv("SPACES_REGION"),
                     aws_access_key_id=os.environ["SPACES_KEY"], aws_secret_access_key=os.environ["SPACES_SECRET"])
    return c, os.environ["SPACES_BUCKET"]


def _key(rel: str) -> str:
    import config                                                                       # MISMA fuente del prefijo que usa el Hub (remote_store._key)
    prefix = str(getattr(config, "SPACES_PREFIX", "") or "").strip("/")
    return (prefix + "/" if prefix else "") + mb.EVIDENCE_PREFIX + rel


def _is_private(client, bucket, key) -> bool:
    acl = client.get_object_acl(Bucket=bucket, Key=key)
    return not any((g.get("Grantee") or {}).get("URI") in _PUBLIC_GRANTEES for g in acl.get("Grants") or [])


def run(evidence_dir: Path, *, apply: bool, expected: str = mb.EVIDENCE_SHA256, client=None, bucket=None) -> int:
    files = mb.evidence_files(evidence_dir)
    manifest = mb.evidence_manifest(files)
    digest = mb.evidence_digest(manifest)
    if digest != expected:
        print(f"ABORT: la carpeta no es la evidencia fijada (huella {digest} ≠ {expected})")
        return 1
    try:
        mb.validate_evidence(files, expected)
    except mb.Abort as exc:
        print(f"ABORT: {exc}")
        return 1
    if client is None:
        client, bucket = _client()
    body = json.dumps(manifest, indent=1, sort_keys=True).encode("utf-8")
    items = [*sorted(files.items()), (mb.EVIDENCE_MANIFEST, body)]               # el manifiesto, al final
    print(f"evidencia local: {len(files)} archivos · huella {digest[:16]}… = fija")
    uploaded = present = 0
    for rel, data in items:
        key = _key(rel)
        existing = mb._get(client, bucket, key)
        if existing is not None and _sha(existing) != _sha(data):
            print(f"ABORT: {key} ya existe con OTRO contenido; no se sobrescribe evidencia")
            return 1
        if existing is not None:
            present += 1
        elif apply:
            client.put_object(Bucket=bucket, Key=key, Body=data, ContentType="application/json")   # sin ACL ⇒ privado (default de Spaces)
            if _sha(mb._get(client, bucket, key)) != _sha(data):
                print(f"ABORT: {key} no quedó verificado tras subirlo")
                return 1
            uploaded += 1
        if apply and not _is_private(client, bucket, key):
            print(f"ABORT: {key} tiene una concesión pública: revisar la ACL del bucket")
            return 1
    if apply:
        print(f"RESULTADO: evidencia verificada en Spaces (privada) · subidos {uploaded} · ya presentes {present}")
    else:
        print(f"DRY-RUN: no se escribió nada · subiría {len(items) - present} · ya presentes {present}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Carga controlada de la evidencia de la migración de Biogenesis a CALIDAD (DRY-RUN por defecto).")
    ap.add_argument("--evidence-dir", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--confirm", default="")
    ap.add_argument("--print-digest", action="store_true")
    args = ap.parse_args(argv)
    if args.print_digest:
        print(mb.evidence_digest(mb.evidence_manifest(mb.evidence_files(Path(args.evidence_dir)))))
        return 0
    if args.apply and args.confirm != CONFIRM:
        print(f"ABORT: --apply exige --confirm {CONFIRM}")
        return 1
    return run(Path(args.evidence_dir), apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
