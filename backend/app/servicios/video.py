# -*- coding: utf-8 -*-
"""Meet-Video: composición de fotogramas y codificación del MP4 con ffmpeg.

Cada fotograma es un fondo (día, noche o lluvia) con el avatar encima y una
etiqueta con su nombre, como en una videollamada. Por ahora la imagen es fija
durante cada tramo; el video cambia de avatar cuando cambia quien habla.
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.servicios.audio import ejecutar_ffmpeg

log = logging.getLogger(__name__)

ANCHO, ALTO = 1280, 720
FPS = 25
# Colocación por defecto del avatar (cada avatar guarda la suya), relativa al
# fotograma: siempre alineado abajo y con el alto proporcional, sin deformar.
ANCHO_AVATAR = 0.65        # fracción del ancho del fotograma
MARGEN_IZQUIERDO = 0.25    # separación respecto al borde izquierdo


def version_composicion(ancho: float, izquierda: float) -> str:
    """Nombre de la caché de fotogramas: cambia en cuanto cambia la colocación."""
    return f"w{ancho:.3f}-x{izquierda:.3f}"


def _ancho_px(ancho: float) -> int:
    return max(2, int(ANCHO * ancho) // 2 * 2)
FUENTE = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")


def _escapar_ruta_filtro(ruta: Path) -> str:
    # Dentro de un filtro de ffmpeg, ':' , '\\' y "'" tienen significado
    return str(ruta).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def componer_fotograma(fondo: Path, avatar: Path, nombre: str, destino: Path,
                       ancho: float = ANCHO_AVATAR, izquierda: float = MARGEN_IZQUIERDO) -> None:
    """Fondo a pantalla completa + avatar abajo + etiqueta con el nombre."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    ancho_avatar = _ancho_px(ancho)
    filtro = (
        f"[0]scale={ANCHO}:{ALTO}:force_original_aspect_ratio=increase,"
        f"crop={ANCHO}:{ALTO},setsar=1[f];"
        f"[1]scale={ancho_avatar}:-2[a];"
        # Si el avatar sale más alto que el fotograma, se recorta por arriba
        f"[f][a]overlay=x=W*{izquierda:.4f}:y=H-h:format=auto"
    )
    texto = destino.with_suffix(".nombre.txt")
    if nombre.strip() and FUENTE.is_file():
        # textfile= evita tener que escapar el nombre dentro del filtro
        texto.write_text(nombre.strip(), "utf-8")
        filtro += (f",drawtext=fontfile={_escapar_ruta_filtro(FUENTE)}"
                   f":textfile={_escapar_ruta_filtro(texto)}:fontsize=24:fontcolor=white"
                   ":x=24:y=h-th-24:box=1:boxcolor=black@0.55:boxborderw=10")
    try:
        ejecutar_ffmpeg(["-i", str(fondo), "-i", str(avatar), "-filter_complex", filtro,
                         "-frames:v", "1", str(destino)], "componer el fotograma")
    finally:
        texto.unlink(missing_ok=True)


def generar_video(tramos: list[tuple[Path, float]], audio: Path, destino: Path) -> None:
    """MP4 con cada imagen durante su tramo (segundos) y el audio completo.

    Los tramos consecutivos con la misma imagen se agrupan antes de codificar.
    """
    agrupados: list[list] = []
    for imagen, segundos in tramos:
        if agrupados and agrupados[-1][0] == imagen:
            agrupados[-1][1] += segundos
        else:
            agrupados.append([imagen, segundos])
    if not agrupados:
        raise ValueError("No hay imágenes para el video.")

    lista = destino.with_name(destino.stem + "_fotogramas.txt")
    lineas = []
    for imagen, segundos in agrupados:
        ruta = str(imagen).replace("'", "'\\''")
        lineas += [f"file '{ruta}'", f"duration {max(segundos, 0.04):.3f}"]
    # El demuxer concat ignora la duración del último archivo si no se repite
    lineas.append(lineas[-2])
    lista.write_text("\n".join(lineas) + "\n", "utf-8")
    try:
        ejecutar_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lista), "-i", str(audio),
                         "-map", "0:v", "-map", "1:a",
                         "-vf", f"fps={FPS},format=yuv420p",
                         "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage",
                         "-crf", "23", "-c:a", "aac", "-b:a", "160k",
                         "-shortest", "-movflags", "+faststart", str(destino)],
                        "generar el video")
    finally:
        lista.unlink(missing_ok=True)
    log.info("Meet-Video: %s (%d tramo(s))", destino.name, len(agrupados))


