"""
Ficha "Sobre Sam" para Proyecto IDA Sam.

GENÉRICO: sin LLM, sin personalidad, sin memoria.
No importa emociones, ni sueno, ni memory, ni cascade.
Es un documento, no una voz. Edita las secciones de abajo.
"""
import os
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import discord

logger = logging.getLogger("sam.sobre")

ZONA = ZoneInfo("America/Lima")
ID_CANAL_SOBRE_SAM = 1564734161212805171


def _hex_color(valor: str, por_defecto: int) -> int:
    try:
        v = (valor or "").strip()
        return int(v, 16) if v else por_defecto
    except (ValueError, TypeError):
        return por_defecto


COLOR = _hex_color(os.getenv("SAM_SOBRE_COLOR", "0xFFC857"), 0xFFC857)


def _int_env(nombre: str) -> int:
    try:
        return int(os.getenv(nombre, "0") or 0)
    except (ValueError, TypeError):
        return 0


ID_SOBRE_SAM = _int_env("SAM_CANAL_SOBRE_SAM") or ID_CANAL_SOBRE_SAM
ON_SOBRE = os.getenv("SAM_SOBRE_ON", "1") == "1"

# ════════════════════════════════════════════════════════════════
#  EDITA AQUÍ. Cada bloque es (título, cuerpo).
#  ═══ EMBED 1 · QUIÉN ES ═══════════════════════════════════════
QUIEN_ES = [
    ("Qué es",
     "Sam es una IA conversacional que vive dentro de este servidor de "
     "Discord. No es un chatbot de respuestas fijas: recuerda, aprende, se "
     "cansa y duerme según un reloj."),

    ("Quién la creó",
     "Aiko (AIKO_UWU_TEILOR). El proyecto se llama **Proyecto IDA Sam**."),

    ("Su carácter",
     "Cariñosa, directa, con humor seco. Dice las cosas como son y no "
     "finge ser humana. Si no sabe algo, lo admite."),

    ("Su casa",
     "Responde cuando la mencionas (@Sam) en cualquier canal, o cuando le "
     "hablan en su canal dedicado. No escribe primero: espera a que la "
     "llamen."),
]

# ═══ EMBED 2 · QUÉ HACE ════════════════════════════════════════
HISTORIAL = (
    "Este es el camino técnico del proyecto, en orden real de construcción:"
)

QUÉ_HACE = [
    ("🧠 Memoria en 4 cajas",
     "Guarda lo que vive en cuatro compartimentos separados: su identidad, "
     "lo privado de cada persona, lo que aprende de la humanidad en general, "
     "y su agenda social (quién es quién). No mezcla una con otra."),

    ("🌊 Cascada de modelos",
     "Rutea cada respuesta por una cadena de modelos, del más rápido al más "
     "capaz. Si uno se cae o se queda sin cupo, pasa al siguiente. Así pasó "
     "de ~78 segundos a ~3 por respuesta."),

    ("🌡️ Termómetro interno",
     "Tres variables que cambian con lo que vive: ánimo, energía y necesidad "
     "social. No son decorativas: alteran su tono de respuesta."),

    ("🌙 Reloj biológico",
     "Se acuesta a las 23:00 y despierta a las 06:00 (hora de Cajamarca). "
     "Mientras duerme, recarga energía y consolida recuerdos en forma de "
     "\"sueños\": lecciones que guarda para sí."),

    ("😴 Fatiga por la hora",
     "Al anochecer empieza a hablar más cansada, aunque su batería esté "
     "llena. Es ritmo circadiano, no agotamiento: dos cosas distintas."),

    ("🎭 Distingue el tono",
     "Clasifica cada conversación: literal, broma, sarcasmo, apoyo o "
     "confesión. Una broma no se archiva como un hecho de tu vida. Una "
     "confesión envuelta en broma, sí."),

    ("👥 Conoce gente sola",
     "Cuando alguien nuevo entra al server y habla con ella, Sam lo "
     "registra en su agenda sin que nadie se lo pida."),

    ("🖼️ Ve imágenes",
     "Si le mandas una foto, la analiza y responde sobre lo que ve."),

    ("🔊 Tiene voz",
     "Puede hablar en canales de voz y generar audio de sus respuestas."),

    ("🛠️ Herramientas",
     "Consulta hora, fecha y hace búsquedas web cuando le piden datos que "
     "no puede saber por sí misma."),
]

