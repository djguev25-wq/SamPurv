import datetime
import pytz
import requests
import os
import logging

logger = logging.getLogger("sam.tools")

def get_current_date():
    """Devuelve la fecha actual formateada."""
    now = datetime.datetime.now()
    return now.strftime("%d/%m/%Y")

def get_world_time(city_or_timezone=None):
    """
    Devuelve la hora actual. Si se provee una zona horaria o ciudad,
    intenta obtener la hora de ese lugar. Por defecto usa la hora de Perú (Cajamarca/Lima).
    """
    try:
        # Mapeo de nombres comunes a zonas horarias oficiales de pytz
        tz_map = {
            "peru": "America/Lima",
            "perú": "America/Lima",
            "lima": "America/Lima",
            "cajamarca": "America/Lima", # Cajamarca usa la misma zona que Lima
            "mexico": "America/Mexico_City",
            "méxico": "America/Mexico_City",
            "espana": "Europe/Madrid",
            "españa": "Europe/Madrid",
            "madrid": "Europe/Madrid",
            "argentina": "America/Argentina/Buenos_Aires",
            "buenos aires": "America/Argentina/Buenos_Aires",
            "colombia": "America/Bogota",
            "bogota": "America/Bogota",
            "chile": "America/Santiago",
            "santiago": "America/Santiago",
            "usa": "America/New_York",
            "estados unidos": "America/New_York",
            "new york": "America/New_York",
        }
        
        if city_or_timezone:
            query = city_or_timezone.lower().strip()
            # Intentamos buscar en el mapa primero, luego probamos la cadena original
            tz_name = tz_map.get(query, query)
            tz = pytz.timezone(tz_name)
            now = datetime.datetime.now(tz)
        else:
            # Por defecto, si no se especifica nada, usamos la hora de Perú (tu hora)
            tz = pytz.timezone("America/Lima")
            now = datetime.datetime.now(tz)
            
        return now.strftime("%H:%M:%S")
    except Exception as e:
        logger.error(f"Error obteniendo la hora: {e}")
        return "No pude determinar la hora en ese lugar."

def deep_search(query):
    """Búsqueda web con tiempo límite: Tavily si hay clave; si no, DuckDuckGo."""
    import investigador
    try:
        return investigador.buscar_texto(query)
    except Exception as e:
        logger.error(f"Error en búsqueda profunda: {e}")
        return "No pude completar la búsqueda en este momento."
