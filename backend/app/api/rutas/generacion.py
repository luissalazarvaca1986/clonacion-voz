# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import queue

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.api.dependencias import ServiciosDep
from app.api.sse import documentar_sse, evento, respuesta_sse
from app.esquemas.audio import PeticionGenerar, TareaCreada
from app.esquemas.sistema import Ok
from app.servicios.sintesis import EVENTOS_FINALES

router = APIRouter(prefix="/api", tags=["Generación"])

_TAREA_NO_ENCONTRADA = {404: {"description": "La tarea no existe o ya se olvidó (15 min)"}}


@router.post("/generar", response_model=TareaCreada, summary="Sintetizar texto",
             responses={400: {"description": "Falta el texto, el modelo, el binario o la voz"}})
def generar(datos: PeticionGenerar, s: ServiciosDep) -> dict:
    """Trocea el texto por frases y lanza la síntesis en segundo plano.
    Responde enseguida con el id de la tarea; el avance se sigue en
    `/api/tarea/{id}/eventos` y el resultado queda en `/api/salidas/{archivo}`.

    Con `modo: "emocional"` el texto se divide en segmentos con emoción (los
    `segmentos` enviados, las marcas `[emocion:intensidad]` del texto o, si no hay
    ninguna de las dos cosas, el análisis del LLM). Cada segmento se sintetiza con
    su temperatura/top-p/top-k y luego ffmpeg le aplica ritmo, tono, volumen y
    pausa antes de unirlos. Si el LLM no responde, sale todo en tono neutro.

    Cada segmento puede indicar `voz` y `avatar`. Con `video: true` se genera
    además un Meet-Video (MP4 1280×720) con el fondo `escena` (dia, noche o
    lluvia): mientras suena cada segmento se ve su avatar sobre su fondo.
    Con `animado: true`, cada tramo se anima (labios, parpadeo, expresión) con el
    motor de animación; tarda unas 15-20 veces la duración del audio en una GTX 1650."""
    ajustes = datos.model_dump(exclude={"texto", "voz", "dispositivo", "modo", "segmentos",
                                        "video", "escena", "avatar", "animado",
                                        "motor_animacion", "opciones_animacion"},
                               exclude_none=True)
    segmentos = ([seg.model_dump(exclude_none=True) for seg in datos.segmentos]
                 if datos.segmentos else None)
    return s.generacion.generar(datos.texto, datos.voz, datos.dispositivo, ajustes,
                                modo=datos.modo, segmentos=segmentos, video=datos.video,
                                escena=datos.escena, avatar=datos.avatar, animado=datos.animado,
                                motor_animacion=datos.motor_animacion,
                                opciones_animacion=(datos.opciones_animacion.model_dump(
                                    exclude_none=True) if datos.opciones_animacion else None))


@router.get("/tarea/{id_tarea}/eventos", response_class=StreamingResponse,
            summary="Progreso de una síntesis (SSE)",
            responses={**documentar_sse(
                "Eventos `bloque`, `log`, y uno final: `fin`, `error` o `cancelada`. En modo "
                "emocional, antes llegan `analisis` (el LLM está trabajando) y `segmentos`, y "
                "cada `bloque` trae `emocion` e `intensidad`.",
                'data: {"tipo": "bloque", "indice": 1, "total": 1, "texto": "Hola mundo"}\n\n'
                'data: {"tipo": "log", "linea": "..."}\n\n'
                'data: {"tipo": "fin", "archivo": "20261009-220339-be22d1d7a784.wav", '
                '"duracion": 3.04, "segundos": 4.3}\n\n'),
                **_TAREA_NO_ENCONTRADA})
async def eventos(id_tarea: str, s: ServiciosDep) -> StreamingResponse:
    tarea = s.tareas.obtener(id_tarea)

    async def flujo():
        while True:
            try:
                dato = tarea.eventos.get_nowait()
            except queue.Empty:
                if tarea.estado != "en_curso":
                    break
                await asyncio.sleep(0.08)
                continue
            yield evento(dato, ensure_ascii=False)
            if dato["tipo"] in EVENTOS_FINALES:
                return

    return respuesta_sse(flujo())


@router.post("/tarea/{id_tarea}/cancelar", response_model=Ok, summary="Cancelar una síntesis",
             responses=_TAREA_NO_ENCONTRADA)
def cancelar(id_tarea: str, s: ServiciosDep) -> dict:
    s.tareas.cancelar(id_tarea)
    return {"ok": True}
