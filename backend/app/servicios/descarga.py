# -*- coding: utf-8 -*-
"""Descarga de los modelos desde Hugging Face en segundo plano."""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Callable

from app.nucleo.errores import Conflicto, PeticionInvalida
from app.servicios import huggingface

log = logging.getLogger(__name__)


class GestorDescarga:
    """Estado compartido de la (única) descarga de modelos en curso."""

    def __init__(self, dir_modelo: Path, al_terminar: Callable[[], None]) -> None:
        self._dir_modelo = dir_modelo
        self._al_terminar = al_terminar
        self._cerrojo = threading.Lock()
        self._reiniciar()

    def _reiniciar(self) -> None:
        self.activa = False
        self.cancelada = False
        self.error = ""
        self.terminada = False
        self.archivo = ""
        self.hecho = 0
        self.total = 0
        self.velocidad = 0.0

    def catalogo(self) -> dict[str, Any]:
        return {
            "repo": huggingface.REPO,
            "url": f"https://huggingface.co/{huggingface.REPO}",
            "catalogo": huggingface.CATALOGO,
            "por_defecto": huggingface.POR_DEFECTO,
            "falta": huggingface.falta_algo(self._dir_modelo),
        }

    def iniciar(self, modelo: str | None, mmproj: str | None) -> dict[str, Any]:
        clave_modelo = str(modelo or huggingface.POR_DEFECTO["modelo"])
        clave_mmproj = str(mmproj or huggingface.POR_DEFECTO["mmproj"])
        if (clave_modelo not in huggingface.CATALOGO["modelo"]
                or clave_mmproj not in huggingface.CATALOGO["mmproj"]):
            raise PeticionInvalida("Cuantización desconocida.")

        with self._cerrojo:
            if self.activa:
                raise Conflicto("Ya hay una descarga en curso.")
            self._reiniciar()
            self.activa = True

        threading.Thread(target=self._trabajo, args=(clave_modelo, clave_mmproj),
                         daemon=True, name="descarga-modelo").start()
        log.info("Descarga iniciada: modelo %s + mmproj %s", clave_modelo, clave_mmproj)
        return {"ok": True, "modelo": clave_modelo, "mmproj": clave_mmproj}

    def cancelar(self) -> None:
        self.cancelada = True

    def _progreso(self, archivo: str, hecho: int, total: int, velocidad: float) -> None:
        self.archivo, self.hecho, self.total, self.velocidad = archivo, hecho, total, velocidad

    def _trabajo(self, clave_modelo: str, clave_mmproj: str) -> None:
        try:
            huggingface.descargar_todo(
                clave_modelo, clave_mmproj, self._dir_modelo, self._progreso,
                verificar=True, cancelado=lambda: self.cancelada)
            self.terminada = True
            self._al_terminar()
            log.info("Descarga terminada y verificada")
        except Exception as exc:  # noqa: BLE001 — se enseña tal cual en la web
            self.error = str(exc)
            log.warning("Descarga fallida: %s", exc)
        finally:
            self.activa = False

    def instantanea(self) -> dict[str, Any]:
        return {
            "activa": self.activa, "terminada": self.terminada, "error": self.error,
            "archivo": self.archivo, "hecho": self.hecho, "total": self.total,
            "velocidad": round(self.velocidad),
        }
