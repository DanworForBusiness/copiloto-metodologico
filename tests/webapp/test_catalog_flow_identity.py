"""
test_catalog_flow_identity.py — Identidad estable de flujos del catálogo.

Root cause (Bug B): un mismo (cliente, dominio) puede contener DOS CatalogEntry
legítimos y distintos (p.ej. Kensing/Ventas con consumo PRODUCTSALESDELIVERY vs
SALESDOCUMENTITEMDEX). La lista rinde un tile por entry, pero el href y el detail
lookup usaban (cliente, dominio) como clave → dos tiles distintos colapsaban al
mismo destino (find_flow devuelve el primero).

Contrato pinneado: cada CatalogEntry tiene un `entry_id` estable, único,
URL-safe y reproducible; el lookup por entry_id resuelve al registro correcto; y
las tiles exponen un discriminador funcional (`consumo`). NO se deduplica, NO se
suman counts, NO se oculta ninguna tile. Fixtures sintéticas — nunca datos reales
ni catalog_local.

Se ejercita el servicio directamente (``load_catalog`` sobre un tmp dir) para no
depender del import dinámico BASE_DIR-relativo de ``catalog_reads._catalog_entries``
(el conftest redirige ``config.BASE_DIR`` a un tmp, lo que rompería ese import
fuera de la suite completa).
"""
import re

from tools.catalog import catalog_service as cs


def _write_flow(dir_path, cliente, dominio, artefactos):
    dir_path.mkdir(parents=True)
    arts = "\n".join(f"    - {a}" for a in artefactos)
    (dir_path / f"flujo_{dir_path.name}.md").write_text(
        "---\n"
        "auto:\n"
        "  tipo: flujo\n"
        f"  dominio: {dominio}\n"
        "  artefactos:\n"
        f"{arts}\n"
        "manual:\n"
        f"  cliente: {cliente}\n"
        "---\n"
        "Cuerpo de prueba.\n",
        encoding="utf-8",
    )


def test_same_client_domain_two_flows_have_distinct_identity_and_routing(tmp_path):
    # Reproduce el caso Kensing/Ventas: mismo cliente+dominio, dos flujos con
    # sets de artefactos distintos (distinto modelo de consumo 4AM).
    _write_flow(tmp_path / "Kliente" / "Ventas", "Kliente", "Ventas",
                ["1TR_SAP_MD_X", "4AM_SND_ALPHA"])
    _write_flow(tmp_path / "Kliente" / "Ventas_4AM_SND_BETA", "Kliente", "Ventas",
                ["1TR_SAP_MD_X", "1TR_SAP_MD_Y", "4AM_SND_BETA"])

    entries = cs.load_catalog(str(tmp_path))
    flows = cs.flows_for_domain(entries, "Ventas")
    assert len(flows) == 2, "no deduplicar: dos registros legítimos, dos tiles"

    ids = [f.entry_id for f in flows]
    assert all(ids), "entry_id no puede ser vacío"
    assert len(set(ids)) == 2, "identidad distinta por registro (no colisión)"

    # Cada entry_id resuelve a SU propio registro, no siempre al primero.
    for flow in flows:
        hit = cs.find_flow_by_id(entries, flow.entry_id)
        assert hit is not None
        assert hit.entry_id == flow.entry_id
        assert sorted(hit.artefactos) == sorted(flow.artefactos)

    # Discriminador funcional: las dos tiles dejan de parecer idénticas.
    consumos = {tuple(f.consumo) for f in flows}
    assert len(consumos) == 2


def test_same_logical_source_key_under_different_roots_yields_same_entry_id(tmp_path):
    # Contrato E: la identidad es independiente del root físico / BASE_DIR — el
    # mismo layout lógico bajo dos carpetas distintas produce los mismos entry_id.
    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    for root in (root_a, root_b):
        _write_flow(root / "Kensing" / "Ventas", "Kensing", "Ventas",
                    ["4AM_SND_PRODUCTSALESDELIVERY"])
        _write_flow(root / "Kensing" / "Ventas_4AM_SND_SALESDOCUMENTITEMDEX",
                    "Kensing", "Ventas", ["4AM_SND_SALESDOCUMENTITEMDEX"])
    ids_a = {e.entry_id for e in cs.load_catalog(str(root_a))}
    ids_b = {e.entry_id for e in cs.load_catalog(str(root_b))}
    assert ids_a == ids_b
    assert len(ids_a) == 2


