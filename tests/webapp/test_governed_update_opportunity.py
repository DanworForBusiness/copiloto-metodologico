"""Detección ad hoc → gobernado (`governed_update_opportunity`): DETECTA y EXPLICA una oportunidad de revisión supervisada; NUNCA persiste.
Distingue detección / comparabilidad / elegibilidad; `space=None` no es coincidencia automática."""
from webapp.services import assessment_service as svc


def _env(level, status, sapsf_class, *, space=None):
    leaves = [{"leaf": f"1TR_SSF_{i}", "contract_class": sapsf_class, "remote_connection": "SSFF"} for i in range(15)]
    return {"client": "Contreras", "terminal_am": "AM_Workforce", "judgment": {"status": status, "level": level},
            "profile_json": {"scope": ({"space": space} if space else {}), "canonical_evidence": {"source_contract_classes": {"leaves": leaves}}}}


def _cand_profile(sapsf_class):
    leaves = [{"leaf": f"1TR_SSF_{i}", "contract_class": sapsf_class, "remote_connection": "SSFF"} for i in range(15)]
    return {"canonical_evidence": {"source_contract_classes": {"leaves": leaves}}}


def _patch_env(monkeypatch, env):
    monkeypatch.setattr(svc, "_load_envelope", lambda c, a: env)


def test_no_governed_envelope_no_opportunity(monkeypatch):
    _patch_env(monkeypatch, None)
    assert svc.governed_update_opportunity("Contreras", "AM_Workforce", {"status": "DETERMINED", "level": "BUENA"}, _cand_profile("GOVERNED_SAP_APPLICATION_API")) is None


def test_no_relevant_difference_no_opportunity(monkeypatch):
    env = _env("BUENA", "DETERMINED", "GOVERNED_SAP_APPLICATION_API")
    _patch_env(monkeypatch, env)
    # mismo nivel y mismos contratos → sin oportunidad
    assert svc.governed_update_opportunity("Contreras", "AM_Workforce", {"status": "DETERMINED", "level": "BUENA"}, _cand_profile("GOVERNED_SAP_APPLICATION_API")) is None


def test_level_change_nd_to_buena_is_an_opportunity(monkeypatch):
    env = _env(None, "NOT_DETERMINABLE", "EXTERNAL_CONTRACT_UNDETERMINED")        # vigente ND
    _patch_env(monkeypatch, env)
    opp = svc.governed_update_opportunity("Contreras", "AM_Workforce", {"status": "DETERMINED", "level": "BUENA"}, _cand_profile("GOVERNED_SAP_APPLICATION_API"))
    assert opp and opp["level_changed"] and opp["contracts_changed"]
    assert opp["vigente"]["level"] is None and opp["candidato"]["level"] == "BUENA"
    assert opp["comparable"] is True                                             # sin espacios explícitos → comparable (detección), procedencia a validar
    assert opp["provenance_validated"] is False


def test_space_mismatch_is_not_comparable(monkeypatch):
    env = _env(None, "NOT_DETERMINABLE", "EXTERNAL_CONTRACT_UNDETERMINED", space="PRD")
    _patch_env(monkeypatch, env)
    opp = svc.governed_update_opportunity("Contreras", "AM_Workforce", {"status": "DETERMINED", "level": "BUENA"}, _cand_profile("GOVERNED_SAP_APPLICATION_API"), space="DEV")
    assert opp and opp["comparable"] is False and opp["provenance_validated"] is False   # espacios DIFIEREN explícitamente → no comparable (no reemplazo directo)


def test_same_space_validates_provenance(monkeypatch):
    env = _env(None, "NOT_DETERMINABLE", "EXTERNAL_CONTRACT_UNDETERMINED", space="DEV")
    _patch_env(monkeypatch, env)
    opp = svc.governed_update_opportunity("Contreras", "AM_Workforce", {"status": "DETERMINED", "level": "BUENA"}, _cand_profile("GOVERNED_SAP_APPLICATION_API"), space="DEV")
    assert opp and opp["comparable"] is True and opp["provenance_validated"] is True


def test_presenter_view_is_compact_and_explains(monkeypatch):
    from webapp.services import assessment_presenter as pres
    env = _env(None, "NOT_DETERMINABLE", "EXTERNAL_CONTRACT_UNDETERMINED", space="PRD")
    _patch_env(monkeypatch, env)
    opp = svc.governed_update_opportunity("Contreras", "AM_Workforce", {"status": "DETERMINED", "level": "BUENA"}, _cand_profile("GOVERNED_SAP_APPLICATION_API"), space="DEV")
    v = pres._update_opportunity_view(opp)
    assert v["vigente_level"] == "NO DETERMINABLE" and v["candidato_level"] == "BUENA" and v["level_changed"]
    assert "GOVERNED_SAP_APPLICATION_API" in v["source_classes_changed"] and v["provenance_validated"] is False
    assert (v["vigente_label"], v["vigente_tone"]) == ("No determinable", "neutra")          # badge gris
    assert (v["candidato_label"], v["candidato_tone"]) == ("Buena", "plena")               # badge verde (mismo mapeo que Adopción)
    assert v["source_classes_text"] == ["API de aplicación SAP gobernada"]
    assert v["motive"] == ""                                                               # sin redundancia: el cambio de nivel lo dicen los badges
    assert pres._update_opportunity_view(None) is None


