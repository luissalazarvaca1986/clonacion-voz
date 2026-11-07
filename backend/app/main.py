# -*- coding: utf-8 -*-
"""Punto de entrada de la API.

    uvicorn app.main:app --host 0.0.0.0 --port 8080

Documentación interactiva: /api/docs (Swagger) y /api/redoc.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.rutas import (animaciones, avatares, emociones, generacion, modelo, proyectos,
                           salidas, sistema, voces)
from app.nucleo.ajustes import Rutas
from app.nucleo.errores import registrar_manejadores
from app.servicios import crear_servicios

VERSION = "2.4.0"

DESCRIPCION = """
Clonación de voz y texto a voz **100 % local** con
[Qwen3-TTS-12Hz-1.7B](https://huggingface.co/ggml-org/Qwen3-TTS-12Hz-1.7B-Base-GGUF)
ejecutado por `llama-tts` de [llama.cpp](https://github.com/ggml-org/llama.cpp).

### Flujo típico
1. `GET /api/estado` — comprobar binario, modelo y GPU.
2. `POST /api/voces` — subir 10-15 s de la voz a clonar.
3. `POST /api/generar` — lanzar la síntesis; devuelve el id de la tarea.
4. `GET /api/tarea/{id}/eventos` — seguir el progreso (Server-Sent Events).
5. `GET /api/salidas/{archivo}` — descargar el WAV.

### Voz emocional
`POST /api/analizar` divide el texto en segmentos con emoción usando un LLM local
(Qwen3.5 2B en CPU vía llama-server). Con `modo: "emocional"` en `/api/generar`,
cada segmento se sintetiza con su temperatura/top-p/top-k y ffmpeg le aplica
ritmo, tono, volumen y pausa antes de unir el entregable. También se pueden fijar
a mano con marcas en el texto: `[alegria:alta] ¡Qué emoción! [tristeza] Pero…`

### Meet-Video
`POST /api/avatares` crea un avatar con su imagen (PNG transparente) y tres fondos
(día, noche, lluvia). Con `video: true` en `/api/generar` se genera además un MP4
del mensaje: cada segmento muestra su avatar sobre el fondo de la `escena` elegida
y habla con su voz (`segmentos[].voz` / `segmentos[].avatar`).

Todos los errores responden `{"detail": "<mensaje legible>"}`.
"""

ETIQUETAS = [
    {"name": "Sistema", "description": "Diagnóstico y configuración persistente."},
    {"name": "Modelo", "description": "Descarga de los pesos .gguf desde Hugging Face."},
    {"name": "Voces", "description": "Biblioteca de voces de referencia para clonar."},
    {"name": "Generación", "description": "Síntesis de texto a voz y su progreso en vivo."},
    {"name": "Emociones", "description": "Voz emocional: un LLM local segmenta el texto y "
                                         "cada segmento se sintetiza con su propio perfil."},
    {"name": "Meet-Video", "description": "Avatares (imagen + fondos día/noche/lluvia) y "
                                          "videos del mensaje con el avatar que habla."},
    {"name": "Animación", "description": "Avatar animado con IA (SadTalker): labios, "
                                         "parpadeo y expresión a partir de un audio."},
    {"name": "Proyectos", "description": "Cada generación con su configuración (MongoDB): "
                                         "consultar, regenerar un segmento y eliminar."},
    {"name": "Historial", "description": "Audios y videos generados y sus partes."},
]

log = logging.getLogger("app")


def _configurar_registro() -> None:
    logging.basicConfig(
        level=os.environ.get("CLONAR_LOG", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


def crear_app(rutas: Rutas | None = None) -> FastAPI:
    rutas = rutas or Rutas.desde_entorno()
    servicios = crear_servicios(rutas)

    @asynccontextmanager
    async def ciclo_vida(_: FastAPI):
        rutas.crear()
        # Trozos y resultados a medias que quedaran de un apagado brusco
        for resto in rutas.tmp.iterdir():
            if resto.is_file():
                resto.unlink(missing_ok=True)
        log.info("Clonador de voz %s · modelo=%s voces=%s salidas=%s · proyectos en %s",
                 VERSION, rutas.modelo, rutas.voces, rutas.salidas, servicios.proyectos.tipo)
        try:
            servicios.salidas.migrar()
        except Exception as exc:  # noqa: BLE001 — sin base de datos el resto sigue funcionando
            log.warning("No se pudo migrar el historial a la base de datos: %s", exc)
        yield
        servicios.tareas.detener_todo()
        servicios.descarga.cancelar()
        servicios.llm.cerrar()

    app = FastAPI(
        title="Clonador de voz local",
        version=VERSION,
        description=DESCRIPCION,
        openapi_tags=ETIQUETAS,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
        lifespan=ciclo_vida,
    )
    app.state.servicios = servicios
    registrar_manejadores(app)
    for modulo in (sistema, modelo, voces, generacion, emociones, avatares, animaciones,
                   proyectos, salidas):
        app.include_router(modulo.router)
    app.include_router(salidas.videos)
    return app


_configurar_registro()
app = crear_app()
