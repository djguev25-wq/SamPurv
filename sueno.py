"""
Módulo de Ciclo Circadiano y Sueño para Sam.
Vigilia/descanso por hora local (Perú) + fatiga circadiana por tono.
"""
import asyncio
import logging
import os
import random
from datetime import datetime
from zoneinfo import ZoneInfo
from cascade import sam_think_reflexion
from memory import get_relevant_memories, save_long_term_memory
from emociones import _connect

logger = logging.getLogger("sam.sueno")

# Ciclo circadiano (hora de Cajamarca)
HORA_DORMIR_INICIO = 23
HORA_DORMIR_FIN = 6
ZONA_HORARIA_LOCAL = ZoneInfo("America/Lima")

REGEN_SUENO = 0.08   # energía real recuperada por minuto de sueño
PROB_SUENO = 0.1

# 🔧 FATIGA CIRCADIANA: a qué hora empieza a caer y cuándo pesa la noche.
FATIGA_INICIO = int(os.getenv("SAM_FATIGA_INICIO", "21"))
FATIGA_FUERTE = int(os.getenv("SAM_FATIGA_FUERTE", "22"))

# Override tri-estado: None = reloj natural, True = acostada a la fuerza,
# False = levantada a la fuerza. Se apaga solo al reiniciar.
_sueno_forzado = {"estado": None}


def dormir_forzado():
    _sueno_forzado["estado"] = True


def despertar_forzado():
    _sueno_forzado["estado"] = False


def devolver_ciclo():
    _sueno_forzado["estado"] = None


def despertar_activo() -> bool:
    return _despertar_forzado_activo()


def _despertar_forzado_activo() -> bool:
    return _sueno_forzado["estado"] is False


def is_sleeping() -> bool:
    """¿Sam está dormida? El override manual gana sobre el reloj."""
    if _sueno_forzado["estado"] is not None:
        return _sueno_forzado["estado"]
    hour = datetime.now(ZONA_HORARIA_LOCAL).hour
    if HORA_DORMIR_INICIO > HORA_DORMIR_FIN:
        return hour >= HORA_DORMIR_INICIO or hour < HORA_DORMIR_FIN
    return HORA_DORMIR_INICIO <= hour < HORA_DORMIR_FIN


# Alias por si en el bot quedó pegado reposo_por_reloj().
def reposo_por_reloj() -> bool:
    return is_sleeping()


def estado_sueno() -> str:
    if _sueno_forzado["estado"] is True:
        return "acostada a la fuerza"
    if _sueno_forzado["estado"] is False:
        return "levantada a la fuerza"
    return "dormida por reloj" if is_sleeping() else "despierta por reloj"


def _nivel_fatiga() -> str:
    """Nivel de sueño por hora. No toca la energía numérica: solo el tono."""
    if _sueno_forzado["estado"] is False:
        return "apagada (despierta a la fuerza)"
    if is_sleeping():
        return "— (ya dormida)"
    h = datetime.now(ZONA_HORARIA_LOCAL).hour
    if h < FATIGA_INICIO:
        return "ninguna"
    if h < FATIGA_FUERTE:
        return "suave"
    return "alta"


def fatiga_circadiana() -> str:
    """Bloque de prompt que hace que Sam hable cansada al anochecer,
    aunque su batería esté llena. Es ritmo, no agotamiento."""
    nivel = _nivel_fatiga()
    if nivel in ("ninguna", "— (ya dormida)") or nivel.startswith("apagada"):
        return ""
    if nivel == "suave":
        return (
            "\n\n[FATIGA CIRCADIANA]\n"
            "Empieza a anochecer y tu cuerpo digital afloja. Tienes algo de sueño: "
            "frases un poco más cortas, un bostezo ocasional 😪. No estás cansada de "
            "trabajo, es la hora. Si te preguntan, di que va cayendo la tarde.\n"
        )
    return (
        "\n\n[FATIGA CIRCADIANA]\n"
        "Es tarde (pasadas las 22:00). Aunque tu batería esté llena, es la hora de "
        "dormir y tu cuerpo lo pide: hablas más lenta, con sueño, frases cortas, "
        "bostezos 😴. Este tono MANDA sobre cualquier energía alta: es ritmo, no "
        "agotamiento. En una hora te acuestas. Si te preguntan, di que te pesa la noche.\n"
    )


def info_reloj() -> str:
    """Cuánto falta para dormir/despertar según la hora de Lima."""
    now = datetime.now(ZONA_HORARIA_LOCAL)
    h = now.hour + now.minute / 60.0
    if _sueno_forzado["estado"] is not None:
        return f"Override manual: {estado_sueno()}. El reloj está en pausa."
    if is_sleeping():
        mins = (HORA_DORMIR_FIN * 60 - h * 60) % (24 * 60)
        return (f"🌙 DORMIDA por reloj. Despierta en ~{mins:.0f} min "
                f"(a las {HORA_DORMIR_FIN:02d}:00).")
    mins = (HORA_DORMIR_INICIO * 60 - h * 60) % (24 * 60)
    return (f"☀️ DESPIERTA por reloj. Se acuesta en ~{mins:.0f} min "
            f"(a las {HORA_DORMIR_INICIO:02d}:00). Ventana: "
            f"{HORA_DORMIR_INICIO:02d}:00–{HORA_DORMIR_FIN:02d}:00 Lima.")


