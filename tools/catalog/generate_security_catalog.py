"""
Generador del catálogo de Seguridad/DAC Datasphere.

Entrada esperada, por cliente (pool unificado, mismo criterio que el
catálogo de dominios — ver `generate_catalog.py`, Ticket A):
  <Cliente>/Seguridad/*.json          (legacy)
  <Cliente>/seguridad/*.json          (legacy, cualquier casing)
  <Cliente>/data-access-controls/*.json  (export CLI de Datasphere — carpeta
      FÍSICA de tipo, no un dominio; mismo tratamiento que las otras 9
      carpetas-tipo del generador de dominios)
  <Cliente>/<cualquiera de las anteriores>/*.csv  (linaje/uso HANA Cockpit)

El matching de nombre de carpeta es EXPLÍCITAMENTE case-insensitive (no
depende de que el filesystem lo sea — portable a Linux). Si un cliente
tiene AMBAS (legacy + CLI), se unen en un solo pool lógico: se deduplica
por identidad de DAC (nombre técnico) si el contenido coincide, y se
reporta `SecurityCollisionError` si el mismo nombre técnico difiere entre
carpetas físicas — nunca se elige uno en silencio (mismo contrato que
`CatalogCollisionError` del catálogo de dominios).

También tolera que --all apunte directamente a una carpeta Seguridad o
data-access-controls. Emite markdown bajo catalog_local/seguridad/<cliente>/*.md
y conserva manual:.
"""
from __future__ import annotations

import argparse
import html
import csv
import hashlib
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CATALOG_MANIFEST = '.catalog_manifest.json'
SECURITY_DIR = 'seguridad'
SECURITY_SOURCE_EXTENSIONS = {'.json', '.csv', '.xlsx'}

# Carpetas de entrada reconocidas para seguridad/DAC, normalizadas (lowercase,
# "-"/"_" a espacio) para matching EXPLÍCITAMENTE case-insensitive y portable
# a Linux (nunca depender de que el filesystem resuelva paths sin distinguir
# mayúsculas, como NTFS) — mismo criterio que `generate_catalog._catalog_dir_kind`.
_SECURITY_INPUT_DIR_NAMES = {'seguridad', 'data access controls'}


def _security_dir_kind(name: str) -> str:
    return re.sub(r'[-_]+', ' ', str(name or '').strip().lower())


def _is_security_input_dir(path: Path) -> bool:
    return path.is_dir() and _security_dir_kind(path.name) in _SECURITY_INPUT_DIR_NAMES


class SecurityCollisionError(Exception):
    """Mixed-mode: el MISMO nombre técnico de DAC aparece en dos carpetas
    físicas de entrada distintas (p. ej. `Seguridad/` legacy y
    `data-access-controls/` CLI) con contenido DISTINTO. Se reporta de forma
    determinística — nunca se elige una copia en silencio (mismo contrato
    que `generate_catalog.CatalogCollisionError`). Dos copias con contenido
    idéntico se deduplican sin error."""


def _is_office_temp_file(path: Path) -> bool:
    return path.name.startswith('~$')
CRITERION_KEYS = (
    '@Danwor.catalog.securityCriterion',
    '@DataWarehouse.businessDefinition.legend',
    '@EndUserText.label',
)

SUMMARY_KEYS = (
    'summary',
    'description',
    'securitySummary',
    'businessDescription',
    'longDescription',
    '@Danwor.catalog.summary',
    '@Danwor.catalog.securitySummary',
    '@Danwor.catalog.description',
    '@DataWarehouse.businessDefinition.summary',
    '@DataWarehouse.businessDefinition.description',
    '@DataWarehouse.businessDefinition.securitySummary',
    '@DataWarehouse.businessDefinition.businessDescription',
    '@DataWarehouse.businessDefinition.longDescription',
)

STRUCTURE_KEYS = (
    'structure',
    '@Danwor.catalog.structure',
    '@DataWarehouse.businessDefinition.structure',
)


@dataclass
class SecurityRoleMatrix:
    cliente: str
    source_file: str
    roles: list[dict[str, Any]] = field(default_factory=list)
    global_roles: list[dict[str, Any]] = field(default_factory=list)
    scoped_roles: list[dict[str, Any]] = field(default_factory=list)
    permission_rows: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SecurityArtifact:
    cliente: str
    criterio: str
    dac_name: str
    dac_label: str = ''
    source_entity: str = ''
    principal_element: str = ''
    contact: str = ''
    structure: str = ''
    summary: str = ''
    fields: list[dict[str, Any]] = field(default_factory=list)
    control_fields: list[dict[str, Any]] = field(default_factory=list)
    lineage_rows: list[dict[str, str]] = field(default_factory=list)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8-sig'))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _generator_sha256() -> str:
    try:
        return _sha256(Path(__file__))
    except Exception:
        return 'unknown'


def _manifest_path(out_root: Path) -> Path:
    return Path(out_root) / CATALOG_MANIFEST


