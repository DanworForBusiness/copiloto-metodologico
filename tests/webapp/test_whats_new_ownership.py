"""COPILOTO WHAT'S NEW — PROPIEDAD EDITORIAL (owner corrective).

CAMBIO DESPLEGADO != NOVEDAD PUBLICADA.

El defecto: la elegibilidad tenía dos fuentes. La manual respetaba el pack; la
otra leía `git log --grep "^Novedad:"` sobre TODO el repositorio. Ese diseño era
correcto cuando el repo ERA el Copiloto — el monolito después sumó Comercial,
Reporting, Staffing y plataforma, y la equivalencia se rompió en silencio. El
síntoma: un fix de autenticación de Graph (Hub/Reporting) apareció anunciado
como novedad del Copiloto.

Estos tests protegen la FUENTE y la ELEGIBILIDAD, no textos concretos. El guard
más fuerte es que en runtime haya exactamente UNA fuente, y que sea pack-scoped.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

from webapp.services import whats_new_service as wn

_ROOT = Path(__file__).resolve().parents[2]
_FUENTE = (_ROOT / "webapp" / "services" / "whats_new_service.py").read_text(encoding="utf-8")


def _sin_docstring_ni_comentarios(src: str) -> str:
    """El docstring del módulo NOMBRA el mecanismo retirado a propósito (explica
    por qué se fue). Contarlo como código daría un falso positivo."""
    sin_doc = src.split('"""', 2)[-1] if src.count('"""') >= 2 else src
    return re.sub(r"#[^\n]*", "", sin_doc)


@pytest.fixture
def pack_tmp(monkeypatch, tmp_path):
    """Un CONTENT_PACKS_DIR sintético con dos packs."""
    monkeypatch.setattr(wn, "CONTENT_PACKS_DIR", tmp_path)
    monkeypatch.setattr(wn, "_cache", {})
    (tmp_path / "pack_activo").mkdir()
    (tmp_path / "otro_pack").mkdir()
    return tmp_path


def _escribir(pack_dir, entradas):
    (pack_dir / wn.MANUAL_FILE).write_text(
        yaml.safe_dump(entradas, allow_unicode=True, sort_keys=False), encoding="utf-8")


# ── A. el repositorio NO publica ───────────────────────────────────────────

def test_un_commit_con_el_trailer_no_produce_una_entrada(pack_tmp, monkeypatch):
    """EL GUARD CENTRAL. Se simula el peor caso: un `git log` que devolvería un
    commit con trailer `Novedad:`. Si quedara cualquier camino derivado del
    repositorio, esta entrada aparecería.
    """
    llamadas = []

    def _espia(*args, **kwargs):        # pragma: no cover - no debería correr
        llamadas.append(args)
        raise AssertionError("What's New invocó un subproceso (¿git?) para publicar")

    monkeypatch.setattr(subprocess, "run", _espia)
    _escribir(pack_tmp / "pack_activo", [
        {"ref": "curada-1", "fecha": "2026-01-10", "titulo": "Una novedad del Copiloto"},
    ])
    publicadas = wn.entries("pack_activo")
    assert [e["ref"] for e in publicadas] == ["curada-1"]
    assert llamadas == []


def test_el_servicio_no_conserva_ningun_camino_de_publicacion_por_repo(pack_tmp):
    """Y no queda un mecanismo alternativo dormido: se retiró, no se desactivó.
    Un camino inactivo pero presente es una invitación a re-encenderlo."""
    codigo = _sin_docstring_ni_comentarios(_FUENTE)
    for rastro in ("subprocess", "git log", '"git"', "_git_entries", "_git_log_raw", "TRAILER"):
        assert rastro not in codigo, f"sigue habiendo rastro del camino por repo: {rastro}"


def test_renderiza_sin_historial_de_git(pack_tmp, monkeypatch):
    """F. Un deploy sin `.git` (imagen, tarball) tiene que publicar igual: la
    fuente es un archivo del pack, no el repositorio."""
    monkeypatch.setattr(wn, "BASE_DIR", pack_tmp / "no-existe", raising=False)
    _escribir(pack_tmp / "pack_activo", [
        {"ref": "sin-git", "fecha": "2026-02-02", "titulo": "Publica igual"},
    ])
    assert [e["ref"] for e in wn.entries("pack_activo")] == ["sin-git"]


# ── B/C. la elegibilidad es del PACK ───────────────────────────────────────

def test_una_entrada_de_otro_pack_no_aparece_en_el_activo(pack_tmp):
    _escribir(pack_tmp / "pack_activo", [
        {"ref": "propia", "fecha": "2026-01-10", "titulo": "Del pack activo"}])
    _escribir(pack_tmp / "otro_pack", [
        {"ref": "ajena", "fecha": "2026-06-01", "titulo": "De otro pack"}])
    refs = {e["ref"] for e in wn.entries("pack_activo")}
    assert refs == {"propia"}
    assert "ajena" not in refs, "la de otro pack es MÁS NUEVA: si se colara, encabezaría"


def test_una_entrada_explicita_del_pack_activo_si_aparece(pack_tmp):
    _escribir(pack_tmp / "pack_activo", [
        {"ref": "anuncio", "fecha": "2026-03-03", "titulo": "Novedad real",
         "categoria": "Catálogo", "detalle": "Con detalle", "link": "/catalog/x"}])
    e = wn.entries("pack_activo")[0]
    assert (e["ref"], e["title"], e["categoria"], e["detail"], e["link"]) == \
        ("anuncio", "Novedad real", "Catálogo", "Con detalle", "/catalog/x")


def test_un_pack_sin_archivo_no_publica_nada(pack_tmp):
    assert wn.entries("otro_pack") == []


# ── D/E. refs y fechas ─────────────────────────────────────────────────────

def test_los_refs_son_unicos_y_el_duplicado_no_duplica_la_fila(pack_tmp):
    """El archivo es append-only y a mano: dos entradas con el mismo `ref` son
    un error de edición. Gana la más nueva, no se pinta dos veces."""
    _escribir(pack_tmp / "pack_activo", [
        {"ref": "repetida", "fecha": "2026-01-01", "titulo": "Vieja"},
        {"ref": "repetida", "fecha": "2026-05-05", "titulo": "Nueva"},
    ])
    publicadas = wn.entries("pack_activo")
    assert len(publicadas) == 1
    assert publicadas[0]["title"] == "Nueva"


def test_ordena_por_fecha_descendente_y_arma_la_etiqueta(pack_tmp):
    _escribir(pack_tmp / "pack_activo", [
        {"ref": "vieja", "fecha": "2026-01-05", "titulo": "A"},
        {"ref": "nueva", "fecha": "2026-11-20", "titulo": "B"},
    ])
    publicadas = wn.entries("pack_activo")
    assert [e["ref"] for e in publicadas] == ["nueva", "vieja"]
    assert publicadas[0]["label"] == "20 nov 2026"
    assert wn.latest_ref("pack_activo") == "nueva"


def test_una_entrada_sin_ref_o_sin_titulo_no_publica(pack_tmp):
    """Publicar exige una identidad estable y algo que decir."""
    _escribir(pack_tmp / "pack_activo", [
        {"fecha": "2026-01-01", "titulo": "Sin ref"},
        {"ref": "sin-titulo", "fecha": "2026-01-02"},
        {"ref": "ok", "fecha": "2026-01-03", "titulo": "Completa"},
    ])
    assert [e["ref"] for e in wn.entries("pack_activo")] == ["ok"]


# ── El estado REAL del pack de producción ─────────────────────────────────

def test_el_pack_real_publica_las_del_copiloto_y_no_la_del_hub():
    """Backfill verificado sobre el pack de verdad: las dos novedades legítimas
    que venían de git sobreviven con su ref estable (la cookie de quien ya las
    vio sigue valiendo), y la del Hub no está."""
    publicadas = {e["ref"]: e for e in wn.entries()}
    assert "d6147d5" in publicadas, "novedad de catálogo perdida en el cambio de propiedad"
    assert "0722a31" in publicadas, "guía de catálogo perdida en el cambio de propiedad"
    assert "a814c9f" not in publicadas, \
        "el fix de autenticación de Graph es del Hub, no del Copiloto"
    assert publicadas["0722a31"]["link"] == "/catalog/como-publicar?pack=datasphere_core"


def test_el_pack_real_no_perdio_ninguna_novedad_curada():
    refs = {e["ref"] for e in wn.entries()}
    assert {"data-products-bdc", "catalogos-danwor"} <= refs


def test_el_ultimo_anuncio_no_cambio(monkeypatch):
    """La cookie `dw-whatsnew-seen` guarda el ref del último visto. Si el
    backfill hubiera quedado arriba, el banner se re-dispararía para todos —
    un cambio de PROPIEDAD no debería reabrirle el cartel a nadie."""
    assert wn.latest_ref() == "data-products-bdc"
