# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import APIRouter

from app.api.dependencias import ServiciosDep
from app.esquemas.emociones import (AjustesSegmento, Analisis, CatalogoEmociones,
                                   PeticionAnalizar, PeticionSugerencia)
from app.servicios import emociones

router = APIRouter(prefix="/api", tags=["Emociones"])


@router.get("/emociones", response_model=CatalogoEmociones, summary="Perfiles de emoción")
def catalogo() -> dict:
    """Cada perfil es un desplazamiento sobre los ajustes base, a intensidad alta
    (media = 70 %, baja = 40 %)."""
    return emociones.catalogo()


@router.post("/analizar", response_model=Analisis, summary="Segmentar el texto por emociones",
             responses={400: {"description": "Texto vacío"},
                        503: {"description": "El LLM no está disponible"}})
def analizar(datos: PeticionAnalizar, s: ServiciosDep) -> dict:
    """Divide el texto en frases y el LLM etiqueta cada una con emoción e
    intensidad; las frases seguidas con la misma etiqueta se agrupan. No sintetiza.

    Si el texto ya trae marcas `[emocion:intensidad]`, se respetan y no se usa el LLM.
    El resultado se puede revisar y mandar tal cual en `segmentos` a `/api/generar`."""
    ajustes = datos.model_dump(exclude={"texto"}, exclude_none=True)
    return s.generacion.analizar(datos.texto, ajustes)


@router.post("/emociones/sugerencia", response_model=AjustesSegmento,
             summary="Ajustes sugeridos para una emoción")
def sugerencia(datos: PeticionSugerencia, s: ServiciosDep) -> dict:
    """Lo que el analizador propondría para un segmento con esa emoción e
    intensidad, sobre los ajustes base indicados (o los de config.json)."""
    base = datos.model_dump(exclude={"emocion", "intensidad"}, exclude_none=True)
    return s.generacion.sugerir(datos.emocion, datos.intensidad, base)
