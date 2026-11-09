import math
import struct

import pytest

pytest.importorskip("edge_tts")
pytest.importorskip("requests")

import escucha  # noqa: E402
import investigador  # noqa: E402
import voice  # noqa: E402


def test_trocear_respeta_limite():
    trozos = voice.trocear("Esto es una frase. " * 100, 120)
    assert trozos and all(len(t) <= 120 for t in trozos)


def test_limpiar_para_voz():
    assert voice.limpiar_para_voz("**Hola** <@123> https://x.com 😀") == "Hola enlace"


def test_url_segura_bloquea_internas():
    for u in ("http://127.0.0.1/", "http://169.254.169.254/", "http://localhost/", "ftp://x.com"):
        assert investigador.url_segura(u)[0] is False


def test_extraer_urls_sin_duplicados():
    assert investigador.extraer_urls("a https://a.com/x, b https://a.com/x).") == ["https://a.com/x"]


def test_html_a_texto_omite_scripts():
    titulo, texto = investigador.html_a_texto("<title>T</title><script>x()</script><p>Hola</p>")
    assert titulo == "T" and texto == "Hola"


def test_rms_distingue_silencio_de_voz():
    tono = b"".join(struct.pack("<h", int(8000 * math.sin(i / 5))) for i in range(4800))
    assert escucha.rms(b"\x00\x00" * 4800) == 0 and escucha.rms(tono) > escucha.RMS_MIN


def test_filtro_alucinaciones_whisper():
    assert escucha._alucinacion("Subtítulos realizados por la comunidad de Amara.org")
    assert not escucha._alucinacion("Oye Sam, ¿qué hora es en Madrid?")
