# -*- coding: utf-8 -*-
"""Utilidades de audio: conversión con ffmpeg, duración y unión de WAVs."""
from __future__ import annotations

import shutil
import subprocess
import wave
from pathlib import Path

from app.nucleo.ajustes import MAX_SEGUNDOS_REFERENCIA, SIN_VENTANA
from app.nucleo.errores import ErrorProceso
from app.servicios.entorno import hay_ffmpeg


def convertir_a_wav(origen: Path, destino: Path, hz: int = 16000) -> None:
    """Normaliza cualquier audio a WAV PCM mono, que es lo que espera el modelo."""
    if not hay_ffmpeg():
        if origen.suffix.lower() in (".wav", ".mp3"):
            shutil.copyfile(origen, destino)
            return
        raise ErrorProceso("Se necesita ffmpeg para convertir este formato de audio.")

    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-i", str(origen), "-ac", "1", "-ar", str(hz),
             # Sin silencio inicial y como máximo 15 s: más referencia no mejora el
             # clon y desborda el contexto (-c) en GPUs de 4 GB.
             "-af", "silenceremove=start_periods=1:start_threshold=-40dB",
             "-t", str(MAX_SEGUNDOS_REFERENCIA),
             "-c:a", "pcm_s16le", str(destino)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=SIN_VENTANA, timeout=120,
        )
    except subprocess.TimeoutExpired as exc:
        raise ErrorProceso("ffmpeg no pudo convertir el audio: tardó demasiado.") from exc
    if r.returncode != 0 or not destino.exists():
        raise ErrorProceso(f"ffmpeg no pudo convertir el audio: {r.stderr[:400]}")


def ejecutar_ffmpeg(args: list[str], que: str) -> None:
    try:
        r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           creationflags=SIN_VENTANA, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ErrorProceso(f"ffmpeg no pudo {que}: {exc}") from exc
    if r.returncode != 0:
        raise ErrorProceso(f"ffmpeg no pudo {que}: {r.stderr[:400]}")


def procesar_pieza(origen: Path, destino: Path, filtros: str, pausa_ms: int) -> None:
    """Aplica la prosodia de un segmento y le añade el silencio que lo sigue."""
    cadena = [f for f in (
        filtros,
        "afade=t=in:d=0.015",                     # evita el chasquido en la unión
        f"apad=pad_dur={pausa_ms / 1000:.3f}" if pausa_ms > 0 else "",
    ) if f]
    ejecutar_ffmpeg(["-i", str(origen), "-af", ",".join(cadena), "-ac", "1",
                     "-c:a", "pcm_s16le", str(destino)], "aplicar la prosodia")


def concatenar(partes: list[Path], destino: Path) -> None:
    """Une las piezas ya procesadas en el entregable final."""
    if not partes:
        raise ErrorProceso("El modelo no generó ningún audio.")
    lista = partes[0].with_name(destino.stem + "_lista.txt")
    # El demuxer concat exige escapar las comillas simples de las rutas
    rutas = (str(p).replace("'", "'\\''") for p in partes)
    lista.write_text("".join(f"file '{r}'\n" for r in rutas), "utf-8")
    try:
        # alimiter: las subidas de volumen de algunas emociones no deben saturar
        ejecutar_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lista),
                         "-af", "alimiter=limit=0.97:level=false", "-c:a", "pcm_s16le",
                         str(destino)], "unir las piezas")
    finally:
        lista.unlink(missing_ok=True)


def duracion_wav(ruta: Path) -> float:
    try:
        with wave.open(str(ruta), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except (OSError, EOFError, wave.Error):
        return 0.0


def unir_wavs(partes: list[Path], destino: Path, pausa_ms: int = 150) -> None:
    """Concatena WAVs del mismo formato intercalando un breve silencio."""
    partes = [p for p in partes if p.exists() and p.stat().st_size > 44]
    if not partes:
        raise ErrorProceso("El modelo no generó ningún audio.")
    if len(partes) == 1:
        shutil.copyfile(partes[0], destino)
        return

    with wave.open(str(partes[0]), "rb") as w0:
        parametros = w0.getparams()
    silencio = b"\x00" * (
        int(parametros.framerate * pausa_ms / 1000)
        * parametros.nchannels * parametros.sampwidth
    )
    with wave.open(str(destino), "wb") as salida:
        salida.setparams(parametros)
        for i, parte in enumerate(partes):
            with wave.open(str(parte), "rb") as w:
                salida.writeframes(w.readframes(w.getnframes()))
            if i < len(partes) - 1 and silencio:
                salida.writeframes(silencio)


def recortar_wav(origen: Path, inicio: float, duracion: float, destino: Path) -> None:
    """Trozo [inicio, inicio + duración) de un WAV, sin cambiar su formato."""
    ejecutar_ffmpeg(["-ss", f"{inicio:.3f}", "-t", f"{max(duracion, 0.01):.3f}", "-i", str(origen),
                     "-c:a", "pcm_s16le", str(destino)], "recortar el audio del segmento")


def anadir_silencio(origen: Path, milisegundos: int, destino: Path) -> None:
    ejecutar_ffmpeg(["-i", str(origen), "-af", f"apad=pad_dur={milisegundos / 1000:.3f}",
                     "-c:a", "pcm_s16le", str(destino)], "añadir la pausa final")


def unir_partes(partes: list[Path], destino: Path) -> None:
    """Concatena los WAV de los segmentos tal cual (ya llevan sus pausas)."""
    lista = destino.with_name(destino.stem + "_lista.txt")
    lista.write_text("".join("file '%s'\n" % str(p).replace("'", "'\\''") for p in partes), "utf-8")
    try:
        ejecutar_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lista), "-c:a", "pcm_s16le",
                         str(destino)], "unir los segmentos")
    finally:
        lista.unlink(missing_ok=True)
