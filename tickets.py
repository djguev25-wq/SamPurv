"""
Sistema de Tickets para Proyecto IDA Sam.

GENÉRICO: sin LLM, sin personalidad, sin memoria.
No importa cerebro, emociones, sueno, memory ni cascade.
Sam no ve los tickets: son infraestructura del server.
"""
import os
import io
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import discord

logger = logging.getLogger("sam.tickets")

ZONA = ZoneInfo("America/Lima")
COLOR = 0xE67E22
COLOR_CERRAR = 0x95A5A6

# ════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ════════════════════════════════════════════════════════════════
ID_CATEGORIA_TICKETS = 1564735905686888559   # 🎫 categoría AYUDA
ID_CANAL_LOGS        = 1564769615509454848   # 📄 ticket-logs

ROLES_STAFF = [
    1564704348179665016,   # 👑 Administradora
    1564704670763454514,   # 🛡️ Soporte svd
]

MAX_MENSAJES_LOG = 500

TIPOS = {
    "error":    ("🐞", "Error",    "error"),
    "reportar": ("🚩", "Reportar", "reportar"),
    "soporte":  ("🛠️", "Soporte",  "soporte"),
    "ayuda":    ("❓", "Ayuda",    "ayuda"),
}

TXT_TICKET = {
    "error": (
        "**Cuéntanos el fallo.**\n"
        "• ¿Qué intentaste hacer?\n"
        "• ¿Qué esperabas que pasara?\n"
        "• ¿Qué pasó en realidad?\n"
        "• Si puedes, manda captura del error.\n\n"
        "Cuanto más específico, más rápido lo arreglamos."
    ),
    "reportar": (
        "**Reporte de conducta.**\n"
        "• ¿Quién es la persona involucrada?\n"
        "• ¿En qué canal y cuándo ocurrió?\n"
        "• ¿Qué se dijo o hizo?\n"
        "• Si tienes capturas o enlaces de mensajes, adjúntalas.\n\n"
        "Esto es confidencial: solo lo ve el staff."
    ),
    "soporte": (
        "**Soporte del servidor.**\n"
        "• ¿Qué necesitas?\n"
        "• ¿Qué intentaste antes?\n"
        "• ¿Afecta a un canal, un rol o un comando en particular?"
    ),
    "ayuda": (
        "**Cualquier otra cosa.**\n"
        "Escríbelo con tus palabras, sin formato.\n"
        "Nosotros lo derivamos al lugar correcto."
    ),
}

TXT_PANEL = (
    "¿Algo no funciona, quieres reportar algo o necesitas ayuda? Abre un ticket.\n\n"
    "Pulsa **Crear ticket** y elige el tipo. Se abre un canal privado que solo "
    "ven tú y el staff. Nadie más lee lo que escribas ahí.\n\n"
    "🐞 **Error** — algo de Sam se rompió o responde mal.\n"
    "🚩 **Reportar** — alguien incumplió las reglas.\n"
    "🛠️ **Soporte** — dudas del servidor, permisos, canales.\n"
    "❓ **Ayuda** — cualquier otra cosa.\n\n"
    "Al cerrar el ticket, el canal desaparece. "
    "Si necesitas guardarlo, copia lo importante antes."
)

_RUTA_CONTADOR = os.path.join(
    os.getenv("SAM_DATA_DIR", "./data_local"), "tickets_contador.json"
)


# ─────────────────────────── CONTADOR ───────────────────────────
def _leer_contador() -> int:
    try:
        with open(_RUTA_CONTADOR, "r", encoding="utf-8") as f:
            return int(json.load(f).get("n", 0))
    except (FileNotFoundError, ValueError, json.JSONDecodeError, OSError):
        return 0


def _subir_contador() -> int:
    n = _leer_contador() + 1
    try:
        os.makedirs(os.path.dirname(_RUTA_CONTADOR) or ".", exist_ok=True)
        with open(_RUTA_CONTADOR, "w", encoding="utf-8") as f:
            json.dump({"n": n}, f)
    except OSError as exc:
        logger.warning("[Tickets] No pude guardar contador: %s", exc)
    return n


# ─────────────────────────── HELPERS ───────────────────────────
def _es_staff(miembro) -> bool:
    if miembro is None or getattr(miembro, "bot", False):
        return False
    ids = {r.id for r in getattr(miembro, "roles", [])}
    return bool(ids & {r for r in ROLES_STAFF if r})


async def _resolver(gremio, canal_id, pista_nombre=""):
    """Busca por ID (con fetch si el caché está frío) y al final por nombre."""
    if not gremio:
        return None
    c = gremio.get_channel(canal_id) if canal_id else None
    if c is None and canal_id:
        try:
            c = await gremio.fetch_channel(canal_id)
        except Exception:
            c = None
    if c is None and pista_nombre:
        c = discord.utils.get(gremio.text_channels, name=pista_nombre)
    return c


