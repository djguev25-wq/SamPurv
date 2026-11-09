import discord
from discord import app_commands
import logging
import re
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
from discord.ext import commands

# ← NUEVO: extensión de escucha (el oído de Sam)
try:
    from discord.ext import voice_recv
    VOICE_RECV_OK = True
except Exception as _e:
    voice_recv = None
    VOICE_RECV_OK = False

from config import *
from cerebro import procesar_mensaje
from memory import *
from sueno import (
    sleep_loop_task, is_sleeping, respuesta_sueno,
    dormir_forzado, despertar_forzado, devolver_ciclo, estado_sueno,
    info_reloj, _nivel_fatiga,
)
from voice import generar_audio_voz
from vision import analyze_image
import tools
import bienvenida as bien
import reglas as reg
import sobre_sam as ss
import desarrollo as dev
import tickets as tk
import debates_memoria as dm

logger = logging.getLogger("sam.bot")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True

bot = commands.Bot(command_prefix=["/", "!"], intents=intents)

AVISO_COOLDOWN = float(os.getenv("SAM_REPOSO_AVISO", "600"))
_aviso_reposo = {"ts": 0.0}
_desperto_pendiente = {"activo": False}


# ← NUEVO: el OÍDO. Callback del BasicSink. Tolerante a la firma real.
def _oirla(*args, **kwargs):
    try:
        a = args[0] if args else None
        if isinstance(a, (bytes, bytearray)):
            print(f"[OIDO] 🎙️ audio crudo → {len(a)} bytes")
        else:
            src = getattr(a, "source", None)
            au = getattr(a, "audio", None)
            quien = getattr(src, "display_name", "?")
            n = len(au) if au is not None else "?"
            print(f"[OIDO] 🎙️ {quien} habló → {n} bytes")
    except Exception as exc:
        print(f"[OIDO] llegó algo ({type(exc).__name__}) args={[type(x).__name__ for x in args]}")


def limpiar_respuesta(texto: str) -> str:
    if not texto:
        return ""
    texto = re.sub(r'\*[^*]+\*', '', texto)
    texto = re.sub(r'\[[^\]]+\]', '', texto)
    ia_phrases = [
        r'como (?:un )?(?:modelo|IA|inteligencia artificial)',
        r'lo siento, soy una IA',
        r'en qué puedo ayudarte',
        r'estoy aquí para asistirte',
        r'mi propósito es ayudar',
        r'como asistente virtual',
        r'será un placer',
        r'¿tienes alguna otra pregunta?'
    ]
    for phrase in ia_phrases:
        texto = re.sub(phrase, '', texto, flags=re.IGNORECASE)

    lineas = [l.strip() for l in texto.split('\n') if l.strip()]
    resultado = '\n'.join(lineas).strip()

    if resultado and len(resultado) > 5 and ord(resultado[0]) < 128:
        resultado = "✨ " + resultado

    return resultado


def construir_tool_info(contenido_low: str) -> str:
    tool_info = ""
    if any(w in contenido_low for w in ["hora", "tiempo", "qué hora"]):
        tz = None
        if "en " in contenido_low:
            parts = contenido_low.split("en ", 1)
            if len(parts) > 1:
                tz = parts[1].split()[0].strip(",.?!")
        tool_info += f" [Hora: {tools.get_world_time(tz)}]"
    if any(w in contenido_low for w in ["fecha", "día", "calendario", "qué día"]):
        tool_info += f" [Fecha: {tools.get_current_date()}]"
    if any(w in contenido_low for w in ["busca", "investiga", "búsqueda", "deep search"]):
        tool_info += f" [Web: {tools.deep_search(contenido_low)}]"
    return tool_info


async def obtener_contexto_sala(canal, excluir_msg_id=None, limite=15):
    mensajes = []
    async for m in canal.history(limit=limite):
        if m.author.bot:
            continue
        if excluir_msg_id and m.id == excluir_msg_id:
            continue
        texto = (m.content or "")[:200].replace("\n", " ")
        if m.attachments:
            texto += " [imagen]"
        if texto.strip():
            mensajes.append(f"{m.author.display_name}: {texto}")
    mensajes.reverse()
    return "\n".join(mensajes)


@bot.event
async def on_ready():
    init_memory_db()
    print(f"[Sam] Conectada como {bot.user}")
    print(f"[Sam] En {len(bot.guilds)} servidor(es)")

    # ← NUEVO: Opus es obligatorio para oír. Intento cargarlo sin crashear.
    try:
        discord.opus._load_default()
        print("[Sam] Opus cargado (necesario para el oído).")
    except Exception as exc:
        print(f"[Sam] ⚠️ Opus no cargado: {type(exc).__name__} — el oído puede fallar.")

    # ← NUEVO: estado de la extensión de escucha
    if VOICE_RECV_OK:
        print("[Sam] Extensión de escucha (voice_recv) lista.")
    else:
        print("[Sam] ⚠️ voice_recv NO cargó. Sam podrá hablar pero no oír.")

    bot.loop.create_task(sleep_loop_task())
    print("[Sam] Ciclo circadiano activado.")

    bot.add_view(tk.VistaPanel())
    bot.add_view(tk.VistaTicket())
    print("[Sam] Vistas de tickets registradas.")

    if "debates_memoria" not in bot.extensions:
        try:
            await bot.load_extension("debates_memoria")
            print("[Sam] Extensión de debates y memoria cargada.")
        except Exception as exc:
            print(f"[Sam] ⚠️ No cargó debates_memoria: {type(exc).__name__}: {exc}")

    try:
        for guild in bot.guilds:
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
        print("[Sam] Comandos slash sincronizados.")
    except Exception as exc:
        logger.warning("[Slash] Error sincronizando (%s).", type(exc).__name__)


