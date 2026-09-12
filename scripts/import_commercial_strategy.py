"""import_commercial_strategy.py — one-time/repeatable importer del documento
"Estrategia Comercial 2026" desde el artefacto HTML standalone suministrado
por el owner (Danwor_Estrategia_Comercial_2026.html) hacia el YAML canónico
que consume `webapp.services.commercial_strategy_repository`.

Parser PRAGMÁTICO (mismo criterio que scripts/pbip_report_assessment.py):
NO es un compilador HTML de propósito general — conoce las clases/ids fijos
de ESE artefacto puntual y extrae solo lo que el modelo de contenido de
Comercial/Estrategia necesita. Stdlib únicamente (`html.parser`), sin bs4/
lxml: es un script offline de un solo uso repetible, no vale la pena una
dependencia nueva del proyecto para esto.

El HTML fuente es READ-ONLY input, nunca se copia a git (mismo criterio que
el proyecto PBIP de pbip_report_assessment.py) — el runtime del Hub NUNCA
lo lee; solo lee el YAML de salida.

Markup constreñido (owner decision — "no arbitrary HTML, no WYSIWYG"): el
texto libre (intro/principio/notas/etc.) se extrae a un mini-markup propio,
NUNCA HTML crudo: `**negrita**`, `*cursiva*`, `[label](url)` (solo http/https),
`\\n` para <br>. `webapp.services.commercial_strategy_repository`/el
renderer son quienes conocen ese mini-markup — este script solo lo produce.

Uso:
    python scripts/import_commercial_strategy.py --source "C:\\ruta\\Danwor_Estrategia_Comercial_2026.html" --out webapp/services/commercial_strategy_data/strategy.yaml
"""
from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

import yaml

_VOID_TAGS = {"br", "img", "hr", "input", "meta", "link"}


class _Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict[str, str], parent: "_Node | None") -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list["_Node | str"] = []
        self.parent = parent


