# -*- coding: utf-8 -*-
"""Biblioteca de voces de referencia.

Cada voz es una carpeta `voces/<id>/` con `referencia.wav` (WAV mono 16 kHz) y
`voz.json` (nombre, transcripción y fecha). La carpeta es el registro: no hay
base de datos.
"""
from __future__ import annotations

import json
import logging
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

from app.nucleo.errores import DemasiadoGrande, NoEncontrado, PeticionInvalida
from app.servicios.audio import convertir_a_wav, duracion_wav

log = logging.getLogger(__name__)

_TROZO = 1 << 20


class BibliotecaVoces:
    def __init__(self, carpeta: Path, max_bytes: int) -> None:
        self._carpeta = carpeta
        self._max_bytes = max_bytes

    def _carpeta_de(self, id_voz: str) -> Path:
        # Path(...).name descarta cualquier intento de salir de la carpeta (../)
        return self._carpeta / Path(str(id_voz)).name

    @staticmethod
    def _meta(carpeta: Path) -> dict[str, Any] | None:
        meta, audio = carpeta / "voz.json", carpeta / "referencia.wav"
        if not meta.is_file() or not audio.is_file():
            return None
        try:
            datos = json.loads(meta.read_text("utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(datos, dict):
            return None
        datos["id"] = carpeta.name
        datos["duracion"] = round(duracion_wav(audio), 2)
        return datos

    def listar(self) -> list[dict[str, Any]]:
        if not self._carpeta.is_dir():
            return []
        voces = [self._meta(c) for c in self._carpeta.iterdir() if c.is_dir()]
        return sorted((v for v in voces if v), key=lambda v: str(v.get("creada", "")), reverse=True)

    def crear(self, archivo: BinaryIO, nombre_archivo: str | None,
              nombre: str, transcripcion: str = "") -> dict[str, Any]:
        id_voz = uuid.uuid4().hex[:12]
        carpeta = self._carpeta / id_voz
        carpeta.mkdir(parents=True)

        sufijo = Path(nombre_archivo or "audio.webm").suffix or ".webm"
        bruto = carpeta / f"original{sufijo}"
        try:
            # Copia por trozos: se corta en cuanto pasa del límite, sin cargarlo entero
            escrito = 0
            with open(bruto, "wb") as destino:
                while trozo := archivo.read(_TROZO):
                    escrito += len(trozo)
                    if escrito > self._max_bytes:
                        raise DemasiadoGrande("El audio de referencia no puede pasar de 60 MB. "
                                              "Con 10-15 segundos de voz es más que suficiente.")
                    destino.write(trozo)
            convertir_a_wav(bruto, carpeta / "referencia.wav")
        except Exception:
            shutil.rmtree(carpeta, ignore_errors=True)
            raise
        bruto.unlink(missing_ok=True)

        (carpeta / "voz.json").write_text(json.dumps({
            "nombre": nombre.strip() or "Voz sin nombre",
            "transcripcion": transcripcion.strip(),
            "creada": datetime.now().isoformat(timespec="seconds"),
        }, indent=2, ensure_ascii=False), "utf-8")
        log.info("Voz creada: %s (%s)", id_voz, nombre.strip())
        return self._meta(carpeta) or {}

    def borrar(self, id_voz: str) -> None:
        carpeta = self._carpeta_de(id_voz)
        if not carpeta.is_dir():
            raise NoEncontrado("Voz no encontrada")
        shutil.rmtree(carpeta, ignore_errors=True)
        log.info("Voz borrada: %s", carpeta.name)

    def audio(self, id_voz: str) -> Path:
        ruta = self._carpeta_de(id_voz) / "referencia.wav"
        if not ruta.is_file():
            raise NoEncontrado("Audio no encontrado")
        return ruta

    def referencia(self, id_voz: str) -> Path:
        """Ruta de la referencia para sintetizar; error 400 si no existe."""
        ruta = self._carpeta_de(id_voz) / "referencia.wav"
        if not ruta.is_file():
            raise PeticionInvalida("La voz de referencia no existe.")
        return ruta
