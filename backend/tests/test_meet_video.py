# -*- coding: utf-8 -*-
from __future__ import annotations

import subprocess

from app.servicios.avatares import id_desde_nombre
from app.servicios.generacion import Hablante, piezas_emocionales
from app.nucleo.configuracion import CONFIG_POR_DEFECTO
from app.servicios.video import ALTO, ANCHO, componer_fotograma, generar_video
from tests.conftest import png, wav_tono


def _sonda(ruta, campos):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", campos, "-of",
                        "default=nw=1:nk=1", str(ruta)], capture_output=True, text=True)
    return r.stdout.split()


def _archivos(imagenes, sin=()):
    return {t: (f"{t}.png", open(r, "rb"), "image/png")
            for t, r in imagenes.items() if t not in sin}


# ---- Avatares ----------------------------------------------------------------
def test_id_desde_nombre():
    assert id_desde_nombre("Luis") == "Luis"
    assert id_desde_nombre("  María José Ñúñez ") == "Maria-Jose-Nunez"
    assert id_desde_nombre("../../etc") == "etc"
    assert id_desde_nombre("¿?") == "avatar"


def test_crear_avatar_en_carpeta_con_su_nombre(cliente, rutas, imagenes):
    r = cliente.post("/api/avatares", data={"nombre": "Luis"}, files=_archivos(imagenes))
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["id"] == "Luis" and a["completo"] and a["transparente"]
    assert a["escenas"] == ["dia", "noche", "lluvia"]
    assert {p.name for p in (rutas.avatares / "Luis").iterdir()} >= {
        "avatar.png", "dia.png", "noche.png", "lluvia.png", "avatar.json"}
    # Mismo nombre: carpeta nueva, sin pisar la anterior
    r2 = cliente.post("/api/avatares", data={"nombre": "Luis"}, files=_archivos(imagenes))
    assert r2.json()["id"] == "Luis-2"
    assert {x["id"] for x in cliente.get("/api/avatares").json()} == {"Luis", "Luis-2"}


def test_avatar_exige_los_tres_fondos(cliente, imagenes):
    r = cliente.post("/api/avatares", data={"nombre": "Incompleto"},
                     files=_archivos(imagenes, sin=("lluvia",)))
    assert r.status_code == 422 and "lluvia" in r.json()["detail"]


def test_avatar_rechaza_archivos_que_no_son_imagen(cliente, imagenes):
    archivos = _archivos(imagenes)
    archivos["noche"] = ("noche.png", b"esto no es un png", "image/png")
    r = cliente.post("/api/avatares", data={"nombre": "Roto"}, files=archivos)
    assert r.status_code == 400 and "noche" in r.json()["detail"]
    assert cliente.get("/api/avatares").json() == []


def test_avatar_con_voz_inexistente(cliente, imagenes):
    r = cliente.post("/api/avatares", data={"nombre": "X", "voz": "nope"}, files=_archivos(imagenes))
    assert r.status_code == 400


def test_vista_y_reemplazo_de_fondo(cliente, imagenes, tmp_path):
    cliente.post("/api/avatares", data={"nombre": "Luis"}, files=_archivos(imagenes))
    vista = cliente.get("/api/avatares/Luis/vista/noche")
    assert vista.status_code == 200 and vista.headers["content-type"] == "image/png"
    destino = tmp_path / "vista.png"
    destino.write_bytes(vista.content)
    assert _sonda(destino, "stream=width,height") == [str(ANCHO), str(ALTO)]

    nuevo = png(tmp_path / "nuevo.png", 640, 360)
    r = cliente.put("/api/avatares/Luis/imagenes/noche",
                    files={"archivo": ("n.png", open(nuevo, "rb"), "image/png")})
    assert r.status_code == 200
    assert cliente.patch("/api/avatares/Luis", json={"nombre": "Luis P."}).json()["nombre"] == "Luis P."
    assert cliente.delete("/api/avatares/Luis").json() == {"ok": True}
    assert cliente.get("/api/avatares/Luis/vista/dia").status_code == 404


# ---- Video -------------------------------------------------------------------
def test_fotograma_y_video_con_cambio_de_avatar(tmp_path, imagenes):
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    componer_fotograma(imagenes["dia"], imagenes["avatar"], "Luis", a)
    componer_fotograma(imagenes["noche"], imagenes["avatar"], "Ana", b)
    audio = tmp_path / "audio.wav"
    audio.write_bytes(wav_tono(3.0))
    video = tmp_path / "meet.mp4"
    generar_video([(a, 1.0), (a, 0.5), (b, 1.5)], audio, video)
    duracion = float(_sonda(video, "format=duration")[0])
    assert abs(duracion - 3.0) < 0.15
    assert _sonda(video, "stream=codec_name") == ["h264", "aac"]
    assert not list(tmp_path.glob("*_fotogramas.txt"))