# ─────────────── BIENVENIDAS / DESPEDIDAS ───────────────
@bot.event
async def on_member_join(member):
    if not bien.ON_BIENVENIDA:
        return
    print(f"[Bienvenida] 🎯 Evento: {member.display_name} entró a {member.guild.name}")
    canal = bien.canal_bienvenida(member.guild)
    if canal is None and bien.ID_BIENVENIDA:
        try:
            canal = await member.guild.fetch_channel(bien.ID_BIENVENIDA)
        except Exception as exc:
            logger.warning("[Bienvenida] fetch falló: %s", type(exc).__name__)
    if canal is None:
        logger.warning("[Bienvenida] No resuelvo el canal en %s", member.guild.name)
        return
    try:
        await canal.send(embed=bien.embed_bienvenida(member))
        print("[Bienvenida] ✅ Embed enviado")
    except Exception as exc:
        logger.warning("[Bienvenida] Falló el envío: %s", type(exc).__name__)


@bot.event
async def on_member_remove(member):
    if not bien.ON_DESPEDIDA:
        return
    print(f"[Despedida] 🎯 Evento: {member.display_name} salió de {member.guild.name}")
    canal = bien.canal_despedida(member.guild)
    if canal is None and bien.ID_DESPEDIDA:
        try:
            canal = await member.guild.fetch_channel(bien.ID_DESPEDIDA)
        except Exception:
            canal = None
    if canal is None:
        return
    try:
        await canal.send(embed=bien.embed_despedida(member))
        print("[Despedida] ✅ Embed enviado")
    except Exception as exc:
        logger.warning("[Despedida] Falló: %s", type(exc).__name__)


@bot.event
async def on_message(message):
    if message.author == bot.user or message.author.bot:
        return

    user_id = str(message.author.id)
    contenido = (message.content or "").strip()
    es_dm = isinstance(message.channel, discord.DMChannel)
    mencionada = bot.user is not None and bot.user in message.mentions

    if contenido and contenido.startswith(("!", "/")):
        await bot.process_commands(message)
        return

    if not es_dm and dm.debate_activo(message.channel.id):
        return  # el debate activo lo atiende debates_memoria

    if not es_dm and not mencionada:
        return

    # REPOSO: SOLO por reloj o !dormir. El cansancio NO la calla (eso lo maneja cerebro.py).
    if is_sleeping():
        _desperto_pendiente["activo"] = True
        if time.time() - _aviso_reposo["ts"] > AVISO_COOLDOWN:
            _aviso_reposo["ts"] = time.time()
            async with message.channel.typing():
                somnolienta = await respuesta_sueno(message.author.display_name)
            await message.channel.send(somnolienta)
        return
    if _desperto_pendiente["activo"]:
        _desperto_pendiente["activo"] = False
        await message.channel.send("✨ Ya desperté, mami. ¿Me contabas algo? 🤍")

    contexto_sala = ""
    if not es_dm:
        contexto_sala = await obtener_contexto_sala(
            message.channel, excluir_msg_id=message.id
        )
        personas = dm.contexto_personas(contenido, message.guild)
        if personas:
            contexto_sala = f"{contexto_sala}\n{personas}".strip()

    image_attachment = next((a for a in message.attachments if a.filename.lower().endswith(
        (".png", ".jpg", ".jpeg", ".webp", ".gif")
    )), None)

    if image_attachment:
        suffix = Path(image_attachment.filename).suffix.lower() or ".img"
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(prefix="sam_image_", suffix=suffix, delete=False) as tmp:
                temp_path = tmp.name
            await image_attachment.save(temp_path)
            descripcion = await analyze_image(temp_path)
            async with message.channel.typing():
                resultado = await procesar_mensaje(
                    user_id=user_id,
                    display_name=message.author.display_name,
                    contenido=contenido,
                    imagen_desc=descripcion,
                    contexto_sala=contexto_sala,
                )

            if resultado.get("decision") == "silencio":
                logger.info(f"[Bot] Sam guardó silencio hacia {message.author.name}.")
                return

            limpia = limpiar_respuesta(resultado["respuesta"])
            if limpia:
                await message.channel.send(limpia[:1900])
        except Exception as exc:
            logger.warning("[Visión] Error (%s).", type(exc).__name__)
            await message.channel.send("No pude analizar esa imagen. ¿Otra vez? ✨")
        finally:
            if temp_path:
                try:
                    Path(temp_path).unlink(missing_ok=True)
                except OSError:
                    pass
        return

    if not contenido:
        return

    tool_info = construir_tool_info(contenido.lower())
    contenido_final = contenido + (f"\n(Datos: {tool_info})" if tool_info else "")

    async with message.channel.typing():
        resultado = await procesar_mensaje(
            user_id=user_id,
            display_name=message.author.display_name,
            contenido=contenido_final,
            contexto_sala=contexto_sala,
        )

    if resultado.get("decision") == "silencio":
        logger.info(f"[Bot] Sam guardó silencio hacia {message.author.name}.")
        return

    limpia = limpiar_respuesta(resultado["respuesta"])
    if limpia:
        await message.channel.send(limpia[:1900])

    await bot.process_commands(message)


