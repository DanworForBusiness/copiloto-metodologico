"""Contacto con el candidato desde el pipeline de CCHH (diálogo «Contactar»): plantillas de mensaje y disponibilidad de canales.

Las plantillas son las de la app de referencia (`components/MensajeCandidatoModal.tsx` + `lib/mensajes.ts`): cuatro situaciones × dos canales (WhatsApp corto/informal, mail con asunto/saludo/cierre). Se
reusan tal cual, con una única adaptación: el saludo lleva el nombre del candidato y el puesto sale del proceso en curso. Son un punto de partida EDITABLE: este módulo no envía, no registra envíos
ni cambia estados — solo calcula textos y qué canales admiten los datos cargados. Abrir WhatsApp/correo es siempre una acción explícita del usuario en el navegador."""
from __future__ import annotations

import re

TIPOS_MENSAJE: tuple[tuple[str, str], ...] = (
    ("primer_contacto", "Primer contacto"),
    ("actualizacion", "Actualización del proceso (feedback positivo)"),
    ("cierre", "Cierre del proceso (avance con otro perfil)"),
    ("pausa", "Pausa del proceso (pendiente de definiciones internas)"),
)
CANALES_MENSAJE: tuple[tuple[str, str], ...] = (("whatsapp", "WhatsApp"), ("mail", "Mail"))

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def whatsapp_digits(phone: str | None) -> str:
    """`wa.me` necesita el número solo con dígitos (código de país incluido). Sin código de país el enlace puede no abrir la conversación correcta (limitación de la referencia, avisada en el diálogo)."""
    return re.sub(r"[^0-9]", "", str(phone or ""))


def valid_email(email: str | None) -> str:
    """Email utilizable (forma básica) o `""`: un valor mal formado no habilita el canal de correo."""
    value = str(email or "").strip()
    return value if _EMAIL_RE.match(value) else ""


def _whatsapp(tipo: str, puesto: str, nombre: str) -> str:
    saludo = f"¡Hola, {nombre}! ¿Cómo estás?" if nombre else "¡Hola! ¿Cómo estás?"
    cuerpos = {
        "primer_contacto": f"Te contacto desde el equipo de Capital Humano de Danwor porque vimos tu perfil y nos pareció muy interesante para una búsqueda de {puesto}.\nNos gustaría conocerte y contarte un poco más sobre la oportunidad. ¿Tendrías disponibilidad para coordinar una entrevista?\n¡Quedo atenta!",
        "actualizacion": f"Quería contarte que seguimos avanzando con el proceso de selección para la posición de {puesto} y que el feedback de tu entrevista fue muy positivo.\nEn este momento estamos avanzando con las siguientes etapas del proceso. Apenas tenga novedades sobre los próximos pasos, me voy a comunicar con vos.\n¡Gracias por tu tiempo y por el interés en Danwor!",
        "cierre": f"Quería contarte que, luego de evaluar a los distintos candidatos, en esta oportunidad decidimos avanzar con otro perfil para la posición de {puesto}.\nQuiero agradecerte por el tiempo que compartiste con nosotros y por el interés en Danwor. Fue un gusto conocerte y espero poder volver a contactarte si surge una oportunidad que se ajuste a tu perfil.\n¡Te deseo muchos éxitos en tus próximos desafíos!",
        "pausa": f"Quería mantenerte al tanto del proceso.\nEn este momento estamos esperando algunas definiciones sobre proyectos y asignaciones, ya que eso va a determinar cómo continuaremos con las próximas incorporaciones. Por ese motivo, por ahora no podemos avanzar con las próximas etapas del proceso para la posición de {puesto}.\nNos gustó mucho tu perfil y queremos seguir teniéndote en consideración. Apenas tenga novedades, me voy a comunicar con vos.\n¡Gracias por tu tiempo y por la buena predisposición!",
    }
    return f"{saludo}\n{cuerpos[tipo]}"