def test_entry_id_is_url_safe_and_reproducible(tmp_path):
    _write_flow(tmp_path / "Cli" / "Dominio", "Cli Ñandú", "Dominio X",
                ["4AM_SND_Z"])
    first = cs.load_catalog(str(tmp_path))[0].entry_id
    second = cs.load_catalog(str(tmp_path))[0].entry_id
    assert first == second, "reproducible entre cargas"
    assert re.fullmatch(r"[a-z0-9-]+", first), "URL-safe [a-z0-9-]"


def test_find_flow_legacy_fallback_still_resolves_by_client_domain(tmp_path):
    # Compatibilidad con links históricos sin flow=: sigue resolviendo por
    # cliente+dominio (al primero), sin romper.
    _write_flow(tmp_path / "Cli" / "Ventas", "Cli", "Ventas", ["4AM_SND_Z"])
    entries = cs.load_catalog(str(tmp_path))
    assert cs.find_flow(entries, "Cli", "Ventas") is not None


def test_flows_partial_emits_distinct_hrefs_per_entry():
    # href A != href B, y ambos llevan flow=<entry_id>. Renderiza el partial real
    # con el entorno Jinja de la app (filtros catalog_verdict_* registrados).
    from webapp.main import templates

    a = cs.CatalogEntry(path="p/a", cliente="Kliente", dominio="Ventas",
                        veredicto="buena", apto="x", artefactos=["4AM_SND_ALPHA"],
                        entry_id="kliente-ventas-alpha")
    b = cs.CatalogEntry(path="p/b", cliente="Kliente", dominio="Ventas",
                        veredicto="buena", apto="x", artefactos=["4AM_SND_BETA"],
                        entry_id="kliente-ventas-beta")
    html = templates.get_template("partials/catalog_flows.html").render(
        flows=[a, b], dominio="Ventas", pack="datasphere_core", foco=None)

    tile_hrefs = [h for h in re.findall(r'hx-get="([^"]+)"', html) if "/catalog/flow" in h]
    assert len(tile_hrefs) == 2
    assert tile_hrefs[0] != tile_hrefs[1], "href distinto por tile"
    assert all("flow=" in h for h in tile_hrefs), "cada href lleva flow=<entry_id>"


def test_lineage_toggle_preserves_flow():
    # El toggle de asociaciones re-pide /catalog/flow; debe conservar flow=<entry_id>
    # para no colapsar a otro flujo del mismo cliente/dominio.
    from webapp.main import templates
    html = templates.get_template("partials/catalog_lineage.html").render(
        lineage_svg="<svg></svg>", show_assoc=False,
        cliente="Kliente", dominio="Ventas", entry_id="kliente-ventas-beta",
        pack="datasphere_core")
    toggle = [h for h in re.findall(r'hx-get="([^"]+)"', html) if "/catalog/flow" in h]
    assert toggle, "el toggle debe re-pedir /catalog/flow"
    assert all("flow=kliente-ventas-beta" in h for h in toggle)


# ── Routing por identidad a nivel de ruta HTTP ──────────────────────────────
def _authed_client(monkeypatch, entries):
    # Evita la fragilidad del import dinámico BASE_DIR-relativo de _catalog_entries
    # (el conftest parchea config.BASE_DIR) inyectando directamente (mod, entries).
    from fastapi.testclient import TestClient
    from webapp.main import app
    from webapp.services import auth_service
    import webapp.main as main

    monkeypatch.setattr(main, "_catalog_entries", lambda: (cs, entries))
    # Aísla la aserción de routing del pipeline de render del detalle: el marcador
    # deja ver qué entry resolvió la ruta.
    monkeypatch.setattr(
        main, "_catalog_flow_view",
        lambda entry, show_assoc=False, pack="": ([{"slug": "m", "html": f"ENTRY:{entry.entry_id}"}], ""),
    )
    session = auth_service.create_session(
        {"email": "t@d.com", "name": "T", "first_name": "T", "family_name": "U"})
    client = TestClient(app)
    client.cookies.set(auth_service.COOKIE_NAME, session)
    return client