@bot.command(name="sam")
async def cmd_sam(ctx, *, mensaje: str):
    if not mensaje.strip():
        await ctx.send("Dime qué quieres conversar conmigo 😊")
        return
    contexto_sala = ""
    if not isinstance(ctx.channel, discord.DMChannel):
        contexto_sala = await obtener_contexto_sala(ctx.channel, excluir_msg_id=ctx.message.id)
    async with ctx.typing():
        resultado = await procesar_mensaje(
            user_id=str(ctx.author.id),
            display_name=ctx.author.display_name,
            contenido=mensaje.strip(),
            contexto_sala=contexto_sala,
        )

    if resultado.get("decision") == "silencio":
        await ctx.send("...")
        return

    limpia = limpiar_respuesta(resultado["respuesta"])
    if limpia:
        await ctx.send(limpia[:1900])


# ─────────────── MEMORIA (owner) ───────────────
@bot.command(name="guardar_memoria")
@commands.is_owner()
async def cmd_guardar_memoria(ctx, user_id: str, tipo: str, *, contenido: str):
    success = save_long_term_memory(user_id, contenido, tipo, importance=5)
    await ctx.send(
        f"✅ Guardado para `{user_id}` [{tipo}]." if success
        else f"❌ No se pudo guardar para `{user_id}`."
    )


@bot.command(name="ver_memoria")
@commands.is_owner()
async def cmd_ver_memoria(ctx, user_id: str):
    try:
        db = sqlite3.connect(DB_PATH, timeout=10)
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT memory_type, content, importance FROM long_term_memories "
            "WHERE user_id=? ORDER BY importance DESC, updated_at DESC LIMIT 60",
            (user_id,)
        ).fetchall()
        db.close()
    except Exception as e:
        await ctx.send(f"⚠️ Error: {e}")
        return
    if not rows:
        await ctx.send(f"No hay recuerdos para `{user_id}`.")
        return
    lineas = [f"- [{r['importance']}·{r['memory_type']}] {r['content']}" for r in rows]
    bloques, actual = [], ""
    for l in lineas:
        if len(actual) + len(l) > 1850:
            bloques.append(actual)
            actual = ""
        actual += l + "\n"
    if actual:
        bloques.append(actual)
    for i, b in enumerate(bloques[:5]):
        if i == 0:
            cab = f"🧠 `{user_id}` ({len(rows)}) — parte {i+1}/{min(len(bloques),5)}:\n"
        else:
            cab = f"(parte {i+1}):\n"
        await ctx.send(cab + b)


@bot.command(name="ver_humanity")
@commands.is_owner()
async def cmd_ver_humanity(ctx):
    try:
        db = sqlite3.connect(DB_PATH, timeout=10)
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT memory_type, content, importance, updated_at "
            "FROM long_term_memories WHERE user_id='HUMANITY' "
            "ORDER BY importance DESC, updated_at DESC LIMIT 50"
        ).fetchall()
        db.close()
    except Exception as e:
        await ctx.send(f"⚠️ Error: {e}")
        return
    if not rows:
        await ctx.send("🌍 HUMANITY tiene 0 filas.")
        return
    lineas = [f"- [{r['importance']}] {r['content']}" for r in rows]
    bloques, actual = [], ""
    for l in lineas:
        if len(actual) + len(l) > 1850:
            bloques.append(actual)
            actual = ""
        actual += l + "\n"
    if actual:
        bloques.append(actual)
    for i, b in enumerate(bloques[:5]):
        if i == 0:
            cab = f"🌍 SABIDURÍA GLOBAL ({len(rows)}) — parte {i+1}/{min(len(bloques),5)}:\n"
        else:
            cab = f"(parte {i+1}):\n"
        await ctx.send(cab + b)


