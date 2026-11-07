# -*- coding: utf-8 -*-
"""Ejecución de llama-tts en segundo plano y seguimiento de las tareas.

Cada tarea corre en su propio hilo, lanza un proceso llama-tts por bloque de
texto y publica su avance en una cola de eventos que /api/tarea/{id}/eventos
reenvía al navegador por SSE:

    {"tipo": "analisis"}                       (modo emocional: el LLM está analizando)
    {"tipo": "segmentos", "segmentos", "origen", "modelo", "segundos"}
    {"tipo": "bloque", "indice", "total", "texto"[, "emocion", "intensidad", "voz", "avatar"]}
    {"tipo": "log", "linea"}
    {"tipo": "segmento", "indice", "total", "etapa": audio|video, "estado": en_curso|listo}
    {"tipo": "fin", "archivo", "duracion", "segundos", "segmentos"[, "video"]}
    {"tipo": "error", "mensaje"}
    {"tipo": "cancelada"}
"""
from __future__ import annotations

import logging
import os
import queue
import shutil
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.nucleo.ajustes import SIN_VENTANA
from app.nucleo.errores import NoEncontrado
from app.servicios.audio import (anadir_silencio, concatenar, duracion_wav, procesar_pieza,
                                recortar_wav, unir_wavs)
from app.servicios.entorno import RutasEfectivas
from app.servicios.video import generar_video

log = logging.getLogger(__name__)

EVENTOS_FINALES = ("fin", "error", "cancelada")


@dataclass
class Pieza:
    """Un trozo de texto que se sintetiza con una llamada a llama-tts."""
    texto: str
    cfg: dict[str, Any]        # configuración completa para construir_comando
    pausa_ms: int              # silencio tras la pieza
    filtros: str = ""          # cadena -af de ffmpeg (prosodia); "" = ninguna
    emocion: str | None = None
    intensidad: str | None = None
    ref: Path | None = None    # voz de referencia (None = voz por defecto del modelo)
    voz: str | None = None     # nombre de la voz, para mostrarlo
    imagen: Path | None = None  # fotograma del Meet-Video mientras suena esta pieza
    avatar: str | None = None  # nombre del avatar, para mostrarlo
    avatar_id: str | None = None  # para agrupar tramos al animar
    escala_expresion: float | None = None  # animación de su tramo: expresión facial…
    estilo_pose: int | None = None         # …y estilo de movimiento de cabeza
    segmento: int = 1                      # a qué segmento del mensaje pertenece
    escena: str | None = None              # fondo del Meet-Video en este segmento
    emocion_visible: bool = False          # si el segmento lleva emoción (para el manifiesto)


@dataclass
class ParteSegmento:
    """Un segmento ya sintetizado: su trozo del audio final y, si hay video, su MP4."""
    indice: int
    inicio: float
    duracion: float
    piezas: list[Pieza]
    audio: Path
    video: Path | None = None

    @property
    def principal(self) -> Pieza:
        return self.piezas[0]

    def manifiesto(self) -> dict[str, Any]:
        p = self.principal
        return {"indice": self.indice, "texto": " ".join(x.texto for x in self.piezas),
                "emocion": p.emocion, "intensidad": p.intensidad, "voz": p.voz,
                "avatar": p.avatar, "escena": p.escena, "duracion": round(self.duracion, 2),
                "audio": self.audio.name, "video": self.video.name if self.video else None}


Planificador = Callable[["Tarea"], list[Pieza]]
# Compone el video de cada segmento (rellena parte.video) y el video final:
# (tarea, partes, audio final, carpeta de trabajo, destino del video final)
ComponedorVideo = Callable[["Tarea", list[ParteSegmento], Path, Path, Path], None]
# Lleva el resultado al historial: (audio final, video final o None, partes)
Publicador = Callable[[Path, "Path | None", list[ParteSegmento]], None]
Trabajo = Callable[["Tarea"], dict[str, Any]]


