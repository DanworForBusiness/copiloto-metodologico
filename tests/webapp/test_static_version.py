"""
test_static_version.py — cache-busting de estáticos por CONTENIDO (hotfix: en producción el build normaliza los mtimes a 1980, así que la huella por
mtime no cambiaba entre deploys y los navegadores conservaban app.js/tools.css viejos).

Contratos: la huella cambia si cambia el contenido de cualquier archivo de primer nivel aunque mtime y tamaño sean idénticos; no cambia si el contenido
no cambia (aunque cambie el mtime); un refresh tras editar un archivo la renueva sin reiniciar; y base.html la usa en app.js y tools.css.
"""
from __future__ import annotations

import os
import re

import pytest

from webapp import templating
from webapp.templating import _StaticVersion, templates


@pytest.fixture()
def static_dir(tmp_path, monkeypatch):
    d = tmp_path / "webapp"
    (d / "static" / "vendor").mkdir(parents=True)
    (d / "static" / "app.js").write_text("console.log('v1')", encoding="utf-8")
    (d / "static" / "tools.css").write_text("a{color:red}", encoding="utf-8")
    (d / "static" / "vendor" / "x.js").write_text("ignored", encoding="utf-8")
    monkeypatch.setattr(templating, "WEB_DIR", d)
    return d / "static"


def _pin_mtime(path, ns=315532800_000_000_000):          # 1980-01-01: lo que deja el buildpack
    os.utime(path, ns=(ns, ns))


def test_version_changes_with_content_even_when_mtime_and_size_are_identical(static_dir):
    for p in static_dir.glob("*.*"):
        _pin_mtime(p)
    before = str(_StaticVersion())
    (static_dir / "app.js").write_text("console.log('v2')", encoding="utf-8")                 # mismo tamaño, otro contenido
    _pin_mtime(static_dir / "app.js")
    assert (static_dir / "app.js").stat().st_size == len("console.log('v1')")
    after = str(_StaticVersion())                                                              # proceso nuevo (como un deploy)
    assert before != after and re.fullmatch(r"[0-9a-f]{12}", after)


def test_version_is_stable_when_content_is_unchanged_and_ignores_subdirectories(static_dir):
    v = _StaticVersion()
    first = str(v)
    os.utime(static_dir / "tools.css", ns=(1, 1))                                              # mtime distinto, mismo contenido
    (static_dir / "vendor" / "x.js").write_text("otro", encoding="utf-8")                      # fuera del primer nivel
    assert str(v) == first == str(_StaticVersion())


def test_edit_in_a_running_process_is_picked_up_without_restart(static_dir):
    v = _StaticVersion()
    first = str(v)
    (static_dir / "tools.css").write_text("a{color:blue;margin:0}", encoding="utf-8")
    assert str(v) != first


def test_missing_static_dir_degrades_to_a_constant(monkeypatch, tmp_path):
    monkeypatch.setattr(templating, "WEB_DIR", tmp_path / "nope")
    assert str(_StaticVersion()) == "0"


def test_base_template_uses_the_content_hash_for_app_js_and_tools_css():
    base = (templating.WEB_DIR / "templates" / "base.html").read_text(encoding="utf-8")
    assert "path='app.js') }}?v={{ static_version }}" in base and "path='tools.css') }}?v={{ static_version }}" in base
    assert re.fullmatch(r"[0-9a-f]{12}", str(templates.env.globals["static_version"]))
