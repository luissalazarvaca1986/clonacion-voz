# -*- coding: utf-8 -*-
from __future__ import annotations

import io
import subprocess
import time
import zipfile

from app.servicios.animacion import ProveedorSadTalker
from app.servicios.llm import ClienteLLM
from app.servicios.video import ALTO, ANCHO, componer_tramo_animado, concatenar_tramos
from tests.conftest import wav_tono


def _sonda(ruta, campos):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", campos, "-of",
                        "default=nw=1:nk=1", str(ruta)], capture_output=True, text=True)
    return r.stdout.split()


def _video_falso(ruta, segundos, ancho=400, alto=350):
    """Simula la salida de SadTalker: MPEG-4 parte 2, sin alfa."""
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", f"testsrc=s={ancho}x{alto}:r=25:d={segundos}", "-c:v", "mpeg4",
                    str(ruta)], check=True)
    return ruta


class ProveedorFalso:
    """Cumple la interfaz ProveedorAnimacion sin GPU ni SadTalker."""
    id, nombre = "falso", "Falso"

    def __init__(self, disponible=True):
        self._disponible, self.llamadas = disponible, []

    def disponible(self):
        return self._disponible

    def capacidades(self):
        return {"opciones": {"estilo_pose": {"tipo": "entero", "min": 0, "max": 45}}}

    def validar(self, opciones):
        return {k: v for k, v in opciones.items() if k == "estilo_pose"}

    def animar(self, tarea, imagen, audio, destino, opciones):
        self.llamadas.append((imagen.name, audio.name, opciones))
        tarea.emitir("progreso", fase="renderizando el rostro", hecho=1, total=1)
        _video_falso(destino, 1.0)


def _esperar(cliente, id_trabajo, limite=30):
    for _ in range(limite * 10):
        estado = cliente.get(f"/api/animaciones/{id_trabajo}").json()
        if estado["estado"] in ("completado", "fallido", "cancelado"):
            return estado
        time.sleep(0.1)
    raise AssertionError("el trabajo no terminó")


def _crear_avatar(cliente, imagenes):
    archivos = {t: (f"{t}.png", open(r, "rb"), "image/png") for t, r in imagenes.items()}
    assert cliente.post("/api/avatares", data={"nombre": "Luis"}, files=archivos).status_code == 200


# ---- Composición -------------------------------------------------------------
def test_tramo_animado_conserva_colocacion_y_duracion(tmp_path, imagenes):
    animado = _video_falso(tmp_path / "anim.mp4", 1.0)
    tramo = tmp_path / "tramo.mp4"
    componer_tramo_animado(imagenes["noche"], imagenes["avatar"], animado, "Luis", 1.6, tramo)
    assert _sonda(tramo, "stream=width,height") == [str(ANCHO), str(ALTO)]
    # El motor dio 1,0 s; el tramo se alarga congelando el último fotograma
    assert abs(float(_sonda(tramo, "format=duration")[0]) - 1.6) < 0.1
    audio = tmp_path / "audio.wav"
    audio.write_bytes(wav_tono(3.2))
    final = tmp_path / "final.mp4"
    concatenar_tramos([tramo, tramo], audio, final)
    assert _sonda(final, "stream=codec_name") == ["h264", "aac"]
    assert abs(float(_sonda(final, "format=duration")[0]) - 3.2) < 0.15


# ---- Trabajos de animación ---------------------------------------------------
def test_sin_motor_configurado_da_503(cliente, rutas, imagenes):
    _crear_avatar(cliente, imagenes)
    (rutas.salidas / "a.wav").write_bytes(wav_tono(1.0))
    r = cliente.post("/api/animaciones", data={"avatar": "Luis", "salida": "a.wav"})
    assert r.status_code == 503
    motores = cliente.get("/api/animaciones/motores").json()
    assert motores[0]["id"] == "sadtalker" and motores[0]["disponible"] is False


