# -*- coding: utf-8 -*-
"""Proyectos: se guardan con su configuración y se puede regenerar un segmento."""
from __future__ import annotations

import sys
import time
import wave

from app.repositorios.proyectos import RepositorioMemoria
from app.servicios.audio import duracion_wav
from tests.conftest import wav_tono


def _configurar_tts_falso(cliente, tmp_path):
    """llama-tts falso: escribe un tono cuya duración depende del texto (así se
    nota qué segmento cambió) y anuncia --mmproj en la ayuda."""
    script = tmp_path / "llama-tts"
    script.write_text(
        f"#!{sys.executable}\n"
        "import math, struct, sys, wave\n"
        "a = sys.argv[1:]\n"
        "if '--help' in a: print('--mmproj'); sys.exit()\n"
        "segundos = 0.1 * len(a[a.index('-p') + 1].split())\n"
        "w = wave.open(a[a.index('-o') + 1], 'wb'); w.setnchannels(1); w.setsampwidth(2)\n"
        "w.setframerate(24000)\n"
        "w.writeframes(b''.join(struct.pack('<h', int(8000 * math.sin(i / 9))) "
        "for i in range(int(segundos * 24000))))\n")
    script.chmod(0o755)
    for nombre in ("modelo.gguf", "mmproj.gguf"):
        (tmp_path / nombre).write_bytes(b"x")
    cliente.post("/api/config", json={"binario": str(script), "modelo": str(tmp_path / "modelo.gguf"),
                                      "mmproj": str(tmp_path / "mmproj.gguf"), "dispositivo": "cpu"})


def _esperar(cliente, id_tarea):
    s = cliente.app.state.servicios
    for _ in range(200):
        tarea = s.tareas.obtener(id_tarea)
        if tarea.estado != "en_curso":
            return tarea
        time.sleep(0.05)
    raise AssertionError("la tarea no terminó")


SEGMENTOS = [
    {"texto": "uno dos tres cuatro cinco", "emocion": "alegria", "intensidad": "alta"},
    {"texto": "seis siete", "emocion": "tristeza", "intensidad": "media"},
    {"texto": "ocho nueve diez", "emocion": "neutro"},
]


def test_generar_guarda_configuracion_y_regenerar_un_segmento(cliente, rutas, tmp_path):
    _configurar_tts_falso(cliente, tmp_path)
    r = cliente.post("/api/generar", json={"modo": "emocional", "segmentos": SEGMENTOS})
    assert r.status_code == 200, r.text
    tarea = _esperar(cliente, r.json()["id"])
    assert tarea.estado == "terminada", tarea.error

    id_proyecto = r.json()["archivo"].removesuffix(".wav")
    proyecto = cliente.get(f"/api/proyectos/{id_proyecto}").json()
    assert proyecto["id"] == id_proyecto
    assert proyecto["config"]["modo"] == "emocional"
    assert [s["emocion"] for s in proyecto["segmentos"]] == ["alegria", "tristeza", "neutro"]
    assert proyecto["segmentos"][0]["ajustes"]["ritmo"] > 1      # sugeridos guardados
    assert cliente.get("/api/salidas").json()[0]["editable"] is True

    partes = rutas.salidas / id_proyecto
    antes = {n: (partes / f"segmento-0{n}.wav").read_bytes() for n in (1, 3)}
    duracion_antes = duracion_wav(rutas.salidas / proyecto["audio"])

    # Segmento 2 con más texto y otra emoción: solo él se rehace
    r2 = cliente.post(f"/api/proyectos/{id_proyecto}/segmentos/2/regenerar",
                      json={"texto": "seis siete y muchas palabras más aquí", "emocion": "calma",
                            "intensidad": "baja", "ajustes": {"ritmo": 1.0}})
    assert r2.status_code == 200, r2.text
    assert _esperar(cliente, r2.json()["id"]).estado == "terminada"

    despues = cliente.get(f"/api/proyectos/{id_proyecto}").json()
    s2 = despues["segmentos"][1]
    assert (s2["texto"], s2["emocion"], s2["intensidad"]) == (
        "seis siete y muchas palabras más aquí", "calma", "baja")
    assert s2["ajustes"]["ritmo"] == 1.0
    assert "muchas palabras" in despues["texto"]
    # Los demás segmentos, intactos; el total, más largo y unido de nuevo
    assert {n: (partes / f"segmento-0{n}.wav").read_bytes() for n in (1, 3)} == antes
    suma = sum(duracion_wav(partes / f"segmento-0{n}.wav") for n in (1, 2, 3))
    total = duracion_wav(rutas.salidas / proyecto["audio"])
    assert total > duracion_antes and abs(total - suma) < 0.02
    with wave.open(str(rutas.salidas / proyecto["audio"])) as w:
        assert w.getframerate() == 24000


def test_proyecto_migrado_no_se_puede_regenerar(cliente, rutas):
    (rutas.salidas / "viejo.wav").write_bytes(wav_tono(0.5))
    cliente.app.state.servicios.salidas.migrar()
    r = cliente.post("/api/proyectos/viejo/segmentos/1/regenerar", json={"texto": "hola"})
    assert r.status_code == 400 and "configuración" in r.json()["detail"]


def test_eliminar_proyecto(cliente, rutas):
    (rutas.salidas / "p.wav").write_bytes(wav_tono(0.5))
    s = cliente.app.state.servicios
    s.salidas.migrar()
    assert cliente.delete("/api/proyectos/p").json() == {"ok": True}
    assert s.proyectos.obtener("p") is None and not (rutas.salidas / "p.wav").exists()
    assert cliente.get("/api/proyectos/p").status_code == 404


def test_repositorio_memoria_actualiza_un_segmento():
    repo = RepositorioMemoria()
    repo.guardar({"_id": "a", "creado": "1", "segmentos": [{"indice": 1, "texto": "x"},
                                                          {"indice": 2, "texto": "y"}]})
    repo.actualizar_segmento("a", 2, {"indice": 2, "texto": "z"}, {"actualizado": "2"})
    p = repo.obtener("a")
    assert [s["texto"] for s in p["segmentos"]] == ["x", "z"] and p["actualizado"] == "2"
    assert repo.eliminar("a") and repo.obtener("a") is None
