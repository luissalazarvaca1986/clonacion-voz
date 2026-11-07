# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse

from app.api.dependencias import ServiciosDep
from app.esquemas.audio import Voz
from app.esquemas.sistema import Ok

router = APIRouter(prefix="/api/voces", tags=["Voces"])

_NO_ENCONTRADA = {404: {"description": "La voz no existe"}}


@router.get("", response_model=list[Voz], summary="Listar la biblioteca de voces")
def listar(s: ServiciosDep) -> list[dict]:
    """Voces guardadas, de la más reciente a la más antigua."""
    return s.voces.listar()


# Síncrona a propósito: ffmpeg bloquea y así corre en el pool de hilos de FastAPI
# sin congelar los flujos SSE que estén abiertos.
@router.post("", response_model=Voz, summary="Añadir una voz",
             responses={413: {"description": "El audio pasa de 60 MB"},
                        500: {"description": "ffmpeg no pudo convertir el audio"}})
def crear(s: ServiciosDep,
          audio: UploadFile = File(..., description="Audio de referencia (wav, mp3, webm, m4a…)"),
          nombre: str = Form(..., description="Nombre visible de la voz"),
          transcripcion: str = Form("", description="Lo que se dice en el audio (opcional)")) -> dict:
    """Convierte el audio a WAV mono de 16 kHz, quita el silencio inicial y lo
    recorta a 15 s. Con 10-15 s de voz limpia basta."""
    return s.voces.crear(audio.file, audio.filename, nombre, transcripcion)


@router.delete("/{id_voz}", response_model=Ok, summary="Eliminar una voz", responses=_NO_ENCONTRADA)
def borrar(id_voz: str, s: ServiciosDep) -> dict:
    s.voces.borrar(id_voz)
    return {"ok": True}


@router.get("/{id_voz}/audio", response_class=FileResponse, summary="Escuchar la referencia",
            responses={200: {"content": {"audio/wav": {}}}, **_NO_ENCONTRADA})
def audio(id_voz: str, s: ServiciosDep) -> FileResponse:
    return FileResponse(s.voces.audio(id_voz), media_type="audio/wav")