class _TreeBuilder(HTMLParser):
    """Árbol mínimo tag/attrs/children (texto como `str`). Ignora el
    contenido de `<script>`/`<style>` — el runtime no necesita ninguno de
    los dos (la mecánica de tabs/nav queda reimplementada en el Hub)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {}, None)
        self._cursor = self.root
        self._skip_depth = 0
        self._skip_tag = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_depth:
            if tag == self._skip_tag:
                self._skip_depth += 1
            return
        if tag in {"script", "style"}:
            self._skip_depth = 1
            self._skip_tag = tag
            return
        node = _Node(tag, {k: (v or "") for k, v in attrs}, self._cursor)
        self._cursor.children.append(node)
        if tag not in _VOID_TAGS:
            self._cursor = node

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth:
            if tag == self._skip_tag:
                self._skip_depth -= 1
            return
        if tag in _VOID_TAGS:
            return
        node = self._cursor
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root:
            self._cursor = node.parent

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if data:
            self._cursor.children.append(data)


def parse_tree(html_text: str) -> _Node:
    builder = _TreeBuilder()
    builder.feed(html_text)
    return builder.root


# ── selección ────────────────────────────────────────────────────────────

def _classes(node: _Node) -> set[str]:
    return set((node.attrs.get("class") or "").split())


def _matches(node: _Node, tag: str | None, cls: str | None, id_: str | None) -> bool:
    if tag is not None and node.tag != tag:
        return False
    if cls is not None and cls not in _classes(node):
        return False
    if id_ is not None and node.attrs.get("id") != id_:
        return False
    return True


def find_all(node: _Node, tag: str | None = None, cls: str | None = None, id_: str | None = None,
             *, stop_at_cls: str | None = None, _include_self: bool = False) -> list[_Node]:
    """DFS de elementos que matchean. `stop_at_cls`: no desciende dentro de
    un descendiente que tenga esa clase (pero SÍ lo evalúa a él mismo) — usado
    para separar el contenido "de este panel" del contenido de sus sub-paneles
    anidados (segmentos con subtabs)."""
    out: list[_Node] = []
    if _include_self and isinstance(node, _Node) and _matches(node, tag, cls, id_):
        out.append(node)
    for child in node.children:
        if not isinstance(child, _Node):
            continue
        if _matches(child, tag, cls, id_):
            out.append(child)
        if stop_at_cls and stop_at_cls in _classes(child) and not _include_self:
            continue
        out.extend(find_all(child, tag, cls, id_, stop_at_cls=stop_at_cls))
    return out


def find_one(node: _Node, tag: str | None = None, cls: str | None = None, id_: str | None = None) -> _Node | None:
    found = find_all(node, tag, cls, id_)
    return found[0] if found else None


def by_id(root: _Node, id_: str) -> _Node:
    node = find_one(root, id_=id_)
    if node is None:
        raise ValueError(f"No se encontró id={id_!r} en el artefacto fuente")
    return node


_WS_RE = re.compile(r"\s+")
# Como `_WS_RE` pero SIN devorar `\n` — usado por `inline()` para colapsar
# espacios/tabs alrededor de un salto de línea intencional (<br>) sin
# aplastarlo a un espacio (bug real: `_WS_RE` sola perdía todos los <br>).
_HSPACE_RE = re.compile(r"[ \t]+")
_NL_PAD_RE = re.compile(r" *\n *")


def text(node: _Node | str) -> str:
    """Texto plano (sin markup) — para labels/valores/celdas simples."""
    if isinstance(node, str):
        return _WS_RE.sub(" ", node).strip()
    parts = [text(c) for c in node.children]
    return _WS_RE.sub(" ", " ".join(p for p in parts if p)).strip()


def inline(node: _Node | str) -> str:
    """Mini-markup constreñido: **bold**, *italic*, [label](url), \\n por <br>.
    Cualquier estilo/atributo presentacional del HTML fuente (p. ej.
    `<strong style="color:#fff;">`) se descarta a propósito — solo se
    conserva el ÉNFASIS SEMÁNTICO (owner decision: no arbitrary HTML)."""
    if isinstance(node, str):
        return _HSPACE_RE.sub(" ", node)
    out = []
    for child in node.children:
        if isinstance(child, str):
            out.append(_HSPACE_RE.sub(" ", child))
            continue
        if child.tag == "br":
            out.append("\n")
        elif child.tag in {"strong", "b"}:
            out.append(f"**{inline(child).strip()}**")
        elif child.tag in {"em", "i"}:
            out.append(f"*{inline(child).strip()}*")
        elif child.tag == "a":
            href = (child.attrs.get("href") or "").strip()
            label = inline(child).strip()
            out.append(f"[{label}]({href})" if href.startswith(("http://", "https://")) else label)
        elif child.tag == "span":
            out.append(inline(child))
        else:
            out.append(inline(child))
    return _NL_PAD_RE.sub("\n", _HSPACE_RE.sub(" ", "".join(out))).strip()


def _priority(node: _Node) -> dict:
    prio = find_one(node, cls="prio")
    code = "p2"
    if prio is not None:
        classes = _classes(prio) - {"prio"}
        code = next(iter(classes), "p2")
    return {"code": code, "text": text(prio) if prio is not None else ""}


def _table(node: _Node) -> dict:
    rows = find_all(node, tag="tr")
    if not rows:
        return {"headers": [], "rows": []}
    headers = [text(th) for th in find_all(rows[0], tag="th")]
    data_rows = []
    for tr in rows[1:] if headers else rows:
        cells = find_all(tr, tag="td")
        if cells:
            data_rows.append([inline(td) for td in cells])
    return {"headers": headers, "rows": data_rows}


def _bullets(node: _Node | None) -> list[str]:
    if node is None:
        return []
    return [inline(li) for li in find_all(node, tag="li")]


def _pending_box(node: _Node, *, stop_at_cls: str | None = None) -> dict | None:
    """`stop_at_cls="subpanel"` cuando `node` es un panel de segmento CON
    sub-tabs: sin este límite, un `.pending` que en realidad vive DENTRO de
    un sub-panel anidado (p. ej. Segmento 2 → Business One) se atribuía por
    error al panel PADRE (bug real, encontrado comparando el render contra
    el HTML fuente: el "Pendiente de definir" de C-Field NNN mostraba los
    items de su sub-tab Business One, no los propios — que no tiene)."""
    candidates = find_all(node, cls="pending", stop_at_cls=stop_at_cls) if stop_at_cls else find_all(node, cls="pending")
    box = candidates[0] if candidates else None
    if box is None:
        return None
    title = find_one(box, cls="ptitle")
    return {"title": text(title) if title is not None else "", "items": _bullets(find_one(box, tag="ul"))}


# ── secciones ────────────────────────────────────────────────────────────

def extract_meta(root: _Node) -> dict:
    sub = find_one(root, cls="sub")
    lines = inline(sub).split("\n") if sub is not None else ["", ""]
    return {
        "brand": text(find_one(root, cls="name")),
        "doc_title": (lines[0] if lines else "").strip(),
        "doc_subtitle": (lines[1] if len(lines) > 1 else "").strip(),
    }


def extract_nav(root: _Node) -> list[dict]:
    side = by_id(root, "side")
    group_labels = {"Estructura": "estructura", "Pilares": "pilares", "Anexo": "anexo"}
    current_group = "estructura"
    items: list[dict] = []
    for child in side.children:
        if not isinstance(child, _Node):
            continue
        if "navlabel" in _classes(child):
            current_group = group_labels.get(text(child), current_group)
        elif "navitem" in _classes(child):
            items.append({"id": child.attrs.get("data-target", ""), "label": text(child), "group": current_group})
    return items


def extract_hero(root: _Node) -> dict:
    hero = by_id(root, "hero")
    h1 = find_one(hero, tag="h1")
    span = find_one(h1, tag="span") if h1 is not None else None
    prefix = ""
    if h1 is not None:
        prefix = "".join(c for c in h1.children if isinstance(c, str))
        prefix = _WS_RE.sub(" ", prefix).strip()
    bignums = []
    for bn in find_all(hero, cls="bignum"):
        bignums.append({"value": text(find_one(bn, cls="n")), "label": text(find_one(bn, cls="l"))})
    return {
        "kicker": text(find_one(hero, cls="kicker")),
        "title_prefix": prefix,
        "title_accent": text(span) if span is not None else "",
        "lead": inline(find_one(hero, cls="lead")),
        "bignums": bignums,
    }


def _objcards(grid: _Node | None) -> list[dict]:
    if grid is None:
        return []
    return [
        {"label": text(find_one(c, cls="otitle")), "value": text(find_one(c, cls="oval")),
         "detail": inline(find_one(c, cls="odetail"))}
        for c in find_all(grid, cls="objcard")
    ]


def extract_objectives(root: _Node) -> dict:
    sec = by_id(root, "p1")
    objgrids = find_all(sec, cls="objgrid")
    calcnotes = find_all(sec, cls="calcnote")
    tables = find_all(sec, tag="table")
    minitable = next((t for t in tables if "minitable" in _classes(t)), None)
    captable = next((t for t in tables if "captable" in _classes(t)), None)
    funnel = find_one(sec, cls="funnel")
    funnel_rows = []
    if funnel is not None:
        for row in find_all(funnel, cls="frow"):
            level = next((c for c in _classes(row) if re.fullmatch(r"l[1-4]", c)), "l1")
            funnel_rows.append({
                "level": int(level[1]), "label": text(find_one(row, cls="flabel")),
                "value": text(find_one(row, cls="fval")), "sub": text(find_one(row, cls="fsub")),
            })
    riskbox = find_one(sec, cls="riskbox")
    return {
        "eyebrow": text(find_one(sec, cls="eyebrow")),
        "title": text(find_one(sec, cls="stitle")),
        "intro": inline(find_one(sec, cls="intro")),
        "result_targets": _objcards(objgrids[0] if objgrids else None),
        "calc_note": inline(calcnotes[0]) if calcnotes else "",
        "ticket_table": _table(minitable) if minitable is not None else {"headers": [], "rows": []},
        "funnel": funnel_rows,
        "demand_targets": _objcards(objgrids[1] if len(objgrids) > 1 else None),
        "coverage_note": inline(riskbox) if riskbox is not None else "",
        "sensitivity_note": inline(calcnotes[1]) if len(calcnotes) > 1 else "",
        "sensitivity_table": _table(captable) if captable is not None else {"headers": [], "rows": []},
        "assumptions_pending": (_pending_box(sec) or {"items": []})["items"],
    }


def extract_demand_generation(root: _Node) -> dict:
    sec = by_id(root, "genda")
    demand_cards = _objcards(find_one(sec, cls="objgrid"))
    clusters = []
    for card in find_all(sec, cls="clcard"):
        clusters.append({
            "tag": text(find_one(card, cls="cltag")),
            "name": text(find_one(card, tag="h4")),
            "bullets": [inline(row) for row in find_all(card, cls="clrow")],
            "message": inline(find_one(card, cls="clmsg")),
        })
    channels = {}
    for box in find_all(sec, cls="chanbox"):
        key = "inbound" if "inb" in _classes(box) else "outbound"
        channels[key] = {"title": text(find_one(box, cls="chtitle")), "items": _bullets(find_one(box, tag="ul"))}
    events_catalog = _table(find_one(sec, cls="captable"))
    calendar = []
    for col in find_all(sec, cls="evcol"):
        items = []
        for it in find_all(col, cls="evitem"):
            name_node = find_one(it, cls="evname")
            sub_node = find_one(it, cls="evsub")
            items.append({
                "name": text(name_node) if name_node is not None else "",
                "is_date": "date" in (_classes(name_node) if name_node is not None else set()),
                "muted": "muted" in _classes(it),
                "sub": inline(sub_node) if sub_node is not None else "",
            })
        calendar.append({"month": text(find_one(col, cls="evmonth")), "items": items})
    return {
        "eyebrow": text(find_one(sec, cls="eyebrow")),
        "title": text(find_one(sec, cls="stitle")),
        "intro": inline(find_one(sec, cls="intro")),
        "demand_target": demand_cards[0] if demand_cards else None,
        "clusters": clusters,
        "channels": channels,
        "events_catalog": events_catalog,
        "calendar": calendar,
        "owner_note": inline(find_one(sec, cls="riskbox")),
    }


def _ordered_content_blocks(panel: _Node) -> list[tuple[str, object]]:
    """Recorre el panel UNA sola vez, en orden de documento, sin descender
    dentro de `.subpanel` anidados, devolviendo `(kind, payload)` para cada
    `blockLbl`/`ul.plain`/`table.contact` encontrado. Reemplaza un `zip()`
    posicional anterior que emparejaba mal blockLbl↔lista apenas un
    blockLbl (p. ej. "Acceso") correspondía a una TABLA en vez de una lista
    — el conteo de blockLbls y de `ul.plain` deja de coincidir 1:1 en ese
    caso, y `zip` corre el resto de los pares (bug real, encontrado
    comparando la salida contra el HTML fuente)."""
    out: list[tuple[str, object]] = []

    def walk(node: _Node) -> None:
        for child in node.children:
            if not isinstance(child, _Node):
                continue
            classes = _classes(child)
            if "subpanel" in classes:
                continue
            if "blockLbl" in classes:
                out.append(("label", text(child)))
                continue
            if child.tag == "ul" and "plain" in classes:
                out.append(("list", _bullets(child)))
                continue
            if child.tag == "table" and "contact" in classes:
                out.append(("table", _table(child)))
                continue
            walk(child)

    walk(panel)
    return out


def _segment_content(panel: _Node) -> dict:
    """Contenido de UN panel de segmento/sub-tab, SIN descender dentro de
    `.subpanel` anidados (esos se procesan aparte, recursivamente)."""
    notes = [inline(p) for p in find_all(panel, cls="principio", stop_at_cls="subpanel")]
    notes += [inline(p) for p in find_all(panel, cls="intro", stop_at_cls="subpanel")]

    access: dict | None = None
    strategy: list[str] = []
    extra_table: dict | None = None
    current_label = ""
    for kind, payload in _ordered_content_blocks(panel):
        if kind == "label":
            current_label = str(payload).strip().lower()
        elif kind == "list":
            if current_label.startswith("acceso") and access is None:
                access = {"kind": "list", "items": payload}
            elif current_label.startswith("estrategia"):
                strategy = payload  # type: ignore[assignment]
        elif kind == "table":
            if current_label.startswith("acceso") and access is None:
                access = {"kind": "table", "table": payload}
            else:
                # Tabla adicional bajo el mismo rótulo "Acceso" (solo
                # Segmento 6 · partners regionales: lista de acceso +
                # tabla de partners) — nunca fuerza un segundo slot de
                # tabla al resto de los segmentos.
                extra_table = payload  # type: ignore[assignment]

    pending = _pending_box(panel, stop_at_cls="subpanel")
    return {"notes": notes, "access": access, "strategy": strategy, "pending": pending, "extra_table": extra_table}


def extract_segments(root: _Node) -> dict:
    sec = by_id(root, "p2")
    tabs = {t.attrs.get("data-seg", ""): text(t) for t in find_all(sec, cls="segtab")}
    items = []
    for panel in find_all(sec, cls="segpanel"):
        panel_id = panel.attrs.get("id", "")
        header = find_one(panel, cls="segheader")
        title = text(find_one(header, tag="h3")) if header is not None else ""
        content = _segment_content(panel)
        subtabs = []
        sub_labels = {t.attrs.get("data-sub", ""): text(t) for t in find_all(panel, cls="subtab")}
        for subpanel in find_all(panel, cls="subpanel"):
            sub_id = subpanel.attrs.get("id", "")
            sub_content = _segment_content(subpanel)
            subtabs.append({"id": sub_id, "tab_label": sub_labels.get(sub_id, ""), **sub_content})
        items.append({
            "id": panel_id, "tab_label": tabs.get(panel_id, ""), "title": title,
            "priority": _priority(header) if header is not None else {"code": "p2", "text": ""},
            **content,
            "subtabs": subtabs or None,
        })
    return {
        "eyebrow": text(find_one(sec, cls="eyebrow")),
        "title": text(find_one(sec, cls="stitle")),
        "intro": inline(find_one(sec, cls="intro")),
        "new_name_note": inline(find_one(sec, cls="riskbox")),
        "items": items,
    }


def extract_focus(root: _Node) -> dict:
    sec = by_id(root, "p3")
    questions = [{"n": int(text(find_one(q, cls="qnum")) or 0), "text": text(find_one(q, cls="qtext"))}
                 for q in find_all(sec, cls="qrow")]
    rules_ul = find_all(sec, tag="ul", cls="plain")
    return {
        "eyebrow": text(find_one(sec, cls="eyebrow")),
        "title": text(find_one(sec, cls="stitle")),
        "intro": inline(find_one(sec, cls="intro")),
        "questions": questions,
        "rules": _bullets(rules_ul[0]) if rules_ul else [],
        "coherence_note": inline(find_all(sec, cls="principio")[-1]) if find_all(sec, cls="principio") else "",
    }


def extract_capacity(root: _Node) -> dict:
    sec = by_id(root, "p4")
    return {
        "eyebrow": text(find_one(sec, cls="eyebrow")),
        "title": text(find_one(sec, cls="stitle")),
        "assignments_table": _table(find_one(sec, cls="captable")),
        "priority_rules": [inline(b) for b in find_all(sec, cls="rulebox")],
    }


def extract_cadence(root: _Node) -> dict:
    sec = by_id(root, "p5")
    meetings = []
    for card in find_all(sec, cls="meetcard"):
        meetings.append({
            "freq": text(find_one(card, cls="mfreq")), "title": text(find_one(card, tag="h4")),
            "participants": text(find_one(card, cls="parts")), "bullets": _bullets(find_one(card, tag="ul")),
        })
    return {
        "eyebrow": text(find_one(sec, cls="eyebrow")),
        "title": text(find_one(sec, cls="stitle")),
        "intro": inline(find_one(sec, cls="intro")),
        "meetings": meetings,
        "note": inline(find_one(sec, cls="principio")),
    }


def extract_exclusions(root: _Node) -> dict:
    sec = by_id(root, "p6")
    items = []
    for it in find_all(sec, cls="noitem"):
        p = find_one(it, tag="p")
        items.append({"text": inline(p) if p is not None else ""})
    return {
        "eyebrow": text(find_one(sec, cls="eyebrow")),
        "title": text(find_one(sec, cls="stitle")),
        "items": items,
    }


_Q_RE = re.compile(r"^q[1-4]$", re.I)


def _normalize_q(raw: str) -> str:
    raw = raw.strip()
    if _Q_RE.match(raw):
        return raw.upper()
    return "a_definir"


def extract_next_steps(root: _Node) -> dict:
    sec = by_id(root, "proximos")
    tasks = []
    for i, tr in enumerate(find_all(sec, tag="tr", cls="task-row"), start=1):
        cells = find_all(tr, tag="td")
        # [drag-handle, tarea, origen, responsable, q, delete] — 6 columnas fijas.
        tasks.append({
            "id": f"t{i:02d}",
            "tarea": inline(cells[1]) if len(cells) > 1 else "",
            "origen": inline(cells[2]) if len(cells) > 2 else "",
            "responsable": text(cells[3]) if len(cells) > 3 else "",
            "q": _normalize_q(text(cells[4])) if len(cells) > 4 else "a_definir",
        })
    return {
        "eyebrow": text(find_one(sec, cls="eyebrow")),
        "title": text(find_one(sec, cls="stitle")),
        "intro": inline(find_one(sec, cls="intro")),
        "tasks": tasks,
        "closing_note": inline(find_all(sec, cls="principio")[-1]) if find_all(sec, cls="principio") else "",
    }


def _map_node(node: _Node) -> dict:
    return {
        "name": text(find_one(node, cls="mname")),
        "role": text(find_one(node, cls="mrole")),
        "desc": inline(find_one(node, cls="mdesc")) if find_one(node, cls="mdesc") else "",
        "sellers": text(find_one(node, cls="msellers")) if find_one(node, cls="msellers") else "",
        "highlight": "highlight" in _classes(node),
    }


def _map_tree(panel: _Node) -> dict:
    root_node = find_one(panel, cls="root")
    branch = find_one(panel, cls="mbranch")
    branches = []
    if branch is not None:
        for top in [c for c in branch.children if isinstance(c, _Node) and "mnode" in _classes(c)]:
            entry = _map_node(top)
            subs = find_one(top, cls="msub")
            leaves = []
            if subs is not None:
                for leaf in [c for c in subs.children if isinstance(c, _Node)]:
                    leaf_entry = _map_node(leaf)
                    inner_sub = find_one(leaf, cls="msub")
                    if inner_sub is not None:
                        leaf_entry["children"] = [_map_node(c) for c in inner_sub.children if isinstance(c, _Node)]
                    leaves.append(leaf_entry)
            entry["children"] = leaves
            branches.append(entry)
    return {"root": _map_node(root_node) if root_node is not None else {}, "branches": branches}


def extract_annex_map(root: _Node) -> dict:
    sec = by_id(root, "mapa")
    panels = find_all(sec, cls="mappanel")
    sap_tree = _map_tree(panels[0]) if panels else {"root": {}, "branches": []}
    lob_tree = _map_tree(panels[1]) if len(panels) > 1 else {"root": {}, "branches": []}
    notes = [inline(n) for n in find_all(sec, cls="mapnote")]
    lob_intro = find_one(panels[1], tag="p") if len(panels) > 1 else None
    return {
        "eyebrow": text(find_one(sec, cls="eyebrow")),
        "title": text(find_one(sec, cls="stitle")),
        "intro": inline(find_one(sec, cls="intro")),
        "sap_tree": sap_tree,
        "lob_tree": lob_tree,
        "lob_intro": inline(lob_intro) if lob_intro is not None else "",
        "notes": notes,
    }


def extract_annex_roles(root: _Node) -> dict:
    sec = by_id(root, "roles")
    roles = []
    for card in find_all(sec, cls="rolecard"):
        labeled_lists = find_all(card, tag="ul")
        no_block = find_one(card, cls="no")
        not_responsible = _bullets(find_one(no_block, tag="ul")) if no_block is not None else []
        responsibilities = _bullets(labeled_lists[0]) if labeled_lists else []
        roles.append({
            "name": text(find_one(card, tag="h3")),
            "who": text(find_one(card, cls="who")),
            "mision": inline(find_one(card, cls="mision")),
            "responsibilities": responsibilities,
            "not_responsible": not_responsible,
        })
    return {
        "eyebrow": text(find_one(sec, cls="eyebrow")),
        "title": text(find_one(sec, cls="stitle")),
        "intro": inline(find_one(sec, cls="intro")),
        "roles": roles,
    }


def extract_annex_process(root: _Node) -> dict:
    sec = by_id(root, "proceso")
    stages = []
    for st in find_all(sec, cls="stage"):
        stages.append({
            "n": text(find_one(st, cls="stagenum")),
            "name": text(find_one(st, cls="sname")),
            "meta": text(find_one(st, cls="smeta")),
            "gate": inline(find_one(st, cls="sgate")),
        })
    return {
        "eyebrow": text(find_one(sec, cls="eyebrow")),
        "title": text(find_one(sec, cls="stitle")),
        "intro": inline(find_one(sec, cls="intro")),
        "stages": stages,
        "cooling_note": inline(find_one(sec, cls="coolbar")),
    }


def extract_annex_rules(root: _Node) -> dict:
    sec = by_id(root, "reglas")
    rules = []
    for g in find_all(sec, cls="grow"):
        rules.append({
            "n": text(find_one(g, cls="gnum")),
            "title": text(find_one(g, cls="gtitle")),
            "detail": inline(find_one(g, cls="gdesc")),
        })
    return {
        "eyebrow": text(find_one(sec, cls="eyebrow")),
        "title": text(find_one(sec, cls="stitle")),
        "intro": inline(find_one(sec, cls="intro")),
        "rules": rules,
    }


def build_document(html_text: str) -> dict:
    root = parse_tree(html_text)
    return {
        "version": 1,
        # `revision` (distinto de `version`, el format version del esquema):
        # contador de CONTENIDO para el Save optimista (ver
        # commercial_strategy_repository.py) — el seed es siempre la
        # revision 1; cada `save()` exitoso en runtime la incrementa.
        "revision": 1,
        "meta": extract_meta(root),
        "nav": extract_nav(root),
        "hero": extract_hero(root),
        "objectives": extract_objectives(root),
        "demand_generation": extract_demand_generation(root),
        "segments": extract_segments(root),
        "focus": extract_focus(root),
        "capacity": extract_capacity(root),
        "cadence": extract_cadence(root),
        "exclusions": extract_exclusions(root),
        "next_steps": extract_next_steps(root),
        "annex_map": extract_annex_map(root),
        "annex_roles": extract_annex_roles(root),
        "annex_process": extract_annex_process(root),
        "annex_rules": extract_annex_rules(root),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Ruta al HTML standalone fuente (fuera del repo).")
    parser.add_argument("--out", required=True, help="Ruta del YAML canónico de salida.")
    args = parser.parse_args()

    source_path = Path(args.source)
    html_text = source_path.read_text(encoding="utf-8")
    document = build_document(html_text)
    document["_import"] = {
        "source_filename": source_path.name,
        "imported_at": datetime.now(timezone.utc).isoformat(),
        "importer": "scripts/import_commercial_strategy.py",
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False, default_flow_style=False, width=100),
        encoding="utf-8",
    )
    print(f"OK: {out_path} escrito desde {source_path.name}")


if __name__ == "__main__":
    main()
