# -*- coding: utf-8 -*-
"""Esquemas de la animación facial del avatar."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class OpcionesAnimacion(BaseModel):
    """Solo opciones que el motor implementa de verdad; cada motor declara las suyas
    en sus capacidades (GET /api/animaciones/motores)."""
    model_config = ConfigDict(extra="forbid")

    estilo_pose: Optional[int] = Field(None, ge=0, le=45, description="Estilo de movimiento de cabeza")
    escala_expresion: Optional[float] = Field(None, ge=0, le=3, description="Intensidad de la expresión")
    quieto: Optional[bool] = Field(None, description="Cabeza casi fija")
    preproceso: Optional[Literal["full", "crop"]] = Field(
        None, description="full = imagen completa · crop = solo la cara")
    dispositivo: Optional[Literal["gpu", "cpu"]] = None


class MotorAnimacion(BaseModel):
    id: str
    nombre: str
    disponible: bool
    capacidades: Optional[dict[str, Any]] = Field(
        None, description="Qué hace de forma nativa y qué opciones admite")


class TrabajoAnimacionCreado(BaseModel):
    id: str = Field(description="Id del trabajo; progreso en /api/tarea/{id}/eventos")
    estado: str
    motor: str
    video: str = Field(description="Nombre del MP4 cuando termine (/api/videos/{video})")
    duracion_audio: float


class EstadoTrabajo(BaseModel):
    id: str
    tipo: str
    estado: Literal["pendiente", "procesando", "completado", "fallido", "cancelado"]
    segundos: float = Field(description="Tiempo transcurrido desde que se creó")
    resultado: dict[str, Any]
    error: str
    registro: list[str] = Field(description="Últimas líneas del motor")


class AnimacionGuardada(BaseModel):
    video: str
    tamano: int
    fecha: str