@bot.command(name="ver_social")
@commands.is_owner()
async def cmd_ver_social(ctx):
    try:
        db = sqlite3.connect(DB_PATH, timeout=10)
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT content, importance FROM long_term_memories "
            "WHERE user_id='SOCIAL' ORDER BY importance DESC, updated_at DESC LIMIT 30"
        ).fetchall()
        db.close()
    except Exception as e:
        await ctx.send(f"⚠️ Error: {e}")
        return
    if not rows:
        await ctx.send("👥 La agenda SOCIAL está vacía todavía.")
        return
    texto = "\n".join(f"- {r['content']}" for r in rows)
    await ctx.send(f"👥 PERSONAS QUE SAM CONOCE ({len(rows)}):\n{texto}"[:1900])


@bot.command(name="db_stats")
@commands.is_owner()
async def cmd_db_stats(ctx):
    try:
        db = sqlite3.connect(DB_PATH, timeout=10)
        rows = db.execute(
            "SELECT user_id, COUNT(*) as n FROM long_term_memories "
            "GROUP BY user_id ORDER BY n DESC LIMIT 15"
        ).fetchall()
        db.close()
    except Exception as e:
        await ctx.send(f"⚠️ Error: {e}")
        return
    if not rows:
        await ctx.send("📊 BD vacía.")
        return
    texto = "\n".join(f"- `{r[0]}`: {r[1]}" for r in rows)
    await ctx.send(f"📊 DISTRIBUCIÓN:\n{texto}")


# ─────────────── INFRAESTRUCTURA (owner) ───────────────
@bot.command(name="cascada_status")
@commands.is_owner()
async def cmd_cascada_status(ctx):
    from cascade import cascada_status
    await ctx.send("🌊 ESTADO DE LA CASCADA:\n" + cascada_status())


@bot.command(name="cascada_test")
@commands.is_owner()
async def cmd_cascada_test(ctx):
    import asyncio
    from cascade import GEMINI_CASCADE, _call_groq, _g
    from google.genai import types

    await ctx.send("🧪 Probando los eslabones, un momento...")
    resultados = []

    for model in GEMINI_CASCADE:
        try:
            cfg = types.GenerateContentConfig(max_output_tokens=20)
            resp = await asyncio.wait_for(
                _g().aio.models.generate_content(
                    model=model,
                    contents=[types.Content(
                        role="user",
                        parts=[types.Part.from_text(text="Di solo OK")]
                    )],
                    config=cfg,
                ),
                timeout=40,
            )
            txt = (resp.text or "").strip()[:15]
            resultados.append(f"🟢 {model} → '{txt}'")
        except Exception as e:
            msg = str(e).lower()
            if "not found" in msg or "404" in msg:
                corto = "404 (nombre mal)"
            elif "429" in msg or "exhausted" in msg:
                corto = "429 (sin cupo)"
            elif "permission" in msg or "403" in msg:
                corto = "403 (sin acceso)"
            else:
                corto = type(e).__name__
            resultados.append(f"🔴 {model} → {corto}")

    try:
        g = await _call_groq(None, [{"role": "user", "content": "Di solo OK"}], 40)
        resultados.append(f"🟢 GROQ → '{(g or '').strip()[:15]}'")
    except Exception as e:
        resultados.append(f"🔴 GROQ → {type(e).__name__}")

    texto = "\n".join(resultados)
    for i in range(0, len(texto), 1900):
        await ctx.send(texto[i:i+1900])


@bot.command(name="listar_modelos")
@commands.is_owner()
async def cmd_listar_modelos(ctx, filtro: str = ""):
    from cascade import _g
    try:
        client = _g()
        modelos = []
        for m in client.models.list():
            name = getattr(m, "name", "") or ""
            disp = getattr(m, "display_name", "") or ""
            slug = name.replace("models/", "")
            if filtro.lower() in (slug + " " + disp).lower():
                modelos.append((slug, disp))
        modelos.sort()
        if not modelos:
            await ctx.send(f"Sin coincidencias para '{filtro}'.")
            return
        texto = "\n".join(f"`{s}` — {d}" for s, d in modelos)
        for i in range(0, len(texto), 1900):
            await ctx.send(texto[i:i+1900])
    except Exception as e:
        await ctx.send(f"⚠️ {type(e).__name__}: {e}")


@bot.command(name="listar_groq")
@commands.is_owner()
async def cmd_listar_groq(ctx):
    from cascade import _gr
    try:
        ms = await _gr().models.list()
        ids = sorted([m.id for m in ms.data])
        if not ids:
            await ctx.send("Groq respondió vacío (¿key inválida?).")
            return
        texto = "\n".join(f"`{i}`" for i in ids)
        for i in range(0, len(texto), 1900):
            await ctx.send(texto[i:i+1900])
    except Exception as e:
        await ctx.send(f"⚠️ {type(e).__name__}: {e}")


