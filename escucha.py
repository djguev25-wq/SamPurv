"""Oído de Sam: convierte el audio de un canal de voz en frases transcritas.

Flujo: el sink recibe PCM por usuario (hilo de red) -> se acumula -> al detectar silencio
se corta la frase -> WAV temporal -> Whisper -> callback asíncrono con (miembro, texto).
Filtros: duración mínima, energía mínima (ruido) y máximo por frase.
"""
import asyncio
import logging
import os
import tempfile
import time
import wave
from array import array

logger = logging.getLogger("sam.escucha")

RATE, CANALES, ANCHO = 48000, 2, 2                 # PCM de discord-ext-voice-recv
SILENCIO_S = float(os.getenv("SAM_ESCUCHA_SILENCIO", "1.1"))   # pausa que cierra una frase
MIN_S = float(os.getenv("SAM_ESCUCHA_MIN", "0.7"))
MAX_S = float(os.getenv("SAM_ESCUCHA_MAX", "25"))
RMS_MIN = int(os.getenv("SAM_ESCUCHA_RMS", "350"))            # por debajo = ruido de fondo
BYTES_S = RATE * CANALES * ANCHO


def rms(pcm: bytes) -> float:
    """Energía RMS de PCM s16le (sin depender de audioop, retirado en Python 3.13)."""
    n = len(pcm) // 2
    if not n:
        return 0.0
    muestras = array("h")
    muestras.frombytes(pcm[: n * 2])
    return (sum(x * x for x in muestras) / n) ** 0.5


def guardar_wav(pcm: bytes) -> str:
    fd, ruta = tempfile.mkstemp(prefix="sam_oido_", suffix=".wav")
    os.close(fd)
    with wave.open(ruta, "wb") as w:
        w.setnchannels(CANALES)
        w.setsampwidth(ANCHO)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return ruta


class Escucha:
    def __init__(self, loop, transcribir, al_oir, ocupada=lambda: False):
        """transcribir(ruta)->str (async); al_oir(miembro, texto) (async);
        ocupada()->bool: True mientras Sam habla (se descarta lo que llegue)."""
        self.loop, self.transcribir, self.al_oir, self.ocupada = loop, transcribir, al_oir, ocupada
        self.bufs: dict[int, dict] = {}
        self.activa = True
        self.tarea = loop.create_task(self._vigilante())

    # Se llama desde el hilo de recepción de audio: no tocar asyncio directamente.
    def feed(self, user, data) -> None:
        if not self.activa or user is None or getattr(user, "bot", False):
            return
        pcm = getattr(data, "pcm", None)
        if not pcm:
            return
        if self.ocupada():
            return
        b = self.bufs.setdefault(user.id, {"user": user, "pcm": bytearray(), "last": 0.0, "voz": False})
        b["pcm"] += pcm
        if rms(pcm) >= RMS_MIN:                    # solo cuenta como "voz" si hay energía real
            b["voz"] = True
            b["last"] = time.monotonic()

    async def _vigilante(self):
        while self.activa:
            await asyncio.sleep(0.3)
            ahora = time.monotonic()
            for uid, b in list(self.bufs.items()):
                dur = len(b["pcm"]) / BYTES_S
                if not b["voz"]:
                    if dur > 2:                    # solo ruido: se descarta
                        b["pcm"].clear()
                    continue
                if (ahora - b["last"] >= SILENCIO_S) or dur >= MAX_S:
                    pcm, user = bytes(b["pcm"]), b["user"]
                    b["pcm"], b["voz"] = bytearray(), False
                    if dur >= MIN_S:
                        self.loop.create_task(self._procesar(user, pcm))

    async def _procesar(self, user, pcm: bytes):
        ruta = None
        try:
            ruta = await asyncio.to_thread(guardar_wav, pcm)
            texto = (await self.transcribir(ruta)).strip()
            if len(texto) >= 2 and not _alucinacion(texto):
                await self.al_oir(user, texto)
        except Exception as exc:
            logger.warning("[Oído] Error procesando frase (%s).", type(exc).__name__)
        finally:
            if ruta:
                try:
                    os.unlink(ruta)
                except OSError:
                    pass

    def detener(self):
        self.activa = False
        self.tarea.cancel()
        self.bufs.clear()


# Whisper suele inventar estas frases con ruido o silencio.
_FUERTES = ("subtítulos", "subtitulos", "amara.org", "gracias por ver", "suscríbete",
            "suscribete", "thanks for watching", "dale like")
_DEBILES = ("música", "musica", "gracias", "adiós", "adios")


def _alucinacion(texto: str) -> bool:
    t = texto.lower().strip(" .!¡¿?")
    if len(t) < 4:
        return True
    if any(f in t for f in _FUERTES) and len(t) < 80:
        return True
    return t in _DEBILES
