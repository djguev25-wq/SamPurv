"""Contexto temporal del servidor Discord para las respuestas de Sam."""
import asyncio
import logging
import re
import discord # Necesario para discord.Status

logger = logging.getLogger("sam.server_context")
_CONTEXT_MARKER = "[DATOS ACTUALES DEL SERVIDOR DISCORD]"
_CHUNK_ATTEMPTED_GUILDS: set[int] = set()

async def _ensure_member_cache(guild) -> None:
    """Intenta cargar la lista de miembros una vez por servidor y ejecución."""
    if guild is None or getattr(guild, "chunked", False):
        return
    if guild.id in _CHUNK_ATTEMPTED_GUILDS:
        return
    _CHUNK_ATTEMPTED_GUILDS.add(guild.id)
    try:
        await asyncio.wait_for(guild.chunk(cache=True), timeout=8)
    except Exception as exc:
        logger.warning(
            "No se pudo completar la caché de miembros para guild_id=%s (%s).",
            guild.id, type(exc).__name__,
        )

def _get_detailed_member_status(guild) -> dict:
    """Analiza los miembros en caché para separar humanos/bots y online/offline."""
    humans_online = 0
    humans_offline = 0
    bots_online = 0
    bots_offline = 0
    
    members = list(getattr(guild, "members", []) or [])
    
    for member in members:
        is_bot = getattr(member, "bot", False)
        status = getattr(member, "status", discord.Status.offline)
        is_online = status in [discord.Status.online, discord.Status.idle, discord.Status.dnd]
        
        if is_bot:
            if is_online:
                bots_online += 1
            else:
                bots_offline += 1
        else:
            if is_online:
                humans_online += 1
            else:
                humans_offline += 1
                
    return {
        "humans_online": humans_online,
        "humans_offline": humans_offline,
        "bots_online": bots_online,
        "bots_offline": bots_offline,
        "total_members_in_cache": len(members)
    }

def _looks_like_server_list_question(query: str) -> bool:
    """Detecta preguntas específicas sobre la lista de servidores donde está conectada."""
    text = re.sub(r"<@!?\d+>", "", (query or "").lower())
    keywords = ["servidores", "lista de servidores", "en que estas", "donde estas", "conectada"]
    return any(k in text for k in keywords)

async def add_server_context(messages: list[dict], guild, channel=None, bot_ref=None) -> list[dict]:
    """Añade datos actuales al sistema SOLO SI SON RELEVANTES PARA LA PREGUNTA."""
    if not messages:
        return messages
        
    facts_blocks = []
    
    # 1. Datos del servidor ACTUAL (si existe)
    if guild is not None:
        await _ensure_member_cache(guild)
        stats = _get_detailed_member_status(guild)
        channel_name = getattr(channel, "name", None) or "no disponible"
        
        current_server_facts = (
            f"{_CONTEXT_MARKER}\n"
            f"NOMBRE DEL SERVIDOR ACTUAL: '{guild.name}'\n"
            f"ID DEL SERVIDOR ACTUAL: {guild.id}\n"
            f"CANAL ACTUAL: #{channel_name}\n\n"
            f"ESTADÍSTICAS DE MIEMBROS EN ESTE SERVIDOR:\n"
            f"- Humanos ONLINE: {stats['humans_online']}\n"
            f"- Humanos OFFLINE: {stats['humans_offline']}\n"
            f"- Bots ONLINE: {stats['bots_online']}\n"
            f"- Bots OFFLINE: {stats['bots_offline']}\n\n"
            "REGLA CRÍTICA DE PRIVACIDAD: Nunca menciones nombres de otros usuarios del servidor "
            "en tus respuestas generales. Solo usa sus nombres si te diriges específicamente a ellos "
            "o si te preguntan por ellos explícitamente. No reveles quién está online a terceros."
        )
        facts_blocks.append(current_server_facts)

    # 2. Datos GLOBALES (Lista de servidores) - CRUCIAL PARA DM O PREGUNTAS ESPECÍFICAS
    latest_user_msg = next((item for item in reversed(messages) if item.get("role") == "user"), None)
    query_text = latest_user_msg["content"] if latest_user_msg else ""
    
    # Si es un DM (guild is None) O si pregunta explícitamente por la lista de servidores
    is_dm = guild is None
    wants_list = _looks_like_server_list_question(query_text)
    
    if bot_ref and (is_dm or wants_list):
        server_names = [g.name for g in bot_ref.guilds]
        global_facts = (
            f"[LISTA GLOBAL DE SERVIDORES DONDE VIVO]\n"
            f"Estoy conectada a {len(server_names)} servidores en total:\n"
            + "\n".join(f"- {name}" for name in server_names)
            + "\n\nINSTRUCCIÓN: Usa esta lista para responder dónde estoy. No inventes servidores."
        )
        facts_blocks.append(global_facts)

    full_facts = "\n\n".join(facts_blocks)
    
    if not full_facts:
        return messages

    system_message = next((item for item in messages if item.get("role") == "system"), None)
    if system_message is not None:
        original = str(system_message.get("content", ""))
        if _CONTEXT_MARKER not in original:
            system_message["content"] = original + "\n\n" + full_facts
    else:
        messages.insert(0, {"role": "system", "content": full_facts})
        
    # Repetir hechos junto a la última pregunta ayuda a priorizarlos
    if latest_user_msg is not None:
        latest_content = str(latest_user_msg.get("content", ""))
        if _CONTEXT_MARKER not in latest_content:
            latest_user_msg["content"] = full_facts + "\n\nPregunta de la persona: " + latest_content
            
    return messages