def test_piezas_llevan_voz_e_imagen_de_su_hablante(tmp_path):
    luis = Hablante(ref=tmp_path / "luis.wav", voz="Luis", avatar_id="Luis", avatar="Luis",
                    imagen=tmp_path / "luis.png")
    ana = Hablante(ref=tmp_path / "ana.wav", voz="Ana", avatar_id="Ana", avatar="Ana",
                   imagen=tmp_path / "ana.png")
    piezas = piezas_emocionales(dict(CONFIG_POR_DEFECTO), [
        {"texto": "Hola.", "emocion": "neutro", "intensidad": "media"},
        {"texto": "¡Qué bien!", "emocion": "alegria", "intensidad": "alta"}], [luis, ana])
    assert [(p.voz, p.avatar, p.imagen.name, p.segmento) for p in piezas] == [
        ("Luis", "Luis", "luis.png", 1), ("Ana", "Ana", "ana.png", 2)]


def test_borrar_salida_borra_su_video(cliente, rutas):
    (rutas.salidas / "x.wav").write_bytes(wav_tono(0.5))
    (rutas.videos / "x.mp4").write_bytes(b"0")
    cliente.app.state.servicios.salidas.migrar()
    assert cliente.get("/api/salidas").json()[0]["video"] == "x.mp4"
    assert cliente.get("/api/videos/x.mp4").status_code == 200
    cliente.delete("/api/salidas/x.wav")
    assert not (rutas.videos / "x.mp4").exists()
    assert cliente.get("/api/videos/x.mp4").status_code == 404


# ---- Posición del avatar sobre el fondo ---------------------------------------
def _caja_avatar(ruta):
    """Columnas donde aparece el avatar (rojo semitransparente sobre fondo azul)."""
    r = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(ruta), "-vf", f"crop={ANCHO}:1:0:{ALTO - 2}",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True)
    fila = r.stdout
    rojas = [x for x in range(ANCHO) if fila[3 * x] > 100]
    return min(rojas), max(rojas)


def test_posicion_por_defecto_y_personalizada(cliente, imagenes, tmp_path):
    datos = {"nombre": "Luis", "ancho": "0.4", "izquierda": "0.1"}
    a = cliente.post("/api/avatares", data=datos, files=_archivos(imagenes)).json()
    assert a["posicion"] == {"ancho": 0.4, "izquierda": 0.1}
    vista = tmp_path / "v.png"
    vista.write_bytes(cliente.get("/api/avatares/Luis/vista/dia").content)
    izq, der = _caja_avatar(vista)
    assert abs(izq - 0.1 * ANCHO) <= 2 and abs((der - izq + 1) - 0.4 * ANCHO) <= 3

    # Moverlo invalida la caché: el fotograma nuevo refleja la posición nueva
    r = cliente.patch("/api/avatares/Luis", json={"ancho": 0.5, "izquierda": 0.45})
    assert r.json()["posicion"] == {"ancho": 0.5, "izquierda": 0.45}
    vista.write_bytes(cliente.get("/api/avatares/Luis/vista/dia").content)
    izq, der = _caja_avatar(vista)
    assert abs(izq - 0.45 * ANCHO) <= 2 and abs((der - izq + 1) - 0.5 * ANCHO) <= 3

    otro = cliente.post("/api/avatares", data={"nombre": "Ana"}, files=_archivos(imagenes)).json()
    assert otro["posicion"] == {"ancho": 0.65, "izquierda": 0.25}


def test_posicion_debe_caber(cliente, imagenes):
    r = cliente.post("/api/avatares", data={"nombre": "X", "ancho": "0.8", "izquierda": "0.5"},
                     files=_archivos(imagenes))
    assert r.status_code == 400 and "caber" in r.json()["detail"]
    cliente.post("/api/avatares", data={"nombre": "Y"}, files=_archivos(imagenes))
    assert cliente.patch("/api/avatares/Y", json={"ancho": 0.05}).status_code == 422
    assert cliente.patch("/api/avatares/Y", json={"izquierda": 0.5}).status_code == 400  # 0,65 + 0,5
