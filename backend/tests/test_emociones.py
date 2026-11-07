# -*- coding: utf-8 -*-
from __future__ import annotations

from contextlib import nullcontext

from app.nucleo.configuracion import CONFIG_POR_DEFECTO
from app.servicios.analisis import AnalizadorEmocional
from app.servicios.audio import concatenar, duracion_wav, procesar_pieza
from app.servicios.emociones import (ajustes_segmento, a_marcas, dividir_frases, filtros_prosodia,
                                     fusionar_consecutivos, parsear_marcas, tiene_marcas)
from app.servicios.generacion import piezas_emocionales
from app.servicios.llm import LLMNoDisponible
from tests.conftest import wav_tono

CFG = dict(CONFIG_POR_DEFECTO)
EJEMPLO = ("Estamos aquí presentando este escenario. ¡Qué emoción que tengo, cuánta alegría! "
           "Pero esto es una realidad que no esperábamos.")


class LLMFalso:
    """Sustituye a ClienteLLM: devuelve etiquetas fijas y registra lo que se le pide."""

    def __init__(self, etiquetas=None, falla=False):
        self.etiquetas, self.falla, self.peticiones = etiquetas, falla, []

    def json_estructurado(self, sistema, usuario, esquema, max_tokens=1024):
        if self.falla:
            raise LLMNoDisponible("caído")
        self.peticiones.append((usuario, esquema))
        n = esquema["properties"]["etiquetas"]["minItems"]
        return {"etiquetas": (self.etiquetas or ["neutro:media"] * n)[:n]}

    def modelo(self):
        return "falso"

    def sesion(self):
        return nullcontext()


# ---- Perfiles ----------------------------------------------------------------
def test_neutro_respeta_los_ajustes_base():
    a = ajustes_segmento(CFG, "neutro", "alta")
    assert (a["temp"], a["top_p"], a["top_k"]) == (CFG["temp"], CFG["top_p"], CFG["top_k"])
    assert (a["ritmo"], a["tono"], a["volumen_db"]) == (1.0, 0.0, 0.0)
    assert filtros_prosodia(a) == ""


def test_intensidad_escala_el_perfil():
    baja, alta = ajustes_segmento(CFG, "alegria", "baja"), ajustes_segmento(CFG, "alegria", "alta")
    assert CFG["temp"] < baja["temp"] < alta["temp"]
    assert 1 < baja["ritmo"] < alta["ritmo"]
    triste = ajustes_segmento(CFG, "tristeza", "alta")
    assert triste["ritmo"] < 1 and triste["tono"] < 0 and triste["pausa_ms"] > alta["pausa_ms"]


def test_ajustes_se_quedan_en_rango():
    a = ajustes_segmento(dict(CFG, temp=1.45, top_p=0.99), "entusiasmo", "alta")
    assert a["temp"] <= 1.5 and a["top_p"] <= 1.0


def test_filtros_de_prosodia():
    f = filtros_prosodia(ajustes_segmento(CFG, "tristeza", "alta"))
    assert f.startswith("rubberband=tempo=0.88") and "volume=-2.50dB" in f


# ---- Marcas y frases ---------------------------------------------------------
def test_marcas_manuales():
    texto = "Hola. [alegria:alta] ¡Qué bien! [Tristeza] Qué pena. [nota] sigue triste"
    assert tiene_marcas(texto)
    assert parsear_marcas(texto) == [
        {"texto": "Hola.", "emocion": "neutro", "intensidad": "media"},
        {"texto": "¡Qué bien!", "emocion": "alegria", "intensidad": "alta"},
        {"texto": "Qué pena. [nota] sigue triste", "emocion": "tristeza", "intensidad": "media"},
    ]
    assert not tiene_marcas("Un texto con [corchetes] normales")


def test_a_marcas_ida_y_vuelta():
    segs = parsear_marcas("[calma:baja] Tranquilos. [enojo:alta] ¡Basta!")
    assert parsear_marcas(a_marcas(segs)) == segs


def test_dividir_frases_y_fusionar():
    assert dividir_frases(EJEMPLO) == [
        "Estamos aquí presentando este escenario.",
        "¡Qué emoción que tengo, cuánta alegría!",
        "Pero esto es una realidad que no esperábamos."]
    segs = [{"texto": "a", "emocion": "neutro", "intensidad": "media"},
            {"texto": "b", "emocion": "neutro", "intensidad": "media"},
            {"texto": "c", "emocion": "alegria", "intensidad": "alta"}]
    assert fusionar_consecutivos(segs) == [
        {"texto": "a b", "emocion": "neutro", "intensidad": "media"},
        {"texto": "c", "emocion": "alegria", "intensidad": "alta"}]


