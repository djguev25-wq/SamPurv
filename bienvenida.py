"""
Sistema de Bienvenidas, Despedidas, Reglas y Anuncios
para Proyecto IDA Sam.

GENÉRICO: sin LLM, sin personalidad, sin memoria.
No importa emociones, ni memory, ni sueno, ni cascade.
Sam no participa: esto es un formulario del servidor, no una voz.
"""
import os
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import discord

logger = logging.getLogger("sam.bienvenida")

ZONA = ZoneInfo("America/Lima")

# ── IDs fijos de Proyecto IDA Sam ─────────────────────────────────
ID_CANAL_BIENVENIDA = 1564733958577455124
ID_CANAL_REGLAS     = 1564734022200852503
ID_CANAL_SOBRE_SAM  = 1564734161212805171


def _hex_color(valor: str, por_defecto: int) -> int:
    """Acepta '0x1ABC9C', '1ABC9C' o vacío. Nunca revienta."""
    try:
        v = (valor or "").strip()
        if not v:
            return por_defecto
        return int(v, 16)
    except (ValueError, TypeError):
        logger.warning("[Bienvenida] Color inválido %r, uso defecto.", valor)
        return por_defecto


COLOR = _hex_color(os.getenv("SAM_BIENV_COLOR", "0x1ABC9C"), 0x1ABC9C)


def _int_env(nombre: str) -> int:
    try:
        return int(os.getenv(nombre, "0") or 0)
    except (ValueError, TypeError):
        return 0


ID_BIENVENIDA = _int_env("SAM_CANAL_BIENVENIDA") or ID_CANAL_BIENVENIDA
ID_REGLAS     = _int_env("SAM_CANAL_REGLAS") or ID_CANAL_REGLAS
ID_SOBRE_SAM  = _int_env("SAM_CANAL_SOBRE_SAM") or ID_CANAL_SOBRE_SAM
ID_DESPEDIDA  = _int_env("SAM_CANAL_DESPEDIDA")
ID_ANUNCIOS   = _int_env("SAM_CANAL_ANUNCIOS")

ON_BIENVENIDA = os.getenv("SAM_BIENVENIDA_ON", "1") == "1"
ON_DESPEDIDA  = os.getenv("SAM_DESPEDIDA_ON", "1") == "1"
ON_ANUNCIOS   = os.getenv("SAM_ANUNCIOS_ON", "1") == "1"

REGLAS = [
    ("📜 1 · Respeto",
     "Trato cordial a personas y a Sam. Sin insultos, acoso ni discriminación."),
    ("🔇 2 · Canales",
     "Usa cada canal para lo que dice su nombre. Spam o flood = silencio."),
    ("🤖 3 · Sobre Sam",
     "Sam responde solo si la mencionas (@Sam) o le hablas en su canal."),
    ("🔒 4 · Privacidad",
     "No compartas datos de nadie. Sam tampoco lo hará."),
    ("🚫 5 · Contenido",
     "Sin NSFW, gore ni material ilegal. El servidor es mixto."),
]

# Frase base. Sin "roles": no existe ese canal.
TXT_BIENV = (
    "Hola {user}, ya estás dentro de {server}.\n"
    "Lee {reglas}, conoce a {sobre} y disfruta el servidor "
    "mientras seguimos mejorando el sistema."
)
TXT_DESP = "**{user}** dejó el servidor."
TXT_TITULO_BIENV = "Bienvenido/a a {server}"
TXT_TITULO_DESP = "Un miembro ha salido"
FOOTER = "Sistema de bienvenida · Proyecto IDA Sam"


def _hora() -> str:
    return datetime.now(ZONA).strftime("%d/%m/%Y %H:%M")


