"""OPERACIÓN EXCEPCIONAL — carga controlada de la EVIDENCIA autoritativa (7 CSN de Biogenesis previos al incidente del 2026-11-06) a un prefijo PRIVADO de Spaces.

Se ejecuta A MANO desde el equipo del operador (no desde el Hub), con las credenciales de Spaces del entorno (SPACES_ENDPOINT/REGION/BUCKET/KEY/SECRET, SPACES_PREFIX opcional).
DRY-RUN por defecto. La evidencia NO está en el repositorio: se indica con --evidence-dir (carpeta con pool/Biogenesis/<tipo>/<nombre>.json).

  · destino: <SPACES_PREFIX>/quarantine/biogenesis-20261106-evidence/catalog_local/.csn_cache/Biogenesis/… — FUERA de SPACES_PERSIST_PATHS: el restore del Hub nunca lo baja
    y el sincronizador nunca lo toca;
  · cada archivo se valida contra el hash FIJO de restore_biogenesis.PLAN antes de subir y se verifica después (sha256 del objeto leído);
  · los objetos quedan PRIVADOS (sin ACL pública): se verifica con get_object_acl que no haya concesiones a AllUsers/AuthenticatedUsers;
  · idempotente: un objeto ya presente con el hash correcto no se vuelve a subir; uno presente con otro contenido ABORTA (nunca se sobrescribe evidencia).

Uso:
  python ops/recovery/biogenesis_20261106/upload_evidence.py --evidence-dir <carpeta>                                    # dry-run
  python ops/recovery/biogenesis_20261106/upload_evidence.py --evidence-dir <carpeta> --apply --confirm SUBIR-EVIDENCIA-BIOGENESIS-20261106
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import restore_biogenesis as rb  # noqa: E402

CONFIRM = "SUBIR-EVIDENCIA-BIOGENESIS-20261106"
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
    return (prefix + "/" if prefix else "") + rb.EVIDENCE_PREFIX + rel


def plan_items(evidence_dir: Path) -> list[tuple[str, Path, str]]:
    items = []
    for rel, spec in rb.PLAN.items():
        if spec["target"] is None:
            continue
        p = evidence_dir / "pool" / rb.CLIENT / rel[len(rb._POOL):]
        if not p.is_file():
            raise SystemExit(f"ABORT: falta {p}")
        if _sha(p.read_bytes()) != spec["target"]:
            raise SystemExit(f"ABORT: {p} no coincide con el hash fijo de la firma autoritativa")
        items.append((rel, p, spec["target"]))
    return items


def _remote(client, bucket, key):
    return rb._get(client, bucket, key)


def _is_private(client, bucket, key) -> bool:
    acl = client.get_object_acl(Bucket=bucket, Key=key)
    return not any((g.get("Grantee") or {}).get("URI") in _PUBLIC_GRANTEES for g in acl.get("Grants") or [])


def run(evidence_dir: Path, *, apply: bool, client=None, bucket=None) -> int:
    items = plan_items(evidence_dir)
    if client is None:
        client, bucket = _client()
    print(f"evidencia local: {len(items)}/7 archivos = hash fijo")
    for rel, path, target in items:
        key = _key(rel)
        existing = _remote(client, bucket, key)
        if existing is not None and _sha(existing) != target:
            print(f"ABORT: {key} ya existe con OTRO contenido; no se sobrescribe evidencia")
            return 1
        state = "ya presente (hash correcto)" if existing is not None else ("SUBIR" if apply else "subiría")
        print(f"  {state}: {key}")
        if apply and existing is None:
            client.put_object(Bucket=bucket, Key=key, Body=path.read_bytes(), ContentType="application/json")      # sin ACL ⇒ privado (default de Spaces)
            if _sha(_remote(client, bucket, key)) != target:
                print(f"ABORT: {key} no quedó verificado tras subirlo")
                return 1
        if apply and not _is_private(client, bucket, key):
            print(f"ABORT: {key} tiene una concesión pública: revisar la ACL del bucket")
            return 1
    print("RESULTADO: evidencia verificada en Spaces (privada)" if apply else "DRY-RUN: no se escribió nada")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Carga controlada de la evidencia de Biogenesis a Spaces (DRY-RUN por defecto).")
    ap.add_argument("--evidence-dir", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--confirm", default="")
    args = ap.parse_args(argv)
    if args.apply and args.confirm != CONFIRM:
        print(f"ABORT: --apply exige --confirm {CONFIRM}")
        return 1
    return run(Path(args.evidence_dir), apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
