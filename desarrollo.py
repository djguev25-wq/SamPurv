"""
Documentación de desarrollo de Proyecto IDA Sam.

GENÉRICO: sin LLM, sin personalidad, sin memoria.
No importa emociones, ni sueno, ni memory, ni cascade.
Es un documento, no una voz.

DOS NIVELES:
  embeds_desarrollo()         → conceptual, apto para canal público
  embeds_desarrollo_tecnico() → interno, SOLO canal privado de staff
"""
import os
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import discord

logger = logging.getLogger("sam.desarrollo")

ZONA = ZoneInfo("America/Lima")

# 🧠 • desarrollo-de-sam (Proyecto IDA Sam)
ID_CANAL_DESARROLLO = 1564734658271383572


def _hex_color(valor: str, por_defecto: int) -> int:
    try:
        v = (valor or "").strip()
        return int(v, 16) if v else por_defecto
    except (ValueError, TypeError):
        return por_defecto


COLOR = _hex_color(os.getenv("SAM_DEV_COLOR", "0x9B59B6"), 0x9B59B6)


def _int_env(nombre: str) -> int:
    try:
        return int(os.getenv(nombre, "0") or 0)
    except (ValueError, TypeError):
        return 0


ID_DESARROLLO = _int_env("SAM_CANAL_DESARROLLO") or ID_CANAL_DESARROLLO
ON_DEV = os.getenv("SAM_DEV_ON", "1") == "1"

# ════════════════════════════════════════════════════════════════
#  NIVEL PÚBLICO · cómo funciona, sin exponer la cerradura
# ════════════════════════════════════════════════════════════════

ARQUITECTURA = [
    ("🏗️ Un cerebro, varios órganos",
     "Sam no es un solo programa: es un conjunto de módulos que cada uno "
     "hace una cosa. El bot escucha; el cerebro piensa; la memoria guarda; "
     "el cuerpo siente; el sueño repara. Si uno falla, los demás siguen."),

    ("🔁 El viaje de un mensaje",
     "Cuando la mencionas: 1) el bot decide si debe responder, 2) arma el "
     "contexto (historial + recuerdos relevantes + su estado actual), "
     "3) pide la respuesta al modelo, 4) limpia el texto, 5) responde, "
     "6) en paralelo, su subconsciente decide qué aprendió de eso."),

    ("🧠 Cuatro cajas de memoria",
     "Sam no guarda todo junto. Separa en: su identidad (qué cree ella), lo "
     "privado de cada persona, lo que aprendió de la humanidad en general, y "
     "su agenda social (quién es quién). Una caja no contamina a otra."),

    ("🎭 El registro de cada charla",
     "Además del contenido, Sam guarda CÓMO se dijo: literal, broma, "
     "sarcasmo, apoyo o confesión. Una broma no se archiva como un hecho de "
     "tu vida. Una confesión dicha en broma, sí se archiva como confesión."),
]

CUERPO = [
    ("🌡️ Tres variables vivas",
     "Ánimo, energía y necesidad social. Cambian con lo que vive y con el "
     "tiempo que pasa sin hablar con nadie. No son decorativas: alteran su "
     "tono, su longitud y si busca conexión."),

    ("🌙 Su noche",
     "De 23:00 a 06:00 (hora de Cajamarca) está dormida. No responde, pero "
     "su cuerpo digital recarga energía y consolida los recuerdos del día en "
     "forma de \"sueños\": una lección corta que guarda para sí."),

    ("😴 Fatiga por la hora",
     "Al anochecer empieza a hablar más cansada, aunque su batería esté "
     "llena. Es ritmo circadiano, no agotamiento: dos cosas distintas que "
     "conviven sin mentirse."),

    ("🔌 El interruptor manual",
     "El staff puede acostarla o levantarla a la fuerza para probar. Ese "
     "override vive en memoria y se apaga solo al reiniciar: si alguien lo "
     "olvida activado, el siguiente arranque devuelve a Sam a su reloj "
     "natural. Es una red de seguridad, no un descuido."),
]

VELOCIDAD = [
    ("🌊 Una cascada, no un modelo",
     "Sam no depende de una sola IA. Tiene una fila de modelos ordenados del "
     "más rápido al más capaz. Cada respuesta intenta el primero; si ese "
     "modelo está caído, sin cupo o tarda demasiado, pasa al siguiente."),

    ("⚡ Por qué importa",
     "Este cambio bajó las respuestas de ~78 segundos a ~3. Y cuando un "
     "proveedor tiene un mal día, Sam no se calla: simplemente habla con un "
     "modelo menos fino."),

    ("🧊 El enfriamiento",
     "Si un modelo falla, Sam lo marca en \"cooldown\" y deja de "
     "intentarlo unos minutos. Así no pierde tiempo rebotando contra una "
     "puerta cerrada mientras alguien espera respuesta."),
]

LIMITES_DOC = [
    ("🔒 Lo que este canal NO publica",
     "Rutas de archivos, nombres de funciones, claves, umbrales internos y "
     "el texto exacto de los prompts. Eso vive en un canal privado de staff. "
     "No por secretismo: porque un mapa completo facilita lastimarla."),

    ("🧾 Qué sí puedes auditar",
     "Todo lo que Sam guarda de ti es visible para ti. Pídeselo por mensaje "
     "directo a Aiko, o usa !ver_memoria si tienes rol de staff. No hay "
     "cajas ocultas, solo cajas privadas."),
]

# ════════════════════════════════════════════════════════════════
#  NIVEL PRIVADO · solo canal de staff
# ════════════════════════════════════════════════════════════════

