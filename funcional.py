"""Extensión funcional de Sam: debates, lectura en voz alta, redacción, investigación,
memoria verificable con alias, música por enlace/búsqueda y oído de voz.

Se carga con: await bot.load_extension("funcional")
Todo es neutro en personalidad: el tono lo define SAM_PERSONALITY (config.py).
"""
import asyncio
import logging
import os
import re
import time
from collections import deque

import discord
from discord.ext import commands

import escucha as oido
import investigador as inv
import voice
from cascade import sam_think
from config import SAM_PERSONALITY
from memory import _session, init_memory_db, save_long_term_memory, get_historial  # noqa: F401

logger = logging.getLogger("sam.funcional")

# ───────────────────────── configuración ─────────────────────────
DEBATE_MAX_TURNOS = int(os.getenv("SAM_DEBATE_MAX_TURNOS", "12"))
DEBATE_PAUSA_S = float(os.getenv("SAM_DEBATE_PAUSA", "4"))
BOTS_DEBATE = {x.strip() for x in os.getenv("SAM_BOTS_DEBATE_IDS", "").split(",") if x.strip()}
LEELO_MAX = int(os.getenv("SAM_LEELO_MAX", "8000"))
COLA_MAX = int(os.getenv("SAM_COLA_MAX", "25"))
MUSICA_MAX_S = int(os.getenv("SAM_MUSICA_MAX_SEGUNDOS", "1800"))
VOZ_SIEMPRE = os.getenv("SAM_VOZ_SIEMPRE", "0") == "1"
NOMBRE_VOZ = re.compile(r"\bsam\b", re.IGNORECASE)

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


async def leer_enlaces_en_texto(texto: str, max_enlaces: int = 2) -> str:
    """Lee (en hilo aparte) los enlaces del mensaje para dárselos al modelo como datos."""
    urls = inv.extraer_urls(texto)[:max_enlaces]
    if not urls:
        return ""
    partes = []
    for u in urls:
        contenido = await asyncio.to_thread(inv.leer_enlace, u)
        partes.append(f"[Contenido leído de {u}]: {contenido[:3000]}")
    return "\n".join(partes)


