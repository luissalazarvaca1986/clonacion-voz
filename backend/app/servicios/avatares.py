# -*- coding: utf-8 -*-
"""Galería de avatares para el Meet-Video.

Cada avatar es una carpeta con su nombre, `avatares/<Nombre>/` (p. ej. `avatares/Luis/`):

    avatar.png      la persona, idealmente PNG con fondo transparente
    dia.png         fondo de día
    noche.png       fondo de noche
    lluvia.png      fondo con lluvia
    avatar.json     nombre, voz asociada y fecha
    compuestas/     fotogramas ya compuestos por escena (caché)

Un avatar solo es útil con los tres fondos, así que se exigen al crearlo.
"""
from __future__ import annotations

import io
import json
import logging
import zipfile
import shutil
import subprocess
import re
import threading
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

from app.nucleo.ajustes import SIN_VENTANA
from app.nucleo.errores import DemasiadoGrande, ErrorProceso, NoEncontrado, PeticionInvalida
from app.servicios.audio import ejecutar_ffmpeg
from app.servicios.video import (ANCHO_AVATAR, MARGEN_IZQUIERDO, componer_fotograma,
                                version_composicion)
from app.servicios.voces import BibliotecaVoces

log = logging.getLogger(__name__)

ESCENAS: dict[str, str] = {"dia": "Día", "noche": "Noche", "lluvia": "Lluvia"}
POSICION_DEFECTO = {"ancho": ANCHO_AVATAR, "izquierda": MARGEN_IZQUIERDO}
ANCHO_MIN, ANCHO_MAX = 0.10, 1.0


def validar_posicion(ancho: float | None, izquierda: float | None,
                     actual: dict[str, float] | None = None) -> dict[str, float]:
    """Ancho y separación izquierda como fracción del fotograma; el avatar debe
    caber entero a lo ancho. Siempre va alineado abajo con el alto proporcional."""
    base = actual or POSICION_DEFECTO
    ancho = float(base["ancho"] if ancho is None else ancho)
    izquierda = float(base["izquierda"] if izquierda is None else izquierda)
    if not ANCHO_MIN <= ancho <= ANCHO_MAX:
        raise PeticionInvalida(f"El ancho debe estar entre {ANCHO_MIN:.0%} y {ANCHO_MAX:.0%}.")
    if izquierda < 0 or izquierda + ancho > 1.0001:
        raise PeticionInvalida("El avatar debe caber en el fotograma: separación izquierda "
                               f"entre 0 % y {1 - ancho:.0%} con ese ancho.")
    return {"ancho": round(ancho, 4), "izquierda": round(izquierda, 4)}
TIPOS_IMAGEN = ("avatar", *ESCENAS)
_TROZO = 1 << 20


_FORMATOS_CON_ALFA = ("rgba", "bgra", "argb", "abgr", "ya8", "ya16", "gbrap", "yuva", "pal8")


def id_desde_nombre(nombre: str) -> str:
    """«Luis Pérez» -> «Luis-Perez»: nombre de carpeta seguro y legible."""
    sin_tildes = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9_-]+", "-", sin_tildes).strip("-_")[:60] or "avatar"


def _tiene_transparencia(ruta: Path) -> bool:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=pix_fmt", "-of", "csv=p=0", str(ruta)],
                           capture_output=True, text=True, timeout=30, creationflags=SIN_VENTANA)
        formato = r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False
    return formato.startswith(_FORMATOS_CON_ALFA)


