"""
test_catalog_integration.py — Bug B (identidad/routing) + Lineage (output/contextual)
funcionando JUNTOS sobre un mismo catálogo.

No son tests aislados por branch: reconstruye dos flujos del MISMO (cliente, dominio)
con outputs (cube) desconectados del linaje directo y verifica de punta a punta que
(a) cada flujo tiene identidad/routing propios (Bug B) y (b) su diagrama incluye el
output con asociaciones por defecto ON cuando el linaje directo está incompleto
(Lineage). Fixture sintética — nunca datos reales ni catalog_local.
"""
from tools.catalog import catalog_service as cs
import webapp.main as m


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _yaml_list(key, values):
    if not values:
        return f"  {key}: []\n"
    return f"  {key}:\n" + "".join(f"  - {v}\n" for v in values)


def _vista(capa, upstreams=(), assoc=(), pattern="DATA_STRUCTURE", data_category="null"):
    return (
        "---\n"
        "auto:\n"
        "  tipo: vista\n"
        f"  capa: {capa}\n"
        f"  modeling_pattern: {pattern}\n"
        f"  data_category: {data_category}\n"
        "  origen_remoto: null\n"
        + _yaml_list("upstreams", upstreams)
        + _yaml_list("upstreams_asociacion", assoc)
        + "---\n\n# Vista\n\nCuerpo.\n"
    )


def _build_flow(base, flow_dir, cliente, cube):
    """Un flujo con cadena directa 1TR->2VD y un cube OUTPUT desconectado
    (upstreams=[], assoc=[2VD]) — reproduce el caso real 4AM sin 3VF."""
    d = base / cliente / flow_dir
    arts = ["1TR_SAP_MD_X", "2VD_MD_X", cube]
    fm_arts = "\n".join(f"  - {a}" for a in arts)
    _write(d / f"flujo_{flow_dir}.md",
           "---\nauto:\n  tipo: flujo\n  dominio: Ventas\n  artefactos:\n"
           f"{fm_arts}\nmanual:\n  cliente: {cliente}\n---\n\n# Flujo\n\n## Hallazgos\n\n- nota\n")
    _write(d / "vistas" / "1TR_SAP_MD_X.md", _vista("Ingesta"))
    _write(d / "vistas" / "2VD_MD_X.md", _vista("Armonización", upstreams=["1TR_SAP_MD_X"],
                                               pattern="ANALYTICAL_DIMENSION"))
    _write(d / "vistas" / f"{cube}.md", _vista("Consumo", assoc=["2VD_MD_X"],
                                               pattern="ANALYTICAL_CUBE", data_category="CUBE"))


def test_identity_and_contextual_lineage_together(tmp_path):
    _build_flow(tmp_path, "Ventas", "Cli", "4AM_SND_ALPHA")
    _build_flow(tmp_path, "Ventas_4AM_SND_BETA", "Cli", "4AM_SND_BETA")

    entries = cs.load_catalog(str(tmp_path))
    flows = cs.flows_for_domain(entries, "Ventas")
    assert len(flows) == 2, "dos flujos legítimos del mismo cliente/dominio"

    # ── Bug B: identidad + routing propios ────────────────────────────────
    ids = [f.entry_id for f in flows]
    assert all(ids) and len(set(ids)) == 2, "entry_id distinto por flujo"
    for f in flows:
        hit = cs.find_flow_by_id(entries, f.entry_id)
        assert hit is not None and hit.entry_id == f.entry_id
        assert sorted(hit.artefactos) == sorted(f.artefactos)

    # ── Lineage: output visible + asociaciones contextual ON ──────────────
    for f in flows:
        cube = next(a for a in f.artefactos if a.startswith("4AM"))
        # apertura fresca (show_assoc None) → default contextual
        _sections, svg, show_assoc, assoc_context = m._catalog_flow_view(f, None, "datasphere_core")
        assert assoc_context is True, f"{cube}: output desconectado-pero-asociado"
        assert show_assoc is True, f"{cube}: asociaciones ON por defecto"
        assert cube in svg, f"{cube}: output presente en el diagrama"
        # al desmarcar (show_assoc False) el output sigue visible pero aislado
        _s2, svg_off, show_off, _ctx = m._catalog_flow_view(f, False, "datasphere_core")
        assert show_off is False
        assert cube in svg_off, f"{cube}: output visible aun aislado"

    # Los dos cubes son distintos → detalle/identidad distintos entre flujos.
    cubes = {next(a for a in f.artefactos if a.startswith("4AM")) for f in flows}
    assert cubes == {"4AM_SND_ALPHA", "4AM_SND_BETA"}