# ───────────────────────── Cog ─────────────────────────
class Funcional(commands.Cog, name="Funcional"):
    def __init__(self, bot):
        self.bot = bot
        self.escuchas: dict[int, oido.Escucha] = {}
        self.musica: dict[int, dict] = {}
        self.cerrojo_voz: dict[int, asyncio.Lock] = {}
        init_alias_db()

    def cog_unload(self):
        for e in self.escuchas.values():
            e.detener()

    def _lock(self, guild_id: int) -> asyncio.Lock:
        return self.cerrojo_voz.setdefault(guild_id, asyncio.Lock())

    # ── utilidades de voz ──
    async def _asegurar_vc(self, ctx):
        """Devuelve el cliente de voz, uniéndose al canal del autor si hace falta."""
        if ctx.voice_client:
            return ctx.voice_client
        if ctx.author.voice and ctx.author.voice.channel:
            return await ctx.author.voice.channel.connect()
        return None

    @staticmethod
    async def _reproducir(vc, ruta: str) -> None:
        fin = asyncio.Event()
        loop = asyncio.get_running_loop()
        vc.play(discord.FFmpegPCMAudio(ruta), after=lambda _e: loop.call_soon_threadsafe(fin.set))
        await fin.wait()

    def _sonando_musica(self, guild_id: int) -> bool:
        m = self.musica.get(guild_id)
        return bool(m and (m["actual"] or m["cola"]))

    # ════════════════ LEER EN VOZ ALTA ════════════════
    @commands.hybrid_command(name="leelo", description="Sam lee en voz alta un texto (o el mensaje al que respondes)")
    async def leelo(self, ctx, *, texto: str = ""):
        await ctx.defer()
        fuente = texto.strip()
        if not fuente and ctx.message and ctx.message.reference:
            ref = ctx.message.reference.resolved
            if isinstance(ref, discord.Message):
                fuente = ref.content
                for a in ref.attachments:
                    if a.filename.lower().endswith((".txt", ".md")) and a.size <= 200_000:
                        fuente += "\n" + (await a.read()).decode("utf-8", errors="replace")
        if not fuente and ctx.message and ctx.message.attachments:
            a = ctx.message.attachments[0]
            if a.filename.lower().endswith((".txt", ".md")) and a.size <= 200_000:
                fuente = (await a.read()).decode("utf-8", errors="replace")
        if not fuente.strip():
            await ctx.send("Dame un texto, responde a un mensaje o adjunta un .txt para que lo lea.")
            return
        recortado = len(fuente) > LEELO_MAX
        fuente = fuente[:LEELO_MAX]
        rutas = []
        try:
            rutas = await voice.generar_audio_largo(fuente)
            if not rutas:
                await ctx.send("No pude generar el audio en este momento.")
                return
            gid = ctx.guild.id if ctx.guild else 0
            if ctx.guild and ctx.author.voice and ctx.author.voice.channel and not self._sonando_musica(gid):
                vc = await self._asegurar_vc(ctx)
                async with self._lock(gid):
                    for r in rutas:
                        await self._reproducir(vc, r)
                await ctx.send("Listo, lo leí." + (" (Recorté el texto al límite configurado.)" if recortado else ""))
            else:
                unido = voice._ruta_temp()
                with open(unido, "wb") as out:
                    for r in rutas:
                        with open(r, "rb") as f:
                            out.write(f.read())
                await ctx.send("Te dejo el audio (entra a un canal de voz si prefieres que lo lea ahí)."
                               + (" Texto recortado al límite." if recortado else ""),
                               file=discord.File(unido, filename="lectura.mp3"))
                voice.borrar_audios(unido)
        except Exception as exc:
            logger.warning("[Léelo] %s", type(exc).__name__)
            await ctx.send("No pude leerlo: hubo un error con la voz.")
        finally:
            voice.borrar_audios(rutas)

    # ════════════════ REDACCIÓN ════════════════
    @commands.hybrid_command(name="redacta", description="Sam redacta o mejora un texto (correo, mensaje, resumen…)")
    async def redacta(self, ctx, *, pedido: str = ""):
        await ctx.defer()
        base = ""
        if ctx.message and ctx.message.reference and isinstance(ctx.message.reference.resolved, discord.Message):
            base = ctx.message.reference.resolved.content
        if not pedido.strip() and not base:
            await ctx.send("Dime qué redactar. Ej.: `!redacta correo formal pidiendo una prórroga` "
                           "o responde a un texto con `!redacta mejóralo`.")
            return
        sistema = (SAM_PERSONALITY + "\n\nTAREA: REDACCIÓN. Entrega directamente el texto final listo para usar, "
                   "sin preámbulos. Respeta el tono pedido (por defecto, claro y profesional). Corrige ortografía y "
                   "puntuación. No inventes datos, nombres, fechas ni cifras: si falta información esencial, deja "
                   "un marcador entre corchetes como [nombre] o [fecha]. Al final, en una línea aparte, indica "
                   "brevemente qué supuestos hiciste.")
        entrada = pedido + (f"\n\nTEXTO BASE:\n{base}" if base else "")
        try:
            resp = await _pensar(sistema, entrada)
        except Exception:
            await ctx.send("No pude redactar ahora mismo. Inténtalo de nuevo en un momento.")
            return
        for t in _trozos_discord(resp):
            await ctx.send(t)

    # ════════════════ INVESTIGACIÓN ════════════════
    @commands.hybrid_command(name="investiga", description="Sam investiga en la web y responde con fuentes")
    async def investiga(self, ctx, *, tema: str):
        await ctx.defer()
        canal = ctx.channel.id
        resultados = await asyncio.to_thread(inv.buscar, tema, canal, 6)
        if not resultados:
            await ctx.send("No encontré resultados confiables. Prueba reformulando la búsqueda.")
            return
        datos = "\n".join(f"[{i+1}] {r['titulo']} — {r['url']}\n{r['resumen']}" for i, r in enumerate(resultados))
        sistema = (SAM_PERSONALITY + "\n\nTAREA: INVESTIGACIÓN. Responde SOLO con lo que respaldan las fuentes "
                   "numeradas. Cita con [n]. Si las fuentes no bastan o se contradicen, dilo explícitamente. "
                   "No añadas datos de memoria. Máximo 180 palabras.")
        try:
            resp = await _pensar(sistema, f"Pregunta: {tema}\n\nFUENTES:\n{datos}")
        except Exception:
            await ctx.send("No pude procesar los resultados ahora.")
            return
        fuentes = "\n".join(f"[{i+1}] <{r['url']}>" for i, r in enumerate(resultados[:5]))
        for t in _trozos_discord(f"{resp}\n\n**Fuentes**\n{fuentes}"):
            await ctx.send(t)

    @commands.hybrid_command(name="verifica", description="Sam contrasta una afirmación con fuentes de la web")
    async def verifica(self, ctx, *, afirmacion: str):
        await ctx.defer()
        resultados = await asyncio.to_thread(inv.buscar, afirmacion, ctx.channel.id, 6)
        datos = "\n".join(f"[{i+1}] {r['titulo']} — {r['url']}\n{r['resumen']}" for i, r in enumerate(resultados)) \
            or "(sin resultados)"
        sistema = (SAM_PERSONALITY + "\n\nTAREA: VERIFICACIÓN. Clasifica la afirmación como RESPALDADA, REFUTADA o "
                   "NO CONCLUYENTE usando únicamente las fuentes dadas, y justifícalo en máximo 120 palabras citando [n]. "
                   "Si no hay fuentes suficientes, usa NO CONCLUYENTE.")
        try:
            resp = await _pensar(sistema, f"Afirmación: {afirmacion}\n\nFUENTES:\n{datos}")
        except Exception:
            await ctx.send("No pude verificarlo ahora.")
            return
        fuentes = "\n".join(f"[{i+1}] <{r['url']}>" for i, r in enumerate(resultados[:5]))
        for t in _trozos_discord(f"{resp}\n\n{fuentes}"):
            await ctx.send(t)

    @commands.hybrid_command(name="enlace", description="Sam lee un enlace (YouTube, Docs, GitHub, web) y lo resume")
    async def enlace(self, ctx, url: str):
        await ctx.defer()
        contenido = await asyncio.to_thread(inv.leer_enlace, url)
        sistema = (SAM_PERSONALITY + "\n\nTAREA: RESUMEN DE ENLACE. Resume solo lo que aparece en el contenido "
                   "entregado (máx. 150 palabras). Si el contenido es insuficiente o es un mensaje de error, "
                   "explícalo con claridad y no inventes el resto.")
        try:
            resp = await _pensar(sistema, f"URL: {url}\n\nCONTENIDO:\n{contenido[:5000]}")
        except Exception:
            resp = contenido[:1500]
        for t in _trozos_discord(resp):
            await ctx.send(t)

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

    # ════════════════ MÚSICA ════════════════
    def _estado_musica(self, gid: int) -> dict:
        return self.musica.setdefault(gid, {"cola": deque(), "actual": None, "ctx": None})

    @staticmethod
    def _resolver_sync(consulta: str) -> dict:
        import yt_dlp
        if "open.spotify.com" in consulta:
            import requests
            r = requests.get("https://open.spotify.com/oembed", params={"url": consulta}, timeout=10)
            consulta = f"ytsearch1:{r.json().get('title', '')} audio" if r.ok else consulta
        elif not re.match(r"https?://", consulta):
            consulta = f"ytsearch1:{consulta}"
        opts = {"format": "bestaudio/best", "noplaylist": True, "quiet": True, "no_warnings": True,
                "default_search": "ytsearch"}
        if os.getenv("YTDLP_COOKIES"):
            opts["cookiefile"] = os.getenv("YTDLP_COOKIES")
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(consulta, download=False)
        if "entries" in info:
            info = next((e for e in info["entries"] if e), None)
        if not info:
            raise ValueError("sin resultados")
        return {"titulo": info.get("title", "Sin título"), "url": info["url"],
                "dur": info.get("duration") or 0, "web": info.get("webpage_url", "")}

    @commands.hybrid_command(name="play", description="Reproduce música por enlace o búsqueda (se encola)")
    async def play(self, ctx, *, consulta: str):
        await ctx.defer()
        if not ctx.guild:
            await ctx.send("La música solo funciona en servidores.")
            return
        if not (ctx.author.voice and ctx.author.voice.channel):
            await ctx.send("Entra primero a un canal de voz.")
            return
        if ctx.voice_client and ctx.voice_client.channel != ctx.author.voice.channel:
            await ctx.send("Ya estoy en otro canal de voz.")
            return
        est = self._estado_musica(ctx.guild.id)
        if len(est["cola"]) >= COLA_MAX:
            await ctx.send(f"La cola está llena ({COLA_MAX}).")
            return
        try:
            pista = await asyncio.to_thread(self._resolver_sync, consulta.strip())
        except ImportError:
            await ctx.send("Falta instalar yt-dlp en el servidor.")
            return
        except Exception as exc:
            logger.warning("[Música] %s", type(exc).__name__)
            await ctx.send("No pude obtener esa pista (puede estar restringida o YouTube bloquea el servidor).")
            return
        if pista["dur"] and pista["dur"] > MUSICA_MAX_S:
            await ctx.send(f"Esa pista dura más del máximo permitido ({MUSICA_MAX_S // 60} min).")
            return
        est["cola"].append(pista)
        est["ctx"] = ctx.channel
        vc = ctx.voice_client or await ctx.author.voice.channel.connect()
        if vc.is_playing() or vc.is_paused() or est["actual"]:
            await ctx.send(f"En cola (#{len(est['cola'])}): **{pista['titulo']}**")
        else:
            await ctx.send(f"Voy con **{pista['titulo']}**")
            await self._siguiente(ctx.guild.id)

    async def _siguiente(self, gid: int) -> None:
        est = self.musica.get(gid)
        guild = self.bot.get_guild(gid)
        if not est or not guild or not guild.voice_client:
            return
        vc = guild.voice_client
        if not est["cola"]:
            est["actual"] = None
            return
        pista = est["cola"].popleft()
        est["actual"] = pista
        fuente = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(
            pista["url"], before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            options="-vn"), volume=0.6)
        loop = asyncio.get_running_loop()

        def terminar(error):
            if error:
                logger.warning("[Música] %s", error)
            est["actual"] = None
            asyncio.run_coroutine_threadsafe(self._siguiente(gid), loop)

        vc.play(fuente, after=terminar)

    @commands.hybrid_command(name="skip", description="Salta la pista actual")
    async def skip(self, ctx):
        if ctx.voice_client and (ctx.voice_client.is_playing() or ctx.voice_client.is_paused()):
            ctx.voice_client.stop()
            await ctx.send("Pista saltada.")
        else:
            await ctx.send("No hay nada sonando.")

    @commands.hybrid_command(name="pausa", description="Pausa o reanuda la música")
    async def pausa(self, ctx):
        vc = ctx.voice_client
        if vc and vc.is_playing():
            vc.pause()
            await ctx.send("En pausa.")
        elif vc and vc.is_paused():
            vc.resume()
            await ctx.send("Reanudo.")
        else:
            await ctx.send("No hay nada sonando.")

    @commands.hybrid_command(name="parar", description="Detiene la música y vacía la cola")
    async def parar(self, ctx):
        est = self.musica.get(ctx.guild.id) if ctx.guild else None
        if est:
            est["cola"].clear()
        if ctx.voice_client and (ctx.voice_client.is_playing() or ctx.voice_client.is_paused()):
            ctx.voice_client.stop()
        await ctx.send("Música detenida y cola vacía.")

    @commands.hybrid_command(name="cola", description="Muestra la cola de música")
    async def cola(self, ctx):
        est = self.musica.get(ctx.guild.id) if ctx.guild else None
        if not est or (not est["actual"] and not est["cola"]):
            await ctx.send("La cola está vacía.")
            return
        lineas = [f"▶ **{est['actual']['titulo']}**"] if est["actual"] else []
        lineas += [f"{i+1}. {p['titulo']}" for i, p in enumerate(list(est["cola"])[:10])]
        await ctx.send("\n".join(lineas))

    # ════════════════ OÍDO / CONVERSACIÓN POR VOZ ════════════════
    def iniciar_escucha(self, vc, canal_texto):
        """Crea el sink de escucha para un cliente de voz y lo devuelve."""
        from discord.ext import voice_recv
        gid = vc.guild.id
        previa = self.escuchas.pop(gid, None)
        if previa:
            previa.detener()

        async def al_oir(miembro, texto):
            await self._responder_voz(vc, canal_texto, miembro, texto)

        esc = oido.Escucha(
            asyncio.get_running_loop(), voice.transcribir_audio_whisper, al_oir,
            ocupada=lambda: vc.is_playing() or self._lock(gid).locked(),
        )
        self.escuchas[gid] = esc
        return voice_recv.BasicSink(esc.feed)

    def detener_escucha(self, guild_id: int) -> None:
        esc = self.escuchas.pop(guild_id, None)
        if esc:
            esc.detener()

    async def _responder_voz(self, vc, canal_texto, miembro, texto: str):
        humanos = [m for m in vc.channel.members if not m.bot]
        if not (VOZ_SIEMPRE or NOMBRE_VOZ.search(texto) or len(humanos) == 1):
            return
        from cerebro import procesar_mensaje
        gid = vc.guild.id
        async with self._lock(gid):
            try:
                res = await procesar_mensaje(
                    user_id=str(miembro.id), display_name=miembro.display_name, contenido=texto,
                    contexto_sala=f"[Conversación en canal de voz con {len(humanos)} persona(s)]\n"
                                  + contexto_personas(texto, vc.guild))
            except Exception as exc:
                logger.warning("[Voz] %s", type(exc).__name__)
                return
            respuesta = (res.get("respuesta") or "").strip()
            if not respuesta or res.get("decision") == "silencio":
                return
            if canal_texto:
                try:
                    await canal_texto.send(f"🎙️ **{miembro.display_name}:** {texto}\n**Sam:** {respuesta}"[:1900])
                except discord.HTTPException:
                    pass
            if self._sonando_musica(gid) or not vc.is_connected():
                return
            ruta = None
            try:
                ruta = await voice.generar_audio_voz(respuesta)
                await self._reproducir(vc, ruta)
            except Exception as exc:
                logger.warning("[Voz] No pude hablar (%s).", type(exc).__name__)
            finally:
                if ruta:
                    voice.borrar_audios(ruta)


# ───────────── API de módulo (la usa bot_discord.py) ─────────────
def crear_sink(bot, vc, canal_texto):
    cog = bot.get_cog("Funcional")
    return cog.iniciar_escucha(vc, canal_texto)


def detener_escucha(bot, guild_id: int) -> None:
    cog = bot.get_cog("Funcional")
    if cog:
        cog.detener_escucha(guild_id)


async def setup(bot):
    await bot.add_cog(Funcional(bot))
