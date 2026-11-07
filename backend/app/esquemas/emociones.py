# -*- coding: utf-8 -*-
"""Esquemas del modo emocional: perfiles, segmentos y análisis."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.esquemas.sistema import Temp, TopK, TopP, vacio_a_none
from app.servicios.emociones import INTENSIDADES, PERFILES

Emocion = Literal[tuple(PERFILES)]          # type: ignore[valid-type]
Intensidad = Literal[tuple(INTENSIDADES)]   # type: ignore[valid-type]


class AjustesSegmentoEntrada(BaseModel):
    """Ajustes revisados por el usuario; los que falten salen de la emoción."""
    model_config = ConfigDict(extra="ignore")

    temp: Optional[float] = Field(None, ge=0.1, le=1.5)
    top_p: Optional[float] = Field(None, gt=0, le=1)
    top_k: Optional[int] = Field(None, ge=0, le=200)
    ritmo: Optional[float] = Field(None, ge=0.5, le=2.0, description="Velocidad (1 = igual)")
    tono: Optional[float] = Field(None, ge=-6, le=6, description="Semitonos")
    volumen_db: Optional[float] = Field(None, ge=-12, le=12)
    pausa_ms: Optional[int] = Field(None, ge=0, le=5000)
    escala_expresion: Optional[float] = Field(None, ge=0, le=3, description="Expresión facial al animar")
    estilo_pose: Optional[int] = Field(None, ge=0, le=45, description="Estilo de cabeza al animar")


class Segmento(BaseModel):
    texto: str = Field(min_length=1, max_length=20000)
    emocion: Emocion = "neutro"
    intensidad: Intensidad = "media"
    voz: Optional[str] = Field(None, description="Id de la voz que dice el segmento; vacío = la "
                                                 "del avatar o la general")
    avatar: Optional[str] = Field(None, description="Id del avatar que aparece en el Meet-Video "
                                                    "mientras suena el segmento")
    escena: Optional[Literal["dia", "noche", "lluvia"]] = Field(
        None, description="Fondo del Meet-Video en este segmento; vacío = el general")
    ajustes: Optional[AjustesSegmentoEntrada] = Field(
        None, description="Ajustes revisados; sin ellos se usan los sugeridos por la emoción")

    _vacios = field_validator("voz", "avatar", "escena", mode="before")(vacio_a_none)


class AjustesSegmento(BaseModel):
    temp: float
    top_p: float
    top_k: int
    ritmo: float = Field(description="Factor de velocidad (1 = sin cambio)")
    tono: float = Field(description="Desplazamiento de tono en semitonos")
    volumen_db: float
    pausa_ms: int = Field(description="Silencio tras el segmento")
    escala_expresion: float = Field(1.0, description="Expresión facial sugerida al animar")
    estilo_pose: int = Field(0, description="Estilo de movimiento de cabeza al animar")


class SegmentoAnalizado(Segmento):
    ajustes: AjustesSegmento


class Analisis(BaseModel):
    origen: Literal["llm", "marcas", "usuario", "neutro"] = Field(
        description="llm = etiquetado por el modelo · marcas = [emocion:intensidad] en el texto")
    modelo: str = Field(description="LLM usado, si lo hubo")
    segundos: float
    segmentos: list[SegmentoAnalizado]


class PeticionAnalizar(BaseModel):
    """Los ajustes base son opcionales: sirven para calcular los ajustes de cada segmento."""
    model_config = ConfigDict(extra="ignore", json_schema_extra={"examples": [{
        "texto": "Estamos aquí presentando este escenario. ¡Qué emoción que tengo, cuánta "
                 "alegría! Pero esto es una realidad que no esperábamos."}]})

    texto: Optional[str] = Field(None, max_length=20000)
    temp: Optional[Temp] = None
    top_p: Optional[TopP] = None
    top_k: Optional[TopK] = None
    pausa_ms: Optional[int] = Field(None, ge=0, le=10000)

    _vacios = field_validator("*", mode="before")(vacio_a_none)


class PeticionSugerencia(BaseModel):
    emocion: Emocion
    intensidad: Intensidad = "media"
    temp: Optional[Temp] = None
    top_p: Optional[TopP] = None
    top_k: Optional[TopK] = None
    pausa_ms: Optional[int] = Field(None, ge=0, le=10000)


class PerfilEmocion(BaseModel):
    id: str
    etiqueta: str
    descripcion: str
    temp: float = Field(description="Se suma a la temperatura base (a intensidad alta)")
    top_p: float
    top_k: int
    ritmo: float
    tono: float
    volumen_db: float
    pausa_ms: int
    expresion: float


class CatalogoEmociones(BaseModel):
    emociones: list[PerfilEmocion]
    intensidades: list[str]
    por_defecto: dict[str, str]