def _tiempo_relativo(desde: datetime) -> str:
    """'hoy', 'ayer', 'hace 3 días', 'hace 2 semanas', 'hace 5 meses', 'hace 1 año'."""
    if desde is None:
        return "sin fecha"
    ahora = datetime.now(desde.tzinfo) if desde.tzinfo else datetime.now()
    dif = (ahora - desde).total_seconds()

    if dif < 86400:
        return "hoy"
    if dif < 172800:
        return "ayer"
    dias = int(dif // 86400)
    if dias < 7:
        return f"hace {dias} día" + ("s" if dias != 1 else "")
    if dias < 14:
        return "hace 1 semana"
    if dias < 30:
        sem = dias // 7
        return f"hace {sem} semana" + ("s" if sem != 1 else "")
    if dias < 365:
        meses = int(dias // 30)
        return "hace 1 mes" if meses == 1 else f"hace {meses} meses"
    anios = round(dias / 365)
    return "hace 1 año" if anios == 1 else f"hace {anios} años"


def _mencion_canal(canal_id, texto_por_defecto="las reglas") -> str:
    """'<#ID>' clickeable, o texto plano si no hay ID configurado."""
    if canal_id:
        return f"<#{canal_id}>"
    return texto_por_defecto


def _canal(gremio, canal_id, nombre):
    if gremio is None:
        return None
    if canal_id:
        c = gremio.get_channel(canal_id)
        if c:
            return c
    return discord.utils.get(gremio.text_channels, name=nombre)


def canal_bienvenida(gremio):
    return _canal(gremio, ID_BIENVENIDA, "bienvenida")


def canal_reglas(gremio):
    return _canal(gremio, ID_REGLAS, "reglas")


def canal_sobre_sam(gremio):
    return _canal(gremio, ID_SOBRE_SAM, "sobre-sam")


def canal_despedida(gremio):
    return _canal(gremio, ID_DESPEDIDA, "despedidas")


def canal_anuncios(gremio):
    return _canal(gremio, ID_ANUNCIOS, "anuncios")


def _embed_base(titulo, descripcion, member=None):
    e = discord.Embed(title=titulo, description=descripcion, color=COLOR)
    if member is not None:
        foto = member.display_avatar.url
        e.set_thumbnail(url=foto)
        e.set_image(url=foto)
    return e


def embed_bienvenida(member) -> discord.Embed:
    g = member.guild
    cuerpo = TXT_BIENV.format(
        user=member.mention,
        server=g.name,
        reglas=_mencion_canal(ID_REGLAS, "las reglas"),
        sobre=_mencion_canal(ID_SOBRE_SAM, "sobre Sam"),
    )
    e = _embed_base(TXT_TITULO_BIENV.format(server=g.name), cuerpo, member)

    joined = getattr(member, "joined_at", None)
    e.add_field(name="Estado", value="Activo", inline=True)
    e.add_field(name="Miembro", value=f"#{g.member_count or '?'}", inline=True)
    e.add_field(name="Cuenta creada",
                value=member.created_at.strftime("%d/%m/%Y"), inline=True)

    if joined:
        local = joined.astimezone(ZONA)
        e.add_field(
            name="Entró",
            value=f"{local.strftime('%d/%m/%Y · %H:%M')}\n{_tiempo_relativo(joined)}",
            inline=False,
        )
        # Discord renderiza esto como "hoy / ayer / hace N días" y lo
        # actualiza solo, sin que el servidor haga nada.
        e.timestamp = joined
        e.set_footer(text="Sistema de bienvenida · Proyecto IDA Sam")
    else:
        e.set_footer(text=f"{FOOTER} · {_hora()}")
    return e


def embed_despedida(member) -> discord.Embed:
    g = member.guild
    cuerpo = TXT_DESP.format(user=member.display_name)
    e = _embed_base(TXT_TITULO_DESP, cuerpo, member)

    joined = getattr(member, "joined_at", None)
    if joined:
        e.add_field(name="Estuvo", value=_tiempo_relativo(joined), inline=True)
    e.add_field(name="Miembros ahora", value=str(g.member_count or "?"), inline=True)
    e.timestamp = discord.utils.utcnow()
    e.set_footer(text=f"Sistema de despedidas · {FOOTER}")
    return e


def embed_reglas(gremio) -> discord.Embed:
    e = discord.Embed(
        title=f"Reglas de {gremio.name}",
        description="Leer y aceptar es requisito para participar.",
        color=COLOR,
    )
    for nombre, texto in REGLAS:
        e.add_field(name=nombre, value=texto, inline=False)
    e.set_footer(text=f"Actualizado {_hora()}")
    return e


def embed_anuncio(titulo, cuerpo, autor) -> discord.Embed:
    e = discord.Embed(title=f"📣 {titulo}", description=cuerpo, color=COLOR)
    e.timestamp = discord.utils.utcnow()
    e.set_footer(text=f"Anuncio de {autor.display_name}")
    if autor.guild and autor.guild.icon:
        e.set_thumbnail(url=autor.guild.icon.url)
    return e