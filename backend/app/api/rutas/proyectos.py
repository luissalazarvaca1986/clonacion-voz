# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.dependencias import ServiciosDep
from app.esquemas.emociones import Segmento
from app.esquemas.sistema import Ok

router = APIRouter(prefix="/api/proyectos", tags=["Proyectos"])

_NO_ENCONTRADO = {404: {"description": "El proyecto no existe"}}


class SegmentoProyecto(BaseModel):
    indice: int
    texto: str
    emocion: Optional[str] = None
    intensidad: Optional[str] = None
    voz: Optional[str] = Field(None, description="Id de la voz")
    avatar: Optional[str] = Field(None, description="Id del avatar")
    escena: Optional[str] = None
    ajustes: Optional[dict[str, Any]] = None
    voz_nombre: Optional[str] = None
    avatar_nombre: Optional[str] = None
    duracion: float = 0
    audio: str
    video: Optional[str] = None


class Proyecto(BaseModel):
    id: str = Field(alias="_id", serialization_alias="id")
    titulo: str
    texto: str
    creado: str
    actualizado: str
    audio: str
    video: Optional[str] = None
    duracion: float
    config: Optional[dict[str, Any]] = Field(None, description="Ajustes generales con los que se generó")
    segmentos: list[SegmentoProyecto]

    model_config = {"populate_by_name": True}


class RegeneracionLanzada(BaseModel):
    id: str = Field(description="Tarea: progreso en /api/tarea/{id}/eventos")
    proyecto: str
    indice: int
    video: bool
    animado: bool


@router.get("/{id_proyecto}", response_model=Proyecto, response_model_by_alias=False,
            summary="Proyecto con toda su configuración", responses=_NO_ENCONTRADO)
def obtener(id_proyecto: str, s: ServiciosDep) -> dict:
    """Ajustes generales y, por segmento, texto, emoción, voz, avatar, fondo y
    ajustes de audio y animación: lo necesario para editarlo y regenerarlo."""
    return s.generacion.proyecto(id_proyecto)


@router.post("/{id_proyecto}/segmentos/{indice}/regenerar", response_model=RegeneracionLanzada,
             summary="Regenerar un segmento",
             responses={400: {"description": "Proyecto sin configuración o datos no válidos"},
                        **_NO_ENCONTRADO})
def regenerar(id_proyecto: str, indice: int, segmento: Segmento, s: ServiciosDep) -> dict:
    """Vuelve a generar solo ese segmento con la configuración enviada (audio y,
    si el proyecto tiene video, su video) y rehace el resultado completo uniendo
    los demás segmentos tal y como estaban."""
    return s.generacion.regenerar_segmento(id_proyecto, indice,
                                           segmento.model_dump(exclude_none=True))


@router.delete("/{id_proyecto}", response_model=Ok, summary="Eliminar un proyecto",
               responses=_NO_ENCONTRADO)
def eliminar(id_proyecto: str, s: ServiciosDep) -> dict:
    """Borra el proyecto de la base de datos y todos sus archivos."""
    proyecto = s.generacion.proyecto(id_proyecto)
    s.salidas.borrar(proyecto["audio"])
    return {"ok": True}