class Tarea:
    def __init__(self, id_: str, total_bloques: int, tipo: str = "sintesis") -> None:
        self.id = id_
        self.tipo = tipo
        self.eventos: queue.Queue[dict[str, Any]] = queue.Queue()
        self.estado = "en_curso"   # en_curso | terminada | error | cancelada
        self.fase = "pendiente"    # pendiente (esperando la GPU) | procesando
        self.proceso: subprocess.Popen[str] | None = None
        self.cancelada = False
        self.total_bloques = total_bloques
        self.creada = time.time()
        self.fin: float | None = None
        self.resultado: dict[str, Any] = {}
        self.error = ""
        self.ultimas_lineas: deque[str] = deque(maxlen=40)
        self._al_cancelar: list[Callable[[], None]] = []

    def emitir(self, tipo: str, **datos: Any) -> None:
        self.eventos.put({"tipo": tipo, **datos})
        if tipo == "log":
            self.ultimas_lineas.append(str(datos.get("linea", "")))
        elif tipo == "error":
            self.error = str(datos.get("mensaje", ""))
        elif tipo == "fin":
            self.resultado = datos
        if tipo in EVENTOS_FINALES:
            self.fin = time.time()

    def al_cancelar(self, accion: Callable[[], None]) -> None:
        """Registra cómo cortar un trabajo externo (p. ej. cerrar una conexión HTTP)."""
        self._al_cancelar.append(accion)

    def cancelar(self) -> None:
        self.cancelada = True
        proceso = self.proceso
        if proceso and proceso.poll() is None:
            proceso.kill()
        for accion in list(self._al_cancelar):
            try:
                accion()
            except Exception:  # noqa: BLE001 — cancelar nunca debe fallar
                log.debug("Fallo al cancelar la tarea %s", self.id, exc_info=True)

    def instantanea(self) -> dict[str, Any]:
        """Estado legible para la API (sin inventar porcentajes)."""
        estado = {"en_curso": "pendiente" if self.fase == "pendiente" else "procesando",
                  "terminada": "completado", "error": "fallido",
                  "cancelada": "cancelado"}[self.estado]
        return {"id": self.id, "tipo": self.tipo, "estado": estado,
                "segundos": round((self.fin or time.time()) - self.creada, 1),
                "resultado": self.resultado, "error": self.error,
                "registro": list(self.ultimas_lineas)[-12:]}


def construir_comando(rutas: RutasEfectivas, cfg: dict[str, Any], texto: str, salida: Path,
                      ref: Path | None, dispositivo: str) -> list[str]:
    """`dispositivo` vacío = CPU pura; en otro caso, el id que reporta llama.cpp."""
    cmd = [
        rutas.binario,
        "-m", rutas.modelo,
        "-mm", rutas.mmproj,
        "-p", texto,
        "-o", str(salida),
        "--tts-lang", str(cfg["idioma"]),
        "-n", str(int(cfg["max_frames"])),
        "--top-k", str(int(cfg["top_k"])),
        "--top-p", str(float(cfg["top_p"])),
        "--temp", str(float(cfg["temp"])),
        "-c", str(int(cfg["contexto"])),
        "-b", str(int(cfg["lote"])),
        "-ub", str(int(cfg["lote"])),
    ]
    if dispositivo:
        # -mmdev es decisivo: sin él el vocoder corre en CPU y tarda ~15x más.
        cmd += ["-ngl", str(int(cfg["capas_gpu"])),
                "--device", dispositivo,
                "-mmdev", dispositivo]
    else:
        cmd += ["-ngl", "0", "--device", "none", "--no-mmproj-offload"]
    if int(cfg.get("hilos") or 0) > 0:
        cmd += ["-t", str(int(cfg["hilos"]))]
    if int(cfg.get("semilla", -1)) >= 0:
        cmd += ["-s", str(int(cfg["semilla"]))]
    if ref is not None:
        cmd += ["--tts-speaker-file", str(ref)]
    return cmd


class _Cancelada(Exception):
    pass


def partes_por_segmento(piezas: list[Pieza], duraciones: list[float]) -> list[list]:
    """[(segmento, inicio, duración, piezas)] según la línea de tiempo del audio final."""
    grupos: list[list] = []
    inicio = 0.0
    for pieza, dur in zip(piezas, duraciones):
        if grupos and grupos[-1][0] == pieza.segmento:
            grupos[-1][2] += dur
            grupos[-1][3].append(pieza)
        else:
            grupos.append([pieza.segmento, inicio, dur, [pieza]])
        inicio += dur
    return grupos


