# -*- coding: utf-8 -*-
"""Troceado del texto en bloques que el modelo pueda sintetizar de una vez."""
from __future__ import annotations

import re


def trocear_texto(texto: str, maximo: int) -> list[str]:
    """Parte el texto en bloques por frases para no agotar el límite de frames."""
    texto = re.sub(r"\s+", " ", texto).strip()
    if not texto:
        return []
    if len(texto) <= maximo:
        return [texto]

    bloques: list[str] = []
    actual = ""
    for frase in re.split(r"(?<=[.!?…。！？;:])\s+", texto):
        while len(frase) > maximo:  # frase gigantesca: cortar por coma o espacio
            corte = max(frase.rfind(",", 0, maximo), frase.rfind(" ", 0, maximo))
            if corte <= maximo // 2:
                corte = maximo
            if actual:
                bloques.append(actual.strip())
                actual = ""
            bloques.append(frase[:corte].strip())
            frase = frase[corte:].strip()
        if len(actual) + len(frase) + 1 <= maximo:
            actual = f"{actual} {frase}".strip()
        else:
            if actual:
                bloques.append(actual.strip())
            actual = frase
    if actual:
        bloques.append(actual.strip())
    return [b for b in bloques if b]
