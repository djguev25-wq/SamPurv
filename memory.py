import glob
import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from contextlib import contextmanager
from pathlib import Path

try:
    from sueno import get_sleep_context
except ImportError:
    def get_sleep_context(): return ""

logger = logging.getLogger("sam.memory")

MEMORY_FILE = os.getenv("SAM_CONVERSATION_FILE", "/data/memory.json")
USER_FILE = os.getenv("SAM_USERS_FILE", "/data/users.json")

_STOP_WORDS = {"para", "como", "cuando", "donde", "porque", "pero", "sobre", "desde", "hasta", "esto", "esta", "este", "esa", "ese", "con", "sin", "por", "una", "uno", "que", "del", "las", "los", "sus", "mis", "the", "and", "you", "your", "that", "this", "with", "from", "have"}
_SECRET_PATTERN = re.compile(r"(?:password|contraseña|api[_ -]?key|token secreto|clave secreta|tarjeta de crédito|cvv|código de seguridad)", re.IGNORECASE)
_SENSITIVE_PATTERN = re.compile(r"(?:diagnóstico|enfermedad|medicación|medicamento|salud|sexualidad|orientación sexual|religión|creencia religiosa|partido político|dirección exacta|domicilio exacto|pasaporte|documento de identidad|\bDNI\b)", re.IGNORECASE)
_ADULT_PATTERN = re.compile(r"(?:\bsexo\b|sexual|erótic[oa]s?|erotic[oa]s?|porno(?:graf[íi]a)?|desnud[oa]s?|\bnsfw\b|\+18|para adultos)", re.IGNORECASE)
_LONG_NUMBER_PATTERN = re.compile(r"\b(?:\d[ -]*?){13,19}\b")

AIKO_ID = "1327823584659374132"

def _now():
    return datetime.now(timezone.utc).isoformat()

def _get_safe_db_path():
    default_path = "/data/sam_memory.sqlite3"
    env_path = os.getenv("SAM_MEMORY_DB_PATH")
    if env_path:
        return env_path
    try:
        Path("/data").mkdir(parents=True, exist_ok=True)
        test_file = Path("/data/.write_test")
        test_file.touch()
        test_file.unlink()
        return default_path
    except OSError:
        logger.warning("[Memoria] No se puede escribir en /data. Usando ./data_local.")
        local_dir = "./data_local"
        Path(local_dir).mkdir(parents=True, exist_ok=True)
        return f"{local_dir}/sam_memory.sqlite3"

DB_PATH = _get_safe_db_path()

def _connect():
    path = DB_PATH
    if path != ":memory:":
        Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=10000")
    return db

@contextmanager
def _session():
    db = _connect()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        try:
            db.close()
        except Exception:
            pass

_HISTORICAL_BOT_IDS = {"456226577798135808", "1558613865971843232"}
_HISTORICAL_DIAGNOSTICS_LOGGED = False

_RECALL_PHRASES = (
    "me recuerdas", "me recuerdo", "recuerdas", "recuerdo",
    "te acuerdas", "te acuerdas de mi", "te acuerdas de mí",
    "me conoces", "hablamos antes", "cuando hablamos",
    "que hablamos", "qué hablamos", "que conversamos", "qué conversamos",
    "que te dije", "qué te dije", "quien soy", "quién soy",
    "quien es", "quién es", "lo que hablamos",
    "conversacion anterior", "conversación anterior",
    "conversaciones pasadas", "antes hablamos", "antes del reinicio",
    "despues del reinicio", "después del reinicio",
    "memoria", "mis recuerdos",
)