def video_estatico(tarea: Tarea, partes: list[ParteSegmento], audio: Path, carpeta: Path,
                   destino: Path) -> None:
    """Cada segmento muestra su fotograma fijo justo lo que dura su audio."""
    tarea.emitir("log", linea="Componiendo el Meet-Video con ffmpeg…")
    for parte in partes:
        tarea.emitir("segmento", indice=parte.indice, total=len(partes), etapa="video",
                     estado="en_curso")
        parte.video = carpeta / f"segmento-{parte.indice:02d}.mp4"
        generar_video([(parte.principal.imagen, parte.duracion)], parte.audio, parte.video)
        tarea.emitir("segmento", indice=parte.indice, total=len(partes), etapa="video",
                     estado="listo")
    generar_video([(p.principal.imagen, p.duracion) for p in partes], audio, destino)


def _publicar_simple(destino: Path, destino_video: Path | None) -> Publicador:
    """Sin almacén: mueve el audio y el video finales a su sitio (las partes se descartan)."""
    def publicar(audio: Path, video: Path | None, _partes: list[ParteSegmento]) -> None:
        if video and destino_video:
            shutil.move(str(video), str(destino_video))
        shutil.move(str(audio), str(destino))
    return publicar


class GestorTareas:
    def __init__(self, dir_tmp: Path, vida_segundos: int) -> None:
        self._dir_tmp = dir_tmp
        self._vida = vida_segundos
        self._tareas: dict[str, Tarea] = {}
        self._cerrojo_tareas = threading.Lock()
        # Por defecto, una síntesis a la vez: dos procesos llama-tts simultáneos duplican
        # el consumo de VRAM y agotan las gráficas pequeñas. En equipos con memoria de
        # sobra CLONAR_GPU_CONCURRENCIA permite varias tareas en paralelo (pipeline).
        self._cerrojo_sintesis = threading.BoundedSemaphore(
            max(1, int(os.environ.get("CLONAR_GPU_CONCURRENCIA") or 1)))

    @staticmethod
    def nuevo_id() -> str:
        return uuid.uuid4().hex[:12]

    def _registrar(self, tarea: Tarea) -> None:
        with self._cerrojo_tareas:
            self._podar()
            self._tareas[tarea.id] = tarea

    def lanzar(self, id_tarea: str, rutas: RutasEfectivas, planificar: Planificador,
               total_estimado: int, dispositivo: str, destino: Path,
               destino_video: Path | None = None,
               componer_video: ComponedorVideo | None = None,
               publicar: Publicador | None = None, pausa_final_ms: int = 0) -> Tarea:
        """`pausa_final_ms` añade silencio al final: lo usa la regeneración de un
        segmento que no es el último, para que encaje en la línea de tiempo."""
        tarea = Tarea(id_tarea, total_estimado)
        self._registrar(tarea)
        threading.Thread(target=self._ejecutar, daemon=True, name=f"sintesis-{tarea.id}",
                         args=(tarea, rutas, planificar, dispositivo, destino,
                               destino_video, componer_video or video_estatico,
                               publicar or _publicar_simple(destino, destino_video),
                               pausa_final_ms)).start()
        return tarea

    def lanzar_trabajo(self, id_tarea: str, tipo: str, trabajo: Trabajo) -> Tarea:
        """Trabajo genérico en segundo plano que usa la GPU (p. ej. una animación).

        Comparte el cerrojo con la síntesis: nunca hay dos procesos en la GPU.
        `trabajo` devuelve los datos del evento «fin».
        """
        tarea = Tarea(id_tarea, 1, tipo=tipo)
        self._registrar(tarea)

        def ejecutar() -> None:
            if not self._cerrojo_sintesis.acquire(blocking=False):
                tarea.emitir("log", linea="La GPU está ocupada; esperando turno…")
                self._cerrojo_sintesis.acquire()
            tarea.fase = "procesando"
            try:
                if tarea.cancelada:
                    raise _Cancelada()
                datos = trabajo(tarea)
                if tarea.cancelada:
                    raise _Cancelada()
                tarea.estado = "terminada"
                tarea.emitir("fin", **datos)
            except _Cancelada:
                tarea.estado = "cancelada"
                tarea.emitir("cancelada")
            except Exception as exc:  # noqa: BLE001 — el mensaje se muestra en la web
                if tarea.cancelada:
                    tarea.estado = "cancelada"
                    tarea.emitir("cancelada")
                else:
                    tarea.estado = "error"
                    tarea.emitir("error", mensaje=str(exc))
                    log.warning("Trabajo %s (%s): error: %s", tarea.id, tipo, exc)
            finally:
                self._cerrojo_sintesis.release()

        threading.Thread(target=ejecutar, daemon=True, name=f"{tipo}-{tarea.id}").start()
        return tarea

    def obtener(self, id_tarea: str) -> Tarea:
        with self._cerrojo_tareas:
            tarea = self._tareas.get(id_tarea)
        if not tarea:
            raise NoEncontrado("Tarea no encontrada")
        return tarea

    def cancelar(self, id_tarea: str) -> None:
        self.obtener(id_tarea).cancelar()
        log.info("Tarea %s: cancelación pedida", id_tarea)

    def en_curso(self) -> int:
        with self._cerrojo_tareas:
            return sum(1 for t in self._tareas.values() if t.estado == "en_curso")

    def detener_todo(self) -> None:
        """Al apagar el servidor: que no queden procesos llama-tts huérfanos."""
        with self._cerrojo_tareas:
            tareas = list(self._tareas.values())
        for t in tareas:
            if t.estado == "en_curso":
                t.cancelar()

    def _podar(self) -> None:
        """Descarta las tareas acabadas hace rato: si no, el registro crece sin límite."""
        ahora = time.time()
        for id_, t in list(self._tareas.items()):
            if t.fin and ahora - t.fin > self._vida:
                del self._tareas[id_]

    def _ejecutar(self, tarea: Tarea, rutas: RutasEfectivas, planificar: Planificador,
                  dispositivo: str, destino: Path, destino_video: Path | None,
                  componer_video: ComponedorVideo, publicar: Publicador,
                  pausa_final_ms: int = 0) -> None:
        """Hilo de trabajo: planifica, sintetiza pieza a pieza y une los resultados."""
        crudos: list[Path] = []
        procesados: list[Path] = []
        temporales: list[Path] = []
        carpetas_temporales: list[Path] = []
        inicio = time.time()
        cerrojo_tomado = False
        try:
            # El plan (p. ej. el análisis emocional con el LLM, que corre en CPU) va
            # antes del cerrojo: no hace esperar a otras síntesis.
            piezas = planificar(tarea)
            if not piezas:
                raise RuntimeError("No hay texto que sintetizar.")
            if not self._cerrojo_sintesis.acquire(blocking=False):
                tarea.emitir("log", linea="Hay otra síntesis en curso; esperando turno…")
                self._cerrojo_sintesis.acquire()
            cerrojo_tomado = True
            tarea.fase = "procesando"
            log.info("Tarea %s: %d pieza(s) en %s", tarea.id, len(piezas), dispositivo or "CPU")

            for i, pieza in enumerate(piezas, start=1):
                if tarea.cancelada:
                    break
                parcial = self._dir_tmp / f"{tarea.id}_{i:03d}.wav"
                extra = {k: v for k, v in (("emocion", pieza.emocion),
                                           ("intensidad", pieza.intensidad),
                                           ("voz", pieza.voz), ("avatar", pieza.avatar)) if v}
                tarea.emitir("bloque", indice=i, total=len(piezas), texto=pieza.texto, **extra)

                cmd = construir_comando(rutas, pieza.cfg, pieza.texto, parcial, pieza.ref,
                                        dispositivo)
                tarea.proceso = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                    creationflags=SIN_VENTANA,
                )
                assert tarea.proceso.stdout is not None
                for linea in tarea.proceso.stdout:
                    linea = linea.rstrip()
                    if linea:
                        tarea.emitir("log", linea=linea)
                codigo = tarea.proceso.wait()
                tarea.proceso = None
                crudos.append(parcial)

                if tarea.cancelada:
                    break
                if codigo != 0 or not parcial.exists():
                    raise RuntimeError(f"llama-tts terminó con código {codigo} en el bloque {i}.")

            if tarea.cancelada:
                tarea.estado = "cancelada"
                tarea.emitir("cancelada")
                log.info("Tarea %s: cancelada", tarea.id)
                return

            # Los resultados se montan en la carpeta temporal y solo pasan al historial
            # al terminar todo: así un audio a medio hacer no se puede ver ni borrar
            # desde la web mientras, por ejemplo, se anima su video.
            audio_tmp = self._dir_tmp / f"{tarea.id}_final.wav"
            video_tmp = self._dir_tmp / f"{tarea.id}_final.mp4"
            temporales += [audio_tmp, video_tmp]
            if all(not p.filtros for p in piezas) and len({p.pausa_ms for p in piezas[:-1]}) <= 1:
                # Modo simple: unión directa, idéntica a la de siempre
                unir_wavs(crudos, audio_tmp, piezas[0].pausa_ms)
                duraciones = [duracion_wav(c) + (piezas[0].pausa_ms / 1000 if i < len(crudos) - 1
                                                 else 0) for i, c in enumerate(crudos)]
            else:
                tarea.emitir("log", linea="Aplicando prosodia y uniendo las piezas con ffmpeg…")
                for i, (crudo, pieza) in enumerate(zip(crudos, piezas)):
                    procesado = crudo.with_name(crudo.stem + "_fx.wav")
                    pausa = pieza.pausa_ms if i < len(piezas) - 1 else 0
                    procesar_pieza(crudo, procesado, pieza.filtros, pausa)
                    procesados.append(procesado)
                concatenar(procesados, audio_tmp)
                duraciones = [duracion_wav(p) for p in procesados]

            if pausa_final_ms > 0:
                con_pausa = self._dir_tmp / f"{tarea.id}_final_pausa.wav"
                temporales.append(con_pausa)
                anadir_silencio(audio_tmp, pausa_final_ms, con_pausa)
                con_pausa.replace(audio_tmp)
                duraciones[-1] += pausa_final_ms / 1000

            # Cada segmento tiene su trozo del audio final, descargable por separado
            carpeta_partes = self._dir_tmp / f"{tarea.id}_partes"
            carpeta_partes.mkdir(exist_ok=True)
            carpetas_temporales.append(carpeta_partes)
            partes = []
            for indice, t0, dur, del_segmento in partes_por_segmento(piezas, duraciones):
                parte = ParteSegmento(indice, t0, dur, del_segmento,
                                      carpeta_partes / f"segmento-{indice:02d}.wav")
                recortar_wav(audio_tmp, t0, dur, parte.audio)
                partes.append(parte)
                tarea.emitir("segmento", indice=indice, total=len(set(p.segmento for p in piezas)),
                             etapa="audio", estado="listo")

            extra_fin = {}
            hay_video = destino_video is not None and any(p.imagen or p.avatar_id for p in piezas)
            if hay_video:
                tarea.emitir("video")
                componer_video(tarea, partes, audio_tmp, carpeta_partes, video_tmp)
                if tarea.cancelada:
                    tarea.estado = "cancelada"
                    tarea.emitir("cancelada")
                    return
                extra_fin["video"] = destino_video.name

            publicar(audio_tmp, video_tmp if hay_video else None, partes)
            extra_fin["segmentos"] = [p.manifiesto() for p in partes]
            tarea.estado = "terminada"
            segundos = round(time.time() - inicio, 1)
            tarea.emitir("fin", archivo=destino.name,
                         duracion=round(duracion_wav(destino), 2), segundos=segundos, **extra_fin)
            log.info("Tarea %s: terminada en %.1f s -> %s", tarea.id, segundos, destino.name)
        except Exception as exc:  # noqa: BLE001 — el mensaje se muestra íntegro en la web
            tarea.estado = "error"
            tarea.emitir("error", mensaje=str(exc))
            log.warning("Tarea %s: error: %s", tarea.id, exc)
        finally:
            if tarea.proceso and tarea.proceso.poll() is None:
                tarea.proceso.kill()
            if cerrojo_tomado:
                self._cerrojo_sintesis.release()
            for p in crudos + procesados + temporales:
                p.unlink(missing_ok=True)
            for carpeta in carpetas_temporales:
                shutil.rmtree(carpeta, ignore_errors=True)
