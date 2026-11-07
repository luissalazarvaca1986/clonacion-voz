# -*- coding: utf-8 -*-
"""Perfiles de emoción y su traducción a ajustes de síntesis y de prosodia.

Qwen3-TTS Base solo clona: no acepta instrucciones de estilo. La emoción se
consigue combinando, por segmento:

* muestreo de llama-tts (temperatura, top-p, top-k): más alto = más expresivo;
* prosodia aplicada después con ffmpeg/rubberband: ritmo, tono y volumen;
* la pausa que sigue al segmento;
* al animar el avatar, la escala de expresión facial.

Los perfiles son *desplazamientos* sobre los ajustes base del usuario, escalados
por la intensidad, de modo que «neutro» es exactamente lo que ya había.

Marcas en el texto, para fijar emociones a mano sin pasar por el LLM:

    [alegria:alta] ¡Qué emoción tengo! [tristeza] Pero esto no lo esperábamos.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from app.servicios.texto import trocear_texto


@dataclass(frozen=True)
class Perfil:
    etiqueta: str
    descripcion: str
    temp: float = 0.0        # se suma a la temperatura base
    top_p: float = 0.0       # se suma al top-p base
    top_k: int = 0           # se suma al top-k base
    ritmo: float = 1.0       # factor de velocidad (1 = igual)
    tono: float = 0.0        # semitonos
    volumen_db: float = 0.0
    pausa_ms: int = 250      # silencio tras el segmento
    expresion: float = 1.0   # escala de expresión facial al animar el avatar


PERFILES: dict[str, Perfil] = {
    "neutro": Perfil("Neutro", "Informativo, sin carga emocional"),
    "alegria": Perfil("Alegría", "Contento, celebración, buenas noticias",
                      temp=0.15, top_p=0.03, top_k=20, ritmo=1.07, tono=1.2, volumen_db=1.5,
                      pausa_ms=200, expresion=1.3),
    "entusiasmo": Perfil("Entusiasmo", "Euforia, emoción intensa, exclamaciones",
                         temp=0.25, top_p=0.04, top_k=30, ritmo=1.12, tono=1.8, volumen_db=2.5,
                         pausa_ms=180, expresion=1.5),
    "sorpresa": Perfil("Sorpresa", "Asombro, algo inesperado",
                       temp=0.2, top_p=0.03, top_k=20, ritmo=1.04, tono=2.0, volumen_db=1.5,
                       pausa_ms=300, expresion=1.4),
    "tristeza": Perfil("Tristeza", "Pena, decepción, malas noticias",
                       temp=-0.2, top_p=-0.08, top_k=-10, ritmo=0.88, tono=-1.5, volumen_db=-2.5,
                       pausa_ms=500, expresion=0.8),
    "enojo": Perfil("Enojo", "Indignación, queja, firmeza",
                    temp=0.15, top_p=0.0, top_k=10, ritmo=1.06, tono=-0.5, volumen_db=3.0,
                    pausa_ms=250, expresion=1.4),
    "miedo": Perfil("Miedo", "Tensión, preocupación, nervios",
                    temp=0.1, top_p=0.0, top_k=10, ritmo=1.1, tono=0.8, volumen_db=-1.5,
                    pausa_ms=300, expresion=1.2),
    "calma": Perfil("Calma", "Sereno, reflexivo, tranquilizador",
                    temp=-0.25, top_p=-0.05, top_k=-10, ritmo=0.93, tono=-0.5, volumen_db=-1.0,
                    pausa_ms=400, expresion=0.8),
}

INTENSIDADES: dict[str, float] = {"baja": 0.4, "media": 0.7, "alta": 1.0}
EMOCION_POR_DEFECTO = "neutro"
INTENSIDAD_POR_DEFECTO = "media"

_MARCA = re.compile(r"\[\s*([a-záéíóúñ]+)\s*(?::\s*(baja|media|alta)\s*)?\]", re.IGNORECASE)
_SIN_TILDES = str.maketrans("áéíóú", "aeiou")


def _clave(nombre: str | None) -> str:
    return (nombre or "").strip().lower().translate(_SIN_TILDES)


def normalizar_emocion(nombre: str | None) -> str:
    return _clave(nombre) if _clave(nombre) in PERFILES else EMOCION_POR_DEFECTO


def normalizar_intensidad(nombre: str | None) -> str:
    clave = (nombre or "").strip().lower()
    return clave if clave in INTENSIDADES else INTENSIDAD_POR_DEFECTO


def _limitar(valor: float, minimo: float, maximo: float) -> float:
    return max(minimo, min(maximo, valor))


def ajustes_segmento(cfg_base: dict[str, Any], emocion: str, intensidad: str) -> dict[str, Any]:
    """Ajustes efectivos de un segmento: base del usuario + perfil × intensidad."""
    p = PERFILES[normalizar_emocion(emocion)]
    k = INTENSIDADES[normalizar_intensidad(intensidad)]
    return {
        "temp": round(_limitar(float(cfg_base["temp"]) + p.temp * k, 0.1, 1.5), 3),
        "top_p": round(_limitar(float(cfg_base["top_p"]) + p.top_p * k, 0.1, 1.0), 3),
        "top_k": int(_limitar(int(cfg_base["top_k"]) + round(p.top_k * k), 0, 200)),
        "ritmo": round(1 + (p.ritmo - 1) * k, 3),
        "tono": round(p.tono * k, 2),
        "volumen_db": round(p.volumen_db * k, 2),
        # Entre segmentos hace falta algo más de aire que entre bloques de una misma frase
        "pausa_ms": int(round(max(int(cfg_base["pausa_ms"]), 250) + (p.pausa_ms - 250) * k)),
        "escala_expresion": round(_limitar(1 + (p.expresion - 1) * k, 0.0, 3.0), 2),
        # Los estilos de cabeza del motor no tienen significado emocional: se
        # sugiere el 0 y el usuario lo cambia por segmento si quiere
        "estilo_pose": 0,
    }


def completar_ajustes(cfg_base: dict[str, Any], segmento: dict[str, Any]) -> dict[str, Any]:
    """Ajustes del segmento: los que haya revisado el usuario y, para el resto,
    los que sugiere su emoción."""
    sugeridos = ajustes_segmento(cfg_base, segmento.get("emocion"), segmento.get("intensidad"))
    propios = {k: v for k, v in (segmento.get("ajustes") or {}).items() if v is not None}
    return {**sugeridos, **{k: v for k, v in propios.items() if k in sugeridos}}


def filtros_prosodia(ajustes: dict[str, Any]) -> str:
    """Cadena -af de ffmpeg para ritmo, tono y volumen ('' si no hay nada que tocar)."""
    filtros = []
    tono_factor = 2 ** (float(ajustes["tono"]) / 12)
    if abs(ajustes["ritmo"] - 1) > 0.005 or abs(tono_factor - 1) > 0.002:
        # rubberband cambia tono y tempo por separado y sin el efecto «ardilla»
        filtros.append(f"rubberband=tempo={ajustes['ritmo']:.4f}:pitch={tono_factor:.4f}"
                       ":formant=preserved")
    if abs(ajustes["volumen_db"]) > 0.05:
        filtros.append(f"volume={ajustes['volumen_db']:+.2f}dB")
    return ",".join(filtros)


def catalogo() -> dict[str, Any]:
    return {
        "emociones": [{"id": clave, **asdict(p)} for clave, p in PERFILES.items()],
        "intensidades": list(INTENSIDADES),
        "por_defecto": {"emocion": EMOCION_POR_DEFECTO, "intensidad": INTENSIDAD_POR_DEFECTO},
    }


# ---- Marcas en el texto ------------------------------------------------------
def tiene_marcas(texto: str) -> bool:
    return any(_clave(m.group(1)) in PERFILES for m in _MARCA.finditer(texto))


def parsear_marcas(texto: str) -> list[dict[str, str]]:
    """`[emocion:intensidad] texto …` -> segmentos. Lo previo a la 1.ª marca es neutro."""
    segmentos: list[dict[str, str]] = []
    emocion, intensidad, inicio = EMOCION_POR_DEFECTO, INTENSIDAD_POR_DEFECTO, 0
    for m in _MARCA.finditer(texto):
        if _clave(m.group(1)) not in PERFILES:
            continue  # corchetes que no son una emoción conocida: se quedan en el texto
        _anadir(segmentos, texto[inicio:m.start()], emocion, intensidad)
        emocion, intensidad = normalizar_emocion(m.group(1)), normalizar_intensidad(m.group(2))
        inicio = m.end()
    _anadir(segmentos, texto[inicio:], emocion, intensidad)
    return segmentos


def _anadir(segmentos: list[dict[str, str]], texto: str, emocion: str, intensidad: str) -> None:
    texto = re.sub(r"\s+", " ", texto).strip()
    if texto:
        segmentos.append({"texto": texto, "emocion": emocion, "intensidad": intensidad})


def a_marcas(segmentos: list[dict[str, str]]) -> str:
    return " ".join(f"[{s['emocion']}:{s['intensidad']}] {s['texto']}" for s in segmentos)


def fusionar_consecutivos(segmentos: list[dict[str, str]]) -> list[dict[str, str]]:
    """Une frases seguidas con la misma emoción e intensidad en un solo segmento."""
    fusionados: list[dict[str, str]] = []
    for s in segmentos:
        if fusionados and (fusionados[-1]["emocion"], fusionados[-1]["intensidad"]) == \
                (s["emocion"], s["intensidad"]):
            fusionados[-1] = {**fusionados[-1], "texto": f"{fusionados[-1]['texto']} {s['texto']}"}
        else:
            fusionados.append(dict(s))
    return fusionados


# ---- Frases ------------------------------------------------------------------
def dividir_frases(texto: str) -> list[str]:
    """Frases (y cláusulas tras ; o :) que el LLM etiqueta una a una."""
    texto = re.sub(r"\s+", " ", texto).strip()
    if not texto:
        return []
    frases = [f.strip() for f in re.split(r"(?<=[.!?…;:])\s+", texto) if f.strip()]
    # Frases enormes sin puntuación: se cortan para no dar al LLM párrafos enteros
    return [trozo for f in frases for trozo in trocear_texto(f, 400)]