@bot.command(name="ver_red")
@commands.is_owner()
async def cmd_ver_red(ctx):
    lineas = []
    for g in bot.guilds:
        total = g.member_count or "?"
        miembros = list(g.members)
        cacheado = "SÍ" if len(miembros) > 0 else "NO"
        online = sum(1 for m in miembros if m.status == discord.Status.online and not m.bot)
        idle = sum(1 for m in miembros if m.status == discord.Status.idle and not m.bot)
        dnd = sum(1 for m in miembros if m.status == discord.Status.dnd and not m.bot)
        offline = sum(1 for m in miembros if m.status == discord.Status.offline and not m.bot)
        bots = sum(1 for m in miembros if m.bot)
        lineas.append(
            f"🌐 {g.name} (id {g.id})\n"
            f"   miembros: {total} | cacheados: {cacheado} ({len(miembros)})\n"
            f"   🟢 {online} · 🌙 {idle} · ⛔ {dnd} · ⚫ {offline} · 🤖 {bots}"
        )
    texto = "\n".join(lineas) or "Sam no está en ningún servidor."
    await ctx.send(f"📡 RED DE SAM:\n{texto}"[:1900])


@bot.command(name="pulso")
@commands.is_owner()
async def cmd_pulso(ctx):
    from cascade import pulso_actual
    await ctx.send("💓 PULSO DE SAM:\n" + pulso_actual())


# ─────────────── CUERPO Y SUEÑO (owner) ───────────────
@bot.command(name="estado")
@commands.is_owner()
async def cmd_estado(ctx):
    from emociones import init_emotion_db, _connect, get_emotional_context_for_prompt
    init_emotion_db()
    db = _connect()
    try:
        r = db.execute("SELECT * FROM emotional_state WHERE id=1").fetchone()
    finally:
        db.close()
    if not r:
        await ctx.send("Sin estado emocional.")
        return
    mins = (time.time() - (r["last_interaction_ts"] or time.time())) / 60
    crudo = (
        "🌡️ ESTADO CRUDO (lo que Sam NO ve):\n"
        f"   Ánimo: {r['mood_valence']:+.2f} · Energía: {r['energy_level']:.2f} · "
        f"Sociedad: {r['social_need']:.2f}\n"
        f"   Última interacción: hace {mins:.0f} min\n"
        f"   Sueño: {estado_sueno()}\n"
        f"   Fatiga por hora: {_nivel_fatiga()}\n"
        "   (El cansancio NO la calla: solo lo siente y lo dice)\n\n"
        "📝 Lo que Sam SÍ lee (traducido):\n"
    )
    try:
        traducido = get_emotional_context_for_prompt().strip()
    except Exception as e:
        traducido = f"⚠️ fallo al traducir: {type(e).__name__}"
    await ctx.send((crudo + traducido)[:1900])


@bot.command(name="forzar_estado")
@commands.is_owner()
async def cmd_forzar_estado(ctx, preset: str):
    from emociones import init_emotion_db, _connect
    presets = {
        "euforia": (0.9, 0.95, 0.2),
        "tristeza": (-0.8, 0.2, 0.6),
        "agotada": (0.0, 0.10, 0.4),
        "reset": (0.0, 0.8, 0.5),
    }
    if preset not in presets:
        await ctx.send(f"Usa uno de: {', '.join(presets)}")
        return
    m, e, s = presets[preset]
    init_emotion_db()
    db = _connect()
    try:
        db.execute(
            "UPDATE emotional_state SET mood_valence=?, energy_level=?, social_need=?, "
            "last_interaction_ts=? WHERE id=1",
            (m, e, s, time.time())
        )
        db.commit()
    finally:
        db.close()
    if preset == "reset":
        devolver_ciclo()
    nota = " (se sentirá cansada, pero TE SIGUE HABLANDO)" if e < 0.25 else ""
    extra = " · Sueño devuelto a su reloj natural." if preset == "reset" else ""
    await ctx.send(f"🎭 Estado forzado a `{preset}`.{nota}{extra}")


@bot.command(name="dormir")
@commands.is_owner()
async def cmd_dormir(ctx):
    """Acuesta a Sam a la fuerza, aunque sea de día. No persiste al reiniciar."""
    dormir_forzado()
    await ctx.send("🌙 Sam se acuesta. Responderá somnolienta aunque sea mediodía. "
                   "Con !despertar la levanto.")


@bot.command(name="despertar")
@commands.is_owner()
async def cmd_despertar(ctx):
    """Levanta a Sam a la fuerza, aunque sea de noche. No persiste al reiniciar."""
    despertar_forzado()
    await ctx.send("🌞 Sam despierta. Responde normal aunque sea de madrugada. "
                   "Al reiniciar vuelve a su reloj natural.")


@bot.command(name="reloj_sueno")
@commands.is_owner()
async def cmd_reloj_sueno(ctx):
    await ctx.send("🕐 " + info_reloj())


# ─────────────── MÓDULOS GENÉRICOS (owner) ───────────────
@bot.command(name="prueba_bienvenida")
@commands.is_owner()
async def cmd_prueba_bienvenida(ctx):
    """Simula la bienvenida con tu propio perfil."""
    canal = bien.canal_bienvenida(ctx.guild)
    if canal is None:
        await ctx.send("❌ No encuentro #bienvenida. Revisa el ID en bienvenida.py.")
        return
    try:
        await canal.send(embed=bien.embed_bienvenida(ctx.author))
        await ctx.send(f"✅ Simulación enviada a {canal.mention}.")
    except Exception as exc:
        await ctx.send(f"⚠️ Falló: {type(exc).__name__}")


