"""Hotfix de integridad del grounding — el asistente NO presenta el veredicto LEGADO del catálogo («mirada Danwor»: plena/buena/parcial) como si describiera el Assessment vigente.

Lo que fijan estos tests: «ver assessment de <modelo>» se resuelve con NAVEGACIÓN al Assessment existente (identidad real, sin reglas por cliente o modelo, preguntando cuál si hay varios); lo que llega al
LLM del catálogo es SOLO identidad (sin veredicto legado, ícono ni texto de aptitud) y declara que no trae evaluación; `DATA_INTEGRITY_RULE` distingue veredicto legado, Juicio vigente y estado del
Assessment; el asistente no agrega mapas de emojis; y todo lo demás (inventario, rankings, navegación, recursos, metodología) sigue igual. Fixtures SINTÉTICAS con modelos cuyo veredicto legado y Juicio
vigente DIFIEREN (el veredicto legado se generó invertido respecto de la adopción).
"""
from __future__ import annotations

import html as _html
import inspect
import re
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import webapp.main as main
from tools.catalog import catalog_service as cs
from tests.webapp.test_assistant_anti_fabrication_prompt import _CapturingClient
from tests.webapp.test_assistant_indicators import CLIENTS, HOST, LEVEL_POS, PACK, _spec, _write
from webapp.services import assessment_presenter as presenter
from webapp.services import assistant_capabilities as caps
from webapp.services import assistant_navigation as nav
from webapp.services import assistant_routing, auth_service, catalog_reads
from webapp.services import assistant_service as svc
from webapp.services.assistant_service import DATA_INTEGRITY_RULE, AssistantChatService
from webapp.services.content_service import load_module

N = 40
LEGACY_WORDS = ("plena", "buena", "parcial", "adecuada", "reutilizable", "referencia danwor", "inspiración", "mirada")
COLOR_EMOJIS = "🟢🟡🟠🔴⚪"


def _write_named(root, i):
    """Igual que `_write` pero con un dominio SIN la palabra «dominio» (que dispara el inventario antes que la ruta de disponibilidad): «Prisma 012»."""
    s = _spec(i)
    d = root / s["client"] / f"D{i:03d}"
    d.mkdir(parents=True)
    lob = f"  linea_de_negocio: {s['lob']}\n" if s["lob"] else ""
    (d / f"flujo_D{i:03d}.md").write_text(
        f"---\nauto:\n  tipo: flujo\n  dominio: Prisma {i:03d}\n  artefactos:\n  - 4AM_X_{i:03d}\nmanual:\n  cliente: {s['client']}\n  veredicto: {s['veredicto']}\n{lob}---\nCuerpo.\n", encoding="utf-8")


def _build(tmp_path, monkeypatch, writer):
    for i in range(N):
        writer(tmp_path, i)
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(tmp_path))                                      # lo que lee el grounding del LLM y el emparejador del router
    # `tests/conftest.py` redirige `config.BASE_DIR` a un tmp: sin esto `_catalog_grounding` no encuentra `catalog_service` y devuelve "" EN SILENCIO (la ruta real nunca se ejercitaba)
    monkeypatch.syspath_prepend(str(Path(cs.__file__).resolve().parent))
    entries = cs.load_catalog(str(tmp_path))
    index = {}
    for e in entries:
        index[e.entry_id] = int(re.search(r"-d(\d{3})-", e.entry_id).group(1))
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (cs, entries))
    monkeypatch.setattr(main, "_catalog_entries", lambda: (cs, entries))
    return types.SimpleNamespace(entries=entries, index=index, root=tmp_path)


@pytest.fixture()
def world(tmp_path, monkeypatch):
    return _build(tmp_path, monkeypatch, _write)


@pytest.fixture()
def named_world(tmp_path, monkeypatch):
    return _build(tmp_path, monkeypatch, _write_named)


def _route(question, stem=HOST):
    return assistant_routing.AssistantRoutingService().route(load_module(stem, PACK), question, PACK)


def _with(world, judgment, legacy):
    """Una entrada cuyo Juicio vigente y veredicto legado DIFIEREN."""
    return next(e for e in world.entries if _spec(world.index[e.entry_id])["judgment"] == judgment and e.veredicto == legacy and _spec(world.index[e.entry_id])["state"] == "CURRENT")


def _name(e):
    return f"{e.dominio} de {e.cliente}"


