"""Extensión de Sam: debates y memoria verificable (alias, recuerdos propios).

Se carga con: await bot.load_extension("debates_memoria")
"""
import logging
import os
import re
import time
from collections import deque

import discord
from discord.ext import commands

from cascade import sam_think
from config import SAM_PERSONALITY
from memory import _session, init_memory_db, save_long_term_memory, get_historial  # noqa: F401

logger = logging.getLogger("sam.debates_memoria")

# ───────────────────────── configuración ─────────────────────────
DEBATE_MAX_TURNOS = int(os.getenv("SAM_DEBATE_MAX_TURNOS", "12"))
DEBATE_PAUSA_S = float(os.getenv("SAM_DEBATE_PAUSA", "4"))
BOTS_DEBATE = {x.strip() for x in os.getenv("SAM_BOTS_DEBATE_IDS", "").split(",") if x.strip()}

_debates: dict[int, dict] = {}


def debate_activo(canal_id: int) -> bool:
    return canal_id in _debates


def _trozos_discord(texto: str, limite: int = 1900) -> list[str]:
    texto = (texto or "").strip()
    salida = []
    while len(texto) > limite:
        corte = texto.rfind("\n", 0, limite)
        corte = corte if corte > limite // 2 else texto.rfind(" ", 0, limite)
        corte = corte if corte > 0 else limite
        salida.append(texto[:corte].strip())
        texto = texto[corte:].strip()
    if texto:
        salida.append(texto)
    return salida


async def _pensar(sistema: str, usuario: str, historial: list | None = None) -> str:
    msgs = [{"role": "system", "content": sistema}] + (historial or []) + [{"role": "user", "content": usuario}]
    return (await sam_think(msgs)).strip()