@bot.command(name="reglas")
@commands.is_owner()
async def cmd_reglas(ctx):
    for e in reg.embeds_reglas(ctx.guild):
        await ctx.send(embed=e)


@bot.command(name="publicar_reglas")
@commands.is_owner()
async def cmd_publicar_reglas(ctx):
    if not reg.ON_REGLAS:
        await ctx.send("Reglas apagadas (SAM_REGLAS_ON=0).")
        return
    canal = reg.canal_reglas(ctx.guild)
    if canal is None:
        await ctx.send("❌ No encuentro #reglas.")
        return
    for e in reg.embeds_reglas(ctx.guild):
        await canal.send(embed=e)
    await ctx.send(f"📜 Publicado en {canal.mention}.")


@bot.command(name="sobre")
@commands.is_owner()
async def cmd_sobre(ctx):
    for e in ss.embeds_sobre(ctx.guild):
        await ctx.send(embed=e)


@bot.command(name="publicar_sobre")
@commands.is_owner()
async def cmd_publicar_sobre(ctx):
    if not ss.ON_SOBRE:
        await ctx.send("Ficha apagada (SAM_SOBRE_ON=0).")
        return
    canal = ss.canal_sobre_sam(ctx.guild)
    if canal is None:
        await ctx.send("❌ No encuentro #sobre-sam.")
        return
    for e in ss.embeds_sobre(ctx.guild):
        await canal.send(embed=e)
    await ctx.send(f"🤖 Ficha publicada en {canal.mention}.")


@bot.command(name="desarrollo")
@commands.is_owner()
async def cmd_desarrollo(ctx):
    for e in dev.embeds_desarrollo(ctx.guild):
        await ctx.send(embed=e)


@bot.command(name="publicar_desarrollo")
@commands.is_owner()
async def cmd_publicar_desarrollo(ctx):
    if not dev.ON_DEV:
        await ctx.send("Desarrollo apagado (SAM_DEV_ON=0).")
        return
    canal = dev.canal_desarrollo(ctx.guild)
    if canal is None:
        await ctx.send("❌ No encuentro #desarrollo-de-sam. Revisa el ID en desarrollo.py.")
        return
    for e in dev.embeds_desarrollo(ctx.guild):
        await canal.send(embed=e)
    await ctx.send(f"🧠 Publicado en {canal.mention}.")


@bot.command(name="publicar_tecnico")
@commands.is_owner()
async def cmd_publicar_tecnico(ctx):
    """⚠️ NIVEL PRIVADO. Solo dentro de un canal privado de staff."""
    canal = ctx.channel
    for e in dev.embeds_desarrollo_tecnico(ctx.guild):
        await canal.send(embed=e)
    await ctx.send(f"🔧 Detalle técnico publicado en {canal.mention}.")


# ─────────────── TICKETS (owner) ───────────────
@bot.command(name="publicar_tickets")
@commands.is_owner()
async def cmd_publicar_tickets(ctx):
    """Publica el panel de tickets EN ESTE canal."""
    await ctx.send(embed=tk.embed_panel(ctx.guild), view=tk.VistaPanel())
    await ctx.send("🎫 Panel publicado. El botón funciona aunque reinicies.")


@bot.command(name="tickets_estado")
@commands.is_owner()
async def cmd_tickets_estado(ctx):
    """Diagnóstico: categoría, logs, roles, jerarquía y permisos reales."""
    g = ctx.guild
    try:
        yo = await g.fetch_member(g.me.id)
    except Exception:
        yo = g.me

    cat, origen = await tk.categoria_resuelta(g)
    logs = await tk._resolver(g, tk.ID_CANAL_LOGS, "ticket-logs")

    roles_ok, roles_malos = [], []
    for rid in tk.ROLES_STAFF:
        if not rid:
            roles_malos.append("0 (sin configurar)")
            continue
        r = g.get_role(rid)
        (roles_ok if r else roles_malos).append(r.name if r else str(rid))

    glob = yo.guild_permissions
    aqui = ctx.channel.permissions_for(yo)

    con_roles = [r.name for r in yo.roles if r.permissions.manage_roles]
    pos_yo = yo.top_role.position
    pos_staff = max(
        [r.position for r in (g.get_role(x) for x in tk.ROLES_STAFF) if r] or [0]
    )

    abiertos = [c.name for c in g.text_channels
                if cat and c.category_id == cat.id]

    await ctx.send(
        "🎫 ESTADO DE TICKETS\n"
        f"   Categoría: {'✅ ' + cat.name if cat else '❌ no resuelta'}"
        f" · vía `{origen}`\n"
        f"   Canal logs: {'✅ ' + logs.name if logs else '❌ no resuelto'}\n"
        f"   Roles OK: {roles_ok or '❌ ninguno'}\n"
        f"   Roles fallidos: {roles_malos or '—'}\n"
        f"   ── permisos del SERVER ──\n"
        f"   Crear canales: {'✅' if glob.manage_channels else '❌'}\n"
        f"   Gestionar roles: {'✅' if glob.manage_roles else '❌'}\n"
        f"   Roles de Sam con 'Gestionar roles': {con_roles or 'NINGUNO'}\n"
        f"   ── efectivos en ESTE canal ──\n"
        f"   Ver canal: {'✅' if aqui.view_channel else '❌'}\n"
        f"   Adjuntar archivos: {'✅' if aqui.attach_files else '❌'}\n"
        f"   ── jerarquía ──\n"
        f"   Rol de Sam: {yo.top_role.name} (pos {pos_yo})\n"
        f"   Rol staff más alto: pos {pos_staff}\n"
        f"   {'✅ Sam puede dar permisos a Staff' if pos_yo > pos_staff else '❌ SUBE el rol de Sam'}\n"
        f"   Abiertos: {len(abiertos)} {abiertos[:6]}\n"
        f"   Contador: #{tk._leer_contador():04d}"
    )