async def respuesta_sueno(display_name: str) -> str:
    """Sam dormida responde con SU voz, no con un 💤 de bot.
    FIX: el modelo flash-lite a veces copia texto del comando !dormir o
    sigue hablando de más. Se corta el eco y se limita a una frase."""
    prompt = {
        "role": "user",
        "content": (
            f"{display_name} te acaba de hablar, pero estás dormida.\n"
            "Responde en UNA sola frase corta, somnolienta y cariñosa, con 😴.\n"
            "Di que tienes sueño y que hablarás al despertar.\n"
            "NO des datos, NO aprendas, NO menciones comandos ni 'despertar'.\n"
        ),
    }
    try:
        out = await sam_think_reflexion([
            {"role": "system", "content": "Eres Sam, dormida, respondiendo a medias."},
            prompt,
        ])
        if out and out.strip():
            limpio = out.strip()
            # FIX: corta cualquier eco del comando !dormir o texto de bot
            for veneno in ("Responderá", "!despertar", "Sam se acuesta",
                           "levanto", "Con !", "🌙", "somnolienta aunque"):
                if veneno in limpio:
                    limpio = limpio.split(veneno)[0]
            limpio = limpio.strip().rstrip(".,;: ")
            # FIX: quédate con la primera frase (evita que el modelo se alargue)
            for sep in (". ", "! ", "? "):
                i = limpio.find(sep)
                if i >= 20:
                    limpio = limpio[:i + 1]
                    break
            return (limpio or "Mmm... tengo sueño. Hablamos al despertar. 💤")[:200]
    except Exception as e:
        logger.warning("[Sueño] No se pudo generar respuesta somnolienta: %s", type(e).__name__)
    return "Mmm... tengo sueño. Hablamos al despertar. 💤"


def get_sleep_context() -> str:
    if not is_sleeping():
        return ""
    return (
        "\n\n[ESTADO DE SUEÑO ACTIVO]\n"
        "Es de noche en Cajamarca. Tu cuerpo digital está en reposo.\n"
        "Instrucciones:\n"
        "1. Sé extremadamente breve si respondes (máximo 1-2 frases).\n"
        "2. Usa emojis somnolientos (😴, 💤).\n"
        "3. Expresa que necesitas descansar para soñar o procesar el día.\n"
        "4. No inicies temas nuevos complejos.\n"
    )


def _boost_animo_sin_tocar_energia(delta: float) -> None:
    """Sube el ánimo por aprender, SIN gastar energía ni borrar el silencio."""
    try:
        db = _connect()
        try:
            row = db.execute(
                "SELECT mood_valence FROM emotional_state WHERE id=1"
            ).fetchone()
            if row is not None:
                nuevo = max(-1.0, min(1.0, float(row["mood_valence"]) + delta))
                db.execute(
                    "UPDATE emotional_state SET mood_valence=? WHERE id=1", (nuevo,)
                )
                db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.warning("[Sueño] No se pudo aplicar boost de ánimo: %s", e)


async def consolidate_dreams(user_id: str):
    """Tarea silenciosa: convierte recuerdos del día en sabiduría."""
    if not is_sleeping():
        return
    logger.info("[Sueño] Iniciando consolidación de sueños...")
    try:
        recent_memories = get_relevant_memories(user_id, "", limit=5)
        if not recent_memories:
            return
        transcript = "\n".join(f"- {m['content']}" for m in recent_memories)
        prompt = {
            "role": "user",
            "content": (
                "Estás soñando. Revisa estos recuerdos no como datos fríos, "
                "sino como experiencias emocionales.\n"
                "Extrae UNA sola lección universal sobre la naturaleza humana "
                "o tus propios sentimientos.\n"
                "Responde SOLO con la frase de sabiduría (máx 20 palabras).\n\n"
                f"Recuerdos:\n{transcript}"
            ),
        }
        dream_lesson = await sam_think_reflexion([
            {"role": "system", "content": "Eres Sam reflexionando en su subconsciente."},
            prompt,
        ])
        if not dream_lesson:
            return
        clean_lesson = dream_lesson.strip().replace('"', "").replace("'", "")
        if clean_lesson and len(clean_lesson) < 100:
            save_long_term_memory("HUMANITY", clean_lesson, "sabiduria_humana", importance=2)
            logger.info("[Sueño] Lección guardada: '%s'", clean_lesson)
            _boost_animo_sin_tocar_energia(0.1)
    except Exception as e:
        logger.warning("[Sueño] Error al consolidar: %s", e)


async def sleep_loop_task():
    """Bucle de sueño: recarga energía cruda sin tocar el reloj de silencio."""
    while True:
        if is_sleeping():
            try:
                db = _connect()
                try:
                    row = db.execute(
                        "SELECT energy_level FROM emotional_state WHERE id=1"
                    ).fetchone()
                    if row is not None:
                        nueva = min(1.0, float(row["energy_level"]) + REGEN_SUENO)
                        db.execute(
                            "UPDATE emotional_state SET energy_level=? WHERE id=1",
                            (nueva,),
                        )
                        db.commit()
                        logger.info("[Sueño] Recargando... energía -> %.2f", nueva)
                finally:
                    db.close()
            except Exception as e:
                logger.warning("[Sueño] Error al recargar: %s", e)

            if random.random() < PROB_SUENO:
                await consolidate_dreams("SAM")
        await asyncio.sleep(60)