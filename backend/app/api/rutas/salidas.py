# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.api.dependencias import ServiciosDep
from app.esquemas.audio import Salida
from app.esquemas.sistema import Ok

router = APIRouter(prefix="/api/salidas", tags=["Historial"])


@router.get("", response_model=list[Salida], summary="Listar los audios generados")
def listar(s: ServiciosDep) -> list[dict]:
    """Los 80 más recientes primero."""
    return s.salidas.listar()


@router.get("/{archivo}", response_class=FileResponse, summary="Descargar un audio",
            responses={200: {"content": {"audio/wav": {}}},
                       404: {"description": "El audio no existe"}})
def obtener(archivo: str, s: ServiciosDep) -> FileResponse:
    ruta = s.salidas.ruta(archivo)
    return FileResponse(ruta, media_type="audio/wav", filename=ruta.name)


@router.get("/{generacion}/partes/{archivo}", response_class=FileResponse,
            summary="Descargar la parte de un segmento",
            responses={200: {"content": {"audio/wav": {}, "video/mp4": {}}},
                       404: {"description": "La parte no existe"}})
def parte(generacion: str, archivo: str, s: ServiciosDep) -> FileResponse:
    """`segmento-NN.wav` (audio del segmento) o `segmento-NN.mp4` (su video con audio).
    `generacion` es el nombre del WAV final sin extensión."""
    ruta = s.salidas.ruta_parte(generacion, archivo)
    tipo = "audio/wav" if ruta.suffix == ".wav" else "video/mp4"
    return FileResponse(ruta, media_type=tipo, filename=f"{Path(generacion).name}-{ruta.name}")


@router.delete("/{archivo}", response_model=Ok, summary="Borrar una generación")
def borrar(archivo: str, s: ServiciosDep) -> dict:
    """Borra el audio, su Meet-Video, las partes de cada segmento y el manifiesto.
    Idempotente: borrar uno que no existe responde ok."""
    s.salidas.borrar(archivo)
    return {"ok": True}


videos = APIRouter(prefix="/api/videos", tags=["Meet-Video"])


@videos.get("/{archivo}", response_class=FileResponse, summary="Descargar un Meet-Video",
            responses={200: {"content": {"video/mp4": {}}},
                       404: {"description": "El video no existe"}})
def obtener_video(archivo: str, s: ServiciosDep) -> FileResponse:
    ruta = s.salidas.ruta_video(archivo)
    return FileResponse(ruta, media_type="video/mp4", filename=ruta.name)
