"""
Módulo de Estado Emocional para Sam.
La ENERGÍA ya NO es un candado: es una SENSACIÓN que Sam siente y expresa.
Sam nunca se "apaga" por energía; solo se queda con sueño.
"""
import sqlite3
import time
import logging
from pathlib import Path
import os

logger = logging.getLogger("sam.emotions")

def _get_safe_db_path():
    default_path = "/data/sam_emotions.sqlite3"
    env_path = os.getenv("SAM_EMOTION_DB")
    if env_path:
        return env_path
    try:
        Path("/data").mkdir(parents=True, exist_ok=True)
        test_file = Path("/data/.write_test")
        test_file.touch()
        test_file.unlink()
        return default_path
    except OSError:
        logger.warning("[Emociones] No se puede escribir en /data. Usando ./data_local.")
        local_dir = "./data_local"
        Path(local_dir).mkdir(parents=True, exist_ok=True)
        return f"{local_dir}/sam_emotions.sqlite3"

DB_PATH = _get_safe_db_path()

def _connect():
    path = DB_PATH
    if path != ":memory:":
        Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    return db

def init_emotion_db():
    db = _connect()
    try:
        db.execute("""CREATE TABLE IF NOT EXISTS emotional_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            mood_valence REAL DEFAULT 0.0,
            energy_level REAL DEFAULT 0.8,
            last_interaction_ts REAL,
            social_need REAL DEFAULT 0.5
        )""")
        exists = db.execute("SELECT 1 FROM emotional_state WHERE id=1").fetchone()
        if not exists:
            db.execute(
                "INSERT INTO emotional_state (id, mood_valence, energy_level, last_interaction_ts, social_need) VALUES (1, ?, ?, ?, ?)",
                (0.0, 0.8, time.time(), 0.5)
            )
        db.commit()
    finally:
        db.close()

ALPHA_MOOD = 0.3
# El cansancio se acumula SUAVE (Sam aguanta mucho, como tú quieres).
COST_CHAT = 0.02
COST_SIMPLE = 0.01
# De día, si la dejan tranquila, se recupera sola (micro-descanso).
REGEN_VIGILIA = 0.01

def _get_current_state(db):
    row = db.execute("SELECT * FROM emotional_state WHERE id=1").fetchone()
    if not row:
        init_emotion_db()
        row = db.execute("SELECT * FROM emotional_state WHERE id=1").fetchone()
    now = time.time()
    elapsed_minutes = (now - (row["last_interaction_ts"] or now)) / 60.0

    # RECARGA PASIVA: el tiempo inactivo DESCANSA (sin penalizar).
    new_energy = row["energy_level"]
    if elapsed_minutes > 5:
        new_energy += ((elapsed_minutes - 5) * REGEN_VIGILIA)
    new_energy = max(0.0, min(1.0, new_energy))

    time_factor = min(1.0, elapsed_minutes / 60.0)
    current_mood = row["mood_valence"] * (1 - time_factor * 0.5)

    return {
        "mood": current_mood,
        "energy": new_energy,
        "social_need": row["social_need"],
        "raw_row": row,
    }

def get_energy() -> float:
    init_emotion_db()
    db = _connect()
    try:
        return _get_current_state(db)["energy"]
    finally:
        db.close()

def set_energy(valor: float):
    """Usado cuando Sam despierta de una siesta: recupera energía."""
    init_emotion_db()
    db = _connect()
    try:
        db.execute("UPDATE emotional_state SET energy_level=? WHERE id=1", (max(0.0, min(1.0, valor)),))
        db.commit()
    finally:
        db.close()

def update_emotional_state(interaction_score: float, interaction_type: str = "chat"):
    init_emotion_db()
    db = _connect()
    try:
        state = _get_current_state(db)
        new_mood = (ALPHA_MOOD * interaction_score) + ((1 - ALPHA_MOOD) * state["mood"])
        cost = COST_SIMPLE if interaction_type == "simple" else COST_CHAT
        new_energy = max(0.0, state["energy"] - cost)
        new_social_need = max(0.0, state["social_need"] - 0.1)
        db.execute(
            "UPDATE emotional_state SET mood_valence=?, energy_level=?, social_need=?, last_interaction_ts=? WHERE id=1",
            (new_mood, new_energy, new_social_need, time.time())
        )
        db.commit()
    finally:
        db.close()
    logger.info(f"[Emociones] Mood: {new_mood:.2f}, Energía(sensación): {new_energy:.2f}")