def _assert_no_legacy(text, entry=None):
    low = text.lower()
    assert not any(w in low for w in LEGACY_WORDS), text
    assert not any(ch in text for ch in COLOR_EMOJIS), text
    if entry is not None:
        assert (entry.apto or "").lower() not in low or not entry.apto


# ══ 1. «ver assessment de <modelo>» = navegación al Assessment EXISTENTE ═══════════════════════════════════════════════════════════════════
def test_the_world_has_models_whose_legacy_verdict_and_current_judgment_differ(world):
    a, b = _with(world, "ADECUADA", "plena"), _with(world, "PLENA", "parcial")
    assert (a.veredicto, b.veredicto) == ("plena", "parcial")                                   # ADECUADA con veredicto «plena»; PLENA con veredicto «parcial»


@pytest.mark.parametrize("judgment,legacy", [("ADECUADA", "plena"), ("PLENA", "parcial"), ("PARCIAL", "parcial")])
def test_viewing_the_assessment_navigates_to_it_and_never_states_the_legacy_verdict(world, judgment, legacy):
    e = _with(world, judgment, legacy)
    route = _route(f"ver assessment de {_name(e)}")
    assert route is not None and len(route.actions) == 1
    action = route.actions[0]
    assert action.url == presenter.assessment_url(e, PACK) and action.embed is True               # mismo contrato de identidad y de navegación que el Catálogo
    assert "Assessment" in route.message and e.dominio in route.message and e.cliente in route.message
    _assert_no_legacy(route.message, e)                                                          # ni el veredicto legado, ni íconos, ni «mirada Danwor», ni un nivel


def test_it_resolves_every_model_of_the_catalog_without_a_per_model_rule(world):
    for e in world.entries:
        route = _route(f"ver assessment de {_name(e)}")
        assert route is not None and [a.url for a in route.actions] == [presenter.assessment_url(e, PACK)], e.entry_id


@pytest.mark.parametrize("template", ["ver assessment de {n}", "Mostrame el assessment de {n}", "¿tenemos assessment de {n}?", "assessment de {n}", "abrí el assessment del modelo {n}",
                                      "quiero ver el assessment de {n}"])
def test_natural_phrasings_resolve_to_the_same_assessment(world, template):
    e = _with(world, "ADECUADA", "plena")
    route = _route(template.format(n=_name(e)))
    assert route is not None and [a.url for a in route.actions] == [presenter.assessment_url(e, PACK)]
    _assert_no_legacy(route.message)


def test_a_partial_name_that_identifies_one_model_of_the_client_resolves(world):
    e = next(x for x in world.entries if world.index[x.entry_id] == 12)
    route = _route(f"ver assessment de 012 de {e.cliente}")                                       # solo un fragmento del nombre; único dentro del cliente
    assert route is not None and [a.url for a in route.actions] == [presenter.assessment_url(e, PACK)]


def test_a_name_shared_by_several_models_asks_which_and_never_picks_one(world):
    route = _route("ver assessment de dominio de Pampa")
    pampa = [e for e in world.entries if e.cliente == "Pampa"]
    assert route is not None and "más de un modelo" in route.message and 1 < len(pampa) <= nav.MAX_OPTIONS
    assert sorted(a.url for a in route.actions) == sorted(presenter.assessment_url(e, PACK) for e in pampa)       # todas las del cliente nombrado y solo ellas
    assert "Muestro los primeros" not in route.message
    _assert_no_legacy(route.message)


def test_the_options_are_told_apart_by_the_catalog_title_and_the_client(world, monkeypatch):
    """Varios modelos pueden compartir dominio y cliente (p. ej. «Ventas»): cada opción lleva el título que el Catálogo muestra para ese modelo."""
    from tests.webapp.test_assistant_indicators import _status
    index = world.index
    monkeypatch.setattr(caps, "governed_read", lambda flows: caps.Governed({e.entry_id: _status(e, index[e.entry_id]) for e in flows}, {e.entry_id: {"group": "SALES"} for e in flows}))
    route = _route("ver assessment de dominio de Pampa")
    labels = [a.label for a in route.actions]
    assert len(labels) == len(set(labels)) > 1 and all(label.startswith("Assessment · ") and label.endswith(" · Pampa") for label in labels)
    pampa = {e.entry_id: e for e in world.entries if e.cliente == "Pampa"}
    assert {a.url for a in route.actions} == {presenter.assessment_url(e, PACK) for e in pampa.values()}


