# -*- coding: utf-8 -*-
"""Esquemas de la descarga de modelos desde Hugging Face."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ArchivoCatalogo(BaseModel):
    archivo: str
    bytes: int
    sha256: str
    etiqueta: str


class Catalogo(BaseModel):
    repo: str
    url: str
    catalogo: dict[str, dict[str, ArchivoCatalogo]] = Field(
        description="Cuantizaciones disponibles por grupo: «modelo» y «mmproj»")
    por_defecto: dict[str, str]
    falta: bool = Field(description="True si en la carpeta no hay un par modelo + mmproj")


class PeticionDescarga(BaseModel):
    model_config = ConfigDict(extra="ignore", json_schema_extra={
        "examples": [{"modelo": "Q4_K_M", "mmproj": "Q8_0"}],
    })

    modelo: Optional[str] = Field(None, description="Clave del catálogo; vacío = recomendado")
    mmproj: Optional[str] = Field(None, description="Clave del catálogo; vacío = recomendado")


class DescargaIniciada(BaseModel):
    ok: bool = True
    modelo: str
    mmproj: str


class ProgresoDescarga(BaseModel):
    """Cada evento del flujo SSE /api/modelo/progreso."""
    activa: bool
    terminada: bool
    error: str
    archivo: str
    hecho: int
    total: int
    velocidad: int = Field(description="Bytes por segundo")