class GaleriaAvatares:
    def __init__(self, carpeta: Path, max_bytes: int, voces: BibliotecaVoces) -> None:
        self._carpeta = carpeta
        self._max_bytes = max_bytes
        self._voces = voces
        self._cerrojo = threading.Lock()   # dos síntesis no componen el mismo fotograma a la vez

    def _carpeta_de(self, id_avatar: str) -> Path:
        return self._carpeta / Path(str(id_avatar)).name

    # ---- Lectura ----------------------------------------------------------
    @staticmethod
    def _meta(carpeta: Path) -> dict[str, Any] | None:
        meta = carpeta / "avatar.json"
        if not meta.is_file():
            return None
        try:
            datos = json.loads(meta.read_text("utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(datos, dict):
            return None
        escenas = [e for e in ESCENAS if (carpeta / f"{e}.png").is_file()]
        return {
            "id": carpeta.name,
            "nombre": datos.get("nombre", carpeta.name),
            "voz": datos.get("voz") or None,
            "creado": datos.get("creado", ""),
            "transparente": bool(datos.get("transparente", False)),
            "posicion": {**POSICION_DEFECTO, **(datos.get("posicion") or {})},
            "escenas": escenas,
            "completo": (carpeta / "avatar.png").is_file() and len(escenas) == len(ESCENAS),
        }

    def listar(self) -> list[dict[str, Any]]:
        if not self._carpeta.is_dir():
            return []
        avatares = [self._meta(c) for c in self._carpeta.iterdir() if c.is_dir()]
        return sorted((a for a in avatares if a), key=lambda a: a["creado"], reverse=True)

    def obtener(self, id_avatar: str) -> dict[str, Any]:
        meta = self._meta(self._carpeta_de(id_avatar))
        if not meta:
            raise NoEncontrado("Avatar no encontrado")
        return meta

    def para_video(self, id_avatar: str) -> dict[str, Any]:
        """Como obtener(), pero con error 400 y exigiendo los tres fondos."""
        meta = self._meta(self._carpeta_de(id_avatar))
        if not meta:
            raise PeticionInvalida(f"El avatar «{id_avatar}» no existe.")
        if not meta["completo"]:
            raise PeticionInvalida(f"Al avatar «{meta['nombre']}» le faltan fondos: necesita "
                                   "día, noche y lluvia.")
        return meta

    def imagen(self, id_avatar: str, tipo: str) -> Path:
        if tipo not in TIPOS_IMAGEN:
            raise NoEncontrado("Imagen no encontrada")
        ruta = self._carpeta_de(id_avatar) / f"{tipo}.png"
        if not ruta.is_file():
            raise NoEncontrado("Imagen no encontrada")
        return ruta

    def fotograma(self, id_avatar: str, escena: str) -> Path:
        """Fondo + avatar + nombre ya compuestos (se cachean hasta que cambie algo)."""
        if escena not in ESCENAS:
            raise PeticionInvalida(f"Escena desconocida: {escena}")
        meta = self.obtener(id_avatar)
        carpeta = self._carpeta_de(id_avatar)
        fondo, avatar = carpeta / f"{escena}.png", carpeta / "avatar.png"
        if not fondo.is_file() or not avatar.is_file():
            raise NoEncontrado("Imagen no encontrada")
        posicion = meta["posicion"]
        version = version_composicion(posicion["ancho"], posicion["izquierda"])
        destino = carpeta / "compuestas" / version / f"{escena}.png"
        with self._cerrojo:
            if not destino.is_file():   # colocación nueva: fuera las versiones antiguas
                for vieja in (carpeta / "compuestas").glob("*"):
                    if vieja.name == version:
                        continue
                    if vieja.is_dir():
                        shutil.rmtree(vieja, ignore_errors=True)
                    else:
                        vieja.unlink(missing_ok=True)
            fuentes = (fondo, avatar, carpeta / "avatar.json")
            if not destino.is_file() or destino.stat().st_mtime < max(
                    f.stat().st_mtime for f in fuentes):
                componer_fotograma(fondo, avatar, meta["nombre"], destino,
                                   posicion["ancho"], posicion["izquierda"])
        return destino

    def zip(self, id_avatar: str) -> bytes:
        """Imagen, fondos y avatar.json en un ZIP (sin la caché de fotogramas)."""
        self.obtener(id_avatar)
        carpeta = self._carpeta_de(id_avatar)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as z:   # los PNG ya van comprimidos
            for nombre in (*(f"{t}.png" for t in TIPOS_IMAGEN), "avatar.json"):
                if (carpeta / nombre).is_file():
                    z.write(carpeta / nombre, f"{carpeta.name}/{nombre}")
        return buffer.getvalue()

    # ---- Escritura --------------------------------------------------------
    def _guardar_imagen(self, archivo: BinaryIO, tipo: str, carpeta: Path) -> Path:
        """Copia por trozos con límite y normaliza a PNG (el avatar conserva la transparencia)."""
        bruto = carpeta / f"{tipo}.subida"
        escrito = 0
        with open(bruto, "wb") as destino:
            while trozo := archivo.read(_TROZO):
                escrito += len(trozo)
                if escrito > self._max_bytes:
                    raise DemasiadoGrande(f"La imagen «{tipo}» pasa de "
                                          f"{self._max_bytes // (1024 * 1024)} MB.")
                destino.write(trozo)
        if not escrito:
            raise PeticionInvalida(f"Falta la imagen «{tipo}».")
        final = carpeta / f"{tipo}.png"
        if tipo == "avatar":
            filtro, formato = "scale='min(1600,iw)':'min(1600,ih)':force_original_aspect_ratio=decrease", "rgba"
        else:
            filtro, formato = "scale='min(1920,iw)':'min(1080,ih)':force_original_aspect_ratio=decrease", "rgb24"
        try:
            ejecutar_ffmpeg(["-i", str(bruto), "-vf", filtro, "-pix_fmt", formato,
                             "-frames:v", "1", str(final)], f"leer la imagen «{tipo}»")
        except ErrorProceso as exc:
            raise PeticionInvalida(f"«{tipo}» no es una imagen válida.") from exc
        finally:
            bruto.unlink(missing_ok=True)
        return final

    def _comprobar_voz(self, voz: str | None) -> str | None:
        if voz:
            self._voces.referencia(voz)   # 400 si no existe
        return voz or None

    def posicion(self, id_avatar: str) -> dict[str, float]:
        return self.obtener(id_avatar)["posicion"]

    def crear(self, nombre: str, voz: str | None, imagenes: dict[str, BinaryIO | None],
              posicion: dict[str, float] | None = None) -> dict[str, Any]:
        faltan = [t for t in TIPOS_IMAGEN if imagenes.get(t) is None]
        if faltan:
            raise PeticionInvalida("Un avatar necesita su imagen y los tres fondos (día, noche "
                                   f"y lluvia). Falta: {', '.join(faltan)}.")
        voz = self._comprobar_voz(voz)
        posicion = validar_posicion((posicion or {}).get("ancho"), (posicion or {}).get("izquierda"))
        base = id_desde_nombre(nombre)
        with self._cerrojo:
            id_avatar, n = base, 2
            while (self._carpeta / id_avatar).exists():   # «Luis», «Luis-2», …
                id_avatar, n = f"{base}-{n}", n + 1
            carpeta = self._carpeta / id_avatar
            carpeta.mkdir(parents=True)
        try:
            for tipo in TIPOS_IMAGEN:
                self._guardar_imagen(imagenes[tipo], tipo, carpeta)
            (carpeta / "avatar.json").write_text(json.dumps({
                "nombre": nombre.strip() or "Avatar sin nombre",
                "voz": voz,
                "posicion": posicion,
                "transparente": _tiene_transparencia(carpeta / "avatar.png"),
                "creado": datetime.now().isoformat(timespec="seconds"),
            }, indent=2, ensure_ascii=False), "utf-8")
        except Exception:
            shutil.rmtree(carpeta, ignore_errors=True)
            raise
        log.info("Avatar creado: %s (%s)", id_avatar, nombre.strip())
        return self.obtener(id_avatar)

    def actualizar(self, id_avatar: str, nombre: str | None = None,
                   voz: str | None = None, quitar_voz: bool = False,
                   ancho: float | None = None, izquierda: float | None = None) -> dict[str, Any]:
        meta = self.obtener(id_avatar)
        archivo = self._carpeta_de(id_avatar) / "avatar.json"
        datos = json.loads(archivo.read_text("utf-8"))
        if nombre is not None and nombre.strip():
            datos["nombre"] = nombre.strip()
        if quitar_voz:
            datos["voz"] = None
        elif voz is not None:
            datos["voz"] = self._comprobar_voz(voz)
        if ancho is not None or izquierda is not None:
            # Al cambiar la posición, los fotogramas en caché dejan de valer solos
            datos["posicion"] = validar_posicion(ancho, izquierda, meta["posicion"])
        archivo.write_text(json.dumps(datos, indent=2, ensure_ascii=False), "utf-8")
        return self.obtener(id_avatar)

    def reemplazar_imagen(self, id_avatar: str, tipo: str, archivo: BinaryIO) -> dict[str, Any]:
        if tipo not in TIPOS_IMAGEN:
            raise PeticionInvalida(f"Tipo de imagen desconocido: {tipo}. "
                                   f"Usa: {', '.join(TIPOS_IMAGEN)}.")
        self.obtener(id_avatar)
        carpeta = self._carpeta_de(id_avatar)
        temporal = carpeta / "reemplazo"
        temporal.mkdir(exist_ok=True)
        try:
            nueva = self._guardar_imagen(archivo, tipo, temporal)
            nueva.replace(carpeta / f"{tipo}.png")
        finally:
            shutil.rmtree(temporal, ignore_errors=True)
        if tipo == "avatar":
            archivo_meta = carpeta / "avatar.json"
            datos = json.loads(archivo_meta.read_text("utf-8"))
            datos["transparente"] = _tiene_transparencia(carpeta / "avatar.png")
            archivo_meta.write_text(json.dumps(datos, indent=2, ensure_ascii=False), "utf-8")
        shutil.rmtree(carpeta / "compuestas", ignore_errors=True)
        return self.obtener(id_avatar)

    def borrar(self, id_avatar: str) -> None:
        carpeta = self._carpeta_de(id_avatar)
        if not (carpeta / "avatar.json").is_file():
            raise NoEncontrado("Avatar no encontrado")
        shutil.rmtree(carpeta, ignore_errors=True)
        log.info("Avatar borrado: %s", carpeta.name)
