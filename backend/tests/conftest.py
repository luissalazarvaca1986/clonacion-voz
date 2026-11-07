# -*- coding: utf-8 -*-
"""Cada test arranca la app sobre carpetas temporales: nunca toca datos reales."""
from __future__ import annotations

import io
import math
import struct
import subprocess
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import crear_app
from app.nucleo.ajustes import Rutas


def wav_tono(segundos: float = 1.0, hz: int = 16000, frecuencia: float = 220.0) -> bytes:
    """WAV mono de 16 bits con un tono: sirve de voz de referencia falsa."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(hz)
        w.writeframes(b"".join(
            struct.pack("<h", int(12000 * math.sin(2 * math.pi * frecuencia * i / hz)))
            for i in range(int(segundos * hz))))
    return buf.getvalue()


def png(ruta: Path, ancho: int, alto: int, transparente: bool = False) -> Path:
    """Imagen de prueba generada con ffmpeg (con canal alfa si se pide)."""
    color = "red@0.5" if transparente else "blue"
    formato = "rgba" if transparente else "rgb24"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", f"color=c={color}:s={ancho}x{alto}", "-vf", f"format={formato}",
                    "-frames:v", "1", str(ruta)], check=True)
    return ruta


@pytest.fixture
def imagenes(tmp_path: Path) -> dict[str, Path]:
    carpeta = tmp_path / "entrada"
    carpeta.mkdir()
    return {"avatar": png(carpeta / "avatar.png", 400, 350, transparente=True),
            **{e: png(carpeta / f"{e}.png", 800, 450) for e in ("dia", "noche", "lluvia")}}


@pytest.fixture
def rutas(tmp_path: Path) -> Rutas:
    return Rutas(base=tmp_path, modelo=tmp_path / "modelo", voces=tmp_path / "voces",
                 salidas=tmp_path / "salidas", tmp=tmp_path / ".tmp",
                 config=tmp_path / "config" / "config.json",
                 avatares=tmp_path / "avatares", videos=tmp_path / "videos",
                 animacion=tmp_path / "animacion")


@pytest.fixture
def cliente(rutas: Rutas):
    with TestClient(crear_app(rutas)) as c:
        yield c
