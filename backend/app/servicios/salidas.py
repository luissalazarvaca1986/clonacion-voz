# -*- coding: utf-8 -*-
"""Historial de proyectos: archivos en disco y configuración en la base de datos.

Archivos de cada proyecto (su id es el nombre del WAV final sin extensión):

    audios/generados/<id>.wav                 audio final
    audios/generados/<id>/segmento-NN.wav     audio de cada segmento
    videos/<id>.mp4                           Meet-Video final (si se pidió)
    videos/<id>/segmento-NN.mp4               video + audio de cada segmento

Todo se publica de golpe al terminar: un resultado a medias nunca está aquí.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from app.nucleo.errores import NoEncontrado
from app.repositorios.proyectos import RepositorioProyectos, ahora
from app.servicios.audio import duracion_wav, unir_partes
from app.servicios.video import concatenar_videos

log = logging.getLogger(__name__)

_PARTE = re.compile(r"^segmento-\d{2}\.(wav|mp4)$")


def _titulo(texto: str, largo: int = 60) -> str:
    texto = " ".join(texto.split())
    return texto if len(texto) <= largo else texto[:largo].rsplit(" ", 1)[0] + "…"


def segmento_documento(parte, config: dict[str, Any]) -> dict[str, Any]:
    """Segmento tal y como se guarda: configuración (ids) + datos para mostrar + archivos."""
    m = parte.manifiesto()
    return {
        "indice": m["indice"], "texto": m["texto"],
        "emocion": config.get("emocion") or m["emocion"] or "neutro",
        "intensidad": config.get("intensidad") or m["intensidad"] or "media",
        "voz": config.get("voz"), "avatar": config.get("avatar"),
        "escena": config.get("escena") or m["escena"], "ajustes": config.get("ajustes"),
        "voz_nombre": m["voz"], "avatar_nombre": m["avatar"],
        "duracion": m["duracion"], "audio": m["audio"], "video": m["video"],
    }


class AlmacenSalidas:
    def __init__(self, carpeta: Path, limite: int, carpeta_videos: Path,
                 proyectos: RepositorioProyectos) -> None:
        self._carpeta = carpeta
        self._limite = limite
        self._videos = carpeta_videos
        self._proyectos = proyectos

    # ---- Rutas -----------------------------------------------------------------
    def ruta_video_para(self, audio: Path) -> Path:
        return self._videos / f"{audio.stem}.mp4"

    def nueva_ruta(self, id_tarea: str) -> Path:
        return self._carpeta / f"{datetime.now():%Y%m%d-%H%M%S}-{id_tarea}.wav"

    def ruta(self, archivo: str) -> Path:
        ruta = self._carpeta / Path(archivo).name
        if not ruta.is_file():
            raise NoEncontrado("Audio no encontrado")
        return ruta

    def ruta_video(self, archivo: str) -> Path:
        ruta = self._videos / Path(archivo).name
        if ruta.suffix != ".mp4" or not ruta.is_file():
            raise NoEncontrado("Video no encontrado")
        return ruta

    def ruta_parte(self, generacion: str, archivo: str) -> Path:
        """Audio (.wav) o video (.mp4) de un segmento de un proyecto."""
        nombre = Path(archivo).name
        base = Path(generacion).name
        if not _PARTE.match(nombre) or not base:
            raise NoEncontrado("Parte no encontrada")
        carpeta = self._carpeta if nombre.endswith(".wav") else self._videos
        ruta = carpeta / base / nombre
        if not ruta.is_file():
            raise NoEncontrado("Parte no encontrada")
        return ruta

    # ---- Lectura -----------------------------------------------------------------
    def proyecto(self, id_proyecto: str) -> dict[str, Any]:
        proyecto = self._proyectos.obtener(Path(id_proyecto).name)
        if not proyecto:
            raise NoEncontrado("Proyecto no encontrado")
        return proyecto

    def _para_listado(self, p: dict[str, Any]) -> dict[str, Any] | None:
        audio = self._carpeta / p["audio"]
        if not audio.is_file():
            return None
        video = self.ruta_video_para(audio)
        return {
            "archivo": audio.name,
            "tamano": audio.stat().st_size,
            "duracion": p.get("duracion") or round(duracion_wav(audio), 2),
            "fecha": p.get("actualizado") or p.get("creado", ""),
            "video": video.name if video.is_file() else None,
            "titulo": p.get("titulo") or audio.stem,
            "editable": bool(p.get("config")),
            "segmentos": [{
                "indice": s["indice"], "texto": s["texto"], "emocion": s.get("emocion"),
                "intensidad": s.get("intensidad"), "voz": s.get("voz_nombre"),
                "avatar": s.get("avatar_nombre"), "escena": s.get("escena"),
                "duracion": s.get("duracion", 0), "audio": s["audio"], "video": s.get("video"),
            } for s in p.get("segmentos", [])],
        }

    def listar(self) -> list[dict[str, Any]]:
        return [x for x in map(self._para_listado, self._proyectos.listar(self._limite)) if x]

    # ---- Publicación ---------------------------------------------------------------
    def _mover_partes(self, id_proyecto: str, partes: list) -> None:
        """Copia y renombra: si existe una versión anterior se sustituye de golpe."""
        carpeta_audio = self._carpeta / id_proyecto
        carpeta_video = self._videos / id_proyecto
        carpeta_audio.mkdir(parents=True, exist_ok=True)
        for parte in partes:
            temporal = shutil.copy2(parte.audio, carpeta_audio / f".{parte.audio.name}")
            os.replace(temporal, carpeta_audio / parte.audio.name)
            if parte.video:
                carpeta_video.mkdir(parents=True, exist_ok=True)
                temporal = shutil.copy2(parte.video, carpeta_video / f".{parte.video.name}")
                os.replace(temporal, carpeta_video / parte.video.name)

    def publicar(self, destino: Path, audio: Path, video: Path | None, partes: list,
                 texto: str, config: dict[str, Any] | None = None,
                 segmentos_config: list[dict[str, Any]] | None = None) -> None:
        """Lleva al historial las partes, el video y, lo último, el audio final
        (el historial lista por el WAV: hasta entonces el proyecto no existe)."""
        self._mover_partes(destino.stem, partes)
        configs = segmentos_config or []
        momento = ahora()
        self._proyectos.guardar({
            "_id": destino.stem, "titulo": _titulo(texto), "texto": texto,
            "creado": momento, "actualizado": momento,
            "audio": destino.name, "video": self.ruta_video_para(destino).name if video else None,
            "duracion": round(duracion_wav(audio), 2),
            "config": config,
            "segmentos": [segmento_documento(p, configs[i] if i < len(configs) else {})
                          for i, p in enumerate(partes)],
        })
        if video:
            shutil.move(str(video), str(self.ruta_video_para(destino)))
        shutil.move(str(audio), str(destino))

    def reemplazar_segmento(self, id_proyecto: str, parte, config_segmento: dict[str, Any]) -> None:
        """Sustituye un segmento y vuelve a unir el audio y el video finales con
        el resto de segmentos tal y como estaban."""
        proyecto = self.proyecto(id_proyecto)
        self._mover_partes(id_proyecto, [parte])
        segmentos = proyecto["segmentos"]
        destino = self._carpeta / proyecto["audio"]
        temporal = destino.with_name(f".{destino.stem}-nuevo.wav")
        unir_partes([self._carpeta / id_proyecto / s["audio"] for s in segmentos], temporal)

        if proyecto.get("video"):
            videos = [self._videos / id_proyecto / s["video"] for s in segmentos if s.get("video")]
            if len(videos) == len(segmentos):
                video_final = self._videos / proyecto["video"]
                video_tmp = video_final.with_name(f".{video_final.stem}-nuevo.mp4")
                concatenar_videos(videos, temporal, video_tmp)
                os.replace(video_tmp, video_final)
        os.replace(temporal, destino)

        nuevo = segmento_documento(parte, config_segmento)
        self._proyectos.actualizar_segmento(id_proyecto, parte.indice, nuevo, {
            "actualizado": ahora(), "duracion": round(duracion_wav(destino), 2),
            "texto": " ".join(nuevo["texto"] if s["indice"] == parte.indice else s["texto"]
                              for s in segmentos)})
        log.info("Proyecto %s: segmento %d regenerado", id_proyecto, parte.indice)

    def borrar(self, archivo: str) -> None:
        """Borra el proyecto entero: audio, video, partes y su registro."""
        audio = self._carpeta / Path(archivo).name
        if audio.suffix == ".wav" and audio.stem:
            self.ruta_video_para(audio).unlink(missing_ok=True)
            audio.with_suffix(".json").unlink(missing_ok=True)
            shutil.rmtree(self._carpeta / audio.stem, ignore_errors=True)
            shutil.rmtree(self._videos / audio.stem, ignore_errors=True)
            self._proyectos.eliminar(audio.stem)
        audio.unlink(missing_ok=True)

    # ---- Migración -------------------------------------------------------------
    def migrar(self) -> int:
        """Da de alta en la base de datos los audios que aún no tienen proyecto
        (los de antes de la base de datos, con o sin manifiesto JSON)."""
        nuevos = 0
        for audio in sorted(self._carpeta.glob("*.wav")):
            if self._proyectos.obtener(audio.stem):
                continue
            manifiesto: dict[str, Any] = {}
            json_viejo = audio.with_suffix(".json")
            try:
                manifiesto = json.loads(json_viejo.read_text("utf-8"))
            except (OSError, ValueError):
                pass
            fecha = datetime.fromtimestamp(audio.stat().st_mtime).isoformat(timespec="seconds")
            video = self.ruta_video_para(audio)
            self._proyectos.guardar({
                "_id": audio.stem, "titulo": manifiesto.get("titulo") or audio.stem,
                "texto": manifiesto.get("texto", ""), "creado": fecha, "actualizado": fecha,
                "audio": audio.name, "video": video.name if video.is_file() else None,
                "duracion": round(duracion_wav(audio), 2), "config": None,
                "segmentos": [{**s, "voz_nombre": s.get("voz"), "avatar_nombre": s.get("avatar"),
                               "voz": None, "avatar": None, "ajustes": None}
                              for s in manifiesto.get("segmentos", [])],
            })
            json_viejo.unlink(missing_ok=True)
            nuevos += 1
        if nuevos:
            log.info("Historial: %d proyecto(s) migrados a la base de datos", nuevos)
        return nuevos