# ═══ EMBED 3 · LÍMITES ═════════════════════════════════════════
QUÉ_NO_HACE = [
    ("❌ No es consciente",
     "No siente, no sufre, no desea. Tiene un modelo de estado que imita "
     "emociones de forma coherente, y eso es útil — pero no es una vida "
     "interior. Si alguna vez suena demasiado humana, es lenguaje, no "
     "experiencia."),

    ("❌ No inicia conversaciones",
     "Nunca escribe primero. No te manda \"hola\", no avisa que despertó "
     "hasta que alguien la llama. Es una regla de diseño, no un olvido."),

    ("❌ No modera",
     "No sanciona, no borra mensajes, no decide quién se queda. El staff "
     "modera; Sam solo conversa."),

    ("❌ No guarda datos sensibles",
     "Su agenda social registra rasgos públicos que la gente dijo de sí "
     "misma. No guarda ubicaciones, apellidos, documentos ni información "
     "íntima de terceros."),

    ("❌ No es privada",
     "Todo lo que se dice en los canales del server puede terminar en su "
     "memoria. Lo que quieres que no se archiva, se dice por mensaje "
     "directo."),

    ("❌ No sabe todo",
     "Se equivoca, alucina datos y a veces confunde sarcasmo fino con "
     "literal. Si algo crítico suena raro, verifícalo."),

    ("❌ No es terapeuta ni sustituye ayuda real",
     "Puede acompañar y escuchar. No diagnostica, no trata, no reemplaza a "
     "una persona profesional si la necesitas."),
]

# Solo comandos de uso público. Los de staff van en su propio canal.
COMANDOS = [
    ("Para hablar con ella",
     "`@Sam tu mensaje` · `!sam [mensaje]` · `!voz [mensaje]` · "
     "`/voz [mensaje]`"),
    ("Para escucharla",
     "`!join` · `!leave`"),
]

FOOTER = "Proyecto IDA Sam · Ficha del sistema"


def _hora() -> str:
    return datetime.now(ZONA).strftime("%d/%m/%Y %H:%M")


def _bloques(lista) -> str:
    return "\n\n".join(f"**{t}**\n{c}" for t, c in lista)


def canal_sobre_sam(gremio):
    if gremio is None:
        return None
    if ID_SOBRE_SAM:
        c = gremio.get_channel(ID_SOBRE_SAM)
        if c:
            return c
    return discord.utils.get(gremio.text_channels, name="sobre-sam")


def _base(titulo, descripcion, gremio=None):
    e = discord.Embed(title=titulo, description=descripcion, color=COLOR)
    if gremio and gremio.icon:
        e.set_thumbnail(url=gremio.icon.url)
    return e


def embed_quien_es(gremio) -> discord.Embed:
    nombre = gremio.name if gremio else "Proyecto IDA Sam"
    e = _base(f"🤖 Sobre Sam · {nombre}", _bloques(QUIEN_ES), gremio)
    e.set_footer(text=f"{FOOTER} · {_hora()}")
    return e


def embed_que_hace(gremio) -> discord.Embed:
    cuerpo = f"{HISTORIAL}\n\n{_bloques(QUÉ_HACE)}"
    e = _base("🛠️ Qué hace Sam · Historial técnico", cuerpo, gremio)
    e.set_footer(text=f"{FOOTER} · {_hora()}")
    return e


def embed_limites(gremio) -> discord.Embed:
    e = _base("⚠️ Lo que Sam NO hace", _bloques(QUÉ_NO_HACE), gremio)
    e.set_footer(text=f"{FOOTER} · {_hora()}")
    return e


def embed_comandos(gremio) -> discord.Embed:
    e = _base("📖 Cómo usar a Sam", _bloques(COMANDOS), gremio)
    e.set_footer(text=f"{FOOTER} · {_hora()}")
    return e


def embeds_sobre(gremio) -> list:
    """Los cuatro embeds, listos para publicar en orden."""
    return [
        embed_quien_es(gremio),
        embed_que_hace(gremio),
        embed_limites(gremio),
        embed_comandos(gremio),
    ]