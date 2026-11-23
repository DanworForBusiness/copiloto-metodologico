"""Invalidación versionada de agregados ante el cambio de taxonomía (módulo virtual «Catálogos»):
la firma incluye `NORMALIZE_TAXONOMY_VERSION`, así un agregado persistido con la taxonomía anterior se
invalida y recalcula CUANDO hay JSONL; si el JSONL falta temporalmente se conserva el stored (sin recálculo
a vacío). No se borran históricos ni se reescribe el JSONL."""
from __future__ import annotations

import json

from webapp.services import activity_aggregates as A
from webapp.services import activity_context as ax


def _write(tmp_path, day, rows):
    (tmp_path / f"activity_{day}.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


# Un section_view de Catálogo con la forma histórica (area=methodology, module=CP04).
_NAV = {"v": 2, "event": "section_view", "scope": "copilot", "area": "methodology", "kind": "navigation",
        "ts_utc": "2026-11-21T10:00:00", "user": "a@d.com", "module": "CP04", "section": "catalog_flow",
        "attrs": {"view_id": "x"}}


def test_signature_includes_normalize_taxonomy_version(tmp_path):
    _write(tmp_path, "20261121", [_NAV])
    with A.reading_from(tmp_path):
        sig = A._signature(tmp_path / "activity_20261121.jsonl")
    assert sig["norm"] == ax.NORMALIZE_TAXONOMY_VERSION


def test_historical_aggregate_with_old_signature_is_invalidated_and_recomputed(tmp_path):
    # Persistimos un agregado con la TAXONOMÍA ANTERIOR: catalog_flow bajo CP04 y una firma SIN `norm`.
    (tmp_path / "agg").mkdir()
    _write(tmp_path, "20261121", [_NAV])
    src = tmp_path / "activity_20261121.jsonl"
    old = {"schema": A.AGG_SCHEMA, "day": "20261121",
           "rows": [{"vertical": "copilot", "area": "methodology", "module": "CP04", "section": "catalog_flow",
                     "kind": "navigation", "event": "section_view", "cls": "usage", "status": "", "user": "a@d.com",
                     "count": 1, "visits_v1": 0, "view_ids": ["x"], "ts": ["2026-11-21T10:00:00"]}],
           "sessions": {},
           "signature": {"size": src.stat().st_size, "mtime_ns": src.stat().st_mtime_ns,
                         "catalog": __import__("webapp.services.activity_catalog", fromlist=["version"]).version(),
                         "schema": A.AGG_SCHEMA}}  # firma vieja: SIN `norm`
    (tmp_path / "agg" / "20261121.json").write_text(json.dumps(old), encoding="utf-8")
    with A.reading_from(tmp_path):
        loaded = A.load_day("20261121", today="20261122")
    rows = [(r["module"], r["section"]) for r in loaded["rows"] if (r.get("section") or "").startswith("catalog")]
    assert rows == [(ax.CATALOG_VIRTUAL_MODULE, "catalog_flow")]          # recalculado con la taxonomía corregida
    assert "CP04" not in {m for m, _ in rows}
    assert loaded["signature"]["norm"] == ax.NORMALIZE_TAXONOMY_VERSION   # persistido con la firma nueva
    # Se reescribió el agregado (write-through), NO el JSONL original.
    assert json.loads((tmp_path / f"activity_20261121.jsonl").read_text(encoding="utf-8").splitlines()[0])["module"] == "CP04"


def test_missing_jsonl_keeps_persisted_aggregate_without_recompute(tmp_path):
    # Sin JSONL (restauración pendiente): se conserva el agregado persistido tal cual, sin recalcular a vacío.
    (tmp_path / "agg").mkdir()
    stored = {"schema": A.AGG_SCHEMA, "day": "20261121", "rows": [{"vertical": "copilot", "area": "catalog",
              "module": "__catalog__", "section": "catalog_flow", "kind": "navigation", "event": "section_view",
              "cls": "usage", "status": "", "user": "a@d.com", "count": 5, "visits_v1": 0, "view_ids": ["x"],
              "ts": ["2026-11-21T10:00:00"]}], "sessions": {}, "signature": {"stale": True}}
    (tmp_path / "agg" / "20261121.json").write_text(json.dumps(stored), encoding="utf-8")
    with A.reading_from(tmp_path):
        loaded = A.load_day("20261121", today="20261122")
    assert loaded is not None and loaded["rows"][0]["count"] == 5     # devuelto tal cual (no recalculado a vacío)
    assert (tmp_path / "agg" / "20261121.json").is_file()              # no se borró


def _sections(view, module):
    for v in view["breakdown"]:
        for m in v["modules"]:
            if m["module"] == module:
                return {s["section"] for s in m["sections"]}
    return set()


def test_area_and_module_filter_coherence_for_virtual_catalog(tmp_path):
    # Dos clases de actividad de Catálogo, ambas module=__catalog__: una NAVEGACIÓN histórica (area=methodology,
    # section=catalog_flow) y un RESULTADO (area=catalog, sin sección).
    _write(tmp_path, "20261121", [_NAV,
        {"v": 2, "event": "catalog_apis_sync", "scope": "copilot", "area": "catalog", "kind": "outcome",
         "ts_utc": "2026-11-21T11:00:00", "user": "a@d.com", "attrs": {"outcome": "success"}}])
    with A.reading_from(tmp_path):
        allm = A.query("20261121", "20261121", {"module": "__catalog__"}, visible_verticals=None)
        cat = A.query("20261121", "20261121", {"module": "__catalog__", "area": "catalog"}, visible_verticals=None)
        meth = A.query("20261121", "20261121", {"module": "__catalog__", "area": "methodology"}, visible_verticals=None)
    # Módulo = Catálogos (sin filtro de área): aparecen AMBAS (la navegación y el resultado sin sección).
    assert "catalog_flow" in _sections(allm, "__catalog__")
    # Área = Catálogo EXCLUYE la navegación histórica (es area=methodology): comportamiento DOCUMENTADO — el remap
    # sólo movió el módulo, no el área. No es incoherencia del módulo; es el contrato «no tocar áreas históricas».
    assert "catalog_flow" not in _sections(cat, "__catalog__")
    # Área = methodology la incluye (sigue siendo su área histórica).
    assert "catalog_flow" in _sections(meth, "__catalog__")


def test_catalog_functional_filter_expansion_counts_each_event_once(tmp_path):
    # Tres navegaciones de Catálogo: acreditada (catalog_connections→Conectividad), compartida (catalog_flow) y
    # transversal (catalog_assessment). Todas area=methodology → módulo virtual por slug.
    from webapp.services import activity_dashboard as D
    def _nav(section, ts):
        return {"v": 2, "event": "section_view", "scope": "copilot", "area": "methodology", "kind": "navigation",
                "ts_utc": ts, "user": "a@d.com", "module": "CP04", "section": section, "attrs": {"view_id": section}}
    _write(tmp_path, "20261121", [_nav("catalog_connections", "2026-11-21T10:00:00"),
                                  _nav("catalog_flow", "2026-11-21T10:01:00"),
                                  _nav("catalog_assessment", "2026-11-21T10:02:00")])
    with A.reading_from(tmp_path):
        todas = A.query("20261121", "20261121", {"module": "__catalog__"}, visible_verticals=None)
        conect = A.query("20261121", "20261121", {"module": "__catalog__", "section": D._catalog_members("conectividad")},
                         visible_verticals=None)
    # «Todas» incluye acreditada + compartida + transversal; cada evento una sola vez (sin duplicar por categoría).
    assert {"catalog_connections", "catalog_flow", "catalog_assessment"} <= _sections(todas, "__catalog__")
    assert todas["kpis"]["events"] == 3
    # Catálogo específico (Conectividad) → sólo su actividad ACREDITADA; la compartida/transversal NO entra.
    assert _sections(conect, "__catalog__") == {"catalog_connections"}
    assert conect["kpis"]["events"] == 1
