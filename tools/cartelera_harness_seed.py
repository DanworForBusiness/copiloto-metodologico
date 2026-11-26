"""Datos REPRESENTATIVOS (ficticios) de Cartelera Virtual para el harness visual de CCHH (`tools/cchh_visual_harness.py`).

Siembra con las funciones REALES del servicio/repositorio (nunca JSON armado a mano) dentro del runtime temporal del harness, y reemplaza la
lectura de proyectos de Operaciones (Graph) por un catálogo ficticio: en el harness no hay Microsoft 365. Nada de esto toca datos productivos.
"""
from __future__ import annotations

from datetime import timedelta

PROJECTS = [
    ("101", "Plataforma de datos corporativa", "201", "Cliente Demo Alimentos"),
    ("102", "Modernización SAP BW a Datasphere", "202", "Cliente Demo Energía"),
    ("103", "Tablero de ventas regional", "203", "Cliente Demo Retail"),
    ("104", "Planificación financiera con SAC", "204", "Cliente Demo Industria"),
    ("105", "Gobierno de datos maestros", "201", "Cliente Demo Alimentos"),
    ("106", "Copiloto metodológico interno", "205", "Danwor"),
]


def patch_projects() -> None:
    from webapp.services.staffing_repository import StaffingRepository
    StaffingRepository.get_projects = lambda self, token, cache_scope=None: list(PROJECTS)       # noqa: E731 — solo harness
    from webapp.services import cartelera_service
    original = cartelera_service.project_options
    cartelera_service.project_options = lambda token: original(token or "harness-token")         # noqa: E731 — sin MSAL en el harness