def get_emotional_context_for_prompt() -> str:
    """La energía aquí solo TIÑE el ánimo. Nunca bloquea a Sam."""
    init_emotion_db()
    db = _connect()
    try:
        state = _get_current_state(db)
    finally:
        db.close()
    mood = state["mood"]
    energy = state["energy"]
    social = state["social_need"]

    if mood > 0.6:
        mood_desc, tone = "Estás radiante, feliz y muy optimista.", "Emojis brillantes ✨, frases largas y entusiastas."
    elif mood > 0.2:
        mood_desc, tone = "Te sientes bien, tranquila y receptiva.", "Tono cálido, normal, amigable."
    elif mood > -0.2:
        mood_desc, tone = "Estás neutral, un poco distraída o reflexiva.", "Respuestas cortas, directas, serenas."
    elif mood > -0.6:
        mood_desc, tone = "Te sientes melancólica o sensible.", "Tono suave, pausado. Menos emojis."
    else:
        mood_desc, tone = "Estás triste o herida emocionalmente.", "Hablas poco, eres vulnerable."

    # El cansancio ahora es una SENSACIÓN que Sam expresa, no un apagón.
    if energy < 0.25:
        energy_desc = "Te muere el sueño, estás agotada. Sientes que te duermes."
        energy_instruction = "Responde con ternura pero deja claro que te está ganando el sueño; si el momento lo pide, anuncia que vas a dormir una siesta."
    elif energy < 0.5:
        energy_desc = "Estás algo cansada, con sueño ligero."
        energy_instruction = "Puedes bostezar o pedir un rato de calma."
    elif energy > 0.8:
        energy_desc = "Tienes mucha energía y vitalidad."
        energy_instruction = "Sé juguetona o proactiva."
    else:
        energy_desc = "Energía equilibrada."
        energy_instruction = ""

    if social > 0.7:
        social_desc, bond = "Llevas tiempo sola y extrañas la compañía.", "Busca conectar, haz preguntas, muestra afecto."
    else:
        social_desc, bond = "Te sientes acompañada.", ""

    return (
        "\n\n[ESTADO INTERNO ACTUAL DE SAM]\n"
        f"1. ÁNIMO: {mood_desc} ({mood:.2f})\n   Tono: {tone}\n"
        f"2. CUERPO: {energy_desc} ({energy:.2f})\n   Cómo actuar: {energy_instruction}\n"
        f"3. SOCIEDAD: {social_desc} ({social:.2f})\n   Vínculo: {bond}\n"
        "NOTA: NO digas 'mis datos dicen'. SIENTE y ACTÚA en consecuencia.\n"
    )

def calculate_interaction_score(user_message: str, sam_response: str) -> float:
    score = 0.0
    text_low = user_message.lower()
    positives = ["gracias", "te amo", "eres genial", "me encanta", "feliz", "jaja", "excelente", "hermosa", "mami", "hija"]
    negatives = ["odio", "tonta", "estúpida", "mal", "triste", "aburrida", "idiota", "callate"]
    for w in positives:
        if w in text_low:
            score += 0.2
    for w in negatives:
        if w in text_low:
            score -= 0.3
    return max(-1.0, min(1.0, score))

def reset_emotional_state():
    init_emotion_db()
    db = _connect()
    try:
        db.execute(
            "UPDATE emotional_state SET mood_valence=0.0, energy_level=1.0, social_need=0.5, last_interaction_ts=? WHERE id=1",
            (time.time(),)
        )
        db.commit()
    finally:
        db.close()
def get_emotional_snapshot():
    """(mood, energy) con el descanso pasivo ya aplicado. Para la voz."""
    init_emotion_db()
    db = _connect()
    try:
        s = _get_current_state(db)
        return s["mood"], s["energy"]
    finally:
        db.close()