@bot.command(name="quien_ve_logs")
@commands.is_owner()
async def cmd_quien_ve_logs(ctx):
    """Diagnóstico del Missing Access: qué rol del bot tiene qué permisos
    y qué ve realmente en #ticket-logs."""
    g = ctx.guild
    try:
        yo = await g.fetch_member(g.me.id)
    except Exception:
        yo = g.me

    lineas = ["🔌 = rol real del bot · 🎨 = rol manual · 📌 = @everyone"]
    for r in sorted(yo.roles, key=lambda x: -x.position):
        if r.is_bot_managed():
            marca = "🔌"
        elif r.is_default():
            marca = "📌"
        else:
            marca = "🎨"
        lineas.append(
            f"{marca} {r.name} · ver:{'✅' if r.permissions.view_channel else '—'}"
        )

    try:
        logs = await g.fetch_channel(tk.ID_CANAL_LOGS)
        efectivo = logs.permissions_for(yo)
        lineas.append(f"   Canal encontrado: {logs.name}")
        lineas.append(
            f"   Efectivo → ver:{'✅' if efectivo.view_channel else '❌'} "
            f"enviar:{'✅' if efectivo.send_messages else '❌'} "
            f"adjuntar:{'✅' if efectivo.attach_files else '❌'} "
            f"historial:{'✅' if efectivo.read_message_history else '❌'}"
        )
    except Exception as exc:
        lineas.append(f"   ❌ No puedo leer el canal: {type(exc).__name__}")

    await ctx.send("\n".join(lineas)[:1900])


@bot.command(name="anunciar")
@commands.is_owner()
async def cmd_anunciar(ctx, titulo: str, *, cuerpo: str):
    """Uso: !anunciar "Título con espacios" el cuerpo del anuncio --here"""
    if not bien.ON_ANUNCIOS:
        await ctx.send("Anuncios apagados (SAM_ANUNCIOS_ON=0).")
        return
    canal = bien.canal_anuncios(ctx.guild)
    if canal is None:
        await ctx.send("❌ No encuentro #anuncios.")
        return
    ping = ctx.message.content.rstrip().endswith("--here")
    texto = cuerpo.rstrip()
    if ping:
        texto = texto[:-len("--here")].rstrip()
    try:
        await canal.send(content="@here" if ping else None,
                         embed=bien.embed_anuncio(titulo, texto, ctx.author))
        await ctx.send(f"📣 Publicado en {canal.mention}.")
    except Exception as exc:
        await ctx.send(f"⚠️ No pude publicar: {type(exc).__name__}")


# ─────────────── VOZ ───────────────
@bot.command(name="voz")
async def cmd_voz(ctx, *, mensaje: str):
    if not mensaje.strip():
        await ctx.send("Dime qué quieres que diga con mi voz 😊")
        return
    contexto_sala = ""
    if not isinstance(ctx.channel, discord.DMChannel):
        contexto_sala = await obtener_contexto_sala(ctx.channel, excluir_msg_id=ctx.message.id)
    async with ctx.typing():
        resultado = await procesar_mensaje(
            user_id=str(ctx.author.id),
            display_name=ctx.author.display_name,
            contenido=mensaje.strip(),
            contexto_sala=contexto_sala,
        )

    if resultado.get("decision") == "silencio":
        await ctx.send("...")
        return

    limpia = limpiar_respuesta(resultado["respuesta"])
    if limpia:
        await ctx.send(limpia[:1900])
        try:
            audio_path = await generar_audio_voz(limpia)
            if ctx.voice_client:
                if ctx.voice_client.is_playing():
                    ctx.voice_client.stop()
                ctx.voice_client.play(discord.FFmpegPCMAudio(audio_path))
        except Exception as exc:
            logger.warning("[Voz] Error (%s).", type(exc).__name__)


