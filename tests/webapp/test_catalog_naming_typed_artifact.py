"""
test_catalog_naming_typed_artifact.py — Reconocimiento de artefactos tipados con
prefijo de capa indebido en la validación de nombres §28/§29.

Un Analytic Model se nombra `AM_...` (sin número de capa). `4AM_...` se tokenizaba
como 4|A|M → falsos positivos "artefacto 'A' no homologado" / "uso 'M' no
homologado", y el detalle mostraba Artefacto=Vista, Uso=M. Debe reconocerse el
artefacto tipado (AM) con un ÚNICO finding por el prefijo de capa indebido; y la
metadata renderizada debe quedar semánticamente correcta (Artefacto=Analytic Model,
sin uso 'M'). Genérico para todos los OTROS (DF/RF/FT/IL/TC/AM/EM/DAC).
"""
import sys
from pathlib import Path

_TOOLS = str(Path(__file__).resolve().parents[2] / "tools" / "catalog")
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)
import generate_catalog as g  # noqa: E402


def _issues(name):
    return g.parse_name(name).get("issues") or []


def test_bare_typed_artifact_canonical():
    pn = g.parse_name("AM_SND_PRODUCTSALESDELIVERY")
    assert pn["canonical"] is True
    assert pn.get("issues") == []


def test_layered_typed_artifact_am_single_finding_no_false_positives():
    pn = g.parse_name("4AM_SND_PRODUCTSALESDELIVERY")
    iss = pn.get("issues") or []
    assert len(iss) == 1
    joined = " ".join(iss)
    assert "AM" in joined and "4" in joined            # anomalía real: prefijo de capa
    assert "artefacto 'A' no homologado" not in joined  # sin falso positivo A
    assert "uso semántico 'M' no homologado" not in joined  # sin falso positivo M
    assert pn["canonical"] is False


def test_layered_typed_artifact_generic_rf():
    iss = _issues("1RF_SND_ENTREGAS")
    assert len(iss) == 1
    assert "RF" in iss[0] and "1" in iss[0]
    assert "artefacto 'R' no homologado" not in iss[0]


def test_controls_unchanged():
    assert g.parse_name("1TR_SAP_MD_PRODUCT")["canonical"] is True
    assert g.parse_name("2VD_MD_PRODUCT")["canonical"] is True          # 2VF-like: Vista/Dimensión
    assert g.parse_name("4VF_SND_DESPACHOS_DBX")["canonical"] is True   # adaptador 4V válido §29


def test_out_of_scope_still_flagged():
    # 4MA (abreviatura MA no tipada) y 2VG/2VS (uso G/S no homologado) NO se "arreglan".
    assert _issues("4MA_STOCK")                     # sigue con finding
    assert _issues("2VG_DIM_CUST_CAT")              # sigue con finding
    assert _issues("2VS_ZTDPCIA_R3_S4")             # sigue con finding


def _rows(md):
    return [l for l in md.splitlines() if l.startswith("| ") and " | " in l]


def test_rendered_metadata_layered_am_is_typed_no_layer_no_uso():
    # 4AM: Artefacto = Analytic Model; SIN fila Capa (válida) ni Uso semántico.
    ent = g.Entity(name="4AM_SND_PRODUCTSALESDELIVERY", file="x.json")
    ent.modeling_pattern = "ANALYTICAL_CUBE"
    ent.data_category = "CUBE"
    md = g.render_vista(ent, "Cli", "Cli · Ventas")
    assert "| Artefacto | Analytic Model |" in md
    assert "| Vista |" not in md
    assert not any(l.startswith("| Capa |") for l in _rows(md))         # sin capa (§29)
    assert not any(l.startswith("| Uso sem") for l in _rows(md))        # sin uso


def test_rendered_metadata_bare_am_is_typed_no_layer_no_uso():
    ent = g.Entity(name="AM_SND_FACTURACION", file="x.json")
    ent.modeling_pattern = "ANALYTICAL_CUBE"
    ent.data_category = "CUBE"
    md = g.render_vista(ent, "Cli", "Cli · Fin")
    assert "| Artefacto | Analytic Model |" in md
    assert not any(l.startswith("| Capa |") for l in _rows(md))
    assert not any(l.startswith("| Uso sem") for l in _rows(md))


def test_rendered_metadata_4v_adapter_keeps_layer_and_uso():
    # 4VF es adaptador §29 válido (gramática por capa): Capa/Artefacto/Uso presentes.
    ent = g.Entity(name="4VF_SND_DESPACHOS_DBX", file="x.json")
    md = g.render_vista(ent, "Cli", "Cli · Desp")
    assert any(l.startswith("| Capa | Consumo |") for l in _rows(md))
    assert any(l.startswith("| Uso sem") and "Fact" in l for l in _rows(md))