def test_many_candidates_are_capped_and_say_so(world):
    route = _route("ver assessment de dominio")
    assert route is not None and len(world.entries) > nav.MAX_OPTIONS
    assert len(route.actions) == nav.MAX_OPTIONS and "Muestro los primeros" in route.message
    valid = {presenter.assessment_url(e, PACK) for e in world.entries}
    assert all(a.url in valid for a in route.actions)


def test_the_exact_name_wins_over_a_longer_name_that_contains_it(world, monkeypatch):
    """«delivery document» no es «delivery document item»: si el nombre completo de un dominio está en la consulta, ese es el más específico."""
    base = _with(world, "ADECUADA", "plena")
    longer = next(e for e in world.entries if e.cliente == base.cliente and e is not base)
    monkeypatch.setattr(base, "dominio", "Delivery Document")
    monkeypatch.setattr(longer, "dominio", "Delivery Document Item")
    only = _route(f"ver assessment de delivery document de {base.cliente}")
    assert [a.url for a in only.actions] == [presenter.assessment_url(base, PACK)]
    both = _route(f"ver assessment de delivery document item de {base.cliente}")
    assert [a.url for a in both.actions] == [presenter.assessment_url(longer, PACK)]


def test_the_url_comes_from_the_real_entry_never_from_the_text(world):
    e = _with(world, "ADECUADA", "plena")
    route = _route(f"ver assessment de {_name(e)} </a><script>alert(1)</script> https://evil.example/x")
    assert route is not None
    assert all(a.url.startswith("/catalog/flow/assessment?flow=") and "evil" not in a.url and "<" not in a.url for a in route.actions)


@pytest.mark.parametrize("question", ["ver assessment de zzzz", "ver assessment de un modelo inexistente", "abrí el assessment", "¿cuántos assessments hay?"])
def test_an_unidentifiable_model_is_not_claimed(world, question):
    assert nav._entry_assessment(question, PACK, HOST) is None


@pytest.mark.parametrize("question", ["¿Qué es un assessment?", "¿Cómo se genera el assessment de {n}?", "¿Para qué sirve el assessment?", "Diferencia entre assessment y juicio",
                                      "¿Por qué el assessment de {n} está desactualizado?"])
def test_conceptual_and_methodological_questions_are_not_navigation(world, question):
    e = _with(world, "ADECUADA", "plena")
    assert nav._entry_assessment(question.format(n=_name(e)), PACK, HOST) is None


@pytest.mark.parametrize("question", [
    "¿Qué modelos tenemos?", "Mostrame 10 modelos", "Modelos de Ventas", "¿Qué entradas tienen mejor cobertura estricta?", "¿Qué entradas tienen mejor adopción metodológica?",
    "Ver informe de cliente para Pampa", "Abrí el Catálogo", "¿Qué documentación tenemos de CDS?", "¿Qué es la adopción metodológica?", "template CDS", "¿Cómo se definen los dominios de negocio?",
])
def test_inventory_rankings_navigation_resources_and_methodology_are_not_claimed(world, question):
    assert nav._entry_assessment(question, PACK, HOST) is None


def test_the_target_sits_in_the_registry_after_the_catalog_and_before_the_rankings():
    assert [t.key for t in nav.TARGETS] == ["catalog_followup", "client_report", "data_catalog", "entry_assessment", "catalog_ranking", "catalog_inventory"]


def test_through_the_real_chat_the_answer_is_navigation_and_never_reaches_the_llm(world, monkeypatch):
    from webapp.routers.assistant import assistant_chat_service, assistant_store
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(assistant_chat_service, "execution_mode", "produccion")
    monkeypatch.setattr(assistant_chat_service, "client_factory", lambda **_k: pytest.fail("la navegación al Assessment no debe invocar al LLM"))
    e = _with(world, "ADECUADA", "plena")
    client = TestClient(main.app)
    client.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": "a@d.com", "name": "A", "first_name": "A", "family_name": "U"}))
    raw = client.post(f"/modules/{HOST}/assistant/chat", data={"question": f"ver assessment de {_name(e)}", "conversation_id": assistant_store.start(HOST, PACK).id, "pack": PACK}).text
    page = _html.unescape(raw)
    assert f'hx-get="{presenter.assessment_url(e, PACK)}"' in page and "Respuesta generada con IA" not in page
    _assert_no_legacy(re.sub(r"<[^>]+>", " ", page.split("section-analysis-output", 1)[1].split("</div>", 1)[0]))