def _mail(tipo: str, puesto: str, nombre: str) -> dict:
    saludo = f"Hola, {nombre}, ¿cómo estás?" if nombre else "Hola, ¿cómo estás?"
    textos = {
        "primer_contacto": (
            f"Oportunidad laboral en Danwor | Posición de {puesto}",
            f"Te contactamos desde el equipo de Capital Humano de Danwor porque vimos tu perfil y nos pareció muy interesante para una búsqueda de {puesto}.\nNos gustaría conocerte, contarte un poco más sobre la oportunidad y conversar sobre tu experiencia.\n¿Tendrías disponibilidad para realizar una entrevista el [día] a las [hora]?\nQuedamos atentos a tu respuesta.\n¡Muchas gracias!"),
        "actualizacion": (
            f"Actualización de tu proceso de selección | {puesto}",
            f"Queríamos contarte que seguimos avanzando con el proceso de selección para la posición de {puesto} y que el feedback de tu entrevista fue muy positivo.\nActualmente estamos avanzando con las siguientes etapas del proceso. Apenas tengamos novedades sobre los próximos pasos, nos estaremos comunicando con vos.\nMuchas gracias por tu tiempo, tu interés en Danwor y la buena predisposición durante el proceso.\n¡Seguimos en contacto!"),
        "cierre": (
            f"Proceso de selección | Posición de {puesto}",
            f"Queríamos agradecerte por el tiempo que compartiste con nosotros durante el proceso de selección para la posición de {puesto}.\nLuego de evaluar a los distintos candidatos, en esta oportunidad decidimos avanzar con otro perfil que se ajusta mejor a las necesidades actuales de la posición.\nValoramos mucho el interés que demostraste por Danwor y fue un gusto conocerte. Con tu autorización, nos gustaría conservar tu perfil para tenerlo en cuenta en futuras oportunidades que puedan alinearse con tu experiencia.\nTe deseamos muchos éxitos en tus próximos desafíos.\n¡Muchas gracias!"),
        "pausa": (
            f"Actualización sobre tu proceso de selección | {puesto}",
            f"Queríamos mantenerte al tanto del proceso de selección para la posición de {puesto}.\nEn este momento estamos esperando algunas definiciones internas relacionadas con proyectos y asignaciones, ya que eso va a determinar cómo continuaremos con las próximas incorporaciones. Por ese motivo, por el momento no podremos avanzar con las siguientes etapas del proceso.\nNos gustó mucho tu perfil y queremos seguir teniéndote en consideración. Apenas tengamos novedades, nos volveremos a comunicar con vos.\nMuchas gracias por tu tiempo, el interés en Danwor y la buena predisposición durante todo el proceso.\n¡Seguimos en contacto!"),
    }
    asunto, cuerpo = textos[tipo]
    return {"asunto": asunto, "cuerpo": f"{saludo}\n{cuerpo}"}


def build_messages(puesto: str | None, nombre: str | None) -> dict:
    """`{tipo: {canal: {asunto, cuerpo}}}` con las 4 situaciones × 2 canales (WhatsApp sin asunto)."""
    puesto_txt = str(puesto or "").strip() or "la posición"
    nombre_txt = str(nombre or "").strip()
    return {
        tipo: {
            "whatsapp": {"asunto": None, "cuerpo": _whatsapp(tipo, puesto_txt, nombre_txt)},
            "mail": _mail(tipo, puesto_txt, nombre_txt),
        }
        for tipo, _label in TIPOS_MENSAJE
    }


def contact_view(profile: dict, busqueda: dict, application: dict) -> dict:
    """Vista del diálogo de contacto para UN candidato (nunca de otros): datos de contacto, canales disponibles y mensajes sugeridos con el contexto del proceso."""
    phone = str(profile.get("phone") or "").strip()
    email = valid_email(profile.get("email"))
    digits = whatsapp_digits(phone)
    nombre = " ".join(p for p in (str(profile.get("first_name") or "").strip(), str(profile.get("last_name") or "").strip()) if p)
    primer_nombre = str(profile.get("first_name") or "").strip()
    return {
        "nombre": nombre or "el candidato",
        "puesto": str(busqueda.get("nombre_puesto") or "").strip(),
        "etapa": application.get("etapa"),
        "email": email,
        "phone": phone,
        "whatsapp_digits": digits,
        "has_whatsapp": bool(digits),
        "has_email": bool(email),
        "default_channel": "whatsapp" if digits else "mail",
        "tipos": TIPOS_MENSAJE,
        "canales": CANALES_MENSAJE,
        "messages": build_messages(busqueda.get("nombre_puesto"), primer_nombre),
    }