# ← CORREGIDO: !join SIN await en listen (escucha en modo sink)
@bot.command(name="join")
async def cmd_join(ctx):
    if not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.send("¡Conéctate a un canal de voz primero! 🎧")
        return
    channel = ctx.author.voice.channel
    try:
        if ctx.voice_client:
            await ctx.voice_client.move_to(channel)
            vc = ctx.voice_client
        else:
            if VOICE_RECV_OK:
                vc = await channel.connect(cls=voice_recv.VoiceRecvClient)
            else:
                vc = await channel.connect()
        # ← FIX: listen() NO es corutina → sin await. En try para no romper el join.
        escucha = False
        if VOICE_RECV_OK:
            try:
                vc.listen(voice_recv.BasicSink(_oirla))
                escucha = True
            except Exception as exc:
                print(f"[OIDO] listen falló: {type(exc).__name__}: {exc}")
        if escucha:
            await ctx.send(f"¡Conectada a {channel.name}! 🎤 **y escuchando**")
        else:
            await ctx.send(f"¡Conectada a {channel.name}! 🎤 (oído no disponible)")
    except Exception as exc:
        await ctx.send(f"⚠️ No pude conectarme así: {type(exc).__name__}: {exc}")


# ← CORREGIDO: !leave limpia la escucha antes de desconectarse
@bot.command(name="leave")
async def cmd_leave(ctx):
    if ctx.voice_client:
        try:
            if hasattr(ctx.voice_client, "stop_listening"):
                ctx.voice_client.stop_listening()
        except Exception:
            pass
        await ctx.voice_client.disconnect()
        await ctx.send("¡Desconectada! 👋")
    else:
        await ctx.send("No estoy en ningún canal de voz.")


@bot.tree.command(name="voz", description="Sam responde con su voz")
@app_commands.describe(mensaje="Lo que quieres decir a Sam")
async def slash_voz(interaction: discord.Interaction, mensaje: str):
    await interaction.response.defer(thinking=True)
    if not mensaje.strip():
        await interaction.followup.send("Dime qué quieres conversar 😊")
        return
    contexto_sala = ""
    if not isinstance(interaction.channel, discord.DMChannel):
        contexto_sala = await obtener_contexto_sala(interaction.channel)
    resultado = await procesar_mensaje(
        user_id=str(interaction.user.id),
        display_name=interaction.user.display_name,
        contenido=mensaje.strip(),
        contexto_sala=contexto_sala,
    )

    if resultado.get("decision") == "silencio":
        await interaction.followup.send("...")
        return

    limpia = limpiar_respuesta(resultado["respuesta"])
    if limpia:
        await interaction.followup.send(limpia[:1900])
        try:
            audio_path = await generar_audio_voz(limpia)
            if interaction.guild.voice_client:
                if interaction.guild.voice_client.is_playing():
                    interaction.guild.voice_client.stop()
                interaction.guild.voice_client.play(discord.FFmpegPCMAudio(audio_path))
        except Exception as exc:
            logger.warning("[SlashVoz] Error (%s).", type(exc).__name__)


# ← CORREGIDO: /join SIN await en listen (escucha en modo sink)
@bot.tree.command(name="join", description="Sam entra a tu canal de voz")
async def slash_join(interaction: discord.Interaction):
    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.response.send_message("¡Conéctate a un canal de voz! 🎧", ephemeral=True)
        return
    channel = interaction.user.voice.channel
    try:
        if interaction.guild.voice_client:
            await interaction.guild.voice_client.move_to(channel)
            vc = interaction.guild.voice_client
        else:
            if VOICE_RECV_OK:
                vc = await channel.connect(cls=voice_recv.VoiceRecvClient)
            else:
                vc = await channel.connect()
        # ← FIX: listen() NO es corutina → sin await. En try para no romper el join.
        escucha = False
        if VOICE_RECV_OK:
            try:
                vc.listen(voice_recv.BasicSink(_oirla))
                escucha = True
            except Exception as exc:
                print(f"[OIDO] listen falló: {type(exc).__name__}: {exc}")
        if escucha:
            await interaction.response.send_message(
                f"¡Conectada a {channel.name}! 🎤 **y escuchando**"
            )
        else:
            await interaction.response.send_message(
                f"¡Conectada a {channel.name}! 🎤 (oído no disponible)"
            )
    except Exception as exc:
        await interaction.response.send_message(
            f"⚠️ No pude conectarme así: {type(exc).__name__}: {exc}", ephemeral=True
        )


# ← CORREGIDO: /leave limpia la escucha antes de desconectarse
@bot.tree.command(name="leave", description="Sam sale del canal de voz")
async def slash_leave(interaction: discord.Interaction):
    if interaction.guild.voice_client:
        try:
            if hasattr(interaction.guild.voice_client, "stop_listening"):
                interaction.guild.voice_client.stop_listening()
        except Exception:
            pass
        await interaction.guild.voice_client.disconnect()
        await interaction.response.send_message("¡Desconectada! 👋")
    else:
        await interaction.response.send_message("No estoy en ningún canal de voz.", ephemeral=True)


if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)