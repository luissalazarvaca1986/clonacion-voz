# -*- coding: utf-8 -*-
"""Análisis emocional del texto: lo divide en segmentos con emoción e intensidad.

El LLM no reescribe nada: el texto se parte aquí en frases y el modelo solo
devuelve una etiqueta `emocion:intensidad` por frase, con la salida forzada por
un esquema JSON. Así no se pierde ni se cambia ninguna palabra.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from app.servicios.emociones import (EMOCION_POR_DEFECTO, INTENSIDAD_POR_DEFECTO, INTENSIDADES,
                                     PERFILES, dividir_frases, fusionar_consecutivos,
                                     normalizar_emocion, normalizar_intensidad, parsear_marcas,
                                     tiene_marcas)
from app.servicios.llm import ClienteLLM, LLMNoDisponible

log = logging.getLogger(__name__)

MAX_FRASES = 120   # más que esto se etiqueta por tandas

_SISTEMA = """Eres director de locución. Decides con qué emoción debe leerse en voz alta cada frase de un texto.

Emociones posibles:
{emociones}

Intensidad: baja, media o alta.

Criterios:
- Usa «neutro» para saludos, datos, presentaciones e información sin carga emocional.
- Fíjate en el sentido, no solo en los signos: «Pero esto no lo esperábamos» es tristeza o decepción.
- Las exclamaciones de celebración son alegría o entusiasmo; las de queja, enojo.
- Reserva «alta» para frases claramente cargadas.

Devuelve exactamente una etiqueta «emocion:intensidad» por frase, en el mismo orden."""

_EJEMPLO_USUARIO = """Frases:
1. Estamos aquí presentando este escenario.
2. ¡Qué emoción que tengo, cuánta alegría!
3. Pero esto es una realidad que no esperábamos."""
_EJEMPLO_RESPUESTA = '{"etiquetas": ["neutro:media", "entusiasmo:alta", "tristeza:media"]}'


def _esquema(n: int) -> dict[str, Any]:
    opciones = [f"{e}:{i}" for e in PERFILES for i in INTENSIDADES]
    return {
        "type": "object",
        "properties": {"etiquetas": {"type": "array", "items": {"enum": opciones},
                                     "minItems": n, "maxItems": n}},
        "required": ["etiquetas"],
    }


class AnalizadorEmocional:
    def __init__(self, llm: ClienteLLM) -> None:
        self._llm = llm
        emociones = "\n".join(f"- {clave}: {p.descripcion}" for clave, p in PERFILES.items())
        self._sistema = _SISTEMA.format(emociones=emociones) + (
            f"\n\nEjemplo:\n{_EJEMPLO_USUARIO}\nRespuesta: {_EJEMPLO_RESPUESTA}")

    def analizar(self, texto: str) -> dict[str, Any]:
        """Segmentos con emoción. Las marcas manuales mandan sobre el LLM.

        Lanza LLMNoDisponible si hace falta el LLM y no responde.
        """
        inicio = time.time()
        if tiene_marcas(texto):
            return {"origen": "marcas", "modelo": "", "segundos": 0.0,
                    "segmentos": parsear_marcas(texto)}

        frases = dividir_frases(texto)
        etiquetas: list[str] = []
        with self._llm.sesion():   # bajo demanda: carga el modelo aquí y lo apaga al salir
            for i in range(0, len(frases), MAX_FRASES):
                etiquetas += self._etiquetar(frases[i:i + MAX_FRASES])

        segmentos = []
        for frase, etiqueta in zip(frases, etiquetas):
            emocion, _, intensidad = etiqueta.partition(":")
            segmentos.append({"texto": frase, "emocion": normalizar_emocion(emocion),
                              "intensidad": normalizar_intensidad(intensidad)})
        segundos = round(time.time() - inicio, 2)
        log.info("Análisis emocional: %d frase(s) -> %d segmento(s) en %.1f s",
                 len(frases), len(fusionar_consecutivos(segmentos)), segundos)
        return {"origen": "llm", "modelo": self._llm.modelo(), "segundos": segundos,
                "segmentos": fusionar_consecutivos(segmentos)}

    def _etiquetar(self, frases: list[str]) -> list[str]:
        if not frases:
            return []
        usuario = "Frases:\n" + "\n".join(f"{n}. {f}" for n, f in enumerate(frases, start=1))
        respuesta = self._llm.json_estructurado(self._sistema, usuario, _esquema(len(frases)),
                                                max_tokens=16 * len(frases) + 64)
        etiquetas = respuesta.get("etiquetas")
        if not isinstance(etiquetas, list):
            raise LLMNoDisponible("El LLM no devolvió etiquetas.")
        # La gramática ya fija la longitud; esto cubre a un servidor que la ignore
        relleno = f"{EMOCION_POR_DEFECTO}:{INTENSIDAD_POR_DEFECTO}"
        return [str(e) for e in etiquetas[:len(frases)]] + \
            [relleno] * max(0, len(frases) - len(etiquetas))


def segmento_neutro(texto: str) -> list[dict[str, str]]:
    return [{"texto": texto, "emocion": EMOCION_POR_DEFECTO, "intensidad": INTENSIDAD_POR_DEFECTO}]
