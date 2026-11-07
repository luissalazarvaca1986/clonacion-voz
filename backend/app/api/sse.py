# -*- coding: utf-8 -*-
"""Utilidades para respuestas Server-Sent Events."""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

from fastapi.responses import StreamingResponse

CABECERAS_SSE = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def evento(datos: dict[str, Any], ensure_ascii: bool = True) -> str:
    return f"data: {json.dumps(datos, ensure_ascii=ensure_ascii)}\n\n"


def respuesta_sse(flujo: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(flujo, media_type="text/event-stream", headers=CABECERAS_SSE)


def documentar_sse(descripcion: str, ejemplo: str) -> dict:
    """Bloque `responses` para que Swagger muestre el flujo SSE."""
    return {200: {"description": descripcion,
                  "content": {"text/event-stream": {"schema": {"type": "string"},
                                                    "example": ejemplo}}}}
