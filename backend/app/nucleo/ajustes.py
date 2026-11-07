# -*- coding: utf-8 -*-
"""Rutas de trabajo y constantes de la aplicación.

Las carpetas se pueden redirigir con variables de entorno, útil en Docker y en
los tests:

    CLONAR_BASE        raíz por defecto de todo lo demás (backend/)
    CLONAR_DIR_MODELO  .gguf del modelo
    CLONAR_DIR_VOCES   biblioteca de voces
    CLONAR_DIR_SALIDAS audios generados
    CLONAR_DIR_AVATARES avatares (imagen + fondos día/noche/lluvia)
    CLONAR_DIR_VIDEOS  Meet-Videos generados
    CLONAR_DIR_TMP     trozos intermedios de cada síntesis
    CLONAR_CONFIG      archivo de configuración (config.json)
    CLONAR_DIR_ANIMACION área de trabajo compartida con el contenedor del animador
    CLONAR_ANIMADOR_URL  servidor de animación facial (SadTalker) ("" = desactivado)
    CLONAR_ANIMADOR_TIMEOUT segundos máximos por animación
    CLONAR_MONGO_URL   MongoDB para proyectos y su configuración ("" = en memoria)
    CLONAR_LLM_MODELO  .gguf del LLM, que se carga solo mientras analiza (bajo demanda)
    CLONAR_LLM_HILOS   hilos de CPU del LLM
    CLONAR_LLM_URL     llama-server externo, si se prefiere uno siempre encendido
    CLONAR_LLM_TIMEOUT segundos máximos por petición al LLM
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

# En Windows evita que cada llamada a llama-tts/ffmpeg abra una consola
SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)

MAX_BYTES_REFERENCIA = 60 * 1024 * 1024   # 60 MB de audio de referencia
MAX_SEGUNDOS_REFERENCIA = 15
VIDA_TAREAS_SEGUNDOS = 900                # se olvidan las tareas acabadas hace 15 min
MAX_HISTORIAL = 80                        # audios que devuelve /api/salidas
MAX_BYTES_IMAGEN = 25 * 1024 * 1024       # por imagen de avatar o fondo
MAX_SEGUNDOS_ANIMACION = 180              # audio máximo de una animación suelta

# Idiomas admitidos por --tts-lang (ISO 639-1)
IDIOMAS: dict[str, str] = {
    "es": "Español",
    "en": "Inglés",
    "zh": "Chino",
    "de": "Alemán",
    "it": "Italiano",
    "pt": "Portugués",
    "ja": "Japonés",
    "ko": "Coreano",
    "fr": "Francés",
    "ru": "Ruso",
}


@dataclass(frozen=True)
class Rutas:
    base: Path
    modelo: Path
    voces: Path
    salidas: Path
    tmp: Path
    config: Path
    avatares: Path
    videos: Path
    animacion: Path = Path("/tmp/clonar-voz-animacion")
    animador_url: str = ""
    animador_timeout: float = 3600.0
    mongo_url: str = ""
    llm_url: str = ""
    llm_timeout: float = 120.0
    llm_modelo: Path | None = None
    llm_hilos: int = 6

    @classmethod
    def desde_entorno(cls) -> "Rutas":
        # backend/app/nucleo/ajustes.py -> backend/
        base = Path(os.environ.get("CLONAR_BASE") or Path(__file__).resolve().parents[2])

        def ruta(variable: str, defecto: Path) -> Path:
            return Path(os.environ.get(variable) or defecto)

        return cls(
            base=base,
            modelo=ruta("CLONAR_DIR_MODELO", base / "modelo"),
            voces=ruta("CLONAR_DIR_VOCES", base / "voces"),
            salidas=ruta("CLONAR_DIR_SALIDAS", base / "salidas"),
            tmp=ruta("CLONAR_DIR_TMP", base / ".tmp"),
            config=ruta("CLONAR_CONFIG", base / "config.json"),
            avatares=ruta("CLONAR_DIR_AVATARES", base / "avatares"),
            videos=ruta("CLONAR_DIR_VIDEOS", base / "videos"),
            animacion=ruta("CLONAR_DIR_ANIMACION", base / "animacion"),
            animador_url=os.environ.get("CLONAR_ANIMADOR_URL", ""),
            animador_timeout=float(os.environ.get("CLONAR_ANIMADOR_TIMEOUT") or 3600),
            mongo_url=os.environ.get("CLONAR_MONGO_URL", ""),
            llm_url=os.environ.get("CLONAR_LLM_URL", ""),
            llm_timeout=float(os.environ.get("CLONAR_LLM_TIMEOUT") or 120),
            llm_modelo=Path(os.environ["CLONAR_LLM_MODELO"]) if os.environ.get("CLONAR_LLM_MODELO") else None,
            llm_hilos=int(os.environ.get("CLONAR_LLM_HILOS") or 6),
        )

    def crear(self) -> None:
        for carpeta in (self.modelo, self.voces, self.salidas, self.tmp, self.config.parent,
                        self.avatares, self.videos, self.animacion):
            carpeta.mkdir(parents=True, exist_ok=True)
