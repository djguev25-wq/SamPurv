"""Investigación web y lectura de enlaces para Sam.

- buscar(): Tavily si hay clave; si no, DuckDuckGo (ddgs). Evita repetir enlaces por canal.
- leer_enlace(): YouTube, Google Docs/Sheets/Slides públicos, GitHub, MediaFire y páginas web.
- Protección SSRF: solo http/https, se bloquean IPs privadas/locales y se validan redirecciones.
Todas las funciones son síncronas; el bot las llama con asyncio.to_thread.
"""
import ipaddress
import logging
import os
import re
import socket
from collections import defaultdict, deque
from html.parser import HTMLParser
from urllib.parse import urlparse, urljoin

import requests

logger = logging.getLogger("sam.investigador")

TIMEOUT = 12
MAX_BYTES = 2_000_000
MAX_TEXTO = int(os.getenv("SAM_MAX_TEXTO_ENLACE", "6000"))
UA = {"User-Agent": "Mozilla/5.0 (compatible; SamBot/1.0)"}
_URL_RE = re.compile(r"https?://[^\s<>()\"']+")
_vistos = defaultdict(lambda: deque(maxlen=40))   # canal -> enlaces ya entregados


def extraer_urls(texto: str) -> list[str]:
    vistas, salida = set(), []
    for u in _URL_RE.findall(texto or ""):
        u = u.rstrip(".,;:!?)")
        if u not in vistas:
            vistas.add(u)
            salida.append(u)
    return salida


# ───────────────────────── seguridad ─────────────────────────
def url_segura(url: str) -> tuple[bool, str]:
    """Permite solo http/https hacia direcciones públicas."""
    try:
        p = urlparse(url)
    except ValueError:
        return False, "URL inválida"
    if p.scheme not in ("http", "https") or not p.hostname:
        return False, "solo se admiten enlaces http/https"
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80))
    except OSError:
        return False, "no se pudo resolver el dominio"
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            return False, "dirección no permitida"
    return True, ""


def _get(url: str, accept: str | None = None, max_redirects: int = 3):
    """GET con validación SSRF en cada salto y límite de tamaño. Devuelve (texto, content_type)."""
    headers = dict(UA)
    if accept:
        headers["Accept"] = accept
    actual = url
    for _ in range(max_redirects + 1):
        ok, motivo = url_segura(actual)
        if not ok:
            raise ValueError(motivo)
        r = requests.get(actual, headers=headers, timeout=TIMEOUT, stream=True, allow_redirects=False)
        if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("Location"):
            actual = urljoin(actual, r.headers["Location"])
            r.close()
            continue
        r.raise_for_status()
        ctype = r.headers.get("Content-Type", "")
        datos, total = [], 0
        for chunk in r.iter_content(65536):
            total += len(chunk)
            if total > MAX_BYTES:
                break
            datos.append(chunk)
        r.close()
        crudo = b"".join(datos)
        return crudo.decode(r.encoding or "utf-8", errors="replace"), ctype
    raise ValueError("demasiadas redirecciones")


# ───────────────────────── HTML → texto ─────────────────────────
class _Texto(HTMLParser):
    _OMITIR = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form"}

    def __init__(self):
        super().__init__()
        self.partes, self.titulo, self._skip, self._en_titulo = [], "", 0, False

    def handle_starttag(self, tag, attrs):
        if tag in self._OMITIR:
            self._skip += 1
        if tag == "title":
            self._en_titulo = True

    def handle_endtag(self, tag):
        if tag in self._OMITIR and self._skip:
            self._skip -= 1
        if tag == "title":
            self._en_titulo = False
        if tag in ("p", "div", "br", "li", "h1", "h2", "h3", "tr"):
            self.partes.append("\n")

    def handle_data(self, data):
        if self._en_titulo:
            self.titulo += data
        elif not self._skip and data.strip():
            self.partes.append(data.strip() + " ")


def html_a_texto(html: str) -> tuple[str, str]:
    p = _Texto()
    try:
        p.feed(html)
    except Exception:
        pass
    texto = re.sub(r"\n\s*\n+", "\n", "".join(p.partes)).strip()
    return p.titulo.strip(), texto


# ───────────────────────── lectores por sitio ─────────────────────────
def _youtube(url: str) -> str:
    out = []
    try:
        r = requests.get("https://www.youtube.com/oembed", params={"url": url, "format": "json"},
                         headers=UA, timeout=TIMEOUT)
        if r.ok:
            d = r.json()
            out.append(f"YouTube — título: {d.get('title')} | canal: {d.get('author_name')}")
    except requests.RequestException:
        pass
    try:  # transcripción opcional (pip install youtube-transcript-api)
        from youtube_transcript_api import YouTubeTranscriptApi
        m = re.search(r"(?:v=|youtu\.be/|shorts/)([\w-]{11})", url)
        if m:
            api = YouTubeTranscriptApi()
            fragmentos = api.fetch(m.group(1), languages=["es", "en"])
            out.append("Transcripción: " + " ".join(f.text for f in fragmentos)[:MAX_TEXTO])
    except Exception:
        out.append("(No se pudo obtener la transcripción; solo hay título y canal.)")
    return "\n".join(out) or "No pude leer ese video de YouTube."