async def categoria_resuelta(gremio):
    """Devuelve la categoría de destino. Si el ID apunta a un canal,
    toma la categoría donde ese canal vive (fallback tolerante)."""
    c = await _resolver(gremio, ID_CATEGORIA_TICKETS)
    if isinstance(c, discord.CategoryChannel):
        return c, "id"
    if isinstance(c, discord.TextChannel):
        if c.category is not None:
            return c.category, f"canal:{c.name}"
        logger.warning("[Tickets] El canal %s no está en ninguna categoría.", c.name)
        return None, "canal-sin-categoria"
    return None, "no-resuelto"


def _overwrites(gremio, autor):
    """@everyone fuera; autor, staff y EL PROPIO BOT adentro.

    El bot usa los MISMOS permisos que el autor. No se le otorgan
    manage_messages ni manage_channels por overwrite: Discord rechaza
    dar por overwrite un permiso que el rol no tiene en el server.
    Para borrar el canal al cerrar basta *Gestionar canales* del rol."""
    adentro = discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        embed_links=True,
        attach_files=True,
        read_message_history=True,
        add_reactions=True,
    )
    ows = {
        gremio.default_role: discord.PermissionOverwrite(view_channel=False),
        autor: adentro,
    }
    if gremio.me is not None:
        ows[gremio.me] = adentro
    else:
        logger.warning("[Tickets] gremio.me es None: el bot no podrá escribir.")

    resueltos = 0
    for rid in ROLES_STAFF:
        if not rid:
            continue
        rol = gremio.get_role(rid)
        if rol is None:
            logger.warning("[Tickets] Rol %s NO existe en este server.", rid)
            continue
        ows[rol] = adentro
        resueltos += 1
    if resueltos == 0:
        logger.warning("[Tickets] ⚠️ Ningún rol de staff resolvió: "
                       "el ticket será invisible para el staff.")
    return ows


# ─────────────────────────── VISTAS ───────────────────────────
class VistaPanel(discord.ui.View):
    """Botón de apertura. Persistente: sobrevive reinicios."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Crear ticket",
        style=discord.ButtonStyle.primary,
        emoji="🎫",
        custom_id="sam:tickets:abrir",
    )
    async def abrir(self, interaction: discord.Interaction, button):
        await interaction.response.send_message(
            "Elige el tipo de ticket:",
            view=VistaElegirTipo(),
            ephemeral=True,
        )


class VistaElegirTipo(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.select(
        placeholder="Selecciona el tipo de ticket…",
        min_values=1,
        max_values=1,
        custom_id="sam:tickets:tipo",
        options=[
            discord.SelectOption(label=v[1], emoji=v[0], value=k)
            for k, v in TIPOS.items()
        ],
    )
    async def elegir(self, interaction: discord.Interaction, select):
        clave = select.values[0]
        await interaction.response.defer(ephemeral=True)
        await _crear_ticket(interaction, clave)
        # NO borrar interaction.message: es ephemeral y el bot no puede (404).


class VistaTicket(discord.ui.View):
    """Botón cerrar. Persistente."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Cerrar ticket",
        style=discord.ButtonStyle.danger,
        emoji="🔒",
        custom_id="sam:tickets:cerrar",
    )
    async def cerrar(self, interaction: discord.Interaction, button):
        canal = interaction.channel
        es_dueno = str(interaction.user.id) in (canal.topic or "")
        if not (_es_staff(interaction.user) or es_dueno):
            await interaction.response.send_message(
                "Solo quien abrió el ticket o el staff pueden cerrarlo.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            "🔒 Cerrando y archivando…", ephemeral=True
        )
        await _archivar_y_borrar(canal, interaction.user)


