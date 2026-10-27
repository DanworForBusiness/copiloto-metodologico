"""CCHH · «Consolidar perfiles» — capa web: permiso EXCLUSIVO de APP_OWNER, flujo plan → confirmación, resultado pendiente (nunca éxito parcial), recuperación (reanudar/revertir), ocultamiento de los perfiles
bloqueados en el resto de Capital Humano y la barra de selección. Datos 100 % sintéticos bajo ``tmp_path``."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from webapp import access
from webapp.main import app
from webapp.services import auth_service, profile_consolidation_service as pcs, profile_document_repository as docs, profile_repository as profiles

_OWNER, _HC = "owner-consol@danwor.com", "cchh-consol@danwor.com"
_ORIGIN = {"Origin": "http://testserver"}
P_A, P_B, P_C = "11111111-aaaa-4aaa-8aaa-111111111111", "22222222-bbbb-4bbb-8bbb-222222222222", "33333333-cccc-4ccc-8ccc-333333333333"
_HARNESS = Path(__file__).resolve().parent / "human_capital_candidates_selection_consolidate.node.js"


@pytest.fixture(autouse=True)
def hub(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", _HC)
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    monkeypatch.setattr(access, "APP_OWNER", _OWNER)
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "local")


def client(email: str | None = _OWNER) -> TestClient:
    c = TestClient(app, follow_redirects=False)
    if email:
        c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": email, "name": "T", "first_name": "T", "family_name": "U"}))
    return c


def seed(**dup_over):
    base = {"last_name": "Sintetica", "email": "j@example.test", "phone": "", "linkedin_url": "", "location": "CABA", "profile_skills": "SAP", "seniority": "Senior", "source": "LinkedIn",
            "tags": [], "notes": "", "potential_rating": None, "potential_mark": None}
    profiles.create_with_explicit_id(P_A, {**base, "first_name": "Julia"})
    profiles.create_with_explicit_id(P_B, {**base, "first_name": "Julia", **dup_over})
    for pid, n in ((P_A, 1), (P_B, 2)):
        docs.create_with_explicit_id(f"doc-{pid[:4]}-{n}", {"profile_id": pid, "provider": "sharepoint", "site_id": "s", "drive_id": "d", "item_id": f"i{n}", "web_url": f"https://sp.example.test/{n}.pdf",
                                                           "filename": f"cv-{n}.pdf", "content_type": "application/pdf", "size_bytes": 10, "uploaded_at": f"2026-01-0{n}T00:00:00+00:00", "uploaded_by": "u", "folder_item_id": f"f{n}"})


def plan_page(c, ids=(P_A, P_B), primary=""):
    return c.post("/cchh/candidatos/consolidar", data={"profile_ids": list(ids), **({"primary_id": primary} if primary else {})}, headers=_ORIGIN)


def digest_of(html):
    return re.search(r'name="plan_digest" value="([0-9a-f]{64})"', html).group(1)


def confirm(c, html, **extra):
    data = {"primary_id": P_A, "duplicate_id": P_B, "plan_digest": digest_of(html), "confirm": "1", **extra}
    return c.post("/cchh/candidatos/consolidar/confirmar", data=data, headers=_ORIGIN)


# ═══════════════ permisos: exclusivos de APP_OWNER ═══════════════
@pytest.mark.parametrize("method,path", [("post", "/cchh/candidatos/consolidar"), ("post", "/cchh/candidatos/consolidar/confirmar"), ("get", "/cchh/consolidaciones"),
                                         ("post", "/cchh/consolidaciones/" + "a" * 32 + "/reanudar"), ("post", "/cchh/consolidaciones/" + "a" * 32 + "/revertir")])
def test_only_the_app_owner_can_reach_every_consolidation_route(method, path):
    seed()
    kw = {"headers": _ORIGIN} if method == "post" else {}
    assert getattr(client(_HC), method)(path, **kw).status_code == 403                      # usuario de CCHH que NO es el dueño
    anonymous = getattr(client(None), method)(path, **kw)
    assert anonymous.status_code == 303 and "/platform" in anonymous.headers["location"]        # sin sesión: al login
    assert getattr(client(_OWNER), method)(path, **kw).status_code != 403


def test_a_non_owner_never_changes_anything_even_with_a_forged_post():
    seed()
    html = plan_page(client(_OWNER)).text
    r = client(_HC).post("/cchh/candidatos/consolidar/confirmar", data={"primary_id": P_A, "duplicate_id": P_B, "plan_digest": digest_of(html), "confirm": "1"}, headers=_ORIGIN)
    assert r.status_code == 403 and profiles.get(P_B).get("deleted_at") is None and not pcs.list_operations()


def test_cross_origin_writes_are_rejected():
    seed()
    assert client().post("/cchh/candidatos/consolidar", data={"profile_ids": [P_A, P_B]}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client().post("/cchh/candidatos/consolidar", data={"profile_ids": [P_A, P_B]}).status_code == 403          # sin Origin ni Referer


def test_the_selection_bar_shows_the_button_only_to_the_owner():
    seed()
    assert "data-hc-consolidate-open" in client(_OWNER).get("/cchh/candidatos").text
    assert "data-hc-consolidate-open" not in client(_HC).get("/cchh/candidatos").text


# ═══════════════ flujo: plan → confirmación ═══════════════
def test_the_plan_page_shows_comparison_documents_relations_and_requires_explicit_confirmation():
    seed(location="Rosario")
    r = plan_page(client())
    assert r.status_code == 200
    html = r.text
    for marker in ("data-consolidation-compare", "data-consolidation-documents", "data-consolidation-relations", "data-consolidation-summary", "data-consolidation-confirm", "data-consolidation-swap"):
        assert marker in html
    assert 'data-field="location" data-kind="conflict"' in html and 'name="res_location"' in html            # conflicto → elección explícita
    assert "cv-1.pdf" in html and "cv-2.pdf" in html and "Se traslada al principal" in html                  # los documentos de AMBOS perfiles
    assert re.search(r"<input[^>]*name=\"confirm\"[^>]*required", html)
    assert profiles.get(P_B).get("deleted_at") is None and not pcs.list_operations()                          # el plan no escribe nada


def test_the_plan_page_requires_exactly_two_profiles():
    seed()
    for ids in ((P_A,), (P_A, P_B, P_C), (P_A, P_A), ()):
        r = plan_page(client(), ids)
        assert r.status_code == 200 and 'data-state="invalid"' in r.text


def test_swapping_the_primary_changes_who_is_kept():
    seed()
    html = plan_page(client(), primary=P_B).text
    assert re.search(r'data-role="primary".*?Julia', html, re.S) and f"<code>{P_B}</code>" in html


def test_confirming_consolidates_and_both_cvs_are_on_the_primary_profile_page():
    seed()
    c = client()
    html = plan_page(c).text
    done = confirm(c, html)
    assert done.status_code == 200 and 'data-outcome="COMPLETED"' in done.text and "data-consolidation-open-profile" in done.text
    ficha = c.get(f"/cchh/candidatos/{P_A}")
    assert ficha.status_code == 200 and "cv-1.pdf" in ficha.text and "cv-2.pdf" in ficha.text                # ambos CVs accesibles desde la ficha del principal
    assert c.get(f"/cchh/candidatos/{P_B}").status_code == 404                                                 # el duplicado dejó de existir como identidad activa
    listing = c.get("/cchh/candidatos").text
    assert listing.count("hc-candidate-name") == 1 and P_B not in listing
    again = confirm(c, html)
    assert 'data-outcome="ALREADY_CONSOLIDATED"' in again.text and len(pcs.list_operations()) == 1             # reintento: no-op


def test_confirming_without_the_checkbox_or_with_unresolved_conflicts_changes_nothing():
    seed(location="Rosario")
    c = client()
    html = plan_page(c).text
    no_box = c.post("/cchh/candidatos/consolidar/confirmar", data={"primary_id": P_A, "duplicate_id": P_B, "plan_digest": digest_of(html), "res_location": P_A}, headers=_ORIGIN)
    assert "Revisá antes de confirmar" in no_box.text and profiles.get(P_B).get("deleted_at") is None
    unresolved = confirm(c, html)
    assert "Falta resolver" in unresolved.text and profiles.get(P_B).get("deleted_at") is None
    resolved = confirm(c, html, res_location=P_B)
    assert 'data-outcome="COMPLETED"' in resolved.text and profiles.get(P_A)["location"] == "Rosario"


def test_a_duplicate_with_relations_shows_the_block_and_cannot_be_submitted():
    seed()
    from webapp.services import entrevista_repository
    entrevista_repository.create({}, profile_id=P_B, busqueda_id=None, application_id=None)
    c = client()
    html = plan_page(c).text
    assert 'data-blocker="DUPLICATE_HAS_RELATIONS"' in html and "Bloquea la consolidación" in html and re.search(r"data-consolidation-submit\s+disabled", html)
    out = c.post("/cchh/candidatos/consolidar/confirmar", data={"primary_id": P_A, "duplicate_id": P_B, "plan_digest": digest_of(html), "confirm": "1"}, headers=_ORIGIN)
    assert 'data-outcome="BLOCKED"' in out.text and profiles.get(P_B).get("deleted_at") is None


# ═══════════════ pendiente: nunca éxito parcial; visibilidad controlada; recuperación ═══════════════
def _break_second_step(monkeypatch):
    real = docs.reassign_for_consolidation

    def broken(document_id, **kw):
        if kw.get("restore") is None:
            raise docs.ProfileDocumentStorageError("inyectado")
        return real(document_id, **kw)
    monkeypatch.setattr(docs, "reassign_for_consolidation", broken)
    return real


def test_a_failed_consolidation_is_shown_as_pending_not_as_success_and_hides_both_profiles(monkeypatch):
    seed()
    real = _break_second_step(monkeypatch)
    c = client()
    out = confirm(c, plan_page(c).text)
    assert 'data-outcome="PENDING"' in out.text and "No es un resultado exitoso" in out.text and 'data-outcome="COMPLETED"' not in out.text
    assert c.get(f"/cchh/candidatos/{P_A}").status_code == 404 and c.get(f"/cchh/candidatos/{P_B}").status_code == 404
    assert c.get(f"/cchh/candidatos/{P_A}/editar").status_code == 404
    listing = c.get("/cchh/candidatos").text
    assert "data-consolidation-pending" in listing and P_A not in listing and P_B not in listing                 # el aviso solo para el dueño; los perfiles no se listan
    assert "data-consolidation-pending" not in client(_HC).get("/cchh/candidatos").text
    ops = c.get("/cchh/consolidaciones").text
    assert 'data-operation-status="PENDING"' in ops and "data-consolidation-resume" in ops and "data-consolidation-abort" in ops
    # la edición/baja directa tampoco llega a tocarlos
    assert c.post(f"/cchh/candidatos/{P_A}/eliminar", headers=_ORIGIN).status_code in (200, 303) and profiles.get(P_A).get("deleted_at") is None
    monkeypatch.setattr(docs, "reassign_for_consolidation", real)
    op = pcs.list_operations()[0]["operation_id"]
    resumed = c.post(f"/cchh/consolidaciones/{op}/reanudar", headers=_ORIGIN)
    assert 'data-outcome="COMPLETED"' in resumed.text and c.get(f"/cchh/candidatos/{P_A}").status_code == 200


def test_a_pending_consolidation_can_be_reverted_from_the_operations_page(monkeypatch):
    seed()
    _break_second_step(monkeypatch)
    c = client()
    confirm(c, plan_page(c).text)
    op = pcs.list_operations()[0]["operation_id"]
    reverted = c.post(f"/cchh/consolidaciones/{op}/revertir", headers=_ORIGIN)
    assert 'data-outcome="ABORTED"' in reverted.text
    assert c.get(f"/cchh/candidatos/{P_A}").status_code == 200 and c.get(f"/cchh/candidatos/{P_B}").status_code == 200
    assert [d["document_id"] for d in docs.list_for_profile(P_B)] == ["doc-2222-2"]
    assert c.post(f"/cchh/consolidaciones/{op}/otra", headers=_ORIGIN).status_code == 404


# ═══════════════ barra de selección (JS) ═══════════════
@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_selection_bar_enables_consolidate_only_with_exactly_two_selected():
    result = subprocess.run(["node", str(_HARNESS)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ALL CONSOLIDATE SELECTION INVARIANTS PASSED" in result.stdout
