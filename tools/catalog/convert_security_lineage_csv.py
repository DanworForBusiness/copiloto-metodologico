"""
Convierte CSV de linaje/uso DAC exportado desde HANA Cockpit a JSON.

Uso:
  python tools/catalog/convert_security_lineage_csv.py --csv data.csv
  python tools/catalog/convert_security_lineage_csv.py --csv data.csv --out data.json

El JSON conserva columnas y filas sin interpretar sem?nticamente; el generador
de seguridad decide luego c?mo usarlo.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def convert_csv_to_json(csv_path: str | Path, out_path: str | Path | None = None) -> Path:
    src = Path(csv_path)
    if out_path is None:
        out = src.with_suffix('.json')
    else:
        out = Path(out_path)

    text = src.read_text(encoding='utf-8-sig')
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=',;\t|')
    except csv.Error:
        dialect = csv.excel

    rows = []
    reader = csv.DictReader(text.splitlines(), dialect=dialect)
    for row in reader:
        rows.append({str(k or '').strip(): str(v or '').strip() for k, v in row.items()})

    payload = {
        'source_file': src.name,
        'row_count': len(rows),
        'columns': list(reader.fieldnames or []),
        'rows': rows,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description='Convierte CSV de linaje DAC a JSON')
    ap.add_argument('--csv', dest='csv_path', required=True, help='CSV origen')
    ap.add_argument('--out', dest='out_path', default='', help='JSON destino opcional')
    args = ap.parse_args()
    out = convert_csv_to_json(args.csv_path, args.out_path or None)
    print(f'[OK] CSV convertido a JSON: {out}')


if __name__ == '__main__':
    main()
