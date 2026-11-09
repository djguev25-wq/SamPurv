import asyncio
import logging
import time
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from google import genai
from google.genai import types
from groq import AsyncGroq

logger = logging.getLogger("sam.cascade")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

# Conversación: VELOCIDAD primero (Lite/Gemma vivos), nobles lentos al fondo.
GEMINI_CASCADE = [
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemma-4-26b-a4b-it",
    "gemma-4-31b-it",
    "gemini-3-flash-preview",
    "gemini-3.5-flash",
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
]

# Reflexión/subconsciente: solo modelos rápidos, nunca toca los nobles.
REFLEXION_CASCADE = [
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
]

MAX_OUT = int(os.getenv("SAM_MAX_OUT", "600"))
INTENTO_TIMEOUT = 18
PROBE_INTERVAL = 7200
DEFAULT_COOLDOWN = 60

_client = None
_groq = None
_cooldown = {}
_pulso = {"modelo": None, "ms": 0, "intento": 0, "ts": 0, "traza": []}

def _g():
    global _client
    if _client is None:
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client

def _gr():
    global _groq
    if _groq is None:
        _groq = AsyncGroq(api_key=GROQ_API_KEY)
    return _groq

def _next_reset_pt():
    try:
        now = datetime.now(ZoneInfo("America/Los_Angeles"))
        nxt = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return nxt.timestamp()
    except Exception:
        return time.time() + 86400

def _classify(err_str: str):
    e = err_str.lower()
    if "per_day" in e or "per day" in e or "daily" in e or "requests_per_day" in e:
        return "daily", _next_reset_pt()
    if ("per_minute" in e or "per minute" in e or "rpm" in e or "tpm" in e
            or "429" in e or "resource_exhausted" in e):
        return "rate", time.time() + 600
    if "timeout" in e or "timed out" in e:
        return "timeout", time.time() + 30
    return "error", time.time() + DEFAULT_COOLDOWN

def _available(model, now):
    info = _cooldown.get(model)
    if not info:
        return True
    if now >= info["until"]:
        _cooldown.pop(model, None)
        return True
    if info["kind"] == "daily" and now - info["last_probe"] > PROBE_INTERVAL:
        info["last_probe"] = now
        return True
    return False

def _split_system(messages):
    sys_parts, rest = [], []
    for m in messages:
        if m.get("role") == "system":
            sys_parts.append(str(m.get("content", "")))
        else:
            rest.append(m)
    return ("\n\n".join(sys_parts) or None), rest

def _to_gemini_contents(messages):
    contents = []
    for m in messages:
        role = "model" if m.get("role") == "assistant" else "user"
        contents.append(types.Content(
            role=role,
            parts=[types.Part.from_text(text=str(m.get("content", "")))],
        ))
    return contents

async def _call_gemini(model, system, contents, timeout, max_out=MAX_OUT):
    cfg = types.GenerateContentConfig(
        system_instruction=system,
        max_output_tokens=max_out,
    )
    resp = await asyncio.wait_for(
        _g().aio.models.generate_content(model=model, contents=contents, config=cfg),
        timeout=timeout,
    )
    return resp.text

async def _call_groq(system, messages, timeout, max_out=MAX_OUT):
    msgs = ([{"role": "system", "content": system}] if system else []) + [
        {"role": m["role"], "content": str(m.get("content", ""))}
        for m in messages if m.get("role") in ("user", "assistant")
    ]
    resp = await asyncio.wait_for(
        _gr().chat.completions.create(
            model=GROQ_MODEL, messages=msgs, max_tokens=max_out,
        ),
        timeout=timeout,
    )
    return resp.choices[0].message.content

async def sam_think(messages, timeout=120):
    system, rest = _split_system(messages)
    contents = _to_gemini_contents(rest)
    now = time.time()
    t0 = time.time()
    intento = 0
    traza = []

    for model in GEMINI_CASCADE:
        if not _available(model, now):
            continue
        intento += 1
        ti = time.time()
        try:
            out = await _call_gemini(model, system, contents, INTENTO_TIMEOUT)
            dt = int((time.time() - ti) * 1000)
            if out and out.strip():
                _cooldown.pop(model, None)
                traza.append((model, dt, "ok"))
                _pulso.update({"modelo": model, "ms": int((time.time()-t0)*1000),
                               "intento": intento, "ts": time.time(), "traza": traza})
                return out
            traza.append((model, dt, "vacío"))
        except Exception as e:
            dt = int((time.time() - ti) * 1000)
            kind, until = _classify(str(e))
            _cooldown[model] = {"until": until, "kind": kind,
                                "set_at": time.time(), "last_probe": time.time()}
            traza.append((model, dt, f"fail:{kind}"))
            logger.warning("[Cascada] %s en cooldown (%s) hasta %s.",
                           model, kind,
                           datetime.fromtimestamp(until).strftime("%H:%M"))

    try:
        ti = time.time()
        out = await _call_groq(system, rest, INTENTO_TIMEOUT)
        dt = int((time.time() - ti) * 1000)
        if out and out.strip():
            traza.append(("GROQ", dt, "ok"))
            _pulso.update({"modelo": "GROQ " + GROQ_MODEL,
                           "ms": int((time.time()-t0)*1000),
                           "intento": intento, "ts": time.time(), "traza": traza})
            return out
    except Exception as e:
        traza.append(("GROQ", 0, f"fail:{type(e).__name__}"))
        logger.error("[Cascada] Groq también falló: %s", type(e).__name__)

    _pulso.update({"modelo": "NINGUNO", "ms": int((time.time()-t0)*1000),
                   "intento": intento, "ts": time.time(), "traza": traza})
    raise RuntimeError("Todos los modelos de la cascada fallaron.")

async def sam_think_reflexion(messages, timeout=30):
    """Subconsciente: solo Lite/Groq. NUNCA compite con la conversación."""
    system, rest = _split_system(messages)
    contents = _to_gemini_contents(rest)
    for model in REFLEXION_CASCADE:
        try:
            out = await _call_gemini(model, system, contents, timeout, max_out=800)
            if out and out.strip():
                return out
        except Exception as e:
            logger.warning("[Reflexión] %s falló (%s).", model, type(e).__name__)
    try:
        return await _call_groq(system, rest, timeout, max_out=800)
    except Exception as e:
        logger.error("[Reflexión] Groq falló: %s", type(e).__name__)
        return ""

def cascada_status():
    now = time.time()
    lines = []
    for model in GEMINI_CASCADE:
        info = _cooldown.get(model)
        if info and now < info["until"]:
            mins = int((info["until"] - now) / 60)
            lines.append(f"🔴 {model} — cooldown ({info['kind']}) ~{mins}min")
        else:
            lines.append(f"🟢 {model} — disponible")
    return "\n".join(lines)

def pulso_actual():
    if not _pulso["modelo"]:
        return "Aún no ha respondido nada desde que encendió."
    traza = "\n".join(
        f"   {m} → {ms}ms {('✅' if r=='ok' else '❌ '+r)}"
        for m, ms, r in _pulso["traza"]
    ) or "   (sin traza)"
    return (f"Última respuesta: {_pulso['modelo']}\n"
            f"Tardó: {_pulso['ms']/1000:.1f} s · intentos: {_pulso['intento']}\n"
            f"Traza del último mensaje:\n{traza}")