def seed() -> None:
    from webapp.services import cartelera_repository as repo, cartelera_service as svc
    actor = "Capital Humano (datos de ejemplo)"
    now = svc.now_utc()
    current = svc.month_key()
    previous, following = svc.shift_month(current, -1), svc.shift_month(current, 1)

    def item(title, body, link="", label=""):
        return {"id": svc.uuid.uuid4().hex[:12], "title": title, "body": body, "link": link, "link_label": label}

    def project(pid, kind, service, tech, body, team, thanks=""):
        _, name, _, client = next(p for p in PROJECTS if p[0] == pid)
        return {"id": svc.uuid.uuid4().hex[:12], "project_id": pid, "project_name": name, "client_name": client, "kind": kind, "service": service,
                "tech": tech, "body": body, "team": team, "thanks": thanks, "link": ""}

    sections = {
        "comunicados": [item("Nuevo esquema de trabajo híbrido", "Desde el próximo mes ajustamos los días de oficina por equipo. Cada líder comparte el calendario de su área.",
                             "https://www.danwor.com/", "Ver la política"),
                        item("Actualización del Hub Danwor", "Ya podés consultar la Cartelera Virtual desde cualquier espacio del Hub con el ícono de la barra superior.")],
        "proyectos": [project("102", "inicio", "Migración y modernización", "SAP Datasphere, SAP BW/4HANA", "Migración del modelo de reporting a Datasphere con capa semántica gobernada.", "Equipo Datasphere"),
                      project("103", "fin", "Analítica de ventas", "SAP Analytics Cloud", "Tablero regional de ventas con actualización diaria.", "Equipo BI", "¡Gracias a todo el equipo por el compromiso!")],
        "bienestar": [item("Pausas activas los miércoles", "Sumate a 15 minutos de estiramiento guiado a las 16 h, presencial o por Teams.")],
        "aprendizaje": [item("Ciclo de charlas internas", "Este mes: buenas prácticas de modelado en Datasphere y gobierno de datos.", "https://www.danwor.com/", "Inscribirme")],
        "otros": [],
        "beneficios": [item("Reintegro de capacitación", "Recordá que podés pedir el reintegro de certificaciones aprobadas.")],
    }
    published_sections = [{"key": s["key"], "wide": s["key"] in ("proyectos",), "items": sections.get(s["key"], [])} for s in svc.SECTIONS]
    repo.save("editions", current, {"month": current, "title": "Compartir conocimiento genera crecimiento", "subtitle": "Novedades, proyectos y celebraciones del mes",
                                    "state": "published", "publish_at": (now - timedelta(days=2)).isoformat(), "published_at": (now - timedelta(days=2)).isoformat(),
                                    "notified_key": None, "sections": published_sections,
                                    "history": [{"at": repo.now_iso(), "actor": actor, "action": "Publicó la edición"}]}, expected_revision=None)
    repo.save("editions", previous, {"month": previous, "title": "Cerramos un trimestre intenso", "subtitle": "Gracias por el esfuerzo de todos",
                                     "state": "published", "publish_at": (now - timedelta(days=32)).isoformat(), "published_at": (now - timedelta(days=32)).isoformat(),
                                     "sections": [{"key": s["key"], "wide": False, "items": sections.get(s["key"], [])[:1] if s["kind"] == "general" else []} for s in svc.SECTIONS],
                                     "history": [{"at": repo.now_iso(), "actor": actor, "action": "Publicó la edición"}]}, expected_revision=None)
    repo.save("editions", following, {"month": following, "title": "Cierre de año", "subtitle": "Celebraciones y agenda de diciembre",
                                      "state": "scheduled", "publish_at": (now + timedelta(days=6)).isoformat(), "published_at": None,
                                      "sections": [{"key": s["key"], "wide": False, "items": []} for s in svc.SECTIONS],
                                      "history": [{"at": repo.now_iso(), "actor": actor, "action": "Programó la publicación"}]}, expected_revision=None)

    today = now.astimezone(svc.BUSINESS_TZ).date()
    people = [("Ana Ejemplo", 0, True, False), ("Bruno Muestra", 2, True, False), ("Carla Prueba", 5, True, False), ("Diego Demo", 11, True, False),
              ("Elena Ficticia", 16, True, False), ("Federico Sin Autorización", 3, False, False), ("Gabriela Oculta", 1, True, True)]
    by_name = {}
    for name, offset, authorized, hidden in people:
        day = today + timedelta(days=offset)
        saved = svc.save_birthday(None, None, name=name, day=day.day, month=day.month, authorized=authorized, actor=actor)
        by_name[name] = saved
        if hidden:                                                         # registro del modelo anterior: la consolidación lo deja sin autorización
            repo.save("birthdays", saved["id"], {"hidden": True, "hidden_change": {"value": True, "by": actor, "at": repo.now_iso()}},
                      expected_revision=saved["revision"])

    # Saludos («Preparar saludo»): publicado para quien cumple hoy, programado para su día a las 9 y otro en borrador; el resto, sin preparar.
    def greet(name, body=None):
        b = by_name[name]
        data = {**svc.greeting_defaults(b), **({"body": body} if body else {})}
        return b, svc.next_birthday(b).year, svc.save_greeting(b["id"], svc.next_birthday(b).year, None, data, actor)
    b, year, g = greet("Ana Ejemplo", "¡Feliz cumpleaños, Ana! Gracias por tu energía y tu compromiso de todos los días. Que lo disfrutes mucho.\nTodo el equipo de Danwor te saluda.")
    svc.greeting_state(b["id"], year, g["revision"], "publish", "", actor)
    b, year, g = greet("Bruno Muestra")
    when = svc.next_birthday(b).isoformat() + "T09:00"
    svc.greeting_state(b["id"], year, g["revision"], "schedule", when, actor)
    greet("Carla Prueba")

    svc.save_institutional(None, mission="Satisfacer las necesidades analíticas del mercado generando valor adicional para nuestros clientes (texto de ejemplo).",
                           vision="Ser reconocidos por el bienestar de nuestros colaboradores y la calidad de nuestros servicios (texto de ejemplo).",
                           values=[{"title": "Integridad", "text": "Actuamos con transparencia."}, {"title": "Empatía", "text": "Valoramos otras perspectivas."},
                                   {"title": "Compromiso", "text": "Cumplimos lo que prometemos."}, {"title": "Trabajo en equipo", "text": "Logramos más juntos."}],
                           albums=[{"title": "Fiesta de fin de año", "date": f"{today.year - 1}-12-15", "description": "Álbum de ejemplo"}], actor=actor)
    svc.save_links(None, [{"title": "Política de licencias", "group": "Políticas", "url": "https://www.danwor.com/"},
                          {"title": "Solicitud de vacaciones", "group": "Formularios", "url": "https://www.danwor.com/"},
                          {"title": "Beneficios corporativos", "group": "Beneficios", "url": "https://www.danwor.com/"}], actor)
    seed_social()
    print("[harness] Cartelera Virtual sembrada: 3 ediciones (publicada, anterior y programada), 7 cumpleaños (3 saludos: publicado, programado y borrador), institucional y accesos directos")