# ─────────────────────────── CREAR ───────────────────────────
async def _crear_ticket(interaction: discord.Interaction, clave: str):
    emoji, nombre, prefijo = TIPOS[clave]
    gremio = interaction.guild
    autor = interaction.user

    categoria, origen = await categoria_resuelta(gremio)
    if categoria is None:
        msg = (
            "❌ No encuentro una categoría para los tickets.\n"
            "Mueve el canal 🎫 • tickets dentro de una categoría "
            "y vuelve a intentarlo."
            if origen == "canal-sin-categoria" else
            "❌ No encuentro la categoría. Revisa "
            "`ID_CATEGORIA_TICKETS` en tickets.py."
        )
        await interaction.followup.send(msg, ephemeral=True)
        return

    ows = _overwrites(gremio, autor)
    n = _subir_contador()
    try:
        canal = await gremio.create_text_channel(
            f"{prefijo}-{n:04d}",
            category=categoria,
            overwrites=ows,
            topic=f"ticket:{clave}:{autor.id}",
            reason=f"Ticket {nombre} abierto por {autor}",
        )
    except discord.Forbidden:
        logger.exception("[Tickets] Forbidden al crear canal")
        await interaction.followup.send(
            "❌ Discord rechazó la creación. Mira la consola: ahí dice "
            "qué permiso exacto falta.",
            ephemeral=True,
        )
        return
    except Exception:
        logger.exception("[Tickets] Creación falló")
        await interaction.followup.send(
            "❌ No pude crear el ticket. Mira la consola.", ephemeral=True
        )
        return

    embed = discord.Embed(
        title=f"Ticket #{n:04d} · {emoji} {nombre}",
        description=(
            f"Abierto por {autor.mention}.\n\n"
            f"{TXT_TICKET[clave]}\n\n"
            "El staff te responderá aquí.\n"
            "Usa **Cerrar ticket** cuando esté resuelto."
        ),
        color=COLOR,
    )
    embed.set_footer(
        text=f"Proyecto IDA Sam · {datetime.now(ZONA).strftime('%d/%m/%Y %H:%M')}"
    )

    try:
        await canal.send(content=f"{autor.mention}", embed=embed, view=VistaTicket())
    except Exception:
        logger.exception("[Tickets] No pude escribir dentro del ticket")

    await interaction.followup.send(
        f"✅ Ticket creado: {canal.mention}", ephemeral=True
    )


# ─────────────────────────── ARCHIVAR ───────────────────────────
async def _transcripcion(canal) -> tuple[str, int]:
    """Lee el histórico del más antiguo al más nuevo.
    history() es un async generator: exige 'async for' y 'await'."""
    lineas = []
    total = 0
    async for m in canal.history(limit=MAX_MENSAJES_LOG, oldest_first=True):
        total += 1
        hora = m.created_at.astimezone(ZONA).strftime("%d/%m %H:%M")
        texto = (m.content or "").replace("\n", " ")
        extra = f" [adjunto: {m.attachments[0].filename}]" if m.attachments else ""
        lineas.append(f"[{hora}] {m.author.display_name}: {texto}{extra}")
    return "\n".join(lineas), total


async def _archivar_y_borrar(canal, quien_cerro):
    gremio = canal.guild
    logs = await _resolver(gremio, ID_CANAL_LOGS, "ticket-logs")

    partes = (canal.topic or "").split(":")
    clave = partes[1] if len(partes) > 2 else "ayuda"
    emoji, nombre, _ = TIPOS.get(clave, ("❓", "Ayuda", "ayuda"))

    dueno = None
    try:
        dueno = gremio.get_member(int(partes[2]))
    except (IndexError, ValueError):
        pass

    texto, total = "", 0
    try:
        texto, total = await _transcripcion(canal)
    except Exception:
        logger.exception("[Tickets] No pude leer la transcripción")

    creado = canal.created_at
    dur = datetime.now(creado.tzinfo) - creado
    h, m = divmod(int(dur.total_seconds() // 60), 60)

    if logs is None:
        logger.warning("[Tickets] ⚠️ Sin canal de logs: el ticket se borra sin archivo.")
    else:
        e = discord.Embed(
            title=f"🗄️ Ticket archivado · {canal.name}",
            description=(
                f"**Tipo:** {emoji} {nombre}\n"
                f"**Abrió:** {dueno.mention if dueno else '(no resuelto)'}\n"
                f"**Cerró:** {quien_cerro.mention}\n"
                f"**Duración:** {h}h {m}m · **Mensajes:** {total}"
            ),
            color=COLOR_CERRAR,
        )
        e.add_field(name="Creado",
                    value=creado.astimezone(ZONA).strftime("%d/%m/%Y %H:%M"),
                    inline=True)
        e.add_field(name="Cerrado",
                    value=datetime.now(ZONA).strftime("%d/%m/%Y %H:%M"),
                    inline=True)
        e.set_footer(text="Proyecto IDA Sam · Log de tickets")

        archivo = None
        if texto:
            buf = io.BytesIO(texto[:1_800_000].encode("utf-8"))
            archivo = discord.File(buf, filename=f"{canal.name}.txt")

        try:
            await logs.send(embed=e, file=archivo)
        except Exception:
            logger.exception("[Tickets] No pude escribir el log")

    try:
        await canal.delete(reason=f"Ticket cerrado por {quien_cerro}")
    except Exception:
        logger.exception("[Tickets] No pude borrar el canal")


def embed_panel(gremio) -> discord.Embed:
    e = discord.Embed(
        title="🎫 Tickets · Proyecto IDA Sam",
        description=TXT_PANEL,
        color=COLOR,
    )
    if gremio and gremio.icon:
        e.set_thumbnail(url=gremio.icon.url)
    e.set_footer(text="Proyecto IDA Sam · Sistema de tickets")
    return e