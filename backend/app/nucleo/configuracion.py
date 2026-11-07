# -*- coding: utf-8 -*-
"""Configuración persistente (config.json) con valores por defecto."""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

CONFIG_POR_DEFECTO: dict[str, Any] = {
    "binario": "",           # ruta a llama-tts ("" = autodetección)
    "modelo": "",            # ruta al .gguf principal ("" = autodetección en ./modelo)
    "mmproj": "",            # ruta al mmproj .gguf
    "dispositivo": "auto",   # auto | cpu | <id de dispositivo, p.ej. Vulkan1>
    "capas_gpu": 99,
    # Con -c 0 llama.cpp usa el contexto del modelo y la caché KV pide 3,5 GB;
    # con lote 512 el vocoder pide otros 874 MB. En GPUs de 4 GB (GTX 1650) no
    # cabe. 2048 tokens y lote 128 bastan para un bloque + 15 s de referencia.
    "contexto": 2048,
    "lote": 128,
    "hilos": 0,              # 0 = automático
    "idioma": "es",
    "top_k": 40,
    "top_p": 0.95,
    "temp": 0.8,
    "semilla": -1,           # -1 = aleatoria
    "max_frames": 1200,      # -n : el modelo genera 12 frames por segundo de audio
    "chars_por_bloque": 280,
    "pausa_ms": 150,         # silencio insertado entre bloques
}


def _coaccionar(clave: str, valor: Any) -> Any:
    """Ajusta el valor al tipo del valor por defecto; si no se puede, usa el defecto."""
    defecto = CONFIG_POR_DEFECTO[clave]
    try:
        if isinstance(defecto, bool):
            return bool(valor)
        if isinstance(defecto, int):
            return int(float(valor))
        if isinstance(defecto, float):
            return float(valor)
        return "" if valor is None else str(valor)
    except (TypeError, ValueError):
        log.warning("config.json: valor no válido para %s (%r); se usa %r", clave, valor, defecto)
        return defecto


class RepositorioConfig:
    def __init__(self, archivo: Path) -> None:
        self._archivo = archivo
        self._cerrojo = threading.Lock()

    def cargar(self) -> dict[str, Any]:
        cfg = dict(CONFIG_POR_DEFECTO)
        if self._archivo.exists():
            try:
                guardada = json.loads(self._archivo.read_text("utf-8"))
                cfg.update({k: _coaccionar(k, v) for k, v in guardada.items()
                            if k in CONFIG_POR_DEFECTO})
            except (OSError, ValueError, AttributeError):
                log.warning("No se pudo leer %s; se usan los valores por defecto", self._archivo)
        return cfg

    def actualizar(self, cambios: dict[str, Any]) -> dict[str, Any]:
        """Mezcla `cambios` (solo claves conocidas) con lo guardado y lo persiste."""
        with self._cerrojo:
            cfg = self.cargar()
            cfg.update({k: _coaccionar(k, v) for k, v in cambios.items()
                        if k in CONFIG_POR_DEFECTO})
            self._guardar(cfg)
            return cfg

    def _guardar(self, cfg: dict[str, Any]) -> None:
        # Escritura atómica: un corte a mitad no deja un config.json roto
        self._archivo.parent.mkdir(parents=True, exist_ok=True)
        temporal = self._archivo.with_suffix(".json.tmp")
        temporal.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), "utf-8")
        os.replace(temporal, self._archivo)
