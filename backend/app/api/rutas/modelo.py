# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.api.dependencias import ServiciosDep
from app.api.sse import documentar_sse, evento, respuesta_sse
from app.esquemas.modelo import Catalogo, DescargaIniciada, PeticionDescarga
from app.esquemas.sistema import Ok

router = APIRouter(prefix="/api/modelo", tags=["Modelo"])


@router.get("/catalogo", response_model=Catalogo, summary="Cuantizaciones disponibles")
def catalogo(s: ServiciosDep) -> dict:
    return s.descarga.catalogo()


@router.post("/descargar", response_model=DescargaIniciada, summary="Descargar el modelo",
             responses={400: {"description": "Cuantización desconocida"},
                        409: {"description": "Ya hay una descarga en curso"}})
def descargar(datos: PeticionDescarga, s: ServiciosDep) -> dict:
    """Descarga en segundo plano el par modelo + mmproj desde Hugging Face.
    Es reanudable y verifica el SHA-256. El avance se sigue en `/api/modelo/progreso`."""
    return s.descarga.iniciar(datos.modelo, datos.mmproj)


@router.post("/cancelar", response_model=Ok, summary="Cancelar la descarga")
def cancelar(s: ServiciosDep) -> dict:
    s.descarga.cancelar()
    return {"ok": True}


@router.get("/progreso", response_class=StreamingResponse, summary="Progreso de la descarga (SSE)",
            responses=documentar_sse(
                "Un evento cada 0,4 s con el esquema ProgresoDescarga; termina cuando "
                "`activa` pasa a false.",
                'data: {"activa": true, "terminada": false, "error": "", "archivo": '
                '"Qwen3-TTS-12Hz-1.7B-Base-Q4_K_M.gguf", "hecho": 1048576, '
                '"total": 1035965280, "velocidad": 5242880}\n\n'))
async def progreso(s: ServiciosDep) -> StreamingResponse:
    async def flujo():
        while True:
            yield evento(s.descarga.instantanea())
            if not s.descarga.activa:
                return
            await asyncio.sleep(0.4)

    return respuesta_sse(flujo())
