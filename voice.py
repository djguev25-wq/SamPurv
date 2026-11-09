"""Voz de Sam: síntesis (ElevenLabs primero, edge-tts de respaldo) y transcripción (Whisper/Groq).

Reglas:
- Si ELEVENLABS_API_KEY y ELEVENLABS_VOICE_ID existen, se usa ElevenLabs.
- Si no hay créditos, la clave falla o hay error de red, se cae a edge-tts sin interrumpir.
- Todo el trabajo bloqueante corre en hilos para no congelar el bucle de Discord.
- Los archivos de audio son temporales y únicos (sin colisiones entre usuarios).
"""
import asyncio
import logging
import os
import re
import tempfile
import time

import edge_tts
import requests

logger = logging.getLogger("sam.voz")

DEFAULT_VOICE = os.getenv("SAM_VOICE", "es-MX-DaliaNeural")
ELEVEN_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVEN_VOICE = os.getenv("ELEVENLABS_VOICE_ID", "")
ELEVEN_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2")
MAX_CHARS_ELEVEN = int(os.getenv("SAM_VOZ_MAX_CHARS_ELEVEN", "600"))
# Tras un fallo de cuota/clave, no se reintenta ElevenLabs durante este tiempo.
ELEVEN_COOLDOWN = int(os.getenv("SAM_ELEVEN_COOLDOWN", "1800"))

_eleven_hasta = 0.0  # marca de tiempo hasta la que ElevenLabs está en pausa

_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF"
    "\U00002B00-\U00002BFF\U00002190-\U000021FF\U0000FE00-\U0000FE0F"
    "\U0000200D\U000020E3]"
)
_URL_RE = re.compile(r"https?://\S+")


def limpiar_para_voz(texto: str) -> str:
    """Quita markdown, emojis, enlaces y tokens de Discord que se leen mal."""
    t = texto or ""
    t = re.sub(r"<[@#][!&]?\d+>", "", t)           # menciones de Discord
    t = re.sub(r"<a?:\w+:\d+>", "", t)              # emojis personalizados
    t = _URL_RE.sub("enlace", t)
    t = t.replace("*", "").replace("`", "").replace("#", "").replace("_", " ")
    t = _EMOJI_RE.sub("", t)
    return re.sub(r"\s+", " ", t).strip()


def trocear(texto: str, maximo: int = 700) -> list[str]:
    """Divide un texto largo en trozos por frases, sin cortar palabras."""
    texto = limpiar_para_voz(texto)
    if not texto:
        return []
    frases = re.split(r"(?<=[.!?…;:])\s+", texto)
    trozos, actual = [], ""
    for f in frases:
        while len(f) > maximo:                       # frase gigante: corta por espacios
            corte = f.rfind(" ", 0, maximo)
            corte = corte if corte > 0 else maximo
            if actual:
                trozos.append(actual)
                actual = ""
            trozos.append(f[:corte].strip())
            f = f[corte:].strip()
        if len(actual) + len(f) + 1 <= maximo:
            actual = f"{actual} {f}".strip()
        else:
            if actual:
                trozos.append(actual)
            actual = f
    if actual:
        trozos.append(actual)
    return [t for t in trozos if t]


def _ruta_temp(sufijo=".mp3") -> str:
    fd, ruta = tempfile.mkstemp(prefix="sam_voz_", suffix=sufijo)
    os.close(fd)
    return ruta


def eleven_disponible() -> bool:
    return bool(ELEVEN_KEY and ELEVEN_VOICE) and time.time() >= _eleven_hasta


def _eleven_sync(texto: str, ruta: str) -> bool:
    """Llama a ElevenLabs. Devuelve True si guardó audio válido."""
    global _eleven_hasta
    try:
        r = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVEN_VOICE}",
            headers={"xi-api-key": ELEVEN_KEY, "Content-Type": "application/json",
                     "Accept": "audio/mpeg"},
            json={"text": texto, "model_id": ELEVEN_MODEL,
                  "voice_settings": {"stability": 0.5, "similarity_boost": 0.8}},
            timeout=40,
        )
        if r.status_code == 200 and r.content:
            with open(ruta, "wb") as f:
                f.write(r.content)
            return True
        if r.status_code in (401, 402, 403, 429):      # clave inválida o sin cuota
            _eleven_hasta = time.time() + ELEVEN_COOLDOWN
            logger.warning("[Voz] ElevenLabs HTTP %s: pausa de %ss.", r.status_code, ELEVEN_COOLDOWN)
        else:
            logger.warning("[Voz] ElevenLabs HTTP %s.", r.status_code)
    except requests.RequestException as exc:
        logger.warning("[Voz] ElevenLabs sin red (%s).", type(exc).__name__)
    return False


async def _edge(texto: str, ruta: str, voz: str) -> None:
    await edge_tts.Communicate(texto, voz, rate="+0%", pitch="+0Hz").save(ruta)


async def generar_audio_voz(texto: str, output_path: str | None = None,
                            voz: str = DEFAULT_VOICE, preferir_local: bool = False) -> str:
    """MP3 de un texto corto. ElevenLabs si hay créditos; si no, edge-tts.

    preferir_local=True fuerza edge-tts (útil para textos largos y ahorrar créditos).
    """
    limpio = limpiar_para_voz(texto)[:MAX_CHARS_ELEVEN] or "Hola."
    ruta = output_path or _ruta_temp()
    if not preferir_local and eleven_disponible():
        if await asyncio.to_thread(_eleven_sync, limpio, ruta):
            return ruta
    await _edge(limpio, ruta, voz)
    return ruta


async def generar_audio_largo(texto: str, voz: str = DEFAULT_VOICE, maximo: int = 700) -> list[str]:
    """Lee textos largos con la voz local (gratuita), un MP3 por trozo, en orden."""
    rutas = []
    for trozo in trocear(texto, maximo):
        ruta = _ruta_temp()
        try:
            await _edge(trozo, ruta, voz)
            rutas.append(ruta)
        except Exception as exc:
            logger.warning("[Voz] Trozo omitido (%s).", type(exc).__name__)
            _borrar(ruta)
    return rutas


def _borrar(ruta: str) -> None:
    try:
        os.unlink(ruta)
    except OSError:
        pass


def borrar_audios(rutas) -> None:
    for r in ([rutas] if isinstance(rutas, str) else rutas):
        _borrar(r)


def _whisper_sync(audio_path: str, idioma: str | None) -> str:
    from groq import Groq
    cliente = Groq(api_key=os.getenv("GROQ_API_KEY"))
    with open(audio_path, "rb") as f:
        kwargs = dict(file=(os.path.basename(audio_path), f.read()),
                      model="whisper-large-v3-turbo", response_format="text",
                      prompt="Conversación en español de Latinoamérica por canal de voz de Discord.")
        if idioma:
            kwargs["language"] = idioma
        return str(cliente.audio.transcriptions.create(**kwargs)).strip()


async def transcribir_audio_whisper(audio_path: str, idioma: str | None = "es") -> str:
    """Transcribe con Whisper (Groq). Sin idioma fijo, Whisper lo detecta solo."""
    if not os.getenv("GROQ_API_KEY"):
        return ""
    try:
        return await asyncio.to_thread(_whisper_sync, audio_path, idioma)
    except Exception as exc:
        logger.warning("[Voz] Transcripción falló (%s).", type(exc).__name__)
        return ""
