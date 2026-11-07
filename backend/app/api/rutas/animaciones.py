# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import ValidationError

from app.api.dependencias import ServiciosDep
from app.esquemas.animacion import (AnimacionGuardada, EstadoTrabajo, MotorAnimacion,
                                    OpcionesAnimacion, TrabajoAnimacionCreado)
from app.esquemas.sistema import Ok
from app.nucleo.errores import PeticionInvalida

router = APIRouter(prefix="/api/animaciones", tags=["Animación"])

_NO_ENCONTRADO = {404: {"description": "El trabajo no existe o ya se olvidó (15 min)"}}


@router.get("/motores", response_model=list[MotorAnimacion], summary="Motores de animación")
def motores(s: ServiciosDep) -> list[dict]:
    """Motores registrados, si están disponibles y sus capacidades reales: el
    front muestra solo las opciones que cada motor implementa."""
    return s.animacion.motores()


@router.get("", response_model=list[AnimacionGuardada], summary="Animaciones generadas")
def listar(s: ServiciosDep) -> list[dict]:
    return s.animacion.listar()


# Síncrona: valida y normaliza con ffmpeg fuera del bucle de eventos; la
# inferencia en sí corre en segundo plano.
@router.post("", response_model=TrabajoAnimacionCreado, summary="Animar un avatar con un audio",
             responses={400: {"description": "Imagen o audio no válidos"},
                        503: {"description": "El motor de animación no está disponible"}})
def crear(s: ServiciosDep,
          motor: str = Form("sadtalker"),
          avatar: str = Form("", description="Avatar de la galería (o sube `imagen`)"),
          imagen: Optional[UploadFile] = File(None, description="Foto PNG/JPG (o elige `avatar`)"),
          salida: str = Form("", description="Audio del historial (o sube `audio`)"),
          audio: Optional[UploadFile] = File(None, description="Audio WAV/MP3 (o elige `salida`)"),
          escena: Optional[Literal["dia", "noche", "lluvia"]] = Form(
              None, description="Solo con avatar: compone sobre ese fondo con transparencia"),
          estilo_pose: Optional[int] = Form(None),
          escala_expresion: Optional[float] = Form(None),
          quieto: Optional[bool] = Form(None),
          preproceso: Optional[str] = Form(None),
          dispositivo: Optional[str] = Form(None)) -> dict:
    """Crea un trabajo: valida y normaliza los archivos, y anima en segundo plano
    (una sola animación o síntesis en la GPU a la vez).

    Estado en `GET /api/animaciones/{id}`, progreso en vivo en
    `/api/tarea/{id}/eventos` (eventos `progreso` con fotogramas reales del motor)."""
    try:
        opciones = OpcionesAnimacion(estilo_pose=estilo_pose, escala_expresion=escala_expresion,
                                     quieto=quieto, preproceso=preproceso, dispositivo=dispositivo)
    except ValidationError as exc:
        raise PeticionInvalida("Opciones de animación no válidas: " +
                               "; ".join(e["msg"] for e in exc.errors())) from exc
    return s.animacion.crear_trabajo(
        motor=motor, opciones=opciones.model_dump(exclude_none=True),
        avatar=avatar or None, imagen=imagen.file if imagen else None,
        nombre_imagen=imagen.filename if imagen else None,
        salida=salida or None, audio=audio.file if audio else None,
        nombre_audio=audio.filename if audio else None, escena=escena)


@router.get("/{id_trabajo}", response_model=EstadoTrabajo, summary="Estado de una animación",
            responses=_NO_ENCONTRADO)
def estado(id_trabajo: str, s: ServiciosDep) -> dict:
    """pendiente → procesando → completado | fallido | cancelado. Sin porcentajes
    inventados: el progreso real llega por los eventos del trabajo."""
    return s.animacion.estado(id_trabajo)


@router.post("/{id_trabajo}/cancelar", response_model=Ok, summary="Cancelar una animación",
             responses=_NO_ENCONTRADO)
def cancelar(id_trabajo: str, s: ServiciosDep) -> dict:
    s.animacion.estado(id_trabajo)   # 404 si no es una animación
    s.tareas.cancelar(id_trabajo)
    return {"ok": True}


@router.delete("/videos/{video}", response_model=Ok, summary="Borrar una animación")
def borrar(video: str, s: ServiciosDep) -> dict:
    s.animacion.borrar(video)
    return {"ok": True}
