# -*- coding: utf-8 -*-
"""Inyección de dependencias: las rutas reciben los servicios ya montados."""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.servicios import Servicios


def obtener_servicios(request: Request) -> Servicios:
    return request.app.state.servicios


ServiciosDep = Annotated[Servicios, Depends(obtener_servicios)]