def _load_manifest(out_root: Path) -> dict[str, Any]:
    path = _manifest_path(out_root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _save_manifest(out_root: Path, manifest: dict[str, Any]) -> None:
    path = _manifest_path(out_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')


def _slug(value: str) -> str:
    value = re.sub(r'[^A-Za-z0-9_-]+', '_', str(value or '').strip())
    value = re.sub(r'_+', '_', value).strip('_')
    return value.lower() or 'sin_nombre'


def _plain(value: Any) -> str:
    if isinstance(value, dict) and '#' in value:
        return str(value.get('#') or '')
    if value is None:
        return ''
    return str(value)


def _find_annotation(obj: Any, keys: tuple[str, ...]) -> str:
    if isinstance(obj, dict):
        for key in keys:
            value = _plain(obj.get(key))
            if value:
                return value
        for value in obj.values():
            found = _find_annotation(value, keys)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find_annotation(value, keys)
            if found:
                return found
    return ''


def _field_rows(elements: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for name, spec in (elements or {}).items():
        spec = spec or {}
        rows.append({
            'name': str(name),
            'label': str(spec.get('@EndUserText.label') or name),
            'type': str(spec.get('type') or ''),
            'length': spec.get('length') or '',
            'key': bool(spec.get('key')),
            'not_null': bool(spec.get('notNull')),
        })
    return rows


def _criterion(defn: dict[str, Any], fallback: str) -> str:
    for key in CRITERION_KEYS:
        value = _plain(defn.get(key))
        if value:
            return value
    return fallback


def _summary(defn: dict[str, Any]) -> str:
    return _find_annotation(defn, SUMMARY_KEYS)


def _structure(defn: dict[str, Any]) -> str:
    return _find_annotation(defn, STRUCTURE_KEYS)


def _read_csv(path: Path) -> list[dict[str, str]]:
    text = path.read_text(encoding='utf-8-sig')
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=',;\t|')
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(text.splitlines(), dialect=dialect)
    return [{str(k or '').strip(): str(v or '').strip() for k, v in row.items()} for row in reader]


def _read_lineage_sources(dirs: list[Path]) -> list[tuple[str, list[dict[str, str]]]]:
    """[(stem, rows)] de cada archivo de linaje (csv, o json con `rows`), pooled
    a través de TODAS las carpetas físicas de entrada del cliente. NO mezcla
    todo: el linaje se asocia luego a un DAC por el nombre del archivo (ver
    `_lineage_belongs_to`), para que `lineage_<DAC>.csv` aplique solo a ese DAC."""
    out: list[tuple[str, list[dict[str, str]]]] = []
    for seg_dir in dirs:
        for path in sorted(seg_dir.glob('*.json')):
            try:
                payload = _load_json(path)
            except Exception:
                continue
            if isinstance(payload, dict) and isinstance(payload.get('rows'), list):
                rows = [{str(k): str(v or '') for k, v in row.items()}
                        for row in payload.get('rows') or [] if isinstance(row, dict)]
                if rows:
                    out.append((path.stem, rows))
        for path in sorted(seg_dir.glob('*.csv')):
            out.append((path.stem, _read_csv(path)))
    return out


def _lineage_belongs_to(stem: str, dac_name: str) -> bool:
    """¿El archivo de linaje `stem` pertenece al DAC `dac_name`? Regla por nombre:
    'lineage_DAC_SND_PBI' -> DAC_SND_PBI. Un export genérico sin el nombre del DAC
    (p. ej. 'data'/'data.csv') NO se ata a ningún DAC (evita contaminar a todos)."""
    if not stem or not dac_name:
        return False
    stripped = re.sub(r'^lineage[_-]+', '', str(stem), flags=re.I).strip().upper()
    dac = str(dac_name).strip().upper()
    return stripped == dac or dac in re.sub(r'[^A-Z0-9_]', '_', str(stem).upper())


def _lineage_for_dac(sources: list[tuple[str, list[dict[str, str]]]], dac_name: str) -> list[dict[str, str]]:
    """Filas de linaje SOLO de las fuentes cuyo nombre referencia este DAC,
    deduplicadas preservando orden."""
    rows: list[dict[str, str]] = []
    for stem, src_rows in sources:
        if _lineage_belongs_to(stem, dac_name):
            rows.extend(src_rows)
    seen = set()
    out = []
    for row in rows:
        key = tuple(sorted(row.items()))
        if key not in seen:
            seen.add(key)
            out.append(row)
    return out


def _row_value(row: dict[str, str], *keys: str) -> str:
    by_lower = {str(k).strip().lower(): str(v or '').strip() for k, v in (row or {}).items()}
    for key in keys:
        value = by_lower.get(key.lower())
        if value:
            return value
    return ''


def _looks_like_lineage_view(value: str) -> bool:
    raw = str(value or '').strip().strip('`')
    if not raw:
        return False
    norm = re.sub(r'[^a-z0-9]+', ' ', raw.lower()).strip()
    if norm in {'business name', 'technical name', 'criterio', 'contacto', 'owner', 'estructura', 'principal element', 'identificador', 'source entity', 'entidad de permisos', 'regla de acceso', 'criterios', 'campos gobernados', 'email', 'user id'}:
        return False
    return bool(re.match(r'^(?:[0-9][A-Z0-9_]*|[A-Z]+[0-9]*[A-Z0-9_]*_[A-Z0-9_]+)$', raw.upper()))


def _lineage_views(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows or []:
        space = _row_value(row, 'SPACE_ID', 'SPACE', 'SCHEMA_NAME')
        view = _row_value(row, 'VIEW_NAME', 'VIEW', 'CONSUMER_VIEW', 'OBJECT_NAME', 'DEPENDENT_OBJECT_NAME')
        if not _looks_like_lineage_view(view):
            continue
        key = (space.upper(), view.upper())
        if key in seen:
            continue
        seen.add(key)
        out.append({'space': space, 'view': view})
    return out


def _discover_client_roots(inbound_root: Path) -> list[tuple[str, list[Path]]]:
    """[(cliente, [carpetas físicas de entrada])] — el pool de un cliente puede
    tener MÁS DE UNA carpeta física (legacy `Seguridad/` + CLI
    `data-access-controls/` coexistiendo, mixed-mode) — se unen en un solo
    pool lógico, nunca se pierde ninguna. Matching explícitamente case-
    insensitive vía `_is_security_input_dir` (ver arriba)."""
    inbound_root = Path(inbound_root)
    if _security_dir_kind(inbound_root.name) in _SECURITY_INPUT_DIR_NAMES:
        return [(inbound_root.parent.name.replace('_', ' '), [inbound_root])]
    direct = sorted(p for p in inbound_root.iterdir() if _is_security_input_dir(p)) \
        if inbound_root.exists() else []
    if direct:
        return [(inbound_root.name.replace('_', ' '), direct)]
    roots = []
    for child in sorted(inbound_root.iterdir() if inbound_root.exists() else []):
        if not child.is_dir():
            continue
        dirs = sorted(p for p in child.iterdir() if _is_security_input_dir(p))
        if dirs:
            roots.append((child.name.replace('_', ' '), dirs))
    return roots


def _source_signature(dirs: list[Path], inbound_root: Path) -> dict[str, Any]:
    files = []
    for root in dirs:
        for path in sorted(p for p in root.rglob('*') if p.is_file()):
            if _is_office_temp_file(path) or path.suffix.lower() not in SECURITY_SOURCE_EXTENSIONS:
                continue
            stat = path.stat()
            try:
                rel = path.relative_to(inbound_root).as_posix()
            except ValueError:
                rel = path.as_posix()
            # Sin mtime: la descarga de SharePoint usa un temp nuevo cada vez → el mtime
            # siempre cambia. Identidad estable = sha256 (contenido) + ruta relativa.
            files.append({
                'path': rel,
                'size': stat.st_size,
                'sha256': _sha256(path),
            })
    return {'files': sorted(files, key=lambda f: f['path'])}


def _client_key(dirs: list[Path], inbound_root: Path, cliente: str) -> str:
    rels = []
    for root in dirs:
        try:
            rels.append(root.relative_to(inbound_root).as_posix())
        except ValueError:
            rels.append(root.as_posix())
    rel = '|'.join(sorted(rels))
    return rel if rel and rel != '.' else cliente.replace(' ', '_')


def _read_manual(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    match = re.match(r'^---\n(.*?)\n---', path.read_text(encoding='utf-8', errors='ignore'), re.S)
    if not match:
        return {}
    try:
        fm = yaml.safe_load(match.group(1)) or {}
    except Exception:
        return {}
    return fm.get('manual') or {}



def _xlsx_shared_strings(z: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(z.read('xl/sharedStrings.xml'))
    except KeyError:
        return []
    ns = {'a': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    values: list[str] = []
    for si in root.findall('a:si', ns):
        values.append(''.join(t.text or '' for t in si.findall('.//a:t', ns)))
    return values


def _xlsx_sheets(z: zipfile.ZipFile) -> dict[str, str]:
    ns = {
        'a': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
        'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    }
    wb = ET.fromstring(z.read('xl/workbook.xml'))
    rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
    relmap = {rel.attrib['Id']: rel.attrib['Target'] for rel in rels}
    out: dict[str, str] = {}
    for sheet in wb.findall('a:sheets/a:sheet', ns):
        rid = sheet.attrib.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
        target = relmap.get(rid or '')
        if target:
            out[sheet.attrib['name']] = 'xl/' + target.lstrip('/')
    return out


def _xlsx_colnum(ref: str) -> int:
    m = re.match(r'([A-Z]+)', ref or '')
    if not m:
        return 0
    n = 0
    for ch in m.group(1):
        n = n * 26 + ord(ch) - 64
    return n


def _xlsx_cell_value(cell: ET.Element, shared: list[str], ns: dict[str, str]) -> str:
    v = cell.find('a:v', ns)
    if v is None:
        inline = cell.find('a:is', ns)
        if inline is not None:
            return ''.join(t.text or '' for t in inline.findall('.//a:t', ns)).strip()
        return ''
    raw = v.text or ''
    if cell.attrib.get('t') == 's' and raw.isdigit():
        idx = int(raw)
        return shared[idx].strip() if idx < len(shared) else raw
    return raw.strip()


def _xlsx_rows(path: Path, sheet_name: str) -> list[list[str]]:
    ns = {'a': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with zipfile.ZipFile(path) as z:
        shared = _xlsx_shared_strings(z)
        sheets = _xlsx_sheets(z)
        sheet_path = sheets.get(sheet_name)
        if not sheet_path:
            return []
        root = ET.fromstring(z.read(sheet_path))
        rows: list[list[str]] = []
        for row in root.findall('a:sheetData/a:row', ns):
            vals = {_xlsx_colnum(c.attrib.get('r', '')): _xlsx_cell_value(c, shared, ns) for c in row.findall('a:c', ns)}
            if not vals:
                rows.append([])
                continue
            max_col = max(vals)
            rows.append([vals.get(i, '') for i in range(1, max_col + 1)])
        return rows


def _xlsx_sheet_names(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as z:
        return list(_xlsx_sheets(z).keys())


def _role_permissions_from_sheet(rows: list[list[str]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    actions = [str(v or '').strip() for v in rows[0][1:9]]
    out: list[dict[str, Any]] = []
    for row in rows[1:]:
        privilege = str(row[0] if row else '').strip()
        if not privilege:
            continue
        granted = []
        for idx, action in enumerate(actions, start=1):
            if action and idx < len(row) and str(row[idx]).strip().upper() == 'X':
                granted.append(action)
        if granted:
            out.append({'privilege': privilege, 'permissions': granted})
    return out


def load_role_matrix(dirs: list[Path], cliente: str) -> list[SecurityRoleMatrix]:
    matrices: list[SecurityRoleMatrix] = []
    for seg_dir in dirs:
        for path in sorted(seg_dir.glob('*.xlsx')):
            if _is_office_temp_file(path):
                continue
            try:
                sheets = _xlsx_sheet_names(path)
                cover_rows = _xlsx_rows(path, 'Cover')
            except Exception:
                continue
            if not cover_rows:
                continue
            headers = [str(h or '').strip() for h in cover_rows[0]]
            roles: list[dict[str, Any]] = []
            for row in cover_rows[1:]:
                if not row or not str(row[0] if len(row) else '').strip():
                    continue
                item = {headers[i]: row[i] if i < len(row) else '' for i in range(len(headers)) if headers[i]}
                role_id = str(item.get('Role ID') or '').strip()
                if not role_id:
                    continue
                item['role_id'] = role_id
                item['category'] = 'scope' if role_id.upper().startswith('SRC_') else 'global'
                if role_id in sheets:
                    item['permissions'] = _role_permissions_from_sheet(_xlsx_rows(path, role_id))
                else:
                    item['permissions'] = []
                roles.append(item)
            matrices.append(SecurityRoleMatrix(
                cliente=cliente,
                source_file=path.name,
                roles=roles,
                global_roles=[r for r in roles if r.get('category') == 'global'],
                scoped_roles=[r for r in roles if r.get('category') == 'scope'],
                permission_rows=[{'role_id': r.get('role_id'), **perm} for r in roles for perm in r.get('permissions', [])],
            ))
    return matrices
def load_security(dirs: list[Path], cliente: str) -> list[SecurityArtifact]:
    definitions: dict[str, dict[str, Any]] = {}
    payloads: list[tuple[Path, dict[str, Any]]] = []
    # Identidad SEMÁNTICA + colisión cross-folder (mismo contrato que
    # `generate_catalog.load_folder`, Ticket A/§5): la firma canónica del
    # `definition` detecta contenido divergente; sólo es colisión si además
    # viene de OTRA carpeta física (legacy `Seguridad/` vs CLI
    # `data-access-controls/`) — el merge intra-carpeta queda igual que antes.
    _defn_origin: dict[str, tuple[str, Path]] = {}
    _collisions: list[str] = []
    for seg_dir in dirs:
        for path in sorted(seg_dir.glob('*.json')):
            try:
                payload = _load_json(path)
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            payloads.append((path, payload))
            defs = payload.get('definitions')
            if not isinstance(defs, dict):
                continue
            for name, defn in defs.items():
                if not isinstance(defn, dict):
                    continue
                sig = json.dumps(defn, sort_keys=True, ensure_ascii=False, default=str)
                prev = _defn_origin.get(name)
                if prev is None:
                    _defn_origin[name] = (sig, seg_dir)
                elif prev[1] != seg_dir and prev[0] != sig:
                    _collisions.append(
                        f"'{name}' definido distinto en {prev[1].name}/ y {seg_dir.name}/")
                definitions[name] = defn
    if _collisions:
        raise SecurityCollisionError(
            f"Colisión de identidad mixed-mode en seguridad/DAC de {cliente} "
            "(misma entidad, contenido distinto en carpetas físicas diferentes): "
            + "; ".join(sorted(set(_collisions))))
    lineage_sources = _read_lineage_sources(dirs)
    artifacts: list[SecurityArtifact] = []
    for name, defn in sorted(definitions.items(), key=lambda kv: kv[0]):
        dac_def = defn.get('@DataWarehouse.dataAccessControl.definition') if isinstance(defn, dict) else None
        if not isinstance(dac_def, dict):
            continue
        source = str(dac_def.get('sourceEntity') or '')
        source_def = definitions.get(source) or {}
        file_payload_summary = ''
        global_payload_summary = ''
        for payload_path, payload in payloads:
            found = _summary(payload)
            if not found:
                continue
            if payload_path.stem.lower() == name.lower():
                file_payload_summary = found
                break
            if not global_payload_summary:
                global_payload_summary = found
        artifact = SecurityArtifact(
            cliente=cliente,
            criterio=_criterion(defn, name),
            dac_name=name,
            dac_label=str(defn.get('@EndUserText.label') or name),
            source_entity=source,
            principal_element=str(dac_def.get('principalElement') or ''),
            contact=str(defn.get('@DataWarehouse.businessDefinition.contact') or ''),
            structure=_structure(defn),
            summary=_summary(defn) or file_payload_summary or global_payload_summary,
            fields=_field_rows(defn.get('elements') or {}),
            control_fields=_field_rows(source_def.get('elements') or {}),
            lineage_rows=_lineage_for_dac(lineage_sources, name),
        )
        artifacts.append(artifact)
    return artifacts


def _verdict(art: SecurityArtifact) -> tuple[str, str]:
    has_control = bool(art.source_entity and art.control_fields)
    has_principal = bool(art.principal_element)
    has_lineage = bool(_lineage_views(art.lineage_rows))
    if has_control and has_principal and has_lineage:
        return 'plena', 'Referencia Danwor — reutilizable'
    if has_control and has_principal:
        return 'buena', 'Reutilizable con nota de linaje'
    return 'parcial', 'Inspiración conceptual con reservas'


def _md_value(value: Any, code: bool = False) -> str:
    text = str(value or '').strip()
    if not text:
        return '-'
    text = text.replace('|', '\\|')
    return f'`{text}`' if code else text


def _md_code_lines(values: list[Any]) -> str:
    lines: list[str] = []
    for value in values or []:
        text = str(value or '').strip()
        if not text:
            continue
        text = text.replace('`', '').replace('|', '\\|')
        lines.append(f'`{text}`')
    return '<br>'.join(lines) if lines else '-'


def render_security(art: SecurityArtifact, manual: dict[str, Any]) -> str:
    verdict, apto = _verdict(art)
    if manual.get('veredicto'):
        verdict = str(manual.get('veredicto'))
    if manual.get('apto'):
        apto = str(manual.get('apto'))
    lineage_views = _lineage_views(art.lineage_rows)
    spaces = sorted({row.get('space', '') for row in lineage_views if row.get('space')})
    views = sorted({row.get('view', '') for row in lineage_views if row.get('view')})
    summary = str(manual.get('summary') or manual.get('resumen') or art.summary or '').strip()
    auto = {
        'tipo': 'security',
        'cliente': art.cliente,
        'criterio': art.criterio,
        'dac': art.dac_name,
        'dac_label': art.dac_label,
        'source_entity': art.source_entity,
        'principal_element': art.principal_element,
        'governed_fields': [f['name'] for f in art.fields],
        'contact': art.contact or None,
        'structure': art.structure or None,
        'summary': summary or None,
        'veredicto': verdict,
        'apto': apto,
        'spaces': spaces,
        'lineage_views': lineage_views,
        'consumers': len(views),
    }
    fm = {'auto': auto, 'manual': manual or {}}
    lines = [
        '---',
        yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).strip(),
        '---',
        '',
        f'# {{dw-black}}Seguridad DAC · {{dw-gold}}{art.criterio}',
        '',
        f'> El criterio **{art.criterio}** se materializa mediante el DAC `{art.dac_name}` y la tabla/control `{art.source_entity}`.',
        '',
        f'## {{dw-black}}Definición DAC · {{dw-gold}}{art.dac_name}',
        '',
    ]
    if summary:
        # Subtítulo de sección (primera línea = blockquote): `render_sections`
        # (webapp/services/content_service.py) lo detecta como `has_subtitle` y
        # lo estiliza como `.section-subtitle` (línea vertical gold, sin fondo)
        # — MISMA fuente que el tile (`SecurityArtifact.summary`/`e.summary` en
        # `catalog_security.html`), nunca un segundo campo/valor.
        lines.append(f'> {summary}')
        lines.append('')
    lines.extend([
        '| Propiedad | Valor |',
        '|---|---|',
        '| {{table_group: Identificación}} | |',
        f'| Business Name | {_md_value(art.dac_label)} |',
        f'| Technical Name | {_md_value(art.dac_name, True)} |',
        f'| Owner | {_md_value(art.contact)} |',
        f'| Estructura | {_md_value(art.structure)} |',
        f'| Identificador | {_md_value(art.principal_element, True)} |',
        f'| Entidad de Permisos | {_md_value(art.source_entity, True)} |',
        '| {{table_group: Criterios}} | |',
        f'| Campos gobernados | {_md_code_lines([f["name"] for f in art.fields])} |',
        '',
        f'## {{dw-black}}Tabla Control · {{dw-gold}}{art.source_entity}',
        '',
        '| Campo | Tipo | Longitud | Clave | Obligatorio |',
        '|---|---|---|---|---|',
    ])
    for f in art.control_fields:
        lines.append(
            f'| {_md_value(f["name"], True)} | {_md_value(f["type"])} | {_md_value(f["length"])} | '
            f'{"Sí" if f["key"] else "No"} | {"Sí" if f["not_null"] else "No"} |'
        )
    lines.extend([
        '',
        f'## {{dw-black}}Uso / Linaje · {{dw-gold}}{len(views)} vistas consumidoras',
        '',
    ])
    if lineage_views:
        lines.extend(['| Espacio | Vista consumidora |', '|---|---|'])
        for row in lineage_views:
            lines.append(f'| {_md_value(row.get("space"), True)} | {_md_value(row.get("view"), True)} |')
    else:
        lines.append('No se detectó linaje consumidor en la muestra disponible.')
    lines.extend([
        '',
        '## Cumplimiento',
        '',
        f'> Esta lectura consolida la evidencia de seguridad para **{art.criterio}** en {art.cliente} y explica por qué el catálogo lo clasifica como **{verdict.upper()} · {apto}**.',
        '',
        f'- ✓ DAC definido: `{art.dac_name}`.',
        f'- ✓ Tabla/control referenciada: `{art.source_entity}`.' if art.source_entity else '- ⚠ Tabla/control no detectada.',
        f'- ✓ Principal element configurado: `{art.principal_element}`.' if art.principal_element else '- ⚠ Principal element no detectado.',
        f'- ✓ Linaje consumidor detectado: {len(views)} vista(s) en {len(spaces)} espacio(s).' if views else '- ⚠ Linaje consumidor no informado.',
    ])
    return '\n'.join(lines).strip() + '\n'



ROLE_MATRIX_TEMPLATE_URL = 'https://danwor.sharepoint.com/:x:/s/PrcticaAnalytics/IQC_KaECHiU5TrOjJBpDhkC7AeH7NJUQhq3AACTGOSpltG8?e=B9uJub'


def _role_anchor(role_id: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', str(role_id or '').lower()).strip('-')
    return f'role-{slug or "sin-id"}'


def _role_has_permissions(role: dict[str, Any]) -> bool:
    return bool(role.get('permissions')) and bool(_role_permission_actions(role))


_ROLE_ENVS = ('DEV', 'QA', 'PRD')


def _role_layer(role_id: str, desc: str = '') -> str:
    """Capa de Datasphere en la que opera el rol, derivada de su función."""
    t = f'{role_id} {desc}'.lower()
    if 'integrator' in t:
        return 'Integración'
    if 'modeler' in t or 'modelador' in t:
        return 'Modelado'
    if 'viewer' in t:
        return 'Consumo'
    return 'Gobierno y Administración'


def _role_scopes(role: dict[str, Any]) -> list[str]:
    """Entornos (DEV/QA/PRD) del alcance del rol, desde la columna Scopes.
    Fiel a la matriz: si un rol debe cubrir otro entorno (p. ej. DAC hasta PRD), se
    corrige la celda Scopes en el XLSX y el catálogo lo refleja al regenerar."""
    raw = str(role.get('Scopes') or '').upper()
    return [e for e in _ROLE_ENVS if e in raw]


_ROLE_ALL_LAYERS = ('Integración', 'Modelado', 'Consumo', 'Gobierno y Administración')


def _role_is_dac(role: dict[str, Any]) -> bool:
    """¿El rol gobierna Data Access Control (DAC)? Por función, no por id."""
    ctx = f"{role.get('role_id', '')} {role.get('Role Description', '')} {role.get('Purpose', '')}".lower()
    return 'data access control' in ctx or 'dac' in re.split(r'[^a-z0-9]+', ctx)


def _role_verdict(matrix: 'SecurityRoleMatrix') -> tuple[str, str, list[tuple[str, bool, str]]]:
    """Rúbrica de cumplimiento de la matriz de roles (mismo espíritu que `_verdict` de
    DAC). Ejes: separación global/alcance, cobertura de las 4 capas de Datasphere,
    presencia de un rol DAC, y AUSENCIA de modelado/integración en Producción (riesgo).
    Devuelve (veredicto, apto, checks) con checks=[(título, ok, detalle)] para explicar
    el semáforo en la sección Cumplimiento. El bloque manual del senior puede pisarlo."""
    roles = list(matrix.global_roles) + list(matrix.scoped_roles)

    def _lyr(r):
        return _role_layer(str(r.get('role_id') or ''), str(r.get('Role Description') or ''))

    layers = {_lyr(r) for r in roles}
    missing = [l for l in _ROLE_ALL_LAYERS if l not in layers]
    has_global, has_scoped = bool(matrix.global_roles), bool(matrix.scoped_roles)
    has_dac = any(_role_is_dac(r) for r in roles)
    prd_model = [str(r.get('role_id') or '') for r in roles
                 if _lyr(r) in ('Integración', 'Modelado') and 'PRD' in _role_scopes(r)]
    checks = [
        ('Separación de funciones (global / alcance)', has_global and has_scoped,
         f'{len(matrix.global_roles)} globales · {len(matrix.scoped_roles)} de alcance'),
        ('Cobertura de capas (Integración · Modelado · Consumo · Gobierno)', not missing,
         'las 4 capas con al menos un rol' if not missing else 'faltan: ' + ', '.join(missing)),
        ('Control de acceso a datos (DAC)', has_dac,
         'rol DAC presente' if has_dac else 'sin rol de Data Access Control'),
        ('Sin modelado/integración en Producción', not prd_model,
         'ningún rol de modelado/integración alcanza PRD' if not prd_model else 'alcanza PRD: ' + ', '.join(prd_model)),
    ]
    if prd_model or not (has_global and has_scoped):
        return 'parcial', 'Inspiración conceptual con reservas — revisar segregación de roles', checks
    if not missing and has_dac:
        return 'plena', 'Referencia Danwor — segregación por capa y entorno con control de datos', checks
    return 'buena', 'Reutilizable con validación de alcance', checks


def _roles_for_diagram(matrix: 'SecurityRoleMatrix') -> list[dict[str, Any]]:
    """Lista compacta de roles para el SVG de cobertura del catálogo: id, categoría
    (global/scope), entornos del alcance, capa y ancla a su sección (si tiene detalle).
    La geometría/envolvente se resuelve en el copiloto (main.py) al renderizar."""
    out = []
    for r in list(matrix.global_roles) + list(matrix.scoped_roles):
        rid = str(r.get('role_id') or '')
        out.append({
            'id': rid,
            'category': str(r.get('category') or 'scope'),
            'scopes': _role_scopes(r),
            'layer': _role_layer(rid, str(r.get('Role Description') or '')),
            # Todos los nodos del SVG son clickeables: con detalle → su sección; sin
            # detalle → el resumen de roles (donde igual aparecen listados).
            'anchor': _role_anchor(rid) if _role_has_permissions(r) else 'roles-overview',
        })
    return out


def _role_link(role_id: str) -> str:
    value = str(role_id or '').strip()
    if not value:
        return ''
    return f'[{_md_value(value, True)}](#{_role_anchor(value)})'


def _role_description(role: dict[str, Any]) -> str:
    return str(role.get('Role Description') or role.get('Description') or '').strip()


def _role_permission_actions(role: dict[str, Any]) -> list[str]:
    preferred = ['Create', 'Read', 'Update', 'Delete', 'Maintain', 'Execute', 'Grant', 'Share']
    found: list[str] = []
    for perm in role.get('permissions') or []:
        for action in perm.get('permissions') or []:
            action = str(action or '').strip()
            if action and action not in found:
                found.append(action)
    return [a for a in preferred if a in found] + [a for a in found if a not in preferred]


def _append_roles_index(lines: list[str], roles_with_detail: list[dict[str, Any]], start_idx: int = 4) -> None:
    lines.extend([
        '',
        '---',
        '',
        '### EN ESTE M\u00d3DULO',
        '',
    ])
    for offset, role in enumerate(roles_with_detail):
        idx = start_idx + offset
        role_id = str(role.get('role_id') or '').strip()
        desc = _role_description(role)
        label = role_id if not desc else f'{role_id} \u2014 {desc}'
        lines.append(f'- **{idx:02d}** [{_md_value(label)}](#{_role_anchor(role_id)})')
    lines.extend(['', '---', ''])


def _append_role_permission_matrix(lines: list[str], role: dict[str, Any], idx: int) -> None:
    role_id = str(role.get('role_id') or '').strip()
    role_kind = 'Global' if role.get('category') == 'global' else 'Alcance'
    # Sin [NN] baked: el n\u00famero de secci\u00f3n lo pone render_sections (eyebrow dorado);
    # duplicarlo ac\u00e1 imprim\u00eda "[04] [04] role_id".
    title = f'## {{dw-black}}{role_id} \u00b7 {{dw-gold}}Matriz de permisos'
    lines.extend(['', f'<a id="{_role_anchor(role_id)}"></a>', title, ''])
    desc = _role_description(role)
    if desc:
        lines.extend([f'> Rol de **{role_kind.lower()}**: {desc}.', ''])
    else:
        lines.extend([f'> Rol de **{role_kind.lower()}**.', ''])

    actions = _role_permission_actions(role)
    permissions = role.get('permissions') or []
    if not actions or not permissions:
        return

    header = '| Objeto de autorizaci\u00f3n | ' + ' | '.join(_md_value(a) for a in actions) + ' |'
    separator = '|---|' + '|'.join('---' for _ in actions) + '|'
    lines.extend([header, separator])
    for perm in permissions:
        privilege = _md_value(perm.get('privilege'), True)
        granted = {str(v or '').strip() for v in (perm.get('permissions') or [])}
        cells = ['\u2713' if action in granted else '' for action in actions]
        lines.append('| ' + privilege + ' | ' + ' | '.join(cells) + ' |')


def render_role_matrix(matrix: SecurityRoleMatrix, manual: dict[str, Any]) -> str:
    permission_count = len(matrix.permission_rows)
    auto_verdict, auto_apto, verdict_checks = _role_verdict(matrix)
    verdict = str(manual.get('veredicto') or auto_verdict)
    apto = str(manual.get('apto') or auto_apto)
    summary = str(manual.get('summary') or manual.get('resumen') or 'Matriz de roles y permisos Datasphere para validar segregaci\u00f3n entre roles globales, roles de alcance y privilegios asignados.').strip()
    template_url = str(manual.get('template_url') or ROLE_MATRIX_TEMPLATE_URL).strip()
    auto = {
        'tipo': 'security',
        'family': 'roles',
        'cliente': matrix.cliente,
        'criterio': 'Seguridad de Roles',
        'dac': '',
        'summary': summary,
        'veredicto': verdict,
        'apto': apto,
        'role_count': len(matrix.roles),
        'global_role_count': len(matrix.global_roles),
        'scoped_role_count': len(matrix.scoped_roles),
        'permission_count': permission_count,
        'source_file': matrix.source_file,
        'roles': _roles_for_diagram(matrix),
    }
    fm = {'auto': auto, 'manual': manual or {}}
    if template_url:
        source_link = f'<a href="{html.escape(template_url, quote=True)}" target="_blank" rel="noopener noreferrer">{_md_value(matrix.source_file, True)}</a>'
    else:
        source_link = _md_value(matrix.source_file, True)
    # TODOS los roles tienen sección (así el SVG de cobertura, que se arma desde las
    # secciones renderizadas, puede mostrar cada rol con su número de sección). Los que
    # no tienen matriz de permisos igual muestran su descripción/alcance (sección breve).
    roles_with_detail = list(matrix.global_roles) + list(matrix.scoped_roles)
    lines = [
        '---',
        yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).strip(),
        '---',
        '',
        '{{support: Matriz template="' + template_url + '"}}' if template_url else '',
        '# {dw-black}Seguridad de Roles \u00b7 {dw-gold}Matriz de permisos',
        '',
        f'> La matriz **{matrix.source_file}** consolida roles globales, roles de alcance y privilegios asignados para {matrix.cliente}.',
    ]
    if roles_with_detail:
        _append_roles_index(lines, roles_with_detail)
    lines.extend([
        '',
        '<a id="roles-overview"></a>',
        '## {dw-black}Resumen \u00b7 {dw-gold}Roles',
        '',
        '| Propiedad | Valor |',
        '|---|---|',
        '| {{table_group: Identificaci\u00f3n}} | |',
        f'| Cliente | {_md_value(matrix.cliente)} |',
        f'| Archivo fuente | {source_link} |',
        f'| Roles globales | {len(matrix.global_roles)} |',
        f'| Roles de alcance | {len(matrix.scoped_roles)} |',
        f'| Privilegios asignados | {permission_count} |',
        '',
        '## {dw-black}Roles Globales \u00b7 {dw-gold}Tenant',
        '',
        '| Role ID | Descripci\u00f3n | Basado en | Asignaci\u00f3n directa |',
        '|---|---|---|---|',
    ])
    detailed_ids = {str(role.get('role_id') or '').strip() for role in roles_with_detail}
    for role in matrix.global_roles:
        role_id = str(role.get('role_id') or '').strip()
        role_ref = _role_link(role_id) if role_id in detailed_ids else _md_value(role_id, True)
        lines.append(f'| {role_ref} | {_md_value(role.get("Role Description"))} | {_md_value(role.get("Based On (Reference)"))} | {_md_value(role.get("Direct Assignment possible?"))} |')
    lines.extend(['', '## {dw-black}Roles de Alcance \u00b7 {dw-gold}Espacios', '', '| Role ID | Descripci\u00f3n | Hereda de | Scopes |', '|---|---|---|---|'])
    for role in matrix.scoped_roles:
        role_id = str(role.get('role_id') or '').strip()
        role_ref = _role_link(role_id) if role_id in detailed_ids else _md_value(role_id, True)
        lines.append(f'| {role_ref} | {_md_value(role.get("Role Description"))} | {_md_value(role.get("Based On (Reference)"), True)} | {_md_value(role.get("Scopes"))} |')

    for offset, role in enumerate(roles_with_detail):
        _append_role_permission_matrix(lines, role, 4 + offset)

    lines.extend([
        '',
        '## Cumplimiento',
        '',
        f'> Esta lectura consolida la matriz de roles de {matrix.cliente} y explica por qu\u00e9 el cat\u00e1logo la clasifica como **{verdict.upper()} \u00b7 {apto}**.',
        '',
    ])
    for titulo, ok, detalle in verdict_checks:
        lines.append(f'- {"\u2713" if ok else "\u26a0"} **{titulo}** \u2014 {detalle}.')
    lines.append(f'- Privilegios asignados detectados: {permission_count}.')
    return '\n'.join(line for line in lines if line is not None).strip() + '\n'


def write_role_catalog(cliente: str, matrices: list[SecurityRoleMatrix], out_root: Path) -> str:
    base = Path(out_root) / SECURITY_DIR / _slug(cliente)
    base.mkdir(parents=True, exist_ok=True)
    count = 0
    for matrix in matrices:
        path = base / f'Seguridad_de_Roles__{_slug(Path(matrix.source_file).stem)}.md'
        path.write_text(render_role_matrix(matrix, _read_manual(path)), encoding='utf-8')
        count += 1
    return f'[OK] {cliente}: {count} matriz(es) de roles'
def write_security_catalog(cliente: str, artifacts: list[SecurityArtifact], out_root: Path) -> str:
    base = Path(out_root) / SECURITY_DIR / _slug(cliente)
    base.mkdir(parents=True, exist_ok=True)
    current: set[str] = set()
    for art in artifacts:
        fname = f'{_slug(art.criterio)}__{_slug(art.dac_name)}.md'
        current.add(fname)
        path = base / fname
        path.write_text(render_security(art, _read_manual(path)), encoding='utf-8')
    # Limpiar .md de DAC obsoletos (p. ej. un criterio/label renombrado deja el .md
    # viejo → tile duplicado). NO tocar los de roles (Seguridad_de_Roles__*).
    removed = 0
    for p in base.glob('*.md'):
        if p.name.startswith('Seguridad_de_Roles__') or p.name in current:
            continue
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    tail = f' ({removed} obsoleto(s) eliminado(s))' if removed else ''
    return f'[OK] {cliente}: {len(current)} criterio(s) de seguridad{tail}'


def generate_all(inbound_root, out_root) -> str:
    inbound_root = Path(inbound_root)
    out_root = Path(out_root)
    roots = _discover_client_roots(inbound_root)
    if not roots:
        return 'Catálogo de seguridad: sin carpetas Seguridad.'
    generator_sig = _generator_sha256()
    manifest = _load_manifest(out_root)
    previous_sig = manifest.get('security_generator_sha256')
    force_all = previous_sig != generator_sig
    entries = manifest.get('security_catalog', {}) if not force_all else {}
    lines: list[str] = []
    generated = 0
    skipped = 0
    next_entries = {}
    for cliente, dirs in sorted(roots, key=lambda item: item[0].lower()):
        key = _client_key(dirs, inbound_root, cliente)
        signature = _source_signature(dirs, inbound_root)
        previous = entries.get(key, {})
        artifacts = load_security(dirs, cliente)
        role_matrices = load_role_matrix(dirs, cliente)
        total_count = len(artifacts) + len(role_matrices)
        if (not force_all and previous.get('signature') == signature and previous.get('count') == total_count):
            skipped += 1
            next_entries[key] = previous
            continue
        if not artifacts and not role_matrices:
            skipped += 1
            next_entries[key] = {'cliente': cliente, 'signature': signature, 'count': 0}
            continue
        if artifacts:
            lines.append(write_security_catalog(cliente, artifacts, out_root))
        if role_matrices:
            lines.append(write_role_catalog(cliente, role_matrices, out_root))
        generated += 1
        next_entries[key] = {
            'cliente': cliente,
            'signature': signature,
            'count': total_count,
        }
    next_manifest = dict(manifest)
    next_manifest.update({
        'security_generator_sha256': generator_sig,
        'security_catalog': next_entries,
    })
    _save_manifest(out_root, next_manifest)
    if not lines:
        lines.append('[OK] Sin cambios en seguridad; se conserva el catálogo generado.')
    return f'Catálogo de seguridad regenerado ({generated} cliente(s), {skipped} sin cambios):\n' + '\n'.join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description='Generador del catálogo de seguridad/DAC Datasphere')
    ap.add_argument('--all', dest='inbound', required=True, help='Ra?z de entrada o carpeta de cliente/Seguridad')
    ap.add_argument('--out', dest='out', required=True, help='Carpeta de salida')
    args = ap.parse_args()
    print(generate_all(args.inbound, args.out))


if __name__ == '__main__':
    main()
