# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import APIRouter

from app.api.dependencias import ServiciosDep
from app.esquemas.sistema import Configuracion, ConfiguracionParcial, Estado, Salud

router = APIRouter(prefix="/api", tags=["Sistema"])


@router.get("/estado", response_model=Estado, summary="Diagnóstico del sistema")
def estado(s: ServiciosDep) -> dict:
    """Binario llama-tts y su versión, modelos encontrados, GPUs detectadas,
    ffmpeg, idiomas admitidos, la configuración efectiva y el estado del LLM."""
    return {**s.entorno.estado(s.config.cargar()), "llm": s.llm.estado(),
            "base_datos": {"tipo": s.proyectos.tipo, "disponible": s.proyectos.disponible()}}


@router.get("/salud", response_model=Salud, summary="Comprobación de vida")
def salud(s: ServiciosDep) -> dict:
    """Respuesta inmediata, sin lanzar procesos: útil para healthchecks de Docker."""
    return {"estado": "ok", "tareas_en_curso": s.tareas.en_curso(),
            "descarga_activa": s.descarga.activa}


@router.get("/config", response_model=Configuracion, summary="Leer la configuración")
def obtener_config(s: ServiciosDep) -> dict:
    return s.config.cargar()


@router.post("/config", response_model=Configuracion, summary="Guardar ajustes por defecto")
def actualizar_config(datos: ConfiguracionParcial, s: ServiciosDep) -> dict:
    """Guarda en config.json solo las claves enviadas y devuelve la configuración completa."""
    cfg = s.config.actualizar(datos.model_dump(exclude_none=True))
    s.entorno.limpiar_cache()
    return cfg
