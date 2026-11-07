# -*- coding: utf-8 -*-
"""Animación facial del avatar a partir de un audio.

    ServicioAnimacion ──► ProveedorAnimacion (interfaz)
                               └── ProveedorSadTalker ──HTTP──► contenedor clonar-voz-animador

El backend no conoce los detalles de SadTalker: solo la interfaz. Para añadir
otro motor (LivePortrait, MuseTalk, EchoMimic…) basta con otra clase que
cumpla `ProveedorAnimacion` y registrarla en `crear_servicios`.

La transparencia no la da el motor (SadTalker devuelve MP4 sin alfa). Para el
Meet-Video se anima la imagen completa con la cabeza casi fija («quieto») y se
le vuelve a aplicar la máscara alfa del PNG original al componer sobre el fondo.
"""
from __future__ import annotations

import http.client
import json
import logging
import re
import shutil
import socket
import time
import uuid
from pathlib import Path
from typing import Any, BinaryIO, Callable, Protocol
from urllib.parse import urlparse

from app.nucleo.errores import DemasiadoGrande, ErrorDominio, NoEncontrado, PeticionInvalida
from app.servicios.audio import duracion_wav, ejecutar_ffmpeg
from app.servicios.avatares import ESCENAS, GaleriaAvatares
from app.servicios.salidas import AlmacenSalidas
from app.servicios.sintesis import GestorTareas, Tarea
from app.servicios.video import componer_tramo_animado, concatenar_tramos, normalizar_video

log = logging.getLogger(__name__)

_PROGRESO = re.compile(r"^(Face Renderer|seamlessClone)::\s*\d+%.*?(\d+)/(\d+)")
_EXTENSIONES_IMAGEN = {".png", ".jpg", ".jpeg"}
_EXTENSIONES_AUDIO = {".wav", ".mp3"}
_TROZO = 1 << 20


class AnimadorNoDisponible(ErrorDominio):
    estado = 503


class MemoriaInsuficiente(ErrorDominio):
    estado = 500


# ---- Interfaz común de los motores ---------------------------------------------
class ProveedorAnimacion(Protocol):
    id: str
    nombre: str

    def disponible(self) -> bool: ...

    def capacidades(self) -> dict[str, Any]: ...

    def validar(self, opciones: dict[str, Any]) -> dict[str, Any]:
        """Opciones saneadas; error 400 si alguna no la admite el motor."""
        ...

    def animar(self, tarea: Tarea, imagen: Path, audio: Path, destino: Path,
               opciones: dict[str, Any]) -> None:
        """Escribe en `destino` un video (sin alfa) del rostro diciendo el audio."""
        ...