def test_route_resolves_exact_entry_by_flow(tmp_path, monkeypatch):
    _write_flow(tmp_path / "Kensing" / "Ventas", "Kensing", "Ventas",
                ["1TR_SAP_MD_PRODUCT", "4AM_SND_PRODUCTSALESDELIVERY"])
    _write_flow(tmp_path / "Kensing" / "Ventas_4AM_SND_SALESDOCUMENTITEMDEX",
                "Kensing", "Ventas",
                ["1TR_SAP_MD_PRODUCT", "1TR_SAP_MD_PLANT", "4AM_SND_SALESDOCUMENTITEMDEX"])
    entries = cs.load_catalog(str(tmp_path))
    by_len = {len(e.artefactos): e for e in entries}
    a, b = by_len[2], by_len[3]  # A=5-art análogo, B=14-art análogo (aquí 2 vs 3)
    client = _authed_client(monkeypatch, entries)

    # flow=A resuelve A; flow=B resuelve B (no siempre el primero).
    ra = client.get(f"/catalog/flow?cliente=Kensing&dominio=Ventas&flow={a.entry_id}",
                    headers={"HX-Request": "true"})
    rb = client.get(f"/catalog/flow?cliente=Kensing&dominio=Ventas&flow={b.entry_id}",
                    headers={"HX-Request": "true"})
    assert ra.status_code == 200 and f"ENTRY:{a.entry_id}" in ra.text
    assert rb.status_code == 200 and f"ENTRY:{b.entry_id}" in rb.text
    assert a.entry_id != b.entry_id


def test_route_without_flow_and_multiple_matches_does_not_pick_first(tmp_path, monkeypatch):
    _write_flow(tmp_path / "Kensing" / "Ventas", "Kensing", "Ventas",
                ["4AM_SND_PRODUCTSALESDELIVERY"])
    _write_flow(tmp_path / "Kensing" / "Ventas_4AM_SND_SALESDOCUMENTITEMDEX",
                "Kensing", "Ventas", ["4AM_SND_SALESDOCUMENTITEMDEX"])
    entries = cs.load_catalog(str(tmp_path))
    client = _authed_client(monkeypatch, entries)

    resp = client.get("/catalog/flow?cliente=Kensing&dominio=Ventas",
                      headers={"HX-Request": "true"})
    assert resp.status_code == 200
    # NO resolvió a un entry puntual (no hay marcador ENTRY:) → no eligió el primero.
    assert "ENTRY:" not in resp.text
    # Ofrece elegir, con las dos tiles (cada una con su flow=).
    tile_hrefs = [h for h in re.findall(r'hx-get="([^"]+)"', resp.text) if "/catalog/flow" in h]
    flows = {re.search(r"flow=([^&\"]+)", h).group(1) for h in tile_hrefs if "flow=" in h}
    assert len(flows) == 2, "muestra ambas opciones distinguibles por identidad"


def test_route_without_flow_and_single_match_opens_it(tmp_path, monkeypatch):
    _write_flow(tmp_path / "Cli" / "Ventas", "Cli", "Ventas",
                ["4AM_SND_PRODUCTSALESDELIVERY"])
    entries = cs.load_catalog(str(tmp_path))
    only = entries[0]
    client = _authed_client(monkeypatch, entries)

    resp = client.get("/catalog/flow?cliente=Cli&dominio=Ventas",
                      headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert f"ENTRY:{only.entry_id}" in resp.text  # fallback legacy: 1 match → abre
