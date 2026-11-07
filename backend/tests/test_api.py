# -*- coding: utf-8 -*-
"""Contrato de la API: rutas, forma de las respuestas y mensajes de error."""
from __future__ import annotations

from tests.conftest import wav_tono

CLAVES_ESTADO = ["binario", "binario_ok", "version", "soporta_qwen3tts", "modelo", "modelo_ok",
                 "mmproj", "mmproj_ok", "dispositivos", "dispositivo_preferido", "ffmpeg",
                 "idiomas", "cpus", "config", "llm", "base_datos"]


def test_estado_tiene_las_claves_en_orden(cliente):
    r = cliente.get("/api/estado")
    assert r.status_code == 200
    assert list(r.json()) == CLAVES_ESTADO
    assert r.json()["modelo_ok"] is False      # carpeta de modelo vacía


def test_salud(cliente):
    assert cliente.get("/api/salud").json() == {
        "estado": "ok", "tareas_en_curso": 0, "descarga_activa": False}


def test_swagger_y_openapi(cliente):
    assert cliente.get("/api/docs").status_code == 200
    esquema = cliente.get("/api/openapi.json").json()
    for ruta in ("/api/estado", "/api/voces", "/api/generar", "/api/tarea/{id_tarea}/eventos",
                 "/api/salidas", "/api/modelo/catalogo"):
        assert ruta in esquema["paths"]


def test_config_ida_y_vuelta(cliente, rutas):
    r = cliente.post("/api/config", json={"temp": 0.65, "idioma": "en", "semilla": ""})
    assert r.status_code == 200
    assert r.json()["temp"] == 0.65 and r.json()["idioma"] == "en"
    assert r.json()["semilla"] == -1           # "" = no indicado
    assert cliente.get("/api/config").json()["temp"] == 0.65
    assert rutas.config.is_file()


def test_config_invalida_da_422_con_detalle_legible(cliente):
    r = cliente.post("/api/config", json={"idioma": "klingon", "top_p": 7})
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], str)
    assert "idioma" in r.json()["detail"] and "top_p" in r.json()["detail"]


def test_voces_crear_listar_escuchar_borrar(cliente):
    assert cliente.get("/api/voces").json() == []

    r = cliente.post("/api/voces", data={"nombre": " Prueba ", "transcripcion": "hola"},
                     files={"audio": ("ref.wav", wav_tono(2.0), "audio/wav")})
    assert r.status_code == 200, r.text
    voz = r.json()
    assert list(voz) == ["nombre", "transcripcion", "creada", "id", "duracion"]
    assert voz["nombre"] == "Prueba" and 1.5 < voz["duracion"] <= 2.01

    assert [v["id"] for v in cliente.get("/api/voces").json()] == [voz["id"]]
    audio = cliente.get(f"/api/voces/{voz['id']}/audio")
    assert audio.status_code == 200 and audio.headers["content-type"] == "audio/wav"

    assert cliente.delete(f"/api/voces/{voz['id']}").json() == {"ok": True}
    assert cliente.get("/api/voces").json() == []


def test_voz_referencia_se_recorta_a_15_s(cliente):
    r = cliente.post("/api/voces", data={"nombre": "larga"},
                     files={"audio": ("larga.wav", wav_tono(20.0), "audio/wav")})
    assert r.status_code == 200 and r.json()["duracion"] <= 15.0


def test_errores_404_con_los_mensajes_originales(cliente):
    assert cliente.delete("/api/voces/nope").json() == {"detail": "Voz no encontrada"}
    assert cliente.get("/api/voces/nope/audio").json() == {"detail": "Audio no encontrado"}
    assert cliente.get("/api/salidas/nope.wav").json() == {"detail": "Audio no encontrado"}
    assert cliente.get("/api/tarea/xx/eventos").json() == {"detail": "Tarea no encontrada"}
    assert cliente.post("/api/tarea/xx/cancelar").status_code == 404


def test_rutas_no_escapan_de_su_carpeta(cliente):
    assert cliente.get("/api/salidas/..%2F..%2Fetc%2Fpasswd").status_code == 404
    assert cliente.delete("/api/voces/..%2F..").status_code == 404


