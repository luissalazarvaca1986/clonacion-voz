# -*- coding: utf-8 -*-
"""El resultado de una tarea solo aparece en el historial cuando está terminado."""
from __future__ import annotations

import sys
import threading
import time

from app.nucleo.configuracion import CONFIG_POR_DEFECTO
from app.servicios.entorno import RutasEfectivas
from app.servicios.sintesis import Pieza
from tests.conftest import wav_tono


def _llama_tts_falso(tmp_path):
    """Script que imita a llama-tts: escribe un WAV en la ruta de -o."""
    tono = tmp_path / "tono.wav"
    tono.write_bytes(wav_tono(0.5))
    script = tmp_path / "llama-tts-falso"
    script.write_text(
        f"#!{sys.executable}\n"
        "import shutil, sys\n"
        "args = sys.argv[1:]\n"
        f"shutil.copyfile({str(tono)!r}, args[args.index('-o') + 1])\n")
    script.chmod(0o755)
    return RutasEfectivas(str(script), "m.gguf", "mm.gguf")


def test_resultado_a_medias_no_se_ve_ni_se_borra(cliente, rutas, tmp_path):
    s = cliente.app.state.servicios
    tts = _llama_tts_falso(tmp_path)
    componiendo, seguir = threading.Event(), threading.Event()

    def video_lento(tarea, partes, audio, carpeta, destino):
        componiendo.set()
        seguir.wait(10)                        # el «video» tarda: aquí el usuario borra cosas
        for parte in partes:
            parte.video = carpeta / f"segmento-{parte.indice:02d}.mp4"
            parte.video.write_bytes(b"mp4")
        destino.write_bytes(b"mp4")

    cfg = dict(CONFIG_POR_DEFECTO)
    piezas = [Pieza(texto="hola", cfg=cfg, pausa_ms=150, imagen=tmp_path / "f.png", segmento=1,
                    avatar="Luis", escena="noche", emocion="alegria", intensidad="alta"),
              Pieza(texto="adiós", cfg=cfg, pausa_ms=150, imagen=tmp_path / "f.png", segmento=2,
                    avatar="Luis", escena="lluvia")]
    destino = s.salidas.nueva_ruta("abc123")

    def publicar(audio, video, partes):
        s.salidas.publicar(destino, audio, video, partes, "hola adiós")

    s.tareas.lanzar("abc123", tts, lambda _t: piezas, 2, "", destino,
                    s.salidas.ruta_video_para(destino), video_lento, publicar)

    assert componiendo.wait(10)
    # Mientras se compone el video, el audio no está en el historial…
    assert cliente.get("/api/salidas").json() == []
    # …y borrarlo desde la web no rompe la tarea
    cliente.delete(f"/api/salidas/{destino.name}")
    seguir.set()

    for _ in range(100):
        tarea = s.tareas.obtener("abc123")
        if tarea.estado != "en_curso":
            break
        time.sleep(0.05)
    assert tarea.estado == "terminada", tarea.error
    lista = cliente.get("/api/salidas").json()
    assert [x["archivo"] for x in lista] == [destino.name]
    assert lista[0]["video"] == destino.with_suffix(".mp4").name
    assert lista[0]["titulo"] == "hola adiós"
    segmentos = lista[0]["segmentos"]
    assert [(p["indice"], p["texto"], p["escena"], p["audio"], p["video"]) for p in segmentos] == [
        (1, "hola", "noche", "segmento-01.wav", "segmento-01.mp4"),
        (2, "adiós", "lluvia", "segmento-02.wav", "segmento-02.mp4")]
    assert segmentos[0]["emocion"] == "alegria"
    # Cada parte se descarga; su audio dura lo que su trozo del audio final
    generacion = destino.stem
    audio1 = cliente.get(f"/api/salidas/{generacion}/partes/segmento-01.wav")
    assert audio1.status_code == 200 and audio1.headers["content-type"] == "audio/wav"
    assert cliente.get(f"/api/salidas/{generacion}/partes/segmento-02.mp4").status_code == 200
    assert cliente.get(f"/api/salidas/{generacion}/partes/..%2Fx.wav").status_code == 404
    assert abs(segmentos[0]["duracion"] - 0.65) < 0.05   # 0,5 s de voz + 150 ms de pausa
    assert not list(rutas.tmp.iterdir())       # sin restos en la carpeta temporal

    # Borrar la generación se lleva todo: audio, video, partes y manifiesto
    cliente.delete(f"/api/salidas/{destino.name}")
    assert not any(rutas.salidas.iterdir()) and not any(rutas.videos.iterdir())
