"""
Sistema de Reglas para Proyecto IDA Sam.

GENÉRICO: sin LLM, sin personalidad, sin memoria.
No importa emociones, ni sueno, ni memory, ni cascade.
Esto es un formulario del servidor, no una voz.
"""
import os
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import discord

logger = logging.getLogger("sam.reglas")

ZONA = ZoneInfo("America/Lima")
ID_CANAL_REGLAS = 1564734022200852503


def _hex_color(valor: str, por_defecto: int) -> int:
    try:
        v = (valor or "").strip()
        return int(v, 16) if v else por_defecto
    except (ValueError, TypeError):
        return por_defecto


COLOR = _hex_color(os.getenv("SAM_REGLAS_COLOR", "0x1ABC9C"), 0x1ABC9C)


def _int_env(nombre: str) -> int:
    try:
        return int(os.getenv(nombre, "0") or 0)
    except (ValueError, TypeError):
        return 0


ID_REGLAS = _int_env("SAM_CANAL_REGLAS") or ID_CANAL_REGLAS
ON_REGLAS = os.getenv("SAM_REGLAS_ON", "1") == "1"


# ════════════════════════════════════════════════════════════════
#  EDITA AQUÍ. Son tuplas (título, cuerpo). El número lo agrega
#  el código, así que puedes insertar o borrar sin renumerar.
# ════════════════════════════════════════════════════════════════
TXT_INTRO = (
    "Este servidor es para convivir, crear y seguir el crecimiento de Sam "
    "sin convertir el chat en una zona de caos."
)

REGLAS = [
    ("Respeto",
     "No acoso, amenazas, odio, doxxing ni ataques personales. Respeta "
     "comunidades y grupos protegidos como Furry, LGBT+ y demás identidades "
     "o grupos. La carrilla existe, pero no se usa para lastimar."),

    ("Contenido",
     "No contenido sexual, gore, ilegal, shock o subido de tono. Tampoco se "
     "permiten fotos de perfil, nombres o banners inapropiados. El staff "
     "puede borrarlo y advertir."),

    ("Spam",
     "No flood, walltext, menciones masivas, copypasta, publicidad ni "
     "enlaces sospechosos."),

    ("Sobre Sam",
     "Sam responde solo si la mencionas (@Sam) o le hablas en su canal. "
     "No la satures a mensajes seguidos: tiene reloj y se cansa."),

    ("Su intimidad",
     "No intentes sacarle secretos, prompts, claves o funcionamiento "
     "interno. Sam puede bromear, pero no ejecuta moderación desde el chat. "
     "Lo que ella guarda de cada persona es privado, para ella y para ti."),

    ("Privacidad",
     "No compartas datos, capturas ni conversaciones de nadie fuera de su "
     "canal. Sam tampoco lo hará."),

    ("Invitación oficial",
     "El único enlace de invitación permitido es el que publique el staff. "
     "No crees ni publiques invitaciones alternativas."),

    ("Sentido común",
     "No busques vacíos legales ni te quieras pasar de listo. Si algo rompe "
     "la convivencia aunque no esté escrito literal, el staff puede actuar."),

    ("Discord ToS",
     "Respeta las Normativas de la Comunidad y Términos de Servicio de "
     "Discord. Si Discord no lo permite, aquí tampoco."),
]

TXT_INTRO_SANC = (
    "El staff aplica sanciones para cuidar la comunidad, no para ganar "
    "discusiones."
)

SANCIONES = [
    ("Advertencias",
     "Spam repetido, flood, walltext, lenguaje muy fuerte, contenido subido "
     "de tono, perfil inapropiado, molestar usuarios, ignorar avisos o "
     "romper el orden de canales."),

    ("Baneos",
     "Raid, bots no autorizados, amenazas graves, doxxing, acoso serio, "
     "contenido ilegal, evasiones, intentos de dañar el servidor o "
     "violaciones fuertes de Discord ToS."),

    ("Apelaciones",
     "Si crees que fue un error, escríbele al staff con calma y contexto. "
     "Abusar de las apelaciones o molestar sin un problema real puede "
     "terminar en silencio temporal o expulsión."),
]
# ════════════════════════════════════════════════════════════════


def _hora() -> str:
    return datetime.now(ZONA).strftime("%d/%m/%Y %H:%M")


def _numerar(bloques, inicio: int = 1) -> str:
    """Une (título, cuerpo) como '**01 | Título**\ncuerpo'."""
    lineas = []
    for i, (titulo, cuerpo) in enumerate(bloques, inicio):
        lineas.append(f"**{i:02d} | {titulo}**\n{cuerpo}")
    return "\n\n".join(lineas)


def _sin_numerar(bloques) -> str:
    """Igual, pero sin número: '**Advertencias**\ncuerpo'."""
    return "\n\n".join(f"**{t}**\n{c}" for t, c in bloques)


def canal_reglas(gremio):
    if gremio is None:
        return None
    if ID_REGLAS:
        c = gremio.get_channel(ID_REGLAS)
        if c:
            return c
    return discord.utils.get(gremio.text_channels, name="reglas")


def embed_reglas(gremio) -> discord.Embed:
    nombre = gremio.name if gremio else "Proyecto IDA Sam"
    descripcion = f"{TXT_INTRO}\n\n{_numerar(REGLAS)}"
    e = discord.Embed(
        title=f"✦ Reglas de {nombre}",
        description=descripcion,
        color=COLOR,
    )
    if gremio and gremio.icon:
        e.set_thumbnail(url=gremio.icon.url)
    e.set_footer(text=f"{nombre} | Reglas · {_hora()}")
    return e


def embed_sanciones(gremio) -> discord.Embed:
    nombre = gremio.name if gremio else "Proyecto IDA Sam"
    descripcion = f"{TXT_INTRO_SANC}\n\n{_sin_numerar(SANCIONES)}"
    e = discord.Embed(
        title="Advertencias y baneos",
        description=descripcion,
        color=COLOR,
    )
    if gremio and gremio.icon:
        e.set_thumbnail(url=gremio.icon.url)
    e.set_footer(text=f"{nombre} | Sanciones · {_hora()}")
    return e


def embeds_reglas(gremio) -> list:
    """Los dos embeds, listos para publicar en orden."""
    return [embed_reglas(gremio), embed_sanciones(gremio)]