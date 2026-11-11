"""
test_share_recipients.py — selector de destinatarios corporativos TRANSVERSAL (`webapp/services/share_recipients.py`).

Proveedor (`SHARE_RECIPIENTS_PROVIDER`, compatibilidad transitoria con `HC_SHARE_RECIPIENTS_PROVIDER`) y contexto (allowlist de la vertical,
resuelto por el backend). SharePoint simulado a nivel de lectura de listas (`ReportingRepository._items`).
"""
from __future__ import annotations

import pytest

import config
from webapp.services import auth_service, share_recipients as sr


@pytest.fixture(autouse=True)
def lists(monkeypatch):
    monkeypatch.setattr(config, "AUTH_DEFAULT_DOMAIN", "danwor.com", raising=False)
    for name, value in {"ALLOWED_USERS": "copi.uno, Copi.Dos@Danwor.com", "ACADEMY_USERS": "aca.uno", "OPERATIONS_ALLOWED_USERS": "ops.uno, externo@gmail.com",
                        "COMMERCIAL_ALLOWED_USERS": "com.uno, ALL", "HUMAN_CAPITAL_ALLOWED_USERS": "hc.uno, ccosta"}.items():
        monkeypatch.setattr(auth_service, name, value)
    monkeypatch.setattr(config, "SHARE_RECIPIENTS_PROVIDER", "", raising=False)
    monkeypatch.setattr(config, "HC_SHARE_RECIPIENTS_PROVIDER", "", raising=False)


@pytest.mark.parametrize("context,expected", [
    ("copilot", ["copi.dos@danwor.com", "copi.uno@danwor.com"]),
    ("academy", ["aca.uno@danwor.com"]),
    ("operations", ["ops.uno@danwor.com"]),                       # externos fuera
    ("commercial", ["com.uno@danwor.com"]),                       # «ALL» mezclado no es destinatario
    ("human-capital", ["ccosta@danwor.com", "hc.uno@danwor.com"]),
])
def test_each_context_uses_its_own_vertical_allowlist(context, expected):
    assert [r["email"] for r in sr.allowlist_recipients(context)] == expected


def test_all_never_becomes_the_whole_directory_and_unknown_contexts_fail_closed(monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    assert sr.allowlist_recipients("copilot") == []
    with pytest.raises(ValueError):
        sr.allowlist_recipients("otra-vertical")


def test_validation_rejects_identities_from_another_vertical_allowlist(monkeypatch):
    monkeypatch.setattr(config, "SHARE_RECIPIENTS_PROVIDER", "allowlist")
    valid, invalid = sr.validate_recipients(["hc.uno@danwor.com", "ops.uno@danwor.com"], "", context="human-capital")
    assert [v["email"] for v in valid] == ["hc.uno@danwor.com"] and invalid == ["ops.uno@danwor.com"]


@pytest.mark.parametrize("new,old,expected", [
    ("allowlist", "", "allowlist"), ("directory", "allowlist", "directory"),   # el nombre nuevo tiene prioridad
    ("", "allowlist", "allowlist"),                                          # compatibilidad transitoria
    ("", "", "directory"), ("otro", "", "directory"),                        # default y valores desconocidos
])
def test_provider_prefers_the_new_variable_keeps_the_old_one_transitionally_and_defaults_to_directory(monkeypatch, new, old, expected):
    monkeypatch.setattr(config, "SHARE_RECIPIENTS_PROVIDER", new)
    monkeypatch.setattr(config, "HC_SHARE_RECIPIENTS_PROVIDER", old)
    assert sr.recipient_provider() == expected


def test_catalog_names_priority_and_no_expansion_through_user_information_list(monkeypatch):
    from webapp.services import reporting_repository
    colab = [{"id": "c1", "fields": {"Title": "Hache Uno", "Colaborador_x002e_LookupId": "1"}}]
    uil = [{"id": "1", "fields": {"Title": "H. Uno (perfil)", "Name": "i:0#.f|membership|hc.uno@danwor.com"}},
           {"id": "2", "fields": {"Title": "Cecilia Sintética", "Name": "i:0#.f|membership|ccosta@danwor.com"}},
           {"id": "3", "fields": {"Title": "Fuera De Lista", "Name": "i:0#.f|membership|ajeno@danwor.com"}}]
    monkeypatch.setattr(reporting_repository.ReportingRepository, "_items", lambda self, site, lst, stage, select=None: colab if stage == "colaboradores" else uil)
    monkeypatch.setattr(config, "SHAREPOINT_GESTION_SITE_ID", "site-x", raising=False)
    body = sr.allowlist_catalog("human-capital", "token")
    assert body["recipients"] == [{"name": "Cecilia Sintética", "email": "ccosta@danwor.com"}, {"name": "Hache Uno", "email": "hc.uno@danwor.com"}]
    assert "ajeno@danwor.com" not in [r["email"] for r in body["recipients"]]


def test_server_side_search_matches_by_email_with_three_characters(monkeypatch):
    monkeypatch.setattr(config, "SHARE_RECIPIENTS_PROVIDER", "allowlist")
    assert [r["email"] for r in sr.search_recipients("operations", "ops", "")] == ["ops.uno@danwor.com"]
    assert sr.search_recipients("operations", "op", "") == []
