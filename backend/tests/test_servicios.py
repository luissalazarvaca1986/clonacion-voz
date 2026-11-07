# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import wave

from app.nucleo.configuracion import CONFIG_POR_DEFECTO, RepositorioConfig
from app.servicios.audio import duracion_wav, unir_wavs
from app.servicios.entorno import RutasEfectivas
from app.servicios.sintesis import construir_comando
from app.servicios.texto import trocear_texto
from tests.conftest import wav_tono


# ---- Troceado ----------------------------------------------------------------
def test_texto_corto_es_un_bloque():
    assert trocear_texto("  Hola   mundo  ", 280) == ["Hola mundo"]


def test_texto_vacio_no_da_bloques():
    assert trocear_texto("   \n ", 280) == []


def test_trocea_por_frases_sin_pasar_el_maximo():
    texto = " ".join(f"Esta es la frase número {i}." for i in range(40))
    bloques = trocear_texto(texto, 100)
    assert len(bloques) > 1
    assert all(len(b) <= 100 for b in bloques)
    assert " ".join(bloques) == texto


def test_frase_gigante_se_corta_por_espacios():
    texto = "palabra " * 100
    bloques = trocear_texto(texto, 50)
    assert all(len(b) <= 50 for b in bloques)
    assert " ".join(bloques).split() == texto.split()


# ---- Audio -------------------------------------------------------------------
def test_unir_wavs_suma_duraciones_y_pausas(tmp_path):
    partes = []
    for i in range(3):
        p = tmp_path / f"p{i}.wav"
        p.write_bytes(wav_tono(0.5))
        partes.append(p)
    destino = tmp_path / "todo.wav"
    unir_wavs(partes, destino, pausa_ms=100)
    assert abs(duracion_wav(destino) - (1.5 + 0.2)) < 0.01
    with wave.open(str(destino)) as w:
        assert w.getnchannels() == 1 and w.getframerate() == 16000


def test_duracion_de_archivo_roto_es_cero(tmp_path):
    roto = tmp_path / "roto.wav"
    roto.write_bytes(b"no es un wav")
    assert duracion_wav(roto) == 0.0


# ---- Configuración -----------------------------------------------------------
def test_config_por_defecto_sin_archivo(tmp_path):
    assert RepositorioConfig(tmp_path / "config.json").cargar() == CONFIG_POR_DEFECTO


def test_config_guarda_solo_claves_conocidas_y_coacciona(tmp_path):
    repo = RepositorioConfig(tmp_path / "config.json")
    cfg = repo.actualizar({"temp": "0.6", "top_k": 50.0, "inventada": 1})
    assert cfg["temp"] == 0.6 and cfg["top_k"] == 50
    guardado = json.loads((tmp_path / "config.json").read_text("utf-8"))
    assert "inventada" not in guardado
    assert repo.cargar()["temp"] == 0.6


def test_config_corrupta_vuelve_a_los_defectos(tmp_path):
    archivo = tmp_path / "config.json"
    archivo.write_text("{ esto no es json", "utf-8")
    assert RepositorioConfig(archivo).cargar() == CONFIG_POR_DEFECTO
    archivo.write_text(json.dumps({"top_k": "mucho"}), "utf-8")
    assert RepositorioConfig(archivo).cargar()["top_k"] == CONFIG_POR_DEFECTO["top_k"]


# ---- Comando llama-tts -------------------------------------------------------
RUTAS = RutasEfectivas("/bin/llama-tts", "/m/modelo.gguf", "/m/mmproj.gguf")


def test_comando_gpu_lleva_mmdev(tmp_path):
    cmd = construir_comando(RUTAS, dict(CONFIG_POR_DEFECTO), "hola", tmp_path / "o.wav",
                            None, "CUDA0")
    assert cmd[cmd.index("--device") + 1] == "CUDA0"
    assert cmd[cmd.index("-mmdev") + 1] == "CUDA0"
    assert cmd[cmd.index("-c") + 1] == "2048"
    assert "--tts-speaker-file" not in cmd


def test_comando_cpu_y_referencia(tmp_path):
    cfg = dict(CONFIG_POR_DEFECTO, semilla=7, hilos=4)
    ref = tmp_path / "ref.wav"
    cmd = construir_comando(RUTAS, cfg, "hola", tmp_path / "o.wav", ref, "")
    assert cmd[cmd.index("-ngl") + 1] == "0"
    assert "--no-mmproj-offload" in cmd and "-mmdev" not in cmd
    assert cmd[cmd.index("-s") + 1] == "7"
    assert cmd[cmd.index("-t") + 1] == "4"
    assert cmd[cmd.index("--tts-speaker-file") + 1] == str(ref)