# ---- Analizador --------------------------------------------------------------
def test_analizador_no_cambia_el_texto_y_agrupa():
    llm = LLMFalso(["neutro:media", "entusiasmo:alta", "tristeza:media"])
    r = AnalizadorEmocional(llm).analizar(EJEMPLO)
    assert r["origen"] == "llm" and r["modelo"] == "falso"
    assert [s["emocion"] for s in r["segmentos"]] == ["neutro", "entusiasmo", "tristeza"]
    assert " ".join(s["texto"] for s in r["segmentos"]) == EJEMPLO
    usuario, esquema = llm.peticiones[0]
    assert "1. Estamos aquí" in usuario and esquema["properties"]["etiquetas"]["maxItems"] == 3


def test_analizador_tolera_etiquetas_raras_o_cortas():
    r = AnalizadorEmocional(LLMFalso(["furia:extrema"])).analizar(EJEMPLO)
    # etiqueta desconocida -> neutro; frases sin etiqueta -> neutro; todo se fusiona
    assert r["segmentos"] == [{"texto": EJEMPLO, "emocion": "neutro", "intensidad": "media"}]


def test_marcas_evitan_el_llm():
    llm = LLMFalso(falla=True)
    r = AnalizadorEmocional(llm).analizar("[alegria] ¡Bien!")
    assert r["origen"] == "marcas" and r["segmentos"][0]["emocion"] == "alegria"


# ---- Piezas y audio ----------------------------------------------------------
def test_piezas_emocionales_trocean_y_heredan_perfil():
    largo = "Frase de relleno bastante larga para trocear. " * 12
    piezas = piezas_emocionales(dict(CFG, chars_por_bloque=100), [
        {"texto": largo, "emocion": "tristeza", "intensidad": "alta"},
        {"texto": "¡Hola!", "emocion": "alegria", "intensidad": "media"}])
    tristes = [p for p in piezas if p.emocion == "tristeza"]
    assert len(tristes) > 1 and all(p.filtros == tristes[0].filtros for p in tristes)
    assert tristes[0].pausa_ms == CFG["pausa_ms"]          # dentro del segmento
    assert tristes[-1].pausa_ms > CFG["pausa_ms"]          # al cambiar de emoción
    assert tristes[0].cfg["temp"] < CFG["temp"] < piezas[-1].cfg["temp"]


def test_prosodia_y_concatenacion_con_ffmpeg(tmp_path):
    a, b = tmp_path / "a.wav", tmp_path / "b.wav"
    a.write_bytes(wav_tono(1.0))
    b.write_bytes(wav_tono(1.0))
    fa, fb = tmp_path / "fa.wav", tmp_path / "fb.wav"
    procesar_pieza(a, fa, filtros_prosodia(ajustes_segmento(CFG, "tristeza", "alta")), 500)
    procesar_pieza(b, fb, "", 0)
    # tristeza: ritmo 0.88 -> ~1.14 s + 0.5 s de pausa
    assert abs(duracion_wav(fa) - (1 / 0.88 + 0.5)) < 0.08
    destino = tmp_path / "final.wav"
    concatenar([fa, fb], destino)
    assert abs(duracion_wav(destino) - (duracion_wav(fa) + 1.0)) < 0.05
    assert not list(tmp_path.glob("*_lista.txt"))


# ---- Ajustes por segmento ----------------------------------------------------
def test_emocion_sugiere_expresion():
    assert ajustes_segmento(CFG, "neutro", "alta")["escala_expresion"] == 1.0
    assert ajustes_segmento(CFG, "entusiasmo", "alta")["escala_expresion"] == 1.5
    assert ajustes_segmento(CFG, "tristeza", "alta")["escala_expresion"] == 0.8
    assert ajustes_segmento(CFG, "alegria", "baja")["escala_expresion"] == 1.12


def test_ajustes_revisados_mandan_sobre_los_sugeridos():
    from app.servicios.emociones import completar_ajustes
    seg = {"texto": "Hola", "emocion": "tristeza", "intensidad": "alta",
           "ajustes": {"ritmo": 1.0, "escala_expresion": 2.0, "estilo_pose": 7, "inventado": 1}}
    a = completar_ajustes(CFG, seg)
    assert a["ritmo"] == 1.0 and a["escala_expresion"] == 2.0 and a["estilo_pose"] == 7
    assert a["tono"] < 0                      # lo no revisado sigue saliendo de la emoción
    assert "inventado" not in a
    piezas = piezas_emocionales(dict(CFG), [seg])
    assert piezas[0].escala_expresion == 2.0 and piezas[0].estilo_pose == 7
    assert "rubberband" in piezas[0].filtros and "tempo=1.0000" in piezas[0].filtros
