"""
resolve_catalog_workbook_ids.py — CCHH Catalog Foundation, provisioning.

Utilidad de UNA SOLA VEZ (no corre en el runtime del Hub, no la importa
ningún router/service) para resolver `HC_CATALOG_ITEM_ID` -- la ÚNICA
config nueva que el catálogo de CCHH necesita -- UNA VEZ que CCHH haya
creado `Catalogos_CCHH.xlsx` en SharePoint.

`Catalogos_CCHH.xlsx` vive en el MISMO site/drive que el resto de Capital
Humano (`HC_SHAREPOINT_SITE_ID`/`HC_SHAREPOINT_DRIVE_ID`, ya configurados
para el feature de documentos/CV, ver config.py) -- este script NUNCA
resuelve ni sugiere un drive nuevo, sólo el `item_id` del archivo dentro
de ese drive ya conocido.

SOLO LECTURA de Graph -- nunca escribe, nunca crea nada. Reusa
`core.microsoft365.graph_get_json` (MISMA primitiva HTTP que el resto del
Hub), resolviendo el item por path DENTRO del drive ya configurado
(`/drives/{drive_id}/root:/{path}:` -> `id`).

Requiere:
  - `HC_SHAREPOINT_DRIVE_ID` YA configurado en el entorno donde se corre
    (lee `config.py`, mismo mecanismo que el resto del Hub) -- si falta,
    el script lo dice y no adivina nada.
  - un TOKEN DE LECTURA de Graph (User.Read/Sites.Read.All) ya adquirido
    por quien corre este script -- NUNCA pedir/pegar un token ajeno acá
    ni loguearlo; se usa una sola vez, en memoria, y se descarta. La forma
    más simple de conseguirlo: iniciar sesión en el Hub como siempre y, en
    la consola del browser (ya logueado), correr
    `await window.dwGraphToken()` y copiar el resultado.

Uso:
    python tools/human_capital/resolve_catalog_workbook_ids.py \
        --token "<graph access token>" \
        [--file-path "CCHH HUB/Configuracion/Catalogos_CCHH.xlsx"]

Imprime `HC_CATALOG_ITEM_ID` listo para pegar en el `.env` local y/o las
env vars de DigitalOcean App Platform. Si el archivo todavía no existe en
SharePoint (404), lo dice explícitamente -- nunca inventa un ID."""
from __future__ import annotations

import argparse
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import config
from core import microsoft365

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"


def resolve_item_id(drive_id: str, file_path: str, token: str) -> str:
    url = f"{GRAPH_ROOT}/drives/{drive_id}/root:/{file_path}:"
    data = microsoft365.graph_get_json(url, token)
    return data["id"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token", required=True, help="Graph access token (lectura), pegado una sola vez")
    parser.add_argument("--file-path", default="CCHH HUB/Configuracion/Catalogos_CCHH.xlsx")
    args = parser.parse_args()

    drive_id = (config.HC_SHAREPOINT_DRIVE_ID or "").strip()
    if not drive_id:
        print(
            "ERROR: HC_SHAREPOINT_DRIVE_ID no está configurado en este entorno -- "
            "es el MISMO drive que usa el resto de Capital Humano (documentos/CV), "
            "tiene que estar seteado antes de poder resolver el catálogo.",
            file=sys.stderr,
        )
        return 1

    print(f"Resolviendo '{args.file_path}' dentro del drive ya configurado ({drive_id}) ...")
    try:
        item_id = resolve_item_id(drive_id, args.file_path, args.token)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            print(
                f"ERROR: '{args.file_path}' NO existe todavía en ese drive (404). "
                "Creá la carpeta/el archivo en SharePoint primero -- este script "
                "nunca inventa un ID para un archivo inexistente.",
                file=sys.stderr,
            )
        else:
            print(f"ERROR resolviendo el archivo: HTTP {exc.code} — {exc.reason}", file=sys.stderr)
        return 1

    print()
    print("Encontrado. Configurá esta variable (.env local y/o DigitalOcean App Platform):")
    print(f"  HC_CATALOG_ITEM_ID={item_id}")
    print("(HC_SHAREPOINT_SITE_ID/HC_SHAREPOINT_DRIVE_ID ya están configurados -- no hace falta ninguna otra variable.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