def test_generar_sin_modelo_da_400(cliente):
    r = cliente.post("/api/generar", json={"texto": "Hola"})
    assert r.status_code == 400
    assert r.json()["detail"].startswith("No se encuentra el")


def test_generar_con_ajuste_fuera_de_rango_da_422(cliente):
    r = cliente.post("/api/generar", json={"texto": "Hola", "temp": -1})
    assert r.status_code == 422 and "temp" in r.json()["detail"]


def test_salidas_listar_y_borrar(cliente, rutas):
    (rutas.salidas / "20260101-000000-abc.wav").write_bytes(wav_tono(1.0))
    cliente.app.state.servicios.salidas.migrar()     # audios sueltos de antes de la base de datos
    lista = cliente.get("/api/salidas").json()
    assert [s["archivo"] for s in lista] == ["20260101-000000-abc.wav"]
    assert list(lista[0]) == ["archivo", "tamano", "duracion", "fecha", "video", "titulo",
                              "editable", "segmentos"]
    assert lista[0]["editable"] is False
    assert cliente.get("/api/salidas/20260101-000000-abc.wav").status_code == 200
    assert cliente.delete("/api/salidas/20260101-000000-abc.wav").json() == {"ok": True}
    assert cliente.get("/api/salidas").json() == []


def test_catalogo_y_descarga_invalida(cliente):
    c = cliente.get("/api/modelo/catalogo").json()
    assert c["falta"] is True and "Q4_K_M" in c["catalogo"]["modelo"]
    r = cliente.post("/api/modelo/descargar", json={"modelo": "Q1"})
    assert r.status_code == 400 and r.json() == {"detail": "Cuantización desconocida."}


# ---- Modo emocional ------------------------------------------------------------
def test_estado_sin_llm_configurado(cliente):
    assert cliente.get("/api/estado").json()["llm"] == {
        "configurado": False, "disponible": False, "modelo": "", "modo": "desactivado",
        "activo": False}


def test_catalogo_de_emociones(cliente):
    c = cliente.get("/api/emociones").json()
    ids = [e["id"] for e in c["emociones"]]
    assert "neutro" in ids and "tristeza" in ids and c["intensidades"] == ["baja", "media", "alta"]


def test_analizar_con_marcas_no_necesita_llm(cliente):
    r = cliente.post("/api/analizar", json={"texto": "[alegria:alta] ¡Bien! [tristeza] Qué pena."})
    assert r.status_code == 200
    d = r.json()
    assert d["origen"] == "marcas"
    assert [(s["emocion"], s["intensidad"]) for s in d["segmentos"]] == [
        ("alegria", "alta"), ("tristeza", "media")]
    assert d["segmentos"][1]["ajustes"]["ritmo"] < 1


def test_analizar_sin_llm_da_503(cliente):
    r = cliente.post("/api/analizar", json={"texto": "Hola a todos."})
    assert r.status_code == 503 and "LLM" in r.json()["detail"]


def test_generar_rechaza_emocion_desconocida(cliente):
    r = cliente.post("/api/generar", json={"modo": "emocional",
                                           "segmentos": [{"texto": "Hola", "emocion": "furia"}]})
    assert r.status_code == 422 and "emocion" in r.json()["detail"]


def test_sugerencia_de_ajustes(cliente):
    r = cliente.post("/api/emociones/sugerencia", json={"emocion": "entusiasmo", "intensidad": "alta",
                                                        "temp": 0.7})
    assert r.status_code == 200
    d = r.json()
    assert d["temp"] == 0.95 and d["escala_expresion"] == 1.5 and d["estilo_pose"] == 0


def test_generar_rechaza_ajustes_de_segmento_fuera_de_rango(cliente):
    r = cliente.post("/api/generar", json={"modo": "emocional", "segmentos": [
        {"texto": "Hola", "emocion": "neutro", "ajustes": {"estilo_pose": 99}}]})
    assert r.status_code == 422 and "estilo_pose" in r.json()["detail"]