def test_animacion_completa_con_motor_falso(cliente, rutas, imagenes):
    s = cliente.app.state.servicios
    falso = ProveedorFalso()
    s.animacion._proveedores = {"falso": falso}
    _crear_avatar(cliente, imagenes)
    (rutas.salidas / "a.wav").write_bytes(wav_tono(1.0))

    r = cliente.post("/api/animaciones", data={"motor": "falso", "avatar": "Luis", "salida": "a.wav",
                                               "escena": "lluvia", "estilo_pose": 3})
    assert r.status_code == 200, r.text
    estado = _esperar(cliente, r.json()["id"])
    assert estado["estado"] == "completado", estado
    video = rutas.videos / estado["resultado"]["video"]
    assert _sonda(video, "stream=width,height")[:2] == [str(ANCHO), str(ALTO)]
    assert falso.llamadas[0][2] == {"estilo_pose": 3}
    assert [a["video"] for a in cliente.get("/api/animaciones").json()] == [video.name]
    assert not list(rutas.animacion.iterdir())        # temporales limpios


def test_foto_subida_sin_fondo_y_validaciones(cliente, rutas, imagenes, tmp_path):
    cliente.app.state.servicios.animacion._proveedores = {"falso": ProveedorFalso()}
    foto = ("cara.png", open(imagenes["dia"], "rb"), "image/png")
    sonido = ("voz.wav", wav_tono(1.0), "audio/wav")
    r = cliente.post("/api/animaciones", data={"motor": "falso"},
                     files={"imagen": foto, "audio": sonido})
    assert r.status_code == 200, r.text
    assert _esperar(cliente, r.json()["id"])["estado"] == "completado"

    malo = cliente.post("/api/animaciones", data={"motor": "falso"},
                        files={"imagen": ("x.gif", b"GIF89a", "image/gif"), "audio": sonido})
    assert malo.status_code == 400 and "Formato" in malo.json()["detail"]
    falso_png = cliente.post("/api/animaciones", data={"motor": "falso"},
                             files={"imagen": ("x.png", b"no es png", "image/png"), "audio": sonido})
    assert falso_png.status_code == 400 and "imagen" in falso_png.json()["detail"]
    sin_audio = cliente.post("/api/animaciones", data={"motor": "falso"}, files={"imagen": foto})
    assert sin_audio.status_code == 400
    escena_sin_avatar = cliente.post("/api/animaciones", data={"motor": "falso", "escena": "dia"},
                                     files={"imagen": foto, "audio": sonido})
    assert escena_sin_avatar.status_code == 400


def test_opciones_fuera_de_rango(cliente, imagenes):
    cliente.app.state.servicios.animacion._proveedores = {"falso": ProveedorFalso()}
    r = cliente.post("/api/animaciones", data={"motor": "falso", "estilo_pose": 99},
                     files={"imagen": ("c.png", open(imagenes["dia"], "rb"), "image/png"),
                            "audio": ("v.wav", wav_tono(1.0), "audio/wav")})
    assert r.status_code == 400 and "Opciones" in r.json()["detail"]


def test_validacion_de_opciones_sadtalker():
    p = ProveedorSadTalker("", __import__("pathlib").Path("/tmp"), 10)
    p._cache = (time.time(), {"opciones": {
        "estilo_pose": {"tipo": "entero", "min": 0, "max": 45},
        "preproceso": {"tipo": "enum", "valores": ["full", "crop"]}}})
    assert p.validar({"estilo_pose": 4, "preproceso": "full"}) == {"estilo_pose": 4, "preproceso": "full"}
    for malas in ({"estilo_pose": 46}, {"preproceso": "extfull"}, {"mirada": 1}):
        try:
            p.validar(malas)
            raise AssertionError(malas)
        except Exception as exc:  # noqa: BLE001
            assert getattr(exc, "estado", None) == 400


# ---- Avatar: ZIP y edición ---------------------------------------------------
def test_zip_del_avatar(cliente, imagenes):
    _crear_avatar(cliente, imagenes)
    cliente.get("/api/avatares/Luis/vista/dia")          # genera caché: no debe ir en el ZIP
    r = cliente.get("/api/avatares/Luis/zip")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert "avatar-Luis.zip" in r.headers["content-disposition"]
    nombres = set(zipfile.ZipFile(io.BytesIO(r.content)).namelist())
    assert nombres == {"Luis/avatar.png", "Luis/dia.png", "Luis/noche.png", "Luis/lluvia.png",
                       "Luis/avatar.json"}


# ---- LLM bajo demanda --------------------------------------------------------
def test_llm_bajo_demanda_sin_modelo(tmp_path):
    llm = ClienteLLM("", 5, modelo=tmp_path / "no-existe.gguf", binario="/no/llama-server")
    assert llm.estado() == {"configurado": False, "disponible": False, "modelo": "",
                            "modo": "bajo_demanda", "activo": False}
