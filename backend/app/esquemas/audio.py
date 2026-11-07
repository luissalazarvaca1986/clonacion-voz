# -*- coding: utf-8 -*-
"""Esquemas de voces, generación e historial."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.esquemas.animacion import OpcionesAnimacion
from app.esquemas.emociones import Segmento
from app.esquemas.sistema import (CapasGpu, CharsBloque, Hilos, Idioma, MaxFrames, PausaMs,
                                  Semilla, Temp, TopK, TopP, vacio_a_none)


class Voz(BaseModel):
    """Voz de la biblioteca. Los campos extra de voz.json se devuelven tal cual."""
    model_config = ConfigDict(extra="allow")

    nombre: str = Field(examples=["Luis"])
    transcripcion: str = ""
    creada: str = Field("", description="Fecha ISO 8601", examples=["2026-10-09T16:05:52"])
    id: str = Field(examples=["bfbe634c94ad"])
    duracion: float = Field(description="Segundos de la referencia (máx. 15)", examples=[15.0])


class PeticionGenerar(BaseModel):
    """Texto a sintetizar. Los ajustes que no se envíen salen de config.json."""
    model_config = ConfigDict(extra="ignore", json_schema_extra={
        "examples": [{"texto": "Hola mundo", "idioma": "es", "dispositivo": "auto",
                      "voz": "bfbe634c94ad", "temp": 0.8}],
    })

    texto: Optional[str] = Field(None, max_length=20000, description="Texto a decir")
    voz: Optional[str] = Field(None, description="Id de la voz de referencia; vacío = voz por defecto")
    dispositivo: Optional[str] = Field(None, description="auto | cpu | id de dispositivo")
    idioma: Optional[Idioma] = None
    top_k: Optional[TopK] = None
    top_p: Optional[TopP] = None
    temp: Optional[Temp] = None
    semilla: Optional[Semilla] = None
    max_frames: Optional[MaxFrames] = None
    chars_por_bloque: Optional[CharsBloque] = None
    pausa_ms: Optional[PausaMs] = None
    capas_gpu: Optional[CapasGpu] = None
    hilos: Optional[Hilos] = None
    modo: Literal["simple", "emocional"] = Field(
        "simple", description="emocional = cada segmento con su perfil de emoción")
    segmentos: Optional[list[Segmento]] = Field(
        None, description="Modo emocional: segmentos ya revisados; si faltan, los decide el LLM "
                          "(o las marcas [emocion:intensidad] del texto)")

    video: bool = Field(False, description="Generar además el Meet-Video (MP4)")
    escena: Literal["dia", "noche", "lluvia"] = Field(
        "dia", description="Fondo del Meet-Video para todo el mensaje")
    avatar: Optional[str] = Field(None, description="Avatar general del Meet-Video")
    animado: bool = Field(False, description="Animar el avatar (SadTalker) en vez de imagen fija")
    motor_animacion: Optional[str] = Field(None, description="Motor de animación; vacío = sadtalker")
    opciones_animacion: Optional[OpcionesAnimacion] = None

    _vacios = field_validator("*", mode="before")(vacio_a_none)


class TareaCreada(BaseModel):
    id: str = Field(description="Id de la tarea, para /api/tarea/{id}/eventos")
    bloques: int = Field(description="Bloques en que se ha troceado el texto")
    dispositivo: str = Field(description="Dispositivo usado, o «CPU»", examples=["CUDA0"])
    archivo: str = Field(description="Nombre del WAV que se generará",
                         examples=["20261009-220339-be22d1d7a784.wav"])
    modo: str = "simple"
    video: Optional[str] = Field(None, description="Nombre del MP4 que se generará, si se pidió")
    animado: bool = False


class ParteGeneracion(BaseModel):
    indice: int
    texto: str
    emocion: Optional[str] = None
    intensidad: Optional[str] = None
    voz: Optional[str] = None
    avatar: Optional[str] = None
    escena: Optional[str] = None
    duracion: float
    audio: str = Field(description="/api/salidas/{generacion}/partes/{audio}")
    video: Optional[str] = Field(None, description="/api/salidas/{generacion}/partes/{video}")


class Salida(BaseModel):
    archivo: str
    tamano: int = Field(description="Bytes")
    duracion: float = Field(description="Segundos")
    fecha: str = Field(description="Fecha ISO 8601 de creación")
    video: Optional[str] = Field(None, description="Meet-Video asociado (/api/videos/{video})")
    titulo: str = Field(description="Comienzo del texto, para mostrar la generación")
    editable: bool = Field(False, description="Guarda su configuración: se pueden regenerar segmentos")
    segmentos: list[ParteGeneracion] = Field(description="Partes descargables por segmento")


class PosicionAvatar(BaseModel):
    """Colocación sobre el fondo, en fracción del fotograma. Siempre alineado abajo
    y con el alto proporcional al ancho (nunca se deforma)."""
    ancho: float = Field(description="Ancho del avatar (0,1 a 1)", examples=[0.65])
    izquierda: float = Field(description="Separación respecto al borde izquierdo", examples=[0.25])


class Avatar(BaseModel):
    id: str = Field(description="Nombre de su carpeta en avatares/", examples=["Luis"])
    nombre: str
    voz: Optional[str] = Field(None, description="Voz con la que habla por defecto")
    creado: str
    transparente: bool = Field(description="La imagen del avatar tiene canal alfa")
    escenas: list[str] = Field(description="Fondos disponibles", examples=[["dia", "noche", "lluvia"]])
    completo: bool = Field(description="Tiene imagen y los tres fondos: ya sirve para video")
    posicion: PosicionAvatar


class CambiosAvatar(BaseModel):
    model_config = ConfigDict(extra="ignore")

    nombre: Optional[str] = Field(None, max_length=60)
    voz: Optional[str] = Field(None, description="Id de la voz; cadena vacía = sin voz asociada")
    ancho: Optional[float] = Field(None, ge=0.1, le=1.0, description="Ancho sobre el fondo")
    izquierda: Optional[float] = Field(None, ge=0, le=0.9, description="Separación izquierda")
