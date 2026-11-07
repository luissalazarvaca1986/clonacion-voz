# -*- coding: utf-8 -*-
"""Errores de dominio y su traducción a respuestas HTTP.

Los servicios lanzan estas excepciones sin saber nada de HTTP; aquí se
convierten en `{"detail": "<mensaje>"}`, el mismo formato que HTTPException,
que es lo que la interfaz web enseña al usuario.
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ErrorDominio(Exception):
    estado = 500

    def __init__(self, mensaje: str) -> None:
        super().__init__(mensaje)
        self.mensaje = mensaje


class PeticionInvalida(ErrorDominio):
    estado = 400


class NoEncontrado(ErrorDominio):
    estado = 404


class Conflicto(ErrorDominio):
    estado = 409


class DemasiadoGrande(ErrorDominio):
    estado = 413


class ErrorProceso(ErrorDominio):
    """Falló una herramienta externa (ffmpeg, llama-tts)."""
    estado = 500


def _mensaje_validacion(error: dict) -> str:
    campo = ".".join(str(p) for p in error.get("loc", ()) if p != "body")
    return f"{campo}: {error.get('msg', 'valor no válido')}" if campo else error.get("msg", "")


def registrar_manejadores(app: FastAPI) -> None:
    @app.exception_handler(ErrorDominio)
    async def _dominio(_: Request, exc: ErrorDominio) -> JSONResponse:
        return JSONResponse({"detail": exc.mensaje}, status_code=exc.estado)

    @app.exception_handler(RequestValidationError)
    async def _validacion(_: Request, exc: RequestValidationError) -> JSONResponse:
        # `detail` como texto, igual que el resto de errores, para que la web lo
        # pueda mostrar tal cual; el desglose va aparte en `errores`.
        errores = exc.errors()
        return JSONResponse(
            {"detail": "Datos no válidos · " + "; ".join(_mensaje_validacion(e) for e in errores),
             "errores": jsonable_encoder(errores, custom_encoder={Exception: str})},
            status_code=422,
        )