def seed_social() -> None:
    """Comunidad e interacciones ficticias, creadas con el MISMO servicio que usa la app (reglas de aprobación incluidas)."""
    from webapp.services import cartelera_repository as repo, cartelera_service as svc, cartelera_social as social
    ch = {"sub": "cchh.visual.qa@danwor.local", "name": "CCHH Visual QA"}                     # Capital Humano (modera)
    lucia = {"sub": "lucia.colaboradora@danwor.local", "name": "Lucía Colaboradora"}
    martin = {"sub": "martin.colaborador@danwor.local", "name": "Martín Colaborador"}
    for u in (ch, lucia, martin):
        social.touch_person(u)
    # La semilla corre ANTES de que el harness sume la identidad de QA a Capital Humano: solo acá, esa identidad modera.
    from unittest import mock
    with mock.patch.object(social, "is_admin", lambda u: bool(u) and u.get("sub") == ch["sub"]):
        _seed_social_content(svc, repo, social, ch, lucia, martin)
    print("[harness] Comunidad sembrada: 8 publicaciones (oficial fijada, encuesta, pendiente, retirada, programada), comentarios, reacciones, votos y un reporte")


def _seed_social_content(svc, repo, social, ch, lucia, martin) -> None:
    L, M = "@{u:lucia.colaboradora@danwor.local}", "@{u:martin.colaborador@danwor.local}"
    welcome = social.save_post(ch, True, {"category": "oficial", "title": "Bienvenida a Comunidad Danwor", "official": True, "pinned": True,
                                          "body": "Este es el nuevo espacio para compartir logros, agradecimientos e ideas. Leé las pautas de la comunidad y ¡sumate!"})
    bi = social.save_post(lucia, False, {"category": "reconocimientos", "title": "¡Felicitaciones al equipo BI!",
                                         "body": f"Cerraron el tablero regional de ventas en tiempo récord. Gracias {M} por la mano con los datos."})
    social.save_post(martin, False, {"category": "comunidad", "title": "Damos la bienvenida a Carla",
                                     "body": "Carla se suma al equipo de Datasphere. ¡Bienvenida!"})
    poll = social.save_post(ch, True, {"category": "ideas", "title": "¿Qué charla interna te gustaría el mes próximo?", "official": True,
                                       "body": "Votá y dejá tu propuesta en los comentarios.",
                                       "poll": {"options": ["Gobierno de datos maestros", "SAP Datasphere avanzado", "IA aplicada a reporting"],
                                                "results": "after_vote"}})
    social.save_post(lucia, False, {"category": "proyectos", "title": "Go-live de la plataforma de datos",
                                    "body": "Ayer salimos en producción con la plataforma de datos corporativa. ¡Gracias a todo el equipo!"})
    social.save_post(martin, False, {"category": "personales", "title": "¡Nació mi hija Emma!", "consent": True,
                                     "body": "Quería compartirles la alegría: el martes nació Emma. Estamos todos muy bien."})   # queda PENDIENTE
    removed = social.save_post(martin, False, {"category": "comunidad", "title": "Vendo bicicleta", "body": "Si a alguien le interesa, escríbanme."})
    social.moderate_post(ch, removed["id"], "retirar", "Spam o fuera de tema · Comunidad no es un espacio de compraventa")
    social.save_post(ch, True, {"category": "oficial", "title": "Cronograma de vacaciones de verano", "official": True,
                                "body": "Compartimos el cronograma de turnos de vacaciones.",
                                "publish_at": (svc.now_utc() + timedelta(days=1)).astimezone(svc.BUSINESS_TZ).date().isoformat() + "T09:00"})
    # interacciones sobre Comunidad
    social.react(martin, f"post:{bi['id']}", "celebrate"); social.react(ch, f"post:{bi['id']}", "like"); social.react(lucia, f"post:{welcome['id']}", "like")
    c1 = social.add_comment(martin, f"post:{bi['id']}", "¡Gracias Lucía! Fue un trabajo en equipo.")
    social.add_comment(lucia, f"post:{bi['id']}", "¡Totalmente! Gran equipo.", c1["id"])
    c3 = social.add_comment(martin, f"post:{poll['id']}", f"Propongo una sobre gobierno de datos, {L} seguro se suma.")
    social.vote(lucia, poll["id"], 0); social.vote(martin, poll["id"], 2)
    social.report(lucia, "comment", c3["id"], "Spam o fuera de tema", "Ejemplo de reporte para revisar la moderación.")
    # interacciones sobre contenido institucional de la Cartelera (Novedades y saludo)
    current = svc.current_edition()
    items = [it for sec in svc.normalize_sections((current or {}).get("sections")) for it in sec.get("items") or []]
    if items:
        social.react(lucia, f"item:{items[0]['id']}", "like"); social.react(martin, f"item:{items[0]['id']}", "thanks")
        social.add_comment(martin, f"item:{items[0]['id']}", "¿El calendario ya está disponible por equipo?")
    for g in repo.list_all("greetings"):
        if svc.greeting_visible(g):
            social.react(martin, f"greeting:{g['id']}", "celebrate")
            social.add_comment(lucia, f"greeting:{g['id']}", "¡Feliz cumpleaños! Que lo disfrutes mucho.")
