# -*- coding: utf-8 -*-
"""Esquemas de estado del sistema y configuración."""
from __future__ import annotations

from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.nucleo.ajustes import IDIOMAS

# ---- Tipos con límites, compartidos con la petición de generación ----------
Idioma = Literal[tuple(IDIOMAS)]  # type: ignore[valid-type]
TopK = Annotated[int, Field(ge=0, le=1000, description="Top-K del muestreo (0 = desactivado)")]
TopP = Annotated[float, Field(gt=0, le=1, description="Top-P (núcleo) del muestreo")]
Temp = Annotated[float, Field(gt=0, le=5, description="Temperatura: baja = estable, alta = expresiva")]
Semilla = Annotated[int, Field(ge=-1, description="-1 = aleatoria")]
MaxFrames = Annotated[int, Field(ge=12, le=24000, description="Tope de frames por bloque (12 = 1 s)")]
CharsBloque = Annotated[int, Field(ge=20, le=5000, description="Caracteres máximos por bloque")]
PausaMs = Annotated[int, Field(ge=0, le=10000, description="Silencio entre bloques, en ms")]
CapasGpu = Annotated[int, Field(ge=0, le=999, description="Capas descargadas en la GPU (-ngl)")]
Hilos = Annotated[int, Field(ge=0, le=1024, description="Hilos de CPU (0 = automático)")]
Contexto = Annotated[int, Field(ge=0, le=131072, description="Tamaño de contexto (-c)")]
Lote = Annotated[int, Field(ge=1, le=8192, description="Tamaño de lote (-b / -ub)")]


def vacio_a_none(valor: Any) -> Any:
    """Las cadenas vacías cuentan como «no indicado», como en la API original."""
    return None if valor == "" else valor


class Configuracion(BaseModel):
    """Contenido de config.json (completo, con los valores por defecto aplicados)."""
    binario: str = Field(description="Ruta a llama-tts; vacío = autodetección")
    modelo: str = Field(description="Ruta al .gguf principal; vacío = autodetección")
    mmproj: str = Field(description="Ruta al mmproj .gguf; vacío = autodetección")
    dispositivo: str = Field(description="auto | cpu | id de dispositivo (p. ej. CUDA0, Vulkan1)")
    capas_gpu: int
    contexto: int
    lote: int
    hilos: int
    idioma: str
    top_k: int
    top_p: float
    temp: float
    semilla: int
    max_frames: int
    chars_por_bloque: int
    pausa_ms: int


class ConfiguracionParcial(BaseModel):
    """Cambios a guardar en config.json. Solo se tocan las claves enviadas."""
    model_config = ConfigDict(extra="ignore", json_schema_extra={
        "examples": [{"idioma": "es", "dispositivo": "auto", "temp": 0.7, "top_p": 0.95}],
    })

    binario: Optional[str] = None
    modelo: Optional[str] = None
    mmproj: Optional[str] = None
    dispositivo: Optional[str] = None
    capas_gpu: Optional[CapasGpu] = None
    contexto: Optional[Contexto] = None
    lote: Optional[Lote] = None
    hilos: Optional[Hilos] = None
    idioma: Optional[Idioma] = None
    top_k: Optional[TopK] = None
    top_p: Optional[TopP] = None
    temp: Optional[Temp] = None
    semilla: Optional[Semilla] = None
    max_frames: Optional[MaxFrames] = None
    chars_por_bloque: Optional[CharsBloque] = None
    pausa_ms: Optional[PausaMs] = None

    _vacios = field_validator("*", mode="before")(vacio_a_none)


class Dispositivo(BaseModel):
    id: str = Field(examples=["CUDA0"])
    nombre: str = Field(examples=["NVIDIA GeForce GTX 1650"])
    memoria_mb: int
    libre_mb: int


class EstadoLLM(BaseModel):
    configurado: bool = Field(description="Hay una URL de llama-server configurada")
    disponible: bool = Field(description="Se puede usar (bajo demanda: el modelo existe)")
    modelo: str
    modo: str = Field("desactivado", description="bajo_demanda | servidor | desactivado")
    activo: bool = Field(False, description="El modelo está cargado en memoria ahora mismo")


class EstadoBaseDatos(BaseModel):
    tipo: str = Field(description="mongodb | memoria")
    disponible: bool


class Estado(BaseModel):
    """Diagnóstico completo: binario, modelos, GPUs, ffmpeg y configuración."""
    binario: str
    binario_ok: bool
    version: str
    soporta_qwen3tts: bool
    modelo: str
    modelo_ok: bool
    mmproj: str
    mmproj_ok: bool
    dispositivos: list[Dispositivo]
    dispositivo_preferido: str
    ffmpeg: bool
    idiomas: dict[str, str]
    cpus: int
    config: Configuracion
    llm: EstadoLLM
    base_datos: EstadoBaseDatos


class Ok(BaseModel):
    ok: bool = True


class Salud(BaseModel):
    estado: Literal["ok"] = "ok"
    tareas_en_curso: int
    descarga_activa: bool