def _google(url: str) -> str:
    m = re.search(r"docs\.google\.com/(document|spreadsheets|presentation)/d/([\w-]+)", url)
    if not m:
        return "Enlace de Google Drive no compatible (archivos binarios no se pueden leer)."
    tipo, ident = m.groups()
    formato = {"document": "txt", "spreadsheets": "csv", "presentation": "txt"}[tipo]
    try:
        texto, _ = _get(f"https://docs.google.com/{tipo}/d/{ident}/export?format={formato}")
        return texto[:MAX_TEXTO]
    except Exception:
        return "No pude leer el documento: debe estar compartido como «cualquiera con el enlace»."


def _github(url: str) -> str:
    p = urlparse(url)
    partes = [x for x in p.path.split("/") if x]
    if len(partes) >= 5 and partes[2] == "blob":
        raw = f"https://raw.githubusercontent.com/{partes[0]}/{partes[1]}/{partes[3]}/{'/'.join(partes[4:])}"
        texto, _ = _get(raw)
        return f"Archivo {'/'.join(partes[4:])} de {partes[0]}/{partes[1]}:\n{texto[:MAX_TEXTO]}"
    if len(partes) >= 2:
        texto, _ = _get(f"https://api.github.com/repos/{partes[0]}/{partes[1]}/readme",
                        accept="application/vnd.github.raw+json")
        return f"README de {partes[0]}/{partes[1]}:\n{texto[:MAX_TEXTO]}"
    return "Enlace de GitHub sin repositorio."


def leer_enlace(url: str) -> str:
    """Devuelve texto legible de un enlace o un mensaje honesto de por qué no se pudo."""
    host = (urlparse(url).hostname or "").lower()
    try:
        if host.endswith(("youtube.com", "youtu.be")):
            return _youtube(url)
        if host == "docs.google.com":
            return _google(url)
        if host == "drive.google.com":
            return "Drive: solo puedo leer Google Docs/Sheets/Slides compartidos, no archivos subidos."
        if host in ("github.com", "www.github.com"):
            return _github(url)
        cuerpo, ctype = _get(url)
        if "html" in ctype or cuerpo.lstrip().startswith("<"):
            titulo, texto = html_a_texto(cuerpo)
            if host.endswith("mediafire.com"):
                return (f"MediaFire — página: {titulo}. Solo puedo ver el nombre; no descargo archivos "
                        "desde esa página.")
            return f"Página: {titulo}\n{texto[:MAX_TEXTO]}"
        return cuerpo[:MAX_TEXTO]
    except ValueError as exc:
        return f"No leí ese enlace ({exc})."
    except requests.RequestException as exc:
        return f"No pude abrir el enlace ({type(exc).__name__})."


# ───────────────────────── búsqueda ─────────────────────────
def _tavily(consulta: str, n: int) -> list[dict]:
    clave = os.getenv("TAVILY_API_KEY")
    if not clave:
        return []
    r = requests.post("https://api.tavily.com/search", timeout=TIMEOUT,
                      json={"api_key": clave, "query": consulta, "search_depth": "advanced",
                            "max_results": n, "include_answer": False})
    r.raise_for_status()
    return [{"titulo": x.get("title", ""), "url": x.get("url", ""),
             "resumen": (x.get("content") or "")[:400]} for x in r.json().get("results", [])]


def _ddg(consulta: str, n: int) -> list[dict]:
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        return [{"titulo": x.get("title", ""), "url": x.get("href", ""),
                 "resumen": (x.get("body") or "")[:400]} for x in DDGS().text(consulta, max_results=n)]
    except Exception as exc:
        logger.warning("[Búsqueda] DuckDuckGo falló (%s).", type(exc).__name__)
        return []


def buscar(consulta: str, canal_id=0, n: int = 5) -> list[dict]:
    """Busca en la web; no repite enlaces ya entregados en el mismo canal."""
    resultados = []
    try:
        resultados = _tavily(consulta, n * 2)
    except requests.RequestException as exc:
        logger.warning("[Búsqueda] Tavily falló (%s).", type(exc).__name__)
    if not resultados:
        resultados = _ddg(consulta, n * 2)
    nuevos = [r for r in resultados if r["url"] and r["url"] not in _vistos[canal_id]]
    elegidos = (nuevos or resultados)[:n]
    for r in elegidos:
        _vistos[canal_id].append(r["url"])
    return elegidos


def buscar_texto(consulta: str, canal_id=0, n: int = 5) -> str:
    res = buscar(consulta, canal_id, n)
    if not res:
        return "No encontré resultados fiables para esa búsqueda."
    return "\n".join(f"- {r['titulo']} ({r['url']}): {r['resumen']}" for r in res)