# ───────────────────────── memoria: alias ─────────────────────────
def init_alias_db() -> None:
    init_memory_db()
    with _session() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS aliases (
            alias TEXT NOT NULL COLLATE NOCASE,
            user_id TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (alias, user_id))""")


def guardar_alias(user_id: str, alias: str) -> bool:
    alias = re.sub(r"\s+", " ", alias.strip().lstrip("@"))[:40]
    if len(alias) < 2:
        return False
    with _session() as db:
        db.execute("INSERT OR IGNORE INTO aliases(alias,user_id) VALUES(?,?)", (alias, str(user_id)))
    return True


def aliases_de(user_id: str) -> list[str]:
    with _session() as db:
        return [r["alias"] for r in db.execute("SELECT alias FROM aliases WHERE user_id=?", (str(user_id),))]


def borrar_alias(user_id: str, alias: str) -> int:
    with _session() as db:
        return db.execute("DELETE FROM aliases WHERE user_id=? AND alias=?", (str(user_id), alias.strip().lstrip("@"))).rowcount


def contexto_personas(texto: str, guild) -> str:
    """Identifica a quién se refiere el mensaje por nombre, apodo o alias (sin necesitar @)."""
    if not texto:
        return ""
    bajo = texto.lower()
    hallados: dict[str, str] = {}
    try:
        with _session() as db:
            filas = db.execute("SELECT alias,user_id FROM aliases").fetchall()
        for f in filas:
            if re.search(rf"(?<!\w){re.escape(f['alias'].lower())}(?!\w)", bajo):
                hallados[f["user_id"]] = f["alias"]
    except Exception as exc:
        logger.warning("[Alias] %s", type(exc).__name__)
    nombres = {}
    if guild is not None and len(guild.members) <= 5000:
        for m in guild.members:
            if m.bot:
                continue
            for n in {m.display_name, m.name, getattr(m, "global_name", None)}:
                if n and len(n) >= 3 and re.search(rf"(?<!\w){re.escape(n.lower())}(?!\w)", bajo):
                    nombres[str(m.id)] = (m.display_name, n)
    lineas = []
    for uid, alias in list(hallados.items())[:5]:
        miembro = guild.get_member(int(uid)) if guild else None
        nombre = miembro.display_name if miembro else f"usuario {uid}"
        lineas.append(f"- «{alias}» es un alias de {nombre}.")
    for uid, (disp, n) in list(nombres.items())[:5]:
        if uid not in hallados:
            lineas.append(f"- «{n}» se refiere al miembro {disp}.")
    if not lineas:
        return ""
    return ("[PERSONAS NOMBRADAS EN EL MENSAJE — solo para identificar de quién se habla; "
            "no reveles datos privados de nadie]\n" + "\n".join(lineas))


# ───────────────────────── Cog ─────────────────────────
class DebatesMemoria(commands.Cog, name="DebatesMemoria"):
    def __init__(self, bot):
        self.bot = bot
        init_alias_db()

    # ════════════════ DEBATES ════════════════
    @commands.hybrid_command(name="debate", description="Inicia un debate: !debate tema | a favor / en contra / libre")
    async def debate(self, ctx, *, tema: str):
        await ctx.defer()
        if ctx.channel.id in _debates:
            await ctx.send("Ya hay un debate activo en este canal. Ciérralo con `!debate_fin`.")
            return
        postura = "libre"
        if "|" in tema:
            tema, _, p = tema.partition("|")
            tema, p = tema.strip(), p.strip().lower()
            if p in ("a favor", "favor", "pro"):
                postura = "a favor"
            elif p in ("en contra", "contra"):
                postura = "en contra"
        tema = tema.strip()[:300]
        _debates[ctx.channel.id] = {
            "tema": tema, "postura": postura, "turnos": 0, "ultimo": 0.0,
            "hist": deque(maxlen=14), "inicio": time.time(), "dueno": ctx.author.id,
        }
        apertura = await self._turno_debate(ctx.channel.id, None, None)
        for t in _trozos_discord(apertura):
            await ctx.send(t)
        await ctx.send(f"_Debate abierto. Respóndeme mencionándome o respondiendo a mi mensaje. "
                       f"Límite: {DEBATE_MAX_TURNOS} turnos. `!debate_fin` para cerrar con un balance._")

    @commands.hybrid_command(name="debate_fin", description="Cierra el debate y entrega un balance")
    async def debate_fin(self, ctx):
        await ctx.defer()
        d = _debates.get(ctx.channel.id)
        if not d:
            await ctx.send("No hay un debate activo en este canal.")
            return
        if ctx.author.id != d["dueno"] and not ctx.author.guild_permissions.manage_messages:
            await ctx.send("Solo quien abrió el debate o el staff puede cerrarlo.")
            return
        balance = await self._balance(d)
        _debates.pop(ctx.channel.id, None)
        for t in _trozos_discord(balance):
            await ctx.send(t)

    async def _balance(self, d: dict) -> str:
        transcripcion = "\n".join(f"{a}: {t}" for a, t in d["hist"]) or "(sin intervenciones)"
        sistema = (SAM_PERSONALITY + "\n\nTAREA: BALANCE DE DEBATE imparcial. Entrega: 1) mejores argumentos de cada "
                   "postura, 2) puntos de acuerdo, 3) desacuerdos que persisten, 4) afirmaciones que requieren "
                   "verificación. No declares ganador salvo que sea una cuestión factual resuelta. Máx. 200 palabras.")
        try:
            return await _pensar(sistema, f"Tema: {d['tema']}\n\nTRANSCRIPCIÓN:\n{transcripcion}")
        except Exception:
            return "Cerré el debate, pero no pude generar el balance."

    async def _turno_debate(self, canal_id: int, autor: str | None, texto: str | None) -> str:
        d = _debates[canal_id]
        if autor and texto:
            d["hist"].append((autor, texto[:600]))
        postura = {"a favor": "Defiendes la postura A FAVOR del tema.",
                   "en contra": "Defiendes la postura EN CONTRA del tema.",
                   "libre": "Adopta la postura que mejor respalden los argumentos; puedes matizar."}[d["postura"]]
        sistema = (SAM_PERSONALITY + f"\n\nMODO DEBATE. Tema: «{d['tema']}». {postura}\n"
                   "Reglas: (1) argumenta con razones claras, máximo 120 palabras; (2) reconoce los puntos válidos del "
                   "otro antes de rebatir; (3) NUNCA inventes cifras, estudios, citas ni fuentes: si una afirmación "
                   "factual no es verificable, di que habría que comprobarla (existe el comando /verifica); (4) ataca "
                   "ideas, jamás personas; (5) no repitas argumentos ya usados; (6) si el otro te convence, cede con "
                   "honestidad y explica por qué cambiaste de postura.")
        hist = [{"role": "user", "content": f"{a}: {t}"} if a != "Sam" else {"role": "assistant", "content": t}
                for a, t in d["hist"]]
        instruccion = (f"{autor} dice: {texto}\nResponde a su argumento." if autor
                       else "Presenta tu apertura: postura y 2 o 3 argumentos principales.")
        # El último turno del usuario ya está en hist; se evita duplicarlo.
        if autor and hist and hist[-1]["role"] == "user":
            hist = hist[:-1]
        resp = await _pensar(sistema, instruccion, hist)
        d["hist"].append(("Sam", resp[:600]))
        d["turnos"] += 1
        d["ultimo"] = time.time()
        return resp

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        d = _debates.get(message.channel.id)
        if not d or message.author.id == self.bot.user.id:
            return
        if message.content.startswith(("!", "/")):
            return
        if message.author.bot and str(message.author.id) not in BOTS_DEBATE:
            return
        dirigido = self.bot.user in message.mentions
        if not dirigido and message.reference and isinstance(message.reference.resolved, discord.Message):
            dirigido = message.reference.resolved.author.id == self.bot.user.id
        if not dirigido:
            return
        if time.time() - d["ultimo"] < DEBATE_PAUSA_S:      # evita bucles rápidos con otros bots
            return
        limpio = re.sub(r"<@!?\d+>", "", message.content).strip()
        if not limpio:
            return
        if d["turnos"] >= DEBATE_MAX_TURNOS:
            balance = await self._balance(d)
            _debates.pop(message.channel.id, None)
            await message.channel.send("Llegamos al límite de turnos. Cierro con un balance:")
            for t in _trozos_discord(balance):
                await message.channel.send(t)
            return
        async with message.channel.typing():
            try:
                resp = await self._turno_debate(message.channel.id, message.author.display_name, limpio)
            except Exception:
                await message.channel.send("Perdí el hilo un momento; repite tu argumento, por favor.")
                return
        for t in _trozos_discord(resp):
            await message.reply(t, mention_author=False)

    # ════════════════ MEMORIA VERIFICABLE ════════════════
    @commands.hybrid_command(name="recuerda", description="Pide a Sam que guarde un dato sobre ti")
    async def recuerda(self, ctx, *, dato: str):
        ok = save_long_term_memory(str(ctx.author.id), dato, "persona", 4)
        await ctx.send("Guardado." if ok else
                       "No pude guardarlo: es muy largo o contiene datos sensibles (claves, números largos…).")

    @commands.hybrid_command(name="mis_recuerdos", description="Muestra lo que Sam tiene guardado sobre ti")
    async def mis_recuerdos(self, ctx):
        with _session() as db:
            filas = db.execute("SELECT id,memory_type,content FROM long_term_memories "
                               "WHERE user_id=? AND memory_type!='experiencia_visual' "
                               "ORDER BY importance DESC, updated_at DESC LIMIT 25", (str(ctx.author.id),)).fetchall()
        if not filas:
            await ctx.send("No tengo nada guardado sobre ti.")
            return
        texto = "**Lo que tengo guardado sobre ti**\n" + "\n".join(
            f"`{f['id']}` [{f['memory_type']}] {f['content'][:150]}" for f in filas)
        texto += "\nBorra uno con `!olvida <id>` o todo con `!olvida_todo`."
        try:
            await ctx.author.send(texto[:1900])          # por privado: es información personal
            await ctx.send("Te lo envié por mensaje privado.")
        except discord.Forbidden:
            await ctx.send("No pude escribirte por privado; activa los DM del servidor.")

    @commands.hybrid_command(name="olvida", description="Borra un recuerdo tuyo por id")
    async def olvida(self, ctx, id_recuerdo: int):
        with _session() as db:
            n = db.execute("DELETE FROM long_term_memories WHERE id=? AND user_id=?",
                           (id_recuerdo, str(ctx.author.id))).rowcount
        await ctx.send("Borrado." if n else "No encontré ese recuerdo entre los tuyos.")

    @commands.hybrid_command(name="olvida_todo", description="Borra todo lo que Sam guarda sobre ti")
    async def olvida_todo(self, ctx):
        with _session() as db:
            n = db.execute("DELETE FROM long_term_memories WHERE user_id=?", (str(ctx.author.id),)).rowcount
            db.execute("DELETE FROM aliases WHERE user_id=?", (str(ctx.author.id),))
        await ctx.send(f"Listo: borré {n} recuerdo(s) y tus alias.")

    @commands.hybrid_command(name="alias", description="Registra un apodo o usuario de redes para reconocer a alguien sin @")
    async def alias(self, ctx, usuario: discord.Member, *, alias: str):
        if ctx.author.id != usuario.id and not ctx.author.guild_permissions.manage_guild:
            await ctx.send("Solo puedes registrar alias propios (o ser staff para registrar los de otros).")
            return
        ok = guardar_alias(str(usuario.id), alias)
        await ctx.send(f"Listo: ahora reconoceré «{alias.strip()}» como {usuario.display_name}." if ok
                       else "El alias debe tener al menos 2 caracteres.")

async def setup(bot):
    await bot.add_cog(DebatesMemoria(bot))
