import os
import mimetypes

from google import genai
from config import GEMINI_API_KEY, GEMINI_VISION_MODELS


async def analyze_image(image_path: str) -> str:
    """
    Analiza una imagen usando Gemini Vision y devuelve
    una descripción objetiva en español.
    """

    if not os.path.exists(image_path):
        return "No pude encontrar la imagen."

    if not GEMINI_API_KEY:
        return "No tengo configurada la clave de Gemini."

    try:
        mime_type, _ = mimetypes.guess_type(image_path)

        if mime_type is None:
            mime_type = "image/jpeg"

        client = genai.Client(api_key=GEMINI_API_KEY)

        from google.genai import types

        with open(image_path, "rb") as image_file:
            image_bytes = image_file.read()

        image_part = types.Part.from_bytes(
            data=image_bytes,
            mime_type=mime_type
        )

        for model in GEMINI_VISION_MODELS:
            try:
                print(f"[VISION] Probando {model}")

                response = client.models.generate_content(
                    model=model,
                   contents=[
                       """
                        TAREA OBLIGATORIA:

                        La imagen puede contener varias imágenes, paneles, viñetas,
                        capturas o un collage.

                        NO hagas un resumen general.

                        Debes analizar la imagen sección por sección.

                        Formato obligatorio:

                        SECCIÓN 1:
                        - Personajes:
                        - Objetos:
                        - Lugar:

                        SECCIÓN 2:
                        - Personajes:
                        - Objetos:
                        - Lugar:

                        SECCIÓN 3:
                        - Personajes:
                        - Objetos:
                        - Lugar:

                        SECCIÓN 4:
                        - Personajes:
                        - Objetos:
                        - Lugar:

                        Identifica TODOS los personajes visibles.

                        No ignores personajes pequeños.

                        No te concentres solamente en la imagen principal.

                        Describe todas las zonas visibles antes de finalizar.

                        Responde solamente en español.
                        """,
                       image_part
                   ]
                )

                if hasattr(response, "text") and response.text:
                    descripcion = response.text.strip()

                    print(f"[VISION] Respondió {model}")
                    print(f"[VISION] Original: {len(descripcion)}")

                    MAX_DISCORD = 3800

                    if len(descripcion) > MAX_DISCORD:
                        descripcion = descripcion[:MAX_DISCORD]
                        print("[VISION] Texto recortado")

                    print(f"[VISION] Final: {len(descripcion)}")

                    return descripcion

            except Exception as e:
                print(f"[VISION] Falló {model}: {e}")

        return "No pude analizar la imagen con ningún modelo de visión."

    except Exception as e:
        return f"Error al analizar la imagen: {str(e)}"