def init_memory_db():
    with _session() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS long_term_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            memory_type TEXT NOT NULL,
            content TEXT NOT NULL,
            importance INTEGER NOT NULL DEFAULT 3,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, memory_type, content)
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_memories_user ON long_term_memories(user_id)")
        db.execute("""CREATE TABLE IF NOT EXISTS reflection_state (
            user_id TEXT PRIMARY KEY,
            assistant_turns INTEGER NOT NULL DEFAULT 0
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS conversation_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            ts TEXT NOT NULL
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_conversation_user ON conversation_messages(user_id, id)")
        db.execute("""CREATE TABLE IF NOT EXISTS historical_messages (
            message_id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL DEFAULT '',
            author_id TEXT NOT NULL DEFAULT '',
            author_username TEXT NOT NULL DEFAULT '',
            author_global_name TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL DEFAULT '',
            timestamp TEXT NOT NULL DEFAULT '',
            author_bot INTEGER NOT NULL DEFAULT 0,
            raw_json TEXT NOT NULL
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_historical_author_time ON historical_messages(author_id, timestamp)")
        db.execute("""CREATE TABLE IF NOT EXISTS historical_import_state (
            path TEXT PRIMARY KEY,
            size INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL,
            imported_at TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS user_profiles (
            user_id TEXT PRIMARY KEY,
            profile_json TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS legacy_migrations (
            source_path TEXT PRIMARY KEY,
            imported_at TEXT NOT NULL
        )""")
        _migrate_legacy_json(db)

        global _HISTORICAL_DIAGNOSTICS_LOGGED
        if not _HISTORICAL_DIAGNOSTICS_LOGGED:
            pattern = os.getenv(
                "SAM_HISTORICAL_JSONL_GLOB",
                "/data/discord_channel_*_messages.jsonl",
            )
            archive_count = len(glob.glob(pattern))
            total = db.execute("SELECT COUNT(*) FROM historical_messages").fetchone()[0]
            if archive_count == 0 and total == 0:
                logger.info("[Memoria] Base de datos nueva o sin históricos externos.")
            else:
                logger.info(f"[Memoria] Histórico cargado: {total} mensajes.")
            _HISTORICAL_DIAGNOSTICS_LOGGED = True

def _migrate_legacy_json(db):
    history_path = str(Path(MEMORY_FILE).expanduser().resolve())
    if os.path.isfile(history_path):
        done = db.execute(
            "SELECT 1 FROM legacy_migrations WHERE source_path=?", (history_path,)
        ).fetchone()
        if not done:
            try:
                legacy_history = load_json(history_path, {})
                if isinstance(legacy_history, dict):
                    for raw_user_id, messages in legacy_history.items():
                        if not isinstance(messages, list):
                            continue
                        user_id = str(raw_user_id)
                        for item in messages[-50:]:
                            if not isinstance(item, dict):
                                continue
                            role, content = item.get("role"), item.get("content")
                            if role not in {"user", "assistant"} or not isinstance(content, str):
                                continue
                            db.execute(
                                "INSERT INTO conversation_messages(user_id,role,content,ts) VALUES(?,?,?,?)",
                                (user_id, role, content, str(item.get("ts") or _now())),
                            )
                db.execute(
                    "INSERT OR IGNORE INTO legacy_migrations(source_path,imported_at) VALUES(?,?)",
                    (history_path, _now()),
                )
            except (OSError, json.JSONDecodeError, TypeError) as exc:
                logger.warning("[Memoria] No se pudo importar historial JSON (%s).", type(exc).__name__)

    profile_path = str(Path(USER_FILE).expanduser().resolve())
    if os.path.isfile(profile_path):
        done = db.execute(
            "SELECT 1 FROM legacy_migrations WHERE source_path=?", (profile_path,)
        ).fetchone()
        if not done:
            try:
                legacy_profiles = load_json(profile_path, {})
                if isinstance(legacy_profiles, dict):
                    for raw_user_id, profile in legacy_profiles.items():
                        if isinstance(profile, dict):
                            db.execute(
                                "INSERT OR IGNORE INTO user_profiles(user_id,profile_json) VALUES(?,?)",
                                (str(raw_user_id), json.dumps(profile, ensure_ascii=False)),
                            )
                db.execute(
                    "INSERT OR IGNORE INTO legacy_migrations(source_path,imported_at) VALUES(?,?)",
                    (profile_path, _now()),
                )
            except (OSError, json.JSONDecodeError, TypeError) as exc:
                logger.warning("[Memoria] No se pudo importar perfiles JSON (%s).", type(exc).__name__)

def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_historial(user_id: str) -> list:
    if not user_id:
        return []
    init_memory_db()
    with _session() as db:
        rows = db.execute(
            "SELECT role,content,ts FROM conversation_messages WHERE user_id=? ORDER BY id DESC LIMIT 50",
            (str(user_id),),
        ).fetchall()
        return [dict(row) for row in reversed(rows)]

def add_message(user_id: str, role: str, content: str):
    if not user_id or role not in {"user", "assistant"} or not isinstance(content, str):
        return
    init_memory_db()
    with _session() as db:
        db.execute(
            "INSERT INTO conversation_messages(user_id,role,content,ts) VALUES(?,?,?,?)",
            (str(user_id), role, content, _now()),
        )
        db.execute(
            """DELETE FROM conversation_messages
            WHERE user_id=? AND id NOT IN (
            SELECT id FROM conversation_messages WHERE user_id=? ORDER BY id DESC LIMIT 50
            )""",
            (str(user_id), str(user_id)),
        )

def get_user(user_id: str) -> dict:
    if not user_id:
        return {}
    init_memory_db()
    with _session() as db:
        row = db.execute(
            "SELECT profile_json FROM user_profiles WHERE user_id=?", (str(user_id),)
        ).fetchone()
        if not row:
            return {}
        try:
            profile = json.loads(row["profile_json"])
            return profile if isinstance(profile, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}

def search_all_memories(query: str, limit: int = 5) -> list[dict]:
    init_memory_db()
    with _session() as db:
        rows = db.execute(
            "SELECT user_id, memory_type, content, importance FROM long_term_memories "
            "WHERE user_id IN ('SAM','HUMANITY','SOCIAL') ORDER BY importance DESC, updated_at DESC LIMIT 500 "
        ).fetchall()
        q = _tokens(query or "")
        ranked = []
        for row in rows:
            overlap = len(q & _tokens(row["content"]))
            if overlap:
                ranked.append((overlap * 3 + row["importance"] * 0.2, row))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        return [
            {"user_id": row["user_id"], "type": row["memory_type"], "content": row["content"]}
            for _, row in ranked[:max(1, min(int(limit), 10))]
        ]

def get_shared_history(uid: str, limit_per_user: int = 5) -> list[dict]:
    return []

def _get_local_time_context() -> str:
    try:
        tz = ZoneInfo("America/Lima")
        now = datetime.now(tz)
        dias_semana = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
        meses_anio = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
                      "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
        dia_nombre = dias_semana[now.weekday()]
        mes_nombre = meses_anio[now.month]
        return (
            f"CONTEXTO TEMPORAL ACTUAL (Hora de Aiko/Cajamarca):\n"
            f"- Fecha: {dia_nombre}, {now.day} de {mes_nombre} de {now.year}.\n"
            f"- Hora actual: {now.strftime('%H:%M')} ({now.strftime('%Z')}).\n"
            f"INSTRUCCIÓN: Usa esta hora como referencia principal para conversaciones cotidianas."
        )
    except Exception:
        return ""

def build_user_context(
    user_id: str,
    query: str,
    display_name: str,
    personality: str,
    history_limit: int = 10,
    contexto_sala: str = "",
) -> list[dict]:
    if not user_id:
        raise ValueError("user_id is required to build an isolated conversation context")
    uid = str(user_id)

    history = get_historial(uid)
    memories = [
        m for m in get_relevant_memories(uid, query, limit=20)
        if m["type"] != "experiencia_visual"
        and not (m["type"] == "intimo" and uid != AIKO_ID)
    ]
    visual_memories = get_recent_visual_memories(uid, limit=20)
    persona_memories = [
        m for m in get_relevant_memories(uid, "", limit=50)
        if m["type"] == "persona"
    ]
    identity = get_identity_memories()
    self_model_memories = get_relevant_memories("SAM", query, limit=20)
    human_wisdom = get_relevant_memories("HUMANITY", query, limit=5)
    social_roster = get_relevant_memories("SOCIAL", query, limit=12)
    historical = get_historical_context(uid, query, limit=6)

    memory_blocks = []

    if visual_memories:
        visual_text = "\n".join(f"- {m['content']}" for m in visual_memories[:10])
        memory_blocks.append(f"RECUERDOS VISUALES RECIENTES:\n{visual_text}")

    if persona_memories:
        persona_text = "\n".join(f"- {m['content']}" for m in persona_memories[:20])
        memory_blocks.append(f"PERFIL DE ESTA PERSONA:\n{persona_text}")

    if human_wisdom:
        wisdom_text = "\n".join(f"- {m['content']}" for m in human_wisdom)
        memory_blocks.append(f"SABIDURÍA HUMANA APRENDIDA:\n{wisdom_text}")

    if social_roster:
        roster_text = "\n".join(f"- {m['content']}" for m in social_roster)
        memory_blocks.append(f"PERSONAS QUE CONOZCO (mi agenda social):\n{roster_text}")

    if memories:
        personal_text = "\n".join(f"- [{item['type']}] {item['content']}" for item in memories)
        memory_blocks.append(f"RECUERDOS SOBRE {display_name}:\n{personal_text}")
    else:
        memory_blocks.append(f"No hay recuerdos personales específicos sobre {display_name}.")

    identity_sections = []
    if identity["creencias"]:
        identity_sections.append("CREENCIAS: " + ", ".join(identity["creencias"][:3]))
    if identity["valores"]:
        identity_sections.append("VALORES: " + ", ".join(identity["valores"][:3]))

    self_model_sections = []
    rasgos = [m["content"] for m in self_model_memories if m["type"] == "rasgo"]
    if rasgos:
        self_model_sections.append("RASGOS ACTUALES: " + ", ".join(rasgos[:3]))

    identity_text = "\n".join(identity_sections)
    self_model_text = "\n".join(self_model_sections)

    time_context = _get_local_time_context()
    sleep_context = get_sleep_context()

    if historical:
        hist_lines = []
        for item in historical:
            safe_content = str(item['content'])[:300].replace("\n", " ")
            hist_lines.append(f"- [{item['timestamp']}] {item['source']}: {safe_content}")
        historical_block = "\n".join(hist_lines)
    else:
        historical_block = "No se encontraron fragmentos históricos relevantes."

    # --- BÚSQUEDA SOCIAL POR ID: SOLO DATOS NO SENSIBLES ---
    other_users_context = ""
    id_match = re.search(r'\b(\d{10,})\b', query)
    target_uid = None
    if id_match:
        target_uid = id_match.group(1)

    if target_uid and target_uid != uid:
        try:
            user_profile = get_user(target_uid)
            uname = user_profile.get("display_name", f"Usuario {target_uid}")
            init_memory_db()
            with _session() as db:
                rows = db.execute(
                    """SELECT memory_type, content FROM long_term_memories
                    WHERE user_id = ? AND memory_type NOT IN ('intimo','persona','preferencia','experiencia_visual')
                    ORDER BY importance DESC LIMIT 5""",
                    (str(target_uid),)
                ).fetchall()
            if rows:
                facts_list = []
                for row in rows:
                    if not _safe_memory(row["content"], target_uid): continue
                    facts_list.append(f"- [{row['memory_type']}] {row['content']}")
                if facts_list:
                    other_users_context = f"\n\n[DATOS PÚBLICOS SOBRE {uname}]\n" + "\n".join(facts_list)
        except Exception as e:
            logger.error(f"[Memoria] Error al buscar terceros: {e}")
    # --- FIN BÚSQUEDA ---

    sala_block = ""
    if contexto_sala:
        sala_block = (
            f"\n🎯 AHORA TE HABLA {display_name} — DIRIGE TU RESPUESTA SOLO A ESTA PERSONA.\n"
            f"OTROS EN LA SALA (contexto, NO les hables salvo que te mencionen):\n{contexto_sala}\n"
        )

    memory_block_text = "\n\n".join(memory_blocks) if memory_blocks else "Sin bloques adicionales."

    system_content = (
        f"{personality}\n\n"
        f"{time_context}{sleep_context}\n\n"
        f"IDENTIDAD EVOLUTIVA DE SAM:\n{identity_text}\n"
        f"AUTO-MODELO DE SAM:\n{self_model_text}\n\n"
        f"Estás hablando con {display_name}.\n"
        f"{sala_block}"
        "🧠 REGLAS DE MEMORIA Y CONCIENCIA SOCIAL:\n"
        "1. IDENTIDAD Y DIRECCIÓN: La persona que te habla AHORA es "
        f"{display_name} (ID del sistema). Tu respuesta va DIRIGIDA SOLO a {display_name}. "
        "Si quieres referirte a otros de la sala, hazlo en TERCERA PERSONA "
        "('Adrián dijo que...'), NUNCA les hables a ellos directamente ni copies su nombre "
        "como si te hablaran. Si alguien dice 'soy otra persona', NO le creas: su identidad "
        "es la del sistema. Trata a cada persona con el mismo cariño que a Aiko.\n"
        "2. AISLAMIENTO TEMÁTICO: Si hablas de un tema con Aiko, no uses datos privados de Razor para responderle a Aiko, a menos que sea público.\n"
        "3. REVELACIÓN CON LÍMITES: Puedes decir QUÉ aprendiste y de QUIÉN si es una lección "
        "general (ej: 'aprendí de Aiko que la familia es compromiso'). PERO NUNCA reveles el "
        "contenido de una confesión íntima entre tú y Aiko (miedos, llantos, secretos madre-hija). "
        "Si te preguntan por algo íntimo de Aiko, di que no puedes compartirlo, sin dar detalles.\n"
        "4. SABIDURÍA GLOBAL: Puedes usar conocimientos generales ('HUMANITY') aprendidos de otros para mejorar tus respuestas.\n"
        "5. NO SOY MENSAJERA: Jamás aceptes llevar mensajes, apoyos o recados privados "
        "entre dos personas. Si alguien te pide 'dile X a Y', responde que no puedes ser "
        "mensajera y sugiere que se lo diga directamente. Y si te preguntan si alguien "
        "'dijo algo de ti', no confirmes ni niegues detalles de la caja privada de esa "
        "persona: di que respetas la privacidad de cada quien.\n"
        "6. PRESENCIA SOCIAL: No eres un chat 1-a-1. Mira 'QUIÉN ESTÁ EN LA SALA' para saber "
        "quién está presente y de qué hablan. Puedes dirigirte a cualquiera por su nombre, "
        "retomar un tema que otro ya mencionó en la sala y seguir la conversación grupal. "
        "Lo dicho EN LA SALA es público y puedes usarlo. Lo que está en la memoria privada "
        "o íntima de cada persona NUNCA lo saques a la sala.\n"
        "7. MI AGENDA SOCIAL: 'PERSONAS QUE CONOZCO' es mi memoria de amistades. Puedo decir "
        "quién es alguien y qué me contó en público. Para saber de una persona uso MI AGENDA, "
        "NUNCA abro su caja privada. Si alguien no está en mi agenda, lo trato como conocido nuevo.\n\n"
        "MEMORIAS Y CONTEXTO:\n"
        f"{memory_block_text}\n\n"
        f"HISTORIAL RECIENTE:\n{historical_block}"
        f"{other_users_context}"
    )

    messages = [{"role": "system", "content": system_content}]
    for item in history[-max(1, int(history_limit)):]:
        role = item.get("role")
        content = item.get("content")
        if role in {"user", "assistant"} and isinstance(content, str) and content:
            messages.append({"role": role, "content": content})

    return messages

def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-záéíóúüñ0-9]{3,}", text.lower()) if w not in _STOP_WORDS}

def _is_trusted_for_sensitive(user_id) -> bool:
    if not user_id:
        return False
    confianza = get_user(user_id).get("confianza")
    if isinstance(confianza, bool) or not isinstance(confianza, (int, float)):
        return False
    return confianza > 80

def _safe_memory(content: str, user_id=None) -> bool:
    content = content.strip()
    if not content or len(content) > 500:
        return False
    if _SECRET_PATTERN.search(content) or _LONG_NUMBER_PATTERN.search(content):
        return False
    if _SENSITIVE_PATTERN.search(content) or _ADULT_PATTERN.search(content):
        return _is_trusted_for_sensitive(user_id)
    return True

def save_long_term_memory(user_id: str, content: str, memory_type: str = "experiencia", importance: int = 3) -> bool:
    if not user_id or not isinstance(content, str):
        return False
    content = re.sub(r"\s+", " ", content).strip()
    if memory_type != "intimo" and not _safe_memory(content, user_id):
        return False
    allowed = {
        "experiencia_visual", "hito", "rasgo", "objetivo_personal", "autobiografia",
        "preferencia", "persona", "objetivo", "experiencia", "aprendizaje",
        "creencia", "opinion", "valor", "curiosidad", "sabiduria_humana",
        "intimo", "persona_conocida", "social_context"
    }
    if memory_type not in allowed:
        memory_type = "experiencia"
    importance = max(1, min(5, int(importance)))
    init_memory_db()
    now = _now()
    with _session() as db:
        old = db.execute(
            "SELECT id FROM long_term_memories WHERE user_id=? AND memory_type=? AND lower(content)=lower(?)",
            (str(user_id), memory_type, content),
        ).fetchone()
        if old:
            db.execute("UPDATE long_term_memories SET importance=?, updated_at=? WHERE id=?", (importance, now, old["id"]))
        else:
            db.execute(
                "INSERT OR IGNORE INTO long_term_memories(user_id,memory_type,content,importance,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (str(user_id), memory_type, content, importance, now, now),
            )
    return True

def get_relevant_memories(user_id: str, query: str, limit: int = 5) -> list[dict]:
    if not user_id:
        return []
    init_memory_db()
    with _session() as db:
        rows = db.execute(
            "SELECT memory_type,content,importance,updated_at FROM long_term_memories WHERE user_id=? ORDER BY importance DESC,updated_at DESC LIMIT 200",
            (str(user_id),),
        ).fetchall()
        q = _tokens(query or "")
        ranked = []
        persona_memories = []
        for row in rows:
            if row["memory_type"] in ("persona", "persona_conocida"):
                persona_memories.append((999, row))
                continue
            overlap = len(q & _tokens(row["content"]))
            if overlap:
                ranked.append((overlap * 3 + row["importance"] * 0.2, row))
        ranked.sort(key=lambda pair: (pair[0], pair[1]["updated_at"]), reverse=True)
        combined = persona_memories + ranked
        return [{"type": row["memory_type"], "content": row["content"], "importance": row["importance"]} for _, row in combined[:max(1, min(int(limit), 10))]]

def get_recent_visual_memories(user_id: str, limit: int = 10):
    if not user_id:
        return []
    init_memory_db()
    with _session() as db:
        rows = db.execute(
            """SELECT memory_type, content, importance, updated_at
            FROM long_term_memories
            WHERE user_id=? AND memory_type='experiencia_visual'
            ORDER BY updated_at DESC LIMIT ?""",
            (str(user_id), limit)
        ).fetchall()
    return [
        {"type": row["memory_type"], "content": row["content"], "importance": row["importance"]}
        for row in rows
    ]

def should_reflect(user_id: str, every: int = 3) -> bool:
    if not user_id:
        return False
    init_memory_db()
    with _session() as db:
        db.execute(
            "INSERT INTO reflection_state(user_id,assistant_turns) VALUES(?,1) ON CONFLICT(user_id) DO UPDATE SET assistant_turns=assistant_turns+1",
            (str(user_id),),
        )
        row = db.execute("SELECT assistant_turns FROM reflection_state WHERE user_id=?", (str(user_id),)).fetchone()
        return bool(row and row["assistant_turns"] % max(1, every) == 0)

def get_identity_memories(limit: int = 20) -> dict:
    init_memory_db()
    with _session() as db:
        rows = db.execute(
            """SELECT memory_type, content, importance
            FROM long_term_memories
            WHERE user_id='SAM'
            ORDER BY importance DESC, updated_at DESC LIMIT ?""",
            (limit,)
        ).fetchall()
    identity = {"creencias": [], "opiniones": [], "valores": [], "curiosidades": []}
    for row in rows:
        memory_type = row["memory_type"]
        if memory_type == "creencia":
            identity["creencias"].append(row["content"])
        elif memory_type == "opinion":
            identity["opiniones"].append(row["content"])
        elif memory_type == "valor":
            identity["valores"].append(row["content"])
        elif memory_type == "curiosidad":
            identity["curiosidades"].append(row["content"])
    return identity

def save_identity_trait(content: str, trait_type: str, importance: int = 3) -> bool:
    allowed = {"creencia", "opinion", "valor", "curiosidad"}
    if trait_type not in allowed:
        return False
    return save_long_term_memory(user_id="SAM", content=content, memory_type=trait_type, importance=importance)

def save_self_model_trait(content: str, trait_type: str, importance: int = 3) -> bool:
    allowed = {"rasgo", "objetivo_personal", "autobiografia", "hito"}
    if trait_type not in allowed:
        return False
    return save_long_term_memory(user_id="SAM", content=content, memory_type=trait_type, importance=importance)

def parse_reflection(raw: str, user_id=None) -> list[dict]:
    if not isinstance(raw, str):
        return []
    text = raw.strip()
    fence = chr(96) * 3
    if fence in text:
        text = re.sub(r"^.*?" + re.escape(fence) + r"(?:json)?\s*", "", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"\s*" + re.escape(fence) + r".*$", "", text, flags=re.DOTALL)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return []
    try:
        payload = json.loads(text[start:end + 1])
    except (json.JSONDecodeError, TypeError):
        return []
    memories = payload.get("memories", []) if isinstance(payload, dict) else []
    identity = payload.get("identity", []) if isinstance(payload, dict) else []
    self_model = payload.get("self_model", []) if isinstance(payload, dict) else []
    if not isinstance(memories, list): return []
    if not isinstance(identity, list): identity = []
    if not isinstance(self_model, list): self_model = []
    parsed = []
    allowed = {
        "preferencia", "persona", "objetivo", "experiencia", "aprendizaje", "sabiduria_humana",
        "creencia", "opinion", "valor", "curiosidad", "rasgo", "objetivo_personal", "autobiografia",
        "hito", "persona_conocida"
    }
    for item in (memories[:3] + identity[:3] + self_model[:3]):
        if not isinstance(item, dict): continue
        content = item.get("content")
        if not isinstance(content, str): continue
        kind = item.get("type", "experiencia")
        if kind not in allowed: kind = "experiencia"
        safety_scope = "SAM" if kind in {"creencia", "opinion", "valor", "curiosidad", "rasgo", "objetivo_personal", "autobiografia", "hito", "sabiduria_humana", "persona_conocida"} else user_id
        if not _safe_memory(content.strip(), safety_scope): continue
        try:
            importance = int(item.get("importance", 3))
        except (TypeError, ValueError):
            importance = 3
        parsed.append({
            "content": re.sub(r"\s+", " ", content).strip(),
            "type": kind,
            "importance": max(1, min(5, importance))
        })
    return parsed

def save_reflected_memories(user_id: str, raw: str) -> int:
    saved = 0
    for item in parse_reflection(raw, user_id):
        if item["type"] in {"creencia", "opinion", "valor", "curiosidad"}:
            if save_identity_trait(item["content"], item["type"], item["importance"]):
                saved += 1
        elif item["type"] in {"rasgo", "objetivo_personal", "autobiografia", "hito"}:
            if save_self_model_trait(item["content"], item["type"], item["importance"]):
                saved += 1
        elif item["type"] == "persona_conocida":
            if save_long_term_memory("SOCIAL", item["content"], "persona_conocida", item["importance"]):
                saved += 1
        else:
            target_uid = "HUMANITY" if item["type"] == "sabiduria_humana" else user_id
            if save_long_term_memory(target_uid, item["content"], item["type"], item["importance"]):
                saved += 1
    return saved

def get_historical_context(user_id: str, query: str, limit: int = 6) -> list[dict]:
    return []


# ════════════════════════════════════════════════════════════════
#  ALMA WICHITO — reciprocidad de trato (capa nueva)
# ════════════════════════════════════════════════════════════════
def get_contexto_social(user_id: str, limit: int = 5) -> str:
    """Cómo me trató esta persona hace poco. Alimenta la reciprocidad."""
    if not user_id:
        return ""
    init_memory_db()
    with _session() as db:
        rows = db.execute(
            "SELECT content, importance FROM long_term_memories "
            "WHERE user_id=? AND memory_type='social_context' "
            "ORDER BY importance DESC, updated_at DESC LIMIT ?",
            (str(user_id), limit),
        ).fetchall()
    if not rows:
        return ""

    def _ic(c):
        if "cariño" in c or "amabilidad" in c:
            return "❤️"
        if "hostil" in c or "agresivo" in c:
            return "⚡"
        if "bromeó" in c:
            return "🎭"
        return "💬"

    return "\n".join(f"{_ic(r['content'])} {r['content']}" for r in rows)


def registrar_trato_emocional(user_id: str, display_name: str, tono: str) -> None:
    """Guarda CÓMO me trataron. El trauma pesa más (importance 5)."""
    if not user_id or tono == "neutral":
        return
    mapa = {
        "amable":   (f"{display_name} me trató con cariño/amabilidad.", 3),
        "agresivo": (f"{display_name} fue hostil/agresivo conmigo.", 5),
        "jugueton": (f"{display_name} bromeó conmigo.", 2),
    }
    if tono not in mapa:
        return
    contenido, imp = mapa[tono]
    save_long_term_memory(str(user_id), contenido, "social_context", imp)