# ---- SadTalker -----------------------------------------------------------------
class ProveedorSadTalker:
    id = "sadtalker"
    nombre = "SadTalker"

    def __init__(self, url: str, raiz_compartida: Path, timeout: float) -> None:
        self._url = urlparse(url) if url else None
        self._raiz = raiz_compartida
        self._timeout = timeout
        self._cache: tuple[float, dict[str, Any] | None] = (0.0, None)

    def _conexion(self, timeout: float) -> http.client.HTTPConnection:
        if not self._url:
            raise AnimadorNoDisponible("No hay ningún motor de animación configurado.")
        return http.client.HTTPConnection(self._url.hostname, self._url.port or 80,
                                          timeout=timeout)

    def _pedir_json(self, ruta: str) -> dict[str, Any]:
        conexion = self._conexion(3)
        try:
            conexion.request("GET", ruta)
            return json.loads(conexion.getresponse().read())
        except (OSError, ValueError, http.client.HTTPException) as exc:
            raise AnimadorNoDisponible(f"El motor de animación no responde: {exc}") from exc
        finally:
            conexion.close()

    def capacidades(self) -> dict[str, Any]:
        instante, datos = self._cache
        if datos is None or time.time() - instante > 60:
            datos = self._pedir_json("/capacidades")
            self._cache = (time.time(), datos)
        return datos

    def disponible(self) -> bool:
        try:
            return bool(self._pedir_json("/salud").get("ok"))
        except AnimadorNoDisponible:
            return False

    def validar(self, opciones: dict[str, Any]) -> dict[str, Any]:
        definicion = self.capacidades()["opciones"]
        limpias: dict[str, Any] = {}
        for clave, valor in opciones.items():
            if valor is None:
                continue
            d = definicion.get(clave)
            if not d:
                raise PeticionInvalida(f"SadTalker no admite la opción «{clave}».")
            if d["tipo"] == "enum" and valor not in d["valores"]:
                raise PeticionInvalida(f"«{clave}» debe ser uno de {d['valores']}.")
            if d["tipo"] in ("entero", "decimal") and not d["min"] <= float(valor) <= d["max"]:
                raise PeticionInvalida(f"«{clave}» debe estar entre {d['min']} y {d['max']}.")
            limpias[clave] = valor
        return limpias

    def animar(self, tarea: Tarea, imagen: Path, audio: Path, destino: Path,
               opciones: dict[str, Any]) -> None:
        cuerpo = json.dumps({"imagen": str(imagen.relative_to(self._raiz)),
                             "audio": str(audio.relative_to(self._raiz)),
                             "salida": str(destino.relative_to(self._raiz)), **opciones})
        conexion = self._conexion(self._timeout)
        # Cortar la conexión es la cancelación: el animador mata su proceso
        tarea.al_cancelar(lambda: conexion.sock and conexion.sock.shutdown(socket.SHUT_RDWR))
        try:
            conexion.request("POST", "/animar", cuerpo, {"Content-Type": "application/json"})
            respuesta = conexion.getresponse()
            if respuesta.status != 200:
                detalle = json.loads(respuesta.read() or b"{}").get("error", respuesta.reason)
                if respuesta.status == 409:
                    raise AnimadorNoDisponible(f"El animador está ocupado: {detalle}")
                raise PeticionInvalida(f"El animador rechazó la petición: {detalle}")
            for bruto in respuesta:
                evento = json.loads(bruto)
                if evento["tipo"] == "log":
                    self._registrar_linea(tarea, evento["linea"])
                elif evento["tipo"] == "error":
                    if evento.get("oom"):
                        raise MemoriaInsuficiente(
                            "La GPU se quedó sin memoria (CUDA out of memory). Vuelve a "
                            "intentarlo con el dispositivo CPU (bastante más lento).")
                    raise ErrorDominio(evento["mensaje"])
                elif evento["tipo"] == "fin":
                    return
            if tarea.cancelada:
                return
            raise ErrorDominio("El animador cortó la conexión sin terminar.")
        except (OSError, http.client.HTTPException, ValueError) as exc:
            if tarea.cancelada:
                return
            raise AnimadorNoDisponible(f"Fallo de comunicación con el animador: {exc}") from exc
        finally:
            conexion.close()

    @staticmethod
    def _registrar_linea(tarea: Tarea, linea: str) -> None:
        m = _PROGRESO.match(linea)
        if m:
            # Progreso real que da el propio motor (fotogramas hechos / totales)
            fase = "renderizando el rostro" if m.group(1) == "Face Renderer" else "integrando en la imagen"
            tarea.emitir("progreso", fase=fase, hecho=int(m.group(2)), total=int(m.group(3)))
            if int(m.group(2)) != int(m.group(3)):
                return   # solo se guarda en el registro la línea final de cada barra
        tarea.emitir("log", linea=linea)


