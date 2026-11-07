# -*- coding: utf-8 -*-
"""Cliente de llama-server (API compatible con OpenAI), con arranque bajo demanda.

Dos modos:

* bajo demanda (por defecto) — el backend lanza `llama-server` como proceso
  hijo solo mientras dura el análisis y lo apaga al terminar. Así el modelo no
  ocupa memoria entre análisis y nunca coincide con el TTS ni con el animador.
* servidor externo — si se configura CLONAR_LLM_URL, se usa ese servidor tal cual.
"""
from __future__ import annotations

import json
import logging
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from app.nucleo.ajustes import SIN_VENTANA
from app.nucleo.errores import ErrorDominio

log = logging.getLogger(__name__)


class LLMNoDisponible(ErrorDominio):
    estado = 503


def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ClienteLLM:
    def __init__(self, url: str, timeout: float, modelo: Path | None = None,
                 binario: str = "", hilos: int = 6, contexto: int = 4096) -> None:
        self._url_externa = url.rstrip("/")
        self._timeout = timeout
        self._modelo = modelo
        self._binario = binario
        self._hilos = hilos
        self._contexto = contexto
        self._url = self._url_externa
        self._proceso: subprocess.Popen[bytes] | None = None
        self._usos = 0
        self._cerrojo = threading.Lock()

    # ---- Configuración -------------------------------------------------------
    @property
    def bajo_demanda(self) -> bool:
        return not self._url_externa and self._modelo is not None

    @property
    def configurado(self) -> bool:
        return bool(self._url_externa) or bool(
            self._modelo and self._modelo.is_file() and Path(self._binario).is_file())

    def modelo(self) -> str:
        if self.bajo_demanda:
            return self._modelo.stem.replace("-Q4_K_M", "").lower()
        try:
            datos = self._pedir("/v1/models", timeout=2)
            return datos["data"][0]["id"].rsplit("/", 1)[-1]
        except (LLMNoDisponible, KeyError, IndexError, TypeError):
            return ""

    def estado(self) -> dict[str, Any]:
        """Para /api/estado: rápido y sin lanzar excepciones ni arrancar el modelo."""
        if self.bajo_demanda:
            return {"configurado": self.configurado, "disponible": self.configurado,
                    "modelo": self.modelo() if self.configurado else "",
                    "modo": "bajo_demanda", "activo": self._proceso is not None}
        if not self._url_externa:
            return {"configurado": False, "disponible": False, "modelo": "",
                    "modo": "desactivado", "activo": False}
        try:
            self._pedir("/health", timeout=2)
            return {"configurado": True, "disponible": True, "modelo": self.modelo(),
                    "modo": "servidor", "activo": True}
        except LLMNoDisponible:
            return {"configurado": True, "disponible": False, "modelo": "",
                    "modo": "servidor", "activo": False}

    # ---- Ciclo de vida del proceso bajo demanda -------------------------------
    @contextmanager
    def sesion(self) -> Iterator[None]:
        """Mantiene el modelo cargado mientras dure el bloque; luego lo apaga."""
        if not self.bajo_demanda:
            yield
            return
        with self._cerrojo:
            if self._usos == 0:
                self._arrancar()
            self._usos += 1
        try:
            yield
        finally:
            with self._cerrojo:
                self._usos -= 1
                if self._usos == 0:
                    self._apagar()

    def _arrancar(self) -> None:
        if not self.configurado:
            raise LLMNoDisponible("No se encuentra el modelo del LLM ni llama-server.")
        puerto = _puerto_libre()
        inicio = time.time()
        self._proceso = subprocess.Popen(
            [self._binario, "-m", str(self._modelo), "--host", "127.0.0.1", "--port", str(puerto),
             "--device", "none", "-ngl", "0", "-t", str(self._hilos), "-c", str(self._contexto),
             "-np", "1", "--jinja", "--alias", self.modelo()],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=SIN_VENTANA)
        self._url = f"http://127.0.0.1:{puerto}"
        while time.time() - inicio < 90:
            if self._proceso.poll() is not None:
                error = (self._proceso.stderr.read() or b"")[-400:].decode("utf-8", "replace")
                self._proceso = None
                raise LLMNoDisponible(f"llama-server no arrancó: {error}")
            try:
                self._pedir("/health", timeout=1)
                log.info("LLM cargado bajo demanda en %.1f s", time.time() - inicio)
                return
            except LLMNoDisponible:
                time.sleep(0.25)
        self._apagar()
        raise LLMNoDisponible("El LLM tardó demasiado en cargar.")

    def _apagar(self) -> None:
        proceso, self._proceso = self._proceso, None
        if proceso and proceso.poll() is None:
            proceso.terminate()
            try:
                proceso.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proceso.kill()
                proceso.wait()
            log.info("LLM apagado: memoria liberada")

    def cerrar(self) -> None:
        with self._cerrojo:
            self._usos = 0
            self._apagar()

    # ---- Peticiones ------------------------------------------------------------
    def _pedir(self, ruta: str, datos: dict[str, Any] | None = None,
               timeout: float | None = None) -> dict[str, Any]:
        if not self._url:
            raise LLMNoDisponible("No hay ningún LLM configurado.")
        peticion = urllib.request.Request(
            self._url + ruta,
            data=json.dumps(datos).encode() if datos is not None else None,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(peticion, timeout=timeout or self._timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            cuerpo = e.read()[:300].decode("utf-8", "replace")
            raise LLMNoDisponible(f"El LLM respondió HTTP {e.code}: {cuerpo}") from e
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            raise LLMNoDisponible(f"No se pudo contactar con el LLM: {e}") from e

    def json_estructurado(self, sistema: str, usuario: str, esquema: dict[str, Any],
                          max_tokens: int = 1024) -> dict[str, Any]:
        """Chat cuya respuesta está forzada por gramática a cumplir `esquema`."""
        with self.sesion():
            respuesta = self._pedir("/v1/chat/completions", {
                "messages": [{"role": "system", "content": sistema},
                             {"role": "user", "content": usuario}],
                "temperature": 0.1,
                "max_tokens": max_tokens,
                # Qwen3.5 «piensa» por defecto: aquí solo queremos la etiqueta
                "chat_template_kwargs": {"enable_thinking": False},
                "response_format": {"type": "json_schema",
                                    "json_schema": {"name": "respuesta", "schema": esquema}},
            })
        try:
            contenido = respuesta["choices"][0]["message"]["content"]
            return json.loads(contenido)
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise LLMNoDisponible(f"Respuesta del LLM no válida: {e}") from e