# ══ 1b. CONSULTAS DE EXISTENCIA: confirman e identifican el recurso, sin atribuirle una evaluación ══════════════════════════════════════════════════
def _flow_params(url):
    from urllib.parse import parse_qs, urlsplit
    parts = urlsplit(url)
    return parts.path, {k: v[0] for k, v in parse_qs(parts.query).items()}


@pytest.mark.parametrize("template", ["¿Tenemos {n}?", "¿tenemos {n}?", "¿Hay {n}?", "¿Existe {n}?"])
def test_an_availability_query_confirms_and_identifies_the_resource_without_any_evaluation(named_world, template):
    for judgment, legacy in (("ADECUADA", "plena"), ("PLENA", "parcial"), ("PARCIAL", "parcial")):
        e = _with(named_world, judgment, legacy)
        route = _route(template.format(n=_name(e)))
        assert route is not None, template
        assert "ya tenemos esto implementado" in route.message and f"**{e.cliente} · {e.dominio}**" in route.message          # confirma e identifica
        _assert_no_legacy(route.message, e)                                                                                      # ni veredicto legado, ni íconos, ni «mirada Danwor»
        assert not any(w in route.message.lower() for w in ("evaluación", "adopción", "juicio", "assessment", "vigente"))      # tampoco lo reemplaza por una evaluación


def test_an_availability_query_keeps_its_destination_and_navigation_action(named_world):
    for e in named_world.entries:                                                                                                      # genérico: todas las entradas, sin reglas por modelo
        route = _route(f"¿Tenemos {_name(e)}?")
        assert route is not None and len(route.actions) == 1, e.entry_id
        action = route.actions[0]
        path, params = _flow_params(action.url)
        assert action.label == "Ver en el catálogo" and action.embed is True
        assert path == "/catalog/flow" and params == {"cliente": e.cliente, "dominio": e.dominio, "flow": e.entry_id, "pack": PACK}


def test_an_availability_query_is_not_a_request_for_the_assessment_and_does_not_navigate_to_it(named_world):
    e = _with(named_world, "ADECUADA", "plena")
    route = _route(f"¿Tenemos {_name(e)}?")
    assert not any("/catalog/flow/assessment" in a.url for a in route.actions)
    assert nav._entry_assessment(f"¿Tenemos {_name(e)}?", PACK, HOST) is None


def test_viewing_the_assessment_still_resolves_next_to_an_availability_query(named_world):
    e = _with(named_world, "ADECUADA", "plena")
    availability = _route(f"¿Tenemos {_name(e)}?")
    assessment = _route(f"ver assessment de {_name(e)}")
    assert [a.label for a in availability.actions] == ["Ver en el catálogo"] and [a.label for a in assessment.actions] == ["Ver Assessment"]
    assert [a.url for a in assessment.actions] == [presenter.assessment_url(e, PACK)]
    assert "ya tenemos esto implementado" not in assessment.message


def test_the_legacy_verdict_filter_route_is_untouched_by_this_fix(named_world):
    """«¿qué referencias reutilizables tenemos?» consulta el veredicto legado a propósito (refactorizarlo queda fuera): sigue contestando por veredicto."""
    route = _route("¿Qué referencias reutilizables tenemos?")
    assert route is not None and "mirada Danwor" in route.message


# ══ 2. LO QUE LLEGA AL LLM: solo identidad, sin veredicto legado ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("judgment,legacy", [("ADECUADA", "plena"), ("PLENA", "parcial"), ("BUENA", "buena")])
def test_the_grounding_carries_identity_only_never_the_legacy_verdict(world, judgment, legacy):
    e = _with(world, judgment, legacy)
    block = svc._catalog_grounding(f"contame del flujo {_name(e)}")
    assert block and f"- {e.dominio} · cliente {e.cliente} (" in block and "artefactos)" in block      # la identidad sí
    assert f"[{e.veredicto}]" not in block and not any(ch in block for ch in COLOR_EMOJIS)             # ni etiqueta ni ícono
    low = block.lower()
    for word in ("reutilizable", "referencia danwor", "inspiración", "mirada", "evidencia danwor", "plena", "parcial", "adecuada"):
        assert word not in low, (word, block)
    if e.apto:
        assert e.apto.lower() not in low


def test_the_grounding_says_it_has_no_assessment_data_and_where_that_data_lives(world):
    block = svc._catalog_grounding("flujos de Pampa")
    assert block.count("FLUJOS PUBLICADOS EN EL CATÁLOGO (solo identidad)") == 1
    for must in ("ÚNICAMENTE", "NO contiene el Assessment ni el Juicio metodológico", "adopción, confianza, cobertura o estado", "se consulta en el Assessment del modelo", "No inventes flujos"):
        assert must in block


