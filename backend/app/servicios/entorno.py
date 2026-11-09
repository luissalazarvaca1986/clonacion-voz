# -*- coding: utf-8 -*-
"""Detección del entorno: binario llama-tts, modelos .gguf, GPUs y ffmpeg."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.nucleo.ajustes import IDIOMAS, SIN_VENTANA

_PATRON_DISPOSITIVO = re.compile(
    r"^\s*([A-Za-z]+\d+):\s+(.+?)\s+\((\d+)\s*MiB,\s*(\d+)\s*MiB free\)\s*$"
)
_MARCAS_DEDICADAS = ("nvidia", "geforce", "rtx", "quadro", "tesla", "radeon rx", "arc")


@dataclass(frozen=True)
class RutasEfectivas:
    binario: str
    modelo: str
    mmproj: str


def _ejecutar(cmd: list[str], timeout: int = 30) -> str:
    try:
        r = subprocess.run(
            cmd, capture_output=True, timeout=timeout, text=True,
            encoding="utf-8", errors="replace", creationflags=SIN_VENTANA,
        )
        return (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return ""


def es_archivo(ruta: str) -> bool:
    return bool(ruta) and Path(ruta).is_file()


def hay_ffmpeg() -> bool:
    return bool(shutil.which("ffmpeg"))


class DetectorEntorno:
    """Las consultas al binario son lentas (cientos de ms): se cachean por ruta."""

    def __init__(self, dir_modelo: Path, base: Path) -> None:
        self._dir_modelo = dir_modelo
        self._base = base
        self._cache: dict[str, Any] = {}
        self._cerrojo = threading.Lock()

    def limpiar_cache(self) -> None:
        with self._cerrojo:
            self._cache.clear()

    def _cacheado(self, clave: str, calcular):
        with self._cerrojo:
            if clave in self._cache:
                return self._cache[clave]
        valor = calcular()
        with self._cerrojo:
            self._cache[clave] = valor
        return valor

    # ---- Localización ------------------------------------------------------
    def buscar_binario(self) -> str:
        """Localiza llama-tts en el PATH o en las rutas de instalación habituales."""
        en_path = shutil.which("llama-tts") or shutil.which("llama-tts.exe")
        if en_path:
            return str(Path(en_path).resolve())

        local = Path(os.environ.get("LOCALAPPDATA", ""))
        candidatos = [
            # Windows (winget)
            local / "Microsoft/WinGet/Packages"
            / "ggml.llamacpp_Microsoft.Winget.Source_8wekyb3d8bbwe/llama-tts.exe",
            self._base / "llamacpp" / "llama-tts.exe",
            self._base / "llama.cpp" / "llama-tts.exe",
            Path("C:/llama.cpp/llama-tts.exe"),
            # macOS (Homebrew) y Linux
            Path("/opt/homebrew/bin/llama-tts"),
            Path("/usr/local/bin/llama-tts"),
            Path("/usr/bin/llama-tts"),
            self._base / "llama.cpp" / "build" / "bin" / "llama-tts",
        ]
        for c in candidatos:
            if c.is_file():
                return str(c)

        raiz = local / "Microsoft/WinGet/Packages"
        if raiz.is_dir():
            for hallado in raiz.glob("*/llama-tts.exe"):
                return str(hallado)
        return ""

    def buscar_modelos(self) -> tuple[str, str]:
        """Devuelve (modelo, mmproj) a partir de los .gguf presentes en la carpeta del modelo."""
        if not self._dir_modelo.is_dir():
            return "", ""
        pares = self.listar_modelos_tts()
        if pares:
            return pares[0]["modelo"], pares[0]["mmproj"]
        ggufs = sorted(self._dir_modelo.glob("*.gguf"))
        modelo = next((g for g in ggufs if not g.name.lower().startswith("mmproj")), None)
        mmproj = next((g for g in ggufs if g.name.lower().startswith("mmproj")), None)
        return (str(modelo) if modelo else "", str(mmproj) if mmproj else "")

    @staticmethod
    def _base_modelo(nombre: str) -> str:
        """«Qwen3-TTS-12Hz-0.6B-Base-Q4_K_M» → «qwen3-tts-12hz-0.6b-base» (sin cuantización)."""
        return re.sub(r"[-.](?:[IQ]\d\w*|bf16|f16|f32)$", "", nombre, flags=re.I).lower()

    @staticmethod
    def _cuantizacion(nombre: str) -> str:
        """«Qwen3-TTS-12Hz-1.7B-Base-Q8_0» → «q8_0»; «…-bf16» → «bf16»; sin sufijo → «»."""
        m = re.search(r"[-.]([IQ]\d\w*|bf16|f16|f32)$", nombre, flags=re.I)
        return m.group(1).lower() if m else ""

    @staticmethod
    def _calidad(cuantizacion: str) -> int:
        """Orden de calidad de una cuantización: f32 > bf16 > f16 > Q8 > … > Q2."""
        if cuantizacion in ("f32", "bf16", "f16"):
            return {"f32": 100, "bf16": 99, "f16": 98}[cuantizacion]
        m = re.match(r"i?q(\d+)", cuantizacion)
        return int(m.group(1)) if m else 0

    def listar_modelos_tts(self) -> list[dict[str, str]]:
        """Modelos TTS de la carpeta, cada uno con el mmproj de su mismo tamaño (con varios
        tamaños, p. ej. 0.6B y 1.7B, se emparejan por el nombre sin la cuantización).
        Entre los mmproj de ese tamaño se prefiere el de la misma cuantización que el modelo
        (BF16 con BF16) y, si no lo hay, el de mayor calidad."""
        if not self._dir_modelo.is_dir():
            return []
        ggufs = sorted(self._dir_modelo.glob("*.gguf"))
        mmprojs = [g for g in ggufs if g.name.lower().startswith("mmproj")]
        pares = []
        for modelo in (g for g in ggufs if not g.name.lower().startswith("mmproj")):
            base = self._base_modelo(modelo.stem)
            candidatos = [m for m in mmprojs if base in m.name.lower()]
            if not candidatos and len(mmprojs) == 1:
                candidatos = mmprojs
            cuant = self._cuantizacion(modelo.stem)
            mmproj = next((m for m in candidatos if self._cuantizacion(m.stem) == cuant), None)
            if mmproj is None and candidatos:
                mmproj = max(candidatos, key=lambda m: self._calidad(self._cuantizacion(m.stem)))
            if mmproj is not None:
                pares.append({"id": modelo.name, "modelo": str(modelo), "mmproj": str(mmproj)})
        return pares

    def modelo_tts(self, archivo: str) -> dict[str, str] | None:
        """El par modelo/mmproj de un archivo de la carpeta (por su nombre), o None."""
        return next((p for p in self.listar_modelos_tts() if p["id"] == archivo), None)

    def rutas_efectivas(self, cfg: dict[str, Any]) -> RutasEfectivas:
        """Rutas de config.json; las vacías se autodetectan."""
        binario = cfg.get("binario") or self.buscar_binario()
        # Prioridad: config.json → CLONAR_TTS_MODELO/MMPROJ (por equipo) → autodetección
        modelo = cfg.get("modelo") or os.environ.get("CLONAR_TTS_MODELO", "")
        mmproj = cfg.get("mmproj") or os.environ.get("CLONAR_TTS_MMPROJ", "")
        if not modelo or not mmproj:
            auto_modelo, auto_mmproj = self.buscar_modelos()
            modelo = modelo or auto_modelo
            mmproj = mmproj or auto_mmproj
        return RutasEfectivas(binario, modelo, mmproj)

    # ---- Capacidades del binario ------------------------------------------
    def listar_dispositivos(self, binario: str) -> list[dict[str, Any]]:
        """Enumera los aceleradores que ve llama.cpp (Vulkan, CUDA, ...)."""
        if not es_archivo(binario):
            return []

        def calcular() -> list[dict[str, Any]]:
            dispositivos = []
            for linea in _ejecutar([binario, "--list-devices"]).splitlines():
                m = _PATRON_DISPOSITIVO.match(linea)
                if m:
                    dispositivos.append({
                        "id": m.group(1),
                        "nombre": m.group(2).strip(),
                        "memoria_mb": int(m.group(3)),
                        "libre_mb": int(m.group(4)),
                    })
            return dispositivos

        return self._cacheado(binario, calcular)

    def dispositivo_preferido(self, binario: str) -> str:
        """Elige la mejor GPU disponible. Prioriza la dedicada sobre la integrada."""
        dispositivos = self.listar_dispositivos(binario)
        if not dispositivos:
            return ""
        for d in dispositivos:
            if any(marca in d["nombre"].lower() for marca in _MARCAS_DEDICADAS):
                return d["id"]
        return max(dispositivos, key=lambda d: d["libre_mb"])["id"]

    def version_binario(self, binario: str) -> str:
        if not es_archivo(binario):
            return ""

        def calcular() -> str:
            m = re.search(r"version:\s*(\S+.*)", _ejecutar([binario, "--version"], timeout=20))
            return m.group(1).strip() if m else ""

        return self._cacheado(f"version::{binario}", calcular)

    def soporta_qwen3tts(self, binario: str) -> bool:
        """Qwen3-TTS exige un llama-tts que acepte --mmproj (build >= b10000 aprox.)."""
        if not es_archivo(binario):
            return False
        return self._cacheado(f"help::{binario}",
                              lambda: "--mmproj" in _ejecutar([binario, "--help"], timeout=25))

    # ---- Resumen para /api/estado -----------------------------------------
    def estado(self, cfg: dict[str, Any]) -> dict[str, Any]:
        rutas = self.rutas_efectivas(cfg)
        return {
            "binario": rutas.binario,
            "binario_ok": es_archivo(rutas.binario),
            "version": self.version_binario(rutas.binario),
            "soporta_qwen3tts": self.soporta_qwen3tts(rutas.binario),
            "modelo": rutas.modelo,
            "modelo_ok": es_archivo(rutas.modelo),
            "mmproj": rutas.mmproj,
            "mmproj_ok": es_archivo(rutas.mmproj),
            "dispositivos": self.listar_dispositivos(rutas.binario),
            "dispositivo_preferido": self.dispositivo_preferido(rutas.binario),
            "ffmpeg": hay_ffmpeg(),
            "idiomas": IDIOMAS,
            "cpus": os.cpu_count() or 4,
            "config": cfg,
        }