def test_presenter_view_motive_only_when_level_unchanged(monkeypatch):
    from webapp.services import assessment_presenter as pres
    env = _env("BUENA", "DETERMINED", "EXTERNAL_CONTRACT_UNDETERMINED")
    _patch_env(monkeypatch, env)
    opp = svc.governed_update_opportunity("Contreras", "AM_Workforce", {"status": "DETERMINED", "level": "BUENA"}, _cand_profile("GOVERNED_SAP_APPLICATION_API"))
    v = pres._update_opportunity_view(opp)
    assert v["level_changed"] is False and v["motive"].startswith("Mismo nivel")


def _render_callout(up, **ctx):
    from webapp.templating import templates
    src = ('{% from "partials/assessment_report_macros.html" import callout %}'
           '{% set v = {"update_opportunity": up} %}') + _callout_block()
    return templates.env.from_string(src).render(up=up, pack="datasphere_core", **ctx)


def _callout_block():
    import pathlib
    text = (pathlib.Path(__file__).resolve().parents[2] / "webapp/templates/partials/catalog_assessment_report.html").read_text(encoding="utf-8")
    start = text.index("{% if v.update_opportunity %}")
    return text[start:text.index("{% endif %}", text.index("</dialog>", start)) + len("{% endif %}")]


def _opp_view(monkeypatch):
    from webapp.services import assessment_presenter as pres
    _patch_env(monkeypatch, _env(None, "NOT_DETERMINABLE", "EXTERNAL_CONTRACT_UNDETERMINED"))
    return pres._update_opportunity_view(svc.governed_update_opportunity("Contreras", "AM_Workforce", {"status": "DETERMINED", "level": "BUENA"},
                                                                         _cand_profile("GOVERNED_SAP_APPLICATION_API"), space="DEV"))


def test_cta_requests_the_update_and_never_approves_or_executes(monkeypatch):
    """PR-1: el CTA abre el diálogo con el detalle y su ÚNICA acción es REGISTRAR la solicitud (POST a /copilot/governed-updates/request).
    Sin hx-*, sin enlaces operativos, sin aprobar ni ejecutar; la ✕ solo oculta en esta vista."""
    import re
    up = _opp_view(monkeypatch)
    html = _render_callout(up, adhoc_result_id="a" * 32, adhoc_stem="04_Modelado_y_Desarrollo")
    assert re.search(r'<button type="button" class="btn-primary asr-update-banner__cta" data-dialog-open="asr-update-dialog">Solicitar actualización</button>', html)
    assert 'class="whatsnew-banner asr-update-banner"' in html                          # MISMO lenguaje que los avisos del Hub
    assert "@click=\"$el.closest('[data-asr-update-opportunity]').remove()\"" in html and "x-show" not in html.split("<dialog", 1)[0]   # ✕ síncrona, sin request
    assert 'id="asr-update-dialog"' in html and 'method="dialog"' in html
    assert re.findall(r'action="([^"]+)"', html) == ["/copilot/governed-updates/request"]    # la única acción del servidor: registrar la solicitud
    assert not re.search(r'hx-(get|post|put|delete)|href=|onclick|/approve|/apply|/execute', html)
    assert "procedimiento gobernado supervisado" in html and "No se modifica nada todavía" in html
    assert "catalog-card__verdict--neutra" in html and "catalog-card__verdict--plena" in html
    banner, dialog = html.split("<dialog", 1)
    assert "API de aplicación SAP gobernada" not in banner and "API de aplicación SAP gobernada" in dialog   # el detalle vive en el diálogo
    assert "requiere validación" in dialog and "Space «DEV»" in dialog


def test_with_an_open_request_the_cta_links_to_it(monkeypatch):
    up = {**_opp_view(monkeypatch), "open_request_id": "b" * 32}
    html = _render_callout(up, adhoc_result_id="a" * 32, adhoc_stem="04_Modelado_y_Desarrollo")
    banner = html.split("<dialog", 1)[0]
    assert f'href="/copilot/governed-updates/{"b" * 32}?pack=datasphere_core"' in banner and "Ver solicitud" in banner and "Solicitar actualización" not in banner


def test_without_an_adhoc_result_the_dialog_has_no_request_form(monkeypatch):
    html = _render_callout(_opp_view(monkeypatch))
    assert "/copilot/governed-updates/request" not in html