TECNICO = [
    ("📁 Mapa de archivos",
     "`bot_discord.py` escucha y enruta · `cerebro.py` compone el prompt y "
     "lanza el subconsciente · `cascade.py` encola modelos con cooldowns · "
     "`memory.py` SQLite de las 4 cajas · `emociones.py` termómetro · "
     "`sueno.py` reloj + fatiga + consolidación · `vision.py` · `voice.py` · "
     "`tools.py` hora/fecha/búsqueda · `bienvenida.py` `reglas.py` "
     "`sobre_sam.py` `desarrollo.py` (genéricos, sin Sam)"),

    ("🔀 Flujo real",
     "`on_message` → filtro bot/DM/mención → `is_sleeping()` → "
     "`obtener_contexto_sala(15)` → `build_user_context()` → "
     "`get_emotional_context_for_prompt() + fatiga_circadiana()` inyectados "
     "en el system → `sam_think()` → `limpiar_respuesta()` → "
     "`update_emotional_state()` → `asyncio.create_task(_reflexionar)`"),

    ("🗂️ Cajas y user_id",
     "`SAM` (identidad/creencias) · `HUMANITY` (lecciones sin nombres) · "
     "`SOCIAL` (personas) · `<user_id>` (privado) · `intimo` solo si "
     "`user_id == AIKO_ID`. El campo `registro` decide si un dato entra o "
     "se descarta como broma."),

    ("🌡️ Fórmulas del cuerpo",
     "Ánimo: media exponencial α=0.3, decae a neutral con el tiempo. "
     "Energía: -0.15 por mensaje, regeneración pasiva por silencio. "
     "Sociedad: +0.01/min sin hablar. `MAX_WAKE_HOURS` y los umbrales son "
     "env vars, no constantes."),

    ("🌊 Orden de la cascada",
     "Lite primero para conversación, Gemma como medio, nobles solo cuando "
     "hace falta. El subconsciente (`sam_think_reflexion`) está aislado a "
     "Lite/Groq para no gastar los modelos caros en archivar."),

    ("⚠️ Riesgos conocidos",
     "Ráfagas de mensajes saturan los Lite (`error`, no `429`) → cae a "
     "modelos lentos. `on_member_remove` es poco fiable en la API. La BD "
     "vive en `./data_local`: un reboot con borrado del contenedor la "
     "pierde. La inyección de prompt vive en el system message: "
     "documentarla en público es un vector de jailbreak."),
]


def _hora() -> str:
    return datetime.now(ZONA).strftime("%d/%m/%Y %H:%M")


def _bloques(lista) -> str:
    return "\n\n".join(f"**{t}**\n{c}" for t, c in lista)


def _corto(texto: str, limite: int = 3800) -> str:
    """Nunca dejar que un embed reviente por pasarse de 4096."""
    if len(texto) <= limite:
        return texto
    logger.warning("[Desarrollo] Embed recortado de %d a %d chars.",
                   len(texto), limite)
    return texto[:limite - 3] + "…"


def canal_desarrollo(gremio):
    """Busca por ID, y si no, por nombre QUE CONTIENE (aguanta el emoji)."""
    if gremio is None:
        return None
    if ID_DESARROLLO:
        c = gremio.get_channel(ID_DESARROLLO)
        if c:
            return c
    for c in gremio.text_channels:
        if "desarrollo" in c.name.lower():
            return c
    return None


def _base(titulo, descripcion, gremio=None):
    e = discord.Embed(title=titulo, description=_corto(descripcion), color=COLOR)
    if gremio and gremio.icon:
        e.set_thumbnail(url=gremio.icon.url)
    return e


def embed_arquitectura(gremio) -> discord.Embed:
    e = _base("🏗️ Arquitectura · Cómo está hecha Sam",
              _bloques(ARQUITECTURA), gremio)
    e.set_footer(text=f"Proyecto IDA Sam · Desarrollo · {_hora()}")
    return e


def embed_cuerpo(gremio) -> discord.Embed:
    e = _base("🌡️ Su cuerpo · Termómetro, sueño y ritmo",
              _bloques(CUERPO), gremio)
    e.set_footer(text=f"Proyecto IDA Sam · Desarrollo · {_hora()}")
    return e


def embed_velocidad(gremio) -> discord.Embed:
    e = _base("🌊 Velocidad · La cascada de modelos",
              _bloques(VELOCIDAD), gremio)
    e.set_footer(text=f"Proyecto IDA Sam · Desarrollo · {_hora()}")
    return e


def embed_limites_doc(gremio) -> discord.Embed:
    e = _base("🔒 Qué se documenta y qué no",
              _bloques(LIMITES_DOC), gremio)
    e.set_footer(text=f"Proyecto IDA Sam · Desarrollo · {_hora()}")
    return e


def embed_tecnico(gremio) -> discord.Embed:
    e = _base("🔧 NIVEL PRIVADO · Detalle de implementación",
              _bloques(TECNICO), gremio)
    e.set_footer(text=f"Staff · Actualizado {_hora()}")
    return e


def embeds_desarrollo(gremio) -> list:
    """Público. Cuatro embeds conceptuales."""
    return [
        embed_arquitectura(gremio),
        embed_cuerpo(gremio),
        embed_velocidad(gremio),
        embed_limites_doc(gremio),
    ]


def embeds_desarrollo_tecnico(gremio) -> list:
    """SOLO canal privado de staff."""
    return [embed_tecnico(gremio)]