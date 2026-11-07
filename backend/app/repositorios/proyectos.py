# -*- coding: utf-8 -*-
"""Persistencia de los proyectos (cada generación del historial) y su configuración.

Un proyecto guarda todo lo necesario para mostrarlo, descargar sus partes y
volver a generar cualquiera de sus segmentos:

    {
      "_id": "20261009-234735-41b47d1d006a",   # nombre del WAV final sin extensión
      "titulo", "texto", "creado", "actualizado",
      "duracion", "audio": "<id>.wav", "video": "<id>.mp4" | None,
      "config": {                              # ajustes generales de la generación
        "voz", "dispositivo", "ajustes", "modo", "video", "escena", "avatar",
        "animado", "motor_animacion", "opciones_animacion"
      },
      "segmentos": [{
        "indice", "texto", "emocion", "intensidad",
        "voz", "avatar", "escena", "ajustes",  # configuración (ids)
        "voz_nombre", "avatar_nombre",         # para mostrar
        "duracion", "audio", "video"           # archivos de la parte
      }]
    }

Dos implementaciones con la misma interfaz: MongoDB (en Docker) y memoria (tests
o ejecución sin base de datos).
"""
from __future__ import annotations

import copy
import logging
import threading
from datetime import datetime
from typing import Any, Protocol

log = logging.getLogger(__name__)


def ahora() -> str:
    return datetime.now().isoformat(timespec="seconds")


class RepositorioProyectos(Protocol):
    tipo: str

    def disponible(self) -> bool: ...

    def guardar(self, proyecto: dict[str, Any]) -> None:
        """Crea o reemplaza el proyecto entero."""
        ...

    def obtener(self, id_proyecto: str) -> dict[str, Any] | None: ...

    def listar(self, limite: int) -> list[dict[str, Any]]:
        """Los más recientes primero."""
        ...

    def actualizar_segmento(self, id_proyecto: str, indice: int, segmento: dict[str, Any],
                            cambios: dict[str, Any]) -> None:
        """Reemplaza un segmento y aplica `cambios` al resto del documento."""
        ...

    def eliminar(self, id_proyecto: str) -> bool: ...


class RepositorioMemoria:
    """Sin base de datos: útil en tests y para arrancar el backend a mano."""
    tipo = "memoria"

    def __init__(self) -> None:
        self._datos: dict[str, dict[str, Any]] = {}
        self._cerrojo = threading.Lock()

    def disponible(self) -> bool:
        return True

    def guardar(self, proyecto: dict[str, Any]) -> None:
        with self._cerrojo:
            self._datos[proyecto["_id"]] = copy.deepcopy(proyecto)

    def obtener(self, id_proyecto: str) -> dict[str, Any] | None:
        with self._cerrojo:
            p = self._datos.get(id_proyecto)
            return copy.deepcopy(p) if p else None

    def listar(self, limite: int) -> list[dict[str, Any]]:
        with self._cerrojo:
            proyectos = sorted(self._datos.values(), key=lambda p: p.get("creado", ""),
                               reverse=True)
            return [copy.deepcopy(p) for p in proyectos[:limite]]

    def actualizar_segmento(self, id_proyecto: str, indice: int, segmento: dict[str, Any],
                            cambios: dict[str, Any]) -> None:
        with self._cerrojo:
            p = self._datos[id_proyecto]
            p["segmentos"] = [copy.deepcopy(segmento) if s["indice"] == indice else s
                              for s in p["segmentos"]]
            p.update(copy.deepcopy(cambios))

    def eliminar(self, id_proyecto: str) -> bool:
        with self._cerrojo:
            return self._datos.pop(id_proyecto, None) is not None


class RepositorioMongo:
    tipo = "mongodb"

    def __init__(self, url: str, base: str = "clonar_voz") -> None:
        from pymongo import DESCENDING, MongoClient   # solo hace falta si se usa Mongo

        self._cliente = MongoClient(url, serverSelectionTimeoutMS=3000, tz_aware=False)
        self._coleccion = self._cliente[base]["proyectos"]
        self._descendente = DESCENDING
        self._indices_creados = False

    def _coleccion_lista(self):
        if not self._indices_creados:
            self._coleccion.create_index([("creado", self._descendente)])
            self._indices_creados = True
        return self._coleccion

    def disponible(self) -> bool:
        try:
            self._cliente.admin.command("ping")
            return True
        except Exception:  # noqa: BLE001 — cualquier fallo de conexión cuenta como caída
            return False

    def guardar(self, proyecto: dict[str, Any]) -> None:
        self._coleccion_lista().replace_one({"_id": proyecto["_id"]}, proyecto, upsert=True)

    def obtener(self, id_proyecto: str) -> dict[str, Any] | None:
        return self._coleccion_lista().find_one({"_id": id_proyecto})

    def listar(self, limite: int) -> list[dict[str, Any]]:
        return list(self._coleccion_lista().find().sort("creado", self._descendente).limit(limite))

    def actualizar_segmento(self, id_proyecto: str, indice: int, segmento: dict[str, Any],
                            cambios: dict[str, Any]) -> None:
        self._coleccion_lista().update_one(
            {"_id": id_proyecto, "segmentos.indice": indice},
            {"$set": {"segmentos.$": segmento, **cambios}})

    def eliminar(self, id_proyecto: str) -> bool:
        return self._coleccion_lista().delete_one({"_id": id_proyecto}).deleted_count > 0