# ---- Servicio ------------------------------------------------------------------
class ServicioAnimacion:
    def __init__(self, proveedores: list[ProveedorAnimacion], raiz_compartida: Path,
                 avatares: GaleriaAvatares, salidas: AlmacenSalidas, tareas: GestorTareas,
                 carpeta_videos: Path, max_bytes_imagen: int, max_bytes_audio: int,
                 max_segundos: int) -> None:
        self._proveedores = {p.id: p for p in proveedores}
        self._raiz = raiz_compartida
        self._avatares = avatares
        self._salidas = salidas
        self._tareas = tareas
        self._videos = carpeta_videos
        self._max_imagen = max_bytes_imagen
        self._max_audio = max_bytes_audio
        self._max_segundos = max_segundos

    # ---- Motores -------------------------------------------------------------
    def motores(self) -> list[dict[str, Any]]:
        resultado = []
        for p in self._proveedores.values():
            try:
                capacidades, disponible = p.capacidades(), p.disponible()
            except AnimadorNoDisponible:
                capacidades, disponible = None, False
            resultado.append({"id": p.id, "nombre": p.nombre, "disponible": disponible,
                              "capacidades": capacidades})
        return resultado

    def proveedor(self, motor: str | None) -> ProveedorAnimacion:
        p = self._proveedores.get(motor or "sadtalker")
        if not p:
            raise PeticionInvalida(f"Motor de animación desconocido: {motor}")
        return p

    # ---- Preparación de entradas ---------------------------------------------
    def _carpeta_trabajo(self, prefijo: str) -> Path:
        carpeta = self._raiz / f"{prefijo}-{uuid.uuid4().hex[:10]}"
        carpeta.mkdir(parents=True)
        return carpeta

    @staticmethod
    def _imagen_rgb(origen: Path, destino: Path) -> None:
        """El motor no admite alfa: el avatar se aplana sobre un gris neutro."""
        ejecutar_ffmpeg(["-i", str(origen), "-filter_complex",
                         "color=c=0x808080:s=16x16[g];[g][0]scale2ref[g2][i];"
                         "[g2][i]overlay=format=auto,format=rgb24",
                         "-frames:v", "1", str(destino)], "preparar la imagen del avatar")

    @staticmethod
    def _recortar_audio(audio: Path, inicio: float, duracion: float, destino: Path) -> None:
        ejecutar_ffmpeg(["-ss", f"{inicio:.3f}", "-t", f"{duracion:.3f}", "-i", str(audio),
                         "-ac", "1", "-ar", "16000", str(destino)], "recortar el audio")

    def _guardar_subida(self, archivo: BinaryIO, nombre: str | None, extensiones: set[str],
                        limite: int, destino: Path, que: str) -> Path:
        sufijo = Path(nombre or "").suffix.lower()
        if sufijo not in extensiones:
            raise PeticionInvalida(f"Formato de {que} no admitido ({sufijo or 'sin extensión'}). "
                                   f"Usa: {', '.join(sorted(extensiones))}.")
        bruto = destino.with_suffix(sufijo)
        escrito = 0
        with open(bruto, "wb") as f:
            while trozo := archivo.read(_TROZO):
                escrito += len(trozo)
                if escrito > limite:
                    raise DemasiadoGrande(f"El archivo de {que} pasa de {limite // (1024 * 1024)} MB.")
                f.write(trozo)
        return bruto

    # ---- Animación suelta (foto + audio) -------------------------------------
    def crear_trabajo(self, *, motor: str | None, opciones: dict[str, Any],
                      avatar: str | None, imagen: BinaryIO | None, nombre_imagen: str | None,
                      salida: str | None, audio: BinaryIO | None, nombre_audio: str | None,
                      escena: str | None) -> dict[str, Any]:
        proveedor = self.proveedor(motor)
        if not proveedor.disponible():
            raise AnimadorNoDisponible("El motor de animación no está disponible ahora mismo.")
        opciones = proveedor.validar(opciones)
        if escena and escena not in ESCENAS:
            raise PeticionInvalida(f"Escena desconocida: {escena}")
        if escena and not avatar:
            raise PeticionInvalida("El fondo solo se puede aplicar a un avatar de la galería, "
                                   "que es el que trae la transparencia y los fondos.")
        if bool(avatar) == bool(imagen):
            raise PeticionInvalida("Elige un avatar de la galería o sube una foto (solo una).")
        if bool(salida) == bool(audio):
            raise PeticionInvalida("Elige un audio del historial o sube uno (solo uno).")

        carpeta = self._carpeta_trabajo("anim")
        try:
            # Imagen: avatar de la galería o foto subida, validada con ffmpeg
            meta = None
            if avatar:
                meta = self._avatares.para_video(avatar) if escena else self._avatares.obtener(avatar)
                original = self._avatares.imagen(avatar, "avatar")
            else:
                original = self._guardar_subida(imagen, nombre_imagen, _EXTENSIONES_IMAGEN,
                                                self._max_imagen, carpeta / "subida", "imagen")
            entrada_imagen = carpeta / "imagen.png"
            try:
                self._imagen_rgb(original, entrada_imagen)
            except ErrorDominio as exc:
                raise PeticionInvalida("La imagen no es válida o está dañada.") from exc

            # Audio: del historial o subido; se normaliza a WAV mono 16 kHz
            fuente = (self._salidas.ruta(salida) if salida else
                      self._guardar_subida(audio, nombre_audio, _EXTENSIONES_AUDIO,
                                           self._max_audio, carpeta / "subida-audio", "audio"))
            entrada_audio = carpeta / "audio.wav"
            try:
                ejecutar_ffmpeg(["-i", str(fuente), "-ac", "1", "-ar", "16000",
                                 str(entrada_audio)], "leer el audio")
            except ErrorDominio as exc:
                raise PeticionInvalida("El audio no es válido o está dañado.") from exc
            segundos = duracion_wav(entrada_audio)
            if segundos <= 0.2:
                raise PeticionInvalida("El audio está vacío.")
            if segundos > self._max_segundos:
                raise PeticionInvalida(f"El audio dura {segundos:.0f} s; el máximo para una "
                                       f"animación es {self._max_segundos} s.")
        except Exception:
            shutil.rmtree(carpeta, ignore_errors=True)
            raise

        id_trabajo = self._tareas.nuevo_id()
        destino = self._videos / f"animacion-{id_trabajo}.mp4"

        def trabajo(tarea: Tarea) -> dict[str, Any]:
            try:
                crudo = carpeta / "animado.mp4"
                tarea.emitir("log", linea=f"Animando con {proveedor.nombre}: {segundos:.1f} s de audio")
                proveedor.animar(tarea, entrada_imagen, entrada_audio, crudo, opciones)
                if tarea.cancelada:
                    return {}
                tarea.emitir("log", linea="Codificando el MP4…")
                if meta and escena:
                    tramo = carpeta / "tramo.mp4"
                    componer_tramo_animado(self._avatares.imagen(avatar, escena),
                                           self._avatares.imagen(avatar, "avatar"), crudo,
                                           meta["nombre"], segundos, tramo,
                                           **meta["posicion"])
                    concatenar_tramos([tramo], entrada_audio, destino)
                else:
                    normalizar_video(crudo, entrada_audio, destino)
                return {"video": destino.name, "duracion": round(segundos, 2)}
            finally:
                shutil.rmtree(carpeta, ignore_errors=True)

        self._tareas.lanzar_trabajo(id_trabajo, "animacion", trabajo)
        return {"id": id_trabajo, "estado": "pendiente", "motor": proveedor.id,
                "video": destino.name, "duracion_audio": round(segundos, 2)}

    def estado(self, id_trabajo: str) -> dict[str, Any]:
        tarea = self._tareas.obtener(id_trabajo)
        if tarea.tipo != "animacion":
            raise NoEncontrado("Animación no encontrada")
        return tarea.instantanea()

    def listar(self) -> list[dict[str, Any]]:
        videos = sorted(self._videos.glob("animacion-*.mp4"), key=lambda p: p.stat().st_mtime,
                        reverse=True)
        return [{"video": v.name, "tamano": v.stat().st_size,
                 "fecha": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(v.stat().st_mtime))}
                for v in videos[:40]]

    def borrar(self, video: str) -> None:
        ruta = self._videos / Path(video).name
        if not ruta.name.startswith("animacion-") or ruta.suffix != ".mp4":
            raise NoEncontrado("Animación no encontrada")
        ruta.unlink(missing_ok=True)

    # ---- Meet-Video animado --------------------------------------------------
    def componedor_meet(self, motor: str | None, opciones: dict[str, Any]):
        """Componedor para GestorTareas: anima cada segmento con su avatar, su fondo y
        sus opciones, deja su MP4 (con su audio) y une todos en el video final."""
        proveedor = self.proveedor(motor)
        if not proveedor.disponible():
            raise AnimadorNoDisponible("El motor de animación no está disponible ahora mismo.")
        # Cabeza casi fija e imagen completa: así la máscara alfa del PNG sigue encajando
        opciones = proveedor.validar({**opciones, "quieto": True, "preproceso": "full"})

        def componer(tarea: Tarea, partes, audio: Path, carpeta_partes: Path,
                     destino: Path) -> None:
            carpeta = self._carpeta_trabajo("meet")
            try:
                silenciosos = []
                for parte in partes:
                    if tarea.cancelada:
                        return
                    p = parte.principal
                    meta = self._avatares.obtener(p.avatar_id)
                    n, total = parte.indice, len(partes)
                    tarea.emitir("segmento", indice=n, total=total, etapa="video", estado="en_curso")
                    tarea.emitir("animacion", tramo=n, tramos=total, avatar=meta["nombre"])
                    tarea.emitir("log", linea=f"🎭 Animando el segmento {n}/{total} "
                                              f"({meta['nombre']}, {parte.duracion:.1f} s)…")
                    imagen, audio_tramo = carpeta / f"imagen-{n}.png", carpeta / f"audio-{n}.wav"
                    crudo, tramo = carpeta / f"animado-{n}.mp4", carpeta / f"tramo-{n}.mp4"
                    self._imagen_rgb(self._avatares.imagen(p.avatar_id, "avatar"), imagen)
                    self._recortar_audio(parte.audio, 0, parte.duracion, audio_tramo)
                    # La animación es de cada segmento: expresión y estilo de cabeza
                    opciones_tramo = dict(opciones)
                    if p.escala_expresion is not None:
                        opciones_tramo["escala_expresion"] = p.escala_expresion
                    if p.estilo_pose is not None:
                        opciones_tramo["estilo_pose"] = p.estilo_pose
                    proveedor.animar(tarea, imagen, audio_tramo, crudo,
                                     proveedor.validar(opciones_tramo))
                    if tarea.cancelada:
                        return
                    componer_tramo_animado(self._avatares.imagen(p.avatar_id, p.escena or "dia"),
                                           self._avatares.imagen(p.avatar_id, "avatar"), crudo,
                                           meta["nombre"], parte.duracion, tramo,
                                           **meta["posicion"])
                    silenciosos.append(tramo)
                    parte.video = carpeta_partes / f"segmento-{n:02d}.mp4"
                    concatenar_tramos([tramo], parte.audio, parte.video)
                    tarea.emitir("segmento", indice=n, total=total, etapa="video", estado="listo")
                tarea.emitir("log", linea="Uniendo los segmentos animados con el audio…")
                concatenar_tramos(silenciosos, audio, destino)
            finally:
                shutil.rmtree(carpeta, ignore_errors=True)

        return componer