def _filtro_avatar_animado(ancho_avatar: int, izquierda: float, nombre_txt: Path | None) -> str:
    """[0] fondo · [1] video animado (sin alfa) · [2] PNG original (de él sale la máscara)."""
    filtro = (
        f"[0]scale={ANCHO}:{ALTO}:force_original_aspect_ratio=increase,"
        f"crop={ANCHO}:{ALTO},setsar=1[f];"
        "[2]alphaextract[m0];[m0][1]scale2ref[m][v];"
        # El motor puede quedarse unos fotogramas corto: se congela el último
        "[v][m]alphamerge,tpad=stop_mode=clone:stop_duration=2,"
        f"scale={ancho_avatar}:-2[a];"
        f"[f][a]overlay=x=W*{izquierda:.4f}:y=H-h:format=auto"
    )
    if nombre_txt:
        filtro += (f",drawtext=fontfile={_escapar_ruta_filtro(FUENTE)}"
                   f":textfile={_escapar_ruta_filtro(nombre_txt)}:fontsize=24:fontcolor=white"
                   ":x=24:y=h-th-24:box=1:boxcolor=black@0.55:boxborderw=10")
    return filtro + f",fps={FPS},format=yuv420p"


def componer_tramo_animado(fondo: Path, avatar_png: Path, animado: Path, nombre: str,
                           duracion: float, destino: Path, ancho: float = ANCHO_AVATAR,
                           izquierda: float = MARGEN_IZQUIERDO) -> None:
    """Tramo sin audio: el avatar animado, recortado con la máscara alfa del PNG
    original, sobre el fondo y con la misma colocación que el fotograma fijo."""
    texto = destino.with_suffix(".nombre.txt")
    usar_texto = bool(nombre.strip()) and FUENTE.is_file()
    if usar_texto:
        texto.write_text(nombre.strip(), "utf-8")
    try:
        ejecutar_ffmpeg(["-loop", "1", "-framerate", str(FPS), "-i", str(fondo),
                         "-i", str(animado), "-i", str(avatar_png),
                         "-filter_complex",
                         _filtro_avatar_animado(_ancho_px(ancho), izquierda,
                                                texto if usar_texto else None),
                         "-t", f"{max(duracion, 0.04):.3f}", "-an",
                         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                         str(destino)], "componer el tramo animado")
    finally:
        texto.unlink(missing_ok=True)


def concatenar_tramos(tramos: list[Path], audio: Path, destino: Path) -> None:
    """Une tramos de video (mismo formato) y les pone el audio completo."""
    lista = destino.with_name(destino.stem + "_tramos.txt")
    lista.write_text("".join("file '%s'\n" % str(t).replace("'", "'\\''") for t in tramos),
                     "utf-8")
    try:
        ejecutar_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lista), "-i", str(audio),
                         "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                         "-c:a", "aac", "-b:a", "160k", "-shortest",
                         "-movflags", "+faststart", str(destino)], "unir los tramos")
    finally:
        lista.unlink(missing_ok=True)


def normalizar_video(origen: Path, audio: Path, destino: Path) -> None:
    """Reencoda la salida del motor (MPEG-4 parte 2) a H.264 + AAC, que sí
    reproducen los navegadores, con el audio original."""
    ejecutar_ffmpeg(["-i", str(origen), "-i", str(audio), "-map", "0:v", "-map", "1:a",
                     "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p",
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                     "-c:a", "aac", "-b:a", "160k", "-shortest",
                     "-movflags", "+faststart", str(destino)], "codificar el video")


def concatenar_videos(videos: list[Path], audio: Path, destino: Path) -> None:
    """Une los videos de los segmentos (pueden venir de caminos distintos: fijo o
    animado) reencodando a un formato común, con el audio completo."""
    lista = destino.with_name(destino.stem + "_videos.txt")
    lista.write_text("".join("file '%s'\n" % str(v).replace("'", "'\\''") for v in videos), "utf-8")
    try:
        ejecutar_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lista), "-i", str(audio),
                         "-map", "0:v", "-map", "1:a", "-vf", f"fps={FPS},format=yuv420p",
                         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                         "-c:a", "aac", "-b:a", "160k", "-shortest",
                         "-movflags", "+faststart", str(destino)], "unir los videos")
    finally:
        lista.unlink(missing_ok=True)