def test_every_entry_of_the_catalog_is_free_of_the_legacy_verdict_in_the_grounding(world):
    for e in world.entries:
        block = svc._catalog_grounding(_name(e))
        assert block and f"[{e.veredicto}]" not in block and e.icon not in block, e.entry_id


def test_without_matches_the_grounding_is_empty_as_before(world):
    assert svc._catalog_grounding("zzzz qqqq") == ""


def test_the_grounding_no_longer_uses_the_legacy_prompt_formatter():
    assert "format_catalog_for_prompt" not in inspect.getsource(svc)


@pytest.fixture()
def llm(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    _CapturingClient.captured = {}
    return AssistantChatService(client_factory=_CapturingClient, execution_mode="produccion")


def _prompt(service, question):
    service.ask(load_module(HOST, PACK), question, PACK, ())
    system, user = (m["content"] for m in _CapturingClient.captured["messages"])
    return system, user


def test_the_prompt_sent_to_the_llm_has_no_legacy_verdict_in_the_catalog_block(world, llm):
    e = _with(world, "ADECUADA", "plena")
    _system, user = _prompt(llm, f"ver assessment de {_name(e)} y contame su adopción")
    block = user.split("FLUJOS PUBLICADOS EN EL CATÁLOGO", 1)[1].split("CONTEXTO (Marco Danwor)", 1)[0]
    assert e.dominio in block
    assert f"[{e.veredicto}]" not in block and not any(ch in block for ch in COLOR_EMOJIS)
    assert "mirada Danwor" not in block and "evidencia Danwor" not in block and "Reutilizable" not in block
    assert user.count(DATA_INTEGRITY_RULE) == 1


def test_an_assessment_question_that_reaches_the_llm_carries_the_limitation_not_a_substitute(world, llm):
    _system, user = _prompt(llm, "ver el assessment de zzzz yyyy")                                # sin modelo identificable: cae al LLM
    assert "TRES COSAS QUE NO SE CONFUNDEN" in user and "no tenés acceso a ese dato" in user and "FLUJOS PUBLICADOS" not in user


# ══ 3. LA REGLA distingue las tres cosas y conserva lo que ya fijaba ═══════════════════════════════════════════════════════════════════════
def test_the_rule_distinguishes_legacy_verdict_current_judgment_and_assessment_state():
    rule = DATA_INTEGRITY_RULE
    assert "TRES COSAS QUE NO SE CONFUNDEN" in rule
    assert "Veredicto legado del catálogo («mirada Danwor»" in rule and "NO una evaluación vigente" in rule
    assert "Nunca lo presentes como el Assessment, como la adopción metodológica ni como el estado" in rule
    assert "Juicio metodológico vigente (Adopción metodológica: Plena, Buena, Adecuada, Parcial o No determinable)" in rule and "solo existe si un Assessment vigente lo respalda" in rule
    assert "Estado del Assessment (vigente, desactualizado, sin assessment, no aplica, no disponible o no compatible)" in rule and "no la calidad del modelo" in rule
    assert "No uses emojis de color ni semáforos" in rule and "los colores los define el producto" in rule


def test_the_rule_keeps_what_the_previous_contract_fixed():
    rule = DATA_INTEGRITY_RULE
    for must in ("no los estimes", "no los inventes", "no tenés acceso a ese dato", "se consulta en el Catálogo", "datos reales"):
        assert must in rule
    for allowed in ("podés explicar conceptos y metodología", "ejemplo hipotético", "ilustrativo"):
        assert allowed.lower() in rule.lower()
    assert "no respondas" not in rule.lower() and "nunca respondas" not in rule.lower()
    assert "salvo los que figuren expresamente, con su fuente, en este mensaje" in rule             # ya no «en el bloque de catálogo»: ese bloque no trae evaluación


# ══ 4. NO se agregan mapas de emojis en el asistente (la autoridad es `status_tone`) ═══════════════════════════════════════════════════════════
def test_the_assistant_modules_define_no_emoji_maps():
    from pathlib import Path
    root = Path(svc.__file__).resolve().parent.parent
    sources = list((root / "services").glob("assistant_*.py")) + [root / "routers" / "assistant.py"]
    assert len(sources) >= 6
    for path in sources:
        text = path.read_text(encoding="utf-8")
        assert not any(ch in text for ch in COLOR_EMOJIS), path.name
