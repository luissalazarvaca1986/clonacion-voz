# -*- coding: utf-8 -*-
"""Caso de uso «generar audio» (y Meet-Video): valida, resuelve y lanza la tarea.

Modos de texto:

* simple    — el texto se trocea y todo se sintetiza con los mismos ajustes.
* emocional — el texto se divide en segmentos con emoción (marcas manuales,
              segmentos ya revisados por el usuario o análisis con el LLM) y
              cada uno se sintetiza y procesa con su propio perfil.

Cada segmento puede llevar su propia voz y su propio avatar. Si no los trae, se
usan los generales de la petición. Si un segmento tiene avatar pero no voz,
habla con la voz asociada a ese avatar.

Con `video` se compone además un MP4: cada tramo muestra el avatar que habla,
sobre su fondo de la escena elegida (día, noche o lluvia). Con `animado`, cada
tramo se anima con el motor de animación facial (SadTalker) en vez de quedar fijo.

Memoria: cada etapa carga su modelo y lo libera al terminar — LLM (análisis),
llama-tts (un proceso por pieza) y animador (un proceso por tramo) — de modo que
nunca hay dos modelos en memoria a la vez.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.nucleo.configuracion import RepositorioConfig
from app.nucleo.errores import PeticionInvalida
from app.servicios.analisis import AnalizadorEmocional, segmento_neutro
from app.servicios.animacion import ServicioAnimacion
from app.servicios.avatares import ESCENAS, GaleriaAvatares
from app.servicios.emociones import (completar_ajustes, filtros_prosodia, normalizar_emocion,
                                     normalizar_intensidad)
from app.servicios.entorno import DetectorEntorno, es_archivo
from app.servicios.llm import LLMNoDisponible
from app.servicios.salidas import AlmacenSalidas
from app.servicios.sintesis import GestorTareas, Pieza, Tarea
from app.servicios.texto import trocear_texto
from app.servicios.voces import BibliotecaVoces

log = logging.getLogger(__name__)

# Ajustes que cada petición puede sobrescribir sin tocar config.json
AJUSTES_POR_PETICION = ("idioma", "top_k", "top_p", "temp", "semilla", "max_frames",
                        "chars_por_bloque", "pausa_ms", "capas_gpu", "hilos")


@dataclass(frozen=True)
class Hablante:
    """Quién dice un segmento: voz (referencia) y, si hay video, su fotograma."""
    ref: Path | None = None
    voz: str | None = None          # nombre visible de la voz
    avatar_id: str | None = None
    avatar: str | None = None       # nombre visible del avatar
    imagen: Path | None = None      # se rellena al planificar (componer cuesta)


def piezas_simples(cfg: dict[str, Any], texto: str, hablante: Hablante = Hablante(),
                   escena: str | None = None) -> list[Pieza]:
    """Todo el texto es un único segmento, con los ajustes generales."""
    return [Pieza(texto=b, cfg=cfg, pausa_ms=int(cfg["pausa_ms"]), ref=hablante.ref,
                  voz=hablante.voz, imagen=hablante.imagen, avatar=hablante.avatar,
                  avatar_id=hablante.avatar_id, segmento=1, escena=escena)
            for b in trocear_texto(texto, int(cfg["chars_por_bloque"]))]


def piezas_emocionales(cfg: dict[str, Any], segmentos: list[dict[str, str]],
                       hablantes: list[Hablante] | None = None,
                       primer_indice: int = 1) -> list[Pieza]:
    """Cada segmento se trocea si es largo; todos sus trozos comparten perfil y hablante."""
    piezas: list[Pieza] = []
    for n, seg in enumerate(segmentos):
        h = hablantes[n] if hablantes else Hablante()
        emocion = normalizar_emocion(seg.get("emocion"))
        intensidad = normalizar_intensidad(seg.get("intensidad"))
        # Los sugeridos por la emoción, salvo los que el usuario haya cambiado
        ajustes = completar_ajustes(cfg, seg)
        cfg_seg = {**cfg, "temp": ajustes["temp"], "top_p": ajustes["top_p"],
                   "top_k": ajustes["top_k"]}
        filtros = filtros_prosodia(ajustes)
        bloques = trocear_texto(seg["texto"], int(cfg["chars_por_bloque"]))
        for j, bloque in enumerate(bloques):
            ultimo = j == len(bloques) - 1
            piezas.append(Pieza(texto=bloque, cfg=cfg_seg, filtros=filtros,
                                pausa_ms=ajustes["pausa_ms"] if ultimo else int(cfg["pausa_ms"]),
                                emocion=emocion, intensidad=intensidad, ref=h.ref, voz=h.voz,
                                imagen=h.imagen, avatar=h.avatar, avatar_id=h.avatar_id,
                                escala_expresion=ajustes["escala_expresion"],
                                estilo_pose=ajustes["estilo_pose"], segmento=primer_indice + n,
                                escena=seg.get("escena")))
    return piezas


def con_ajustes(cfg: dict[str, Any], segmentos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Segmentos con los ajustes efectivos que se les aplicarán (para mostrarlos)."""
    return [{**s, "ajustes": completar_ajustes(cfg, s)} for s in segmentos]


class ServicioGeneracion:
    def __init__(self, config: RepositorioConfig, entorno: DetectorEntorno,
                 voces: BibliotecaVoces, salidas: AlmacenSalidas, tareas: GestorTareas,
                 analizador: AnalizadorEmocional, avatares: GaleriaAvatares,
                 animacion: ServicioAnimacion) -> None:
        self._config = config
        self._entorno = entorno
        self._voces = voces
        self._salidas = salidas
        self._tareas = tareas
        self._analizador = analizador
        self._avatares = avatares
        self._animacion = animacion

    def _cfg(self, ajustes: dict[str, Any]) -> dict[str, Any]:
        cfg = self._config.cargar()
        cfg.update({k: v for k, v in ajustes.items()
                    if k in AJUSTES_POR_PETICION and v not in (None, "")})
        if archivo := ajustes.get("modelo_tts"):
            # Modelo elegido para esta petición (p. ej. desde la web de conversación)
            par = self._entorno.modelo_tts(str(archivo))
            if par is None:
                raise PeticionInvalida(f"No existe el modelo TTS «{archivo}» (o le falta su mmproj).")
            cfg["modelo"], cfg["mmproj"] = par["modelo"], par["mmproj"]
        return cfg

    def _hablante(self, voz: str | None, avatar: str | None, con_video: bool,
                  voz_general: str | None = None) -> Hablante:
        """Valida voz y avatar (error 400 si no existen) y decide qué voz suena."""
        meta = self._avatares.para_video(avatar) if avatar else None
        voz = voz or (meta["voz"] if meta else None) or voz_general
        ref = self._voces.referencia(voz) if voz else None
        nombre_voz = None
        if voz:
            nombre_voz = next((v["nombre"] for v in self._voces.listar() if v["id"] == voz), voz)
        if con_video and not meta:
            raise PeticionInvalida("Elige un avatar para el Meet-Video.")
        return Hablante(ref=ref, voz=nombre_voz,
                        avatar_id=meta["id"] if meta else None,
                        avatar=meta["nombre"] if meta else None)

    def _con_imagen(self, h: Hablante, escena: str) -> Hablante:
        if not h.avatar_id:
            return h
        return Hablante(h.ref, h.voz, h.avatar_id, h.avatar,
                        self._avatares.fotograma(h.avatar_id, escena))

    def sugerir(self, emocion: str, intensidad: str, ajustes: dict[str, Any]) -> dict[str, Any]:
        """Ajustes que sugiere una emoción sobre la configuración base."""
        return completar_ajustes(self._cfg(ajustes), {"emocion": emocion, "intensidad": intensidad})

    def analizar(self, texto: str | None, ajustes: dict[str, Any]) -> dict[str, Any]:
        """Vista previa de la segmentación emocional (no sintetiza nada)."""
        texto = (texto or "").strip()
        if not texto:
            raise PeticionInvalida("Escribe el texto que quieres analizar.")
        resultado = self._analizador.analizar(texto)
        resultado["segmentos"] = con_ajustes(self._cfg(ajustes), resultado["segmentos"])
        return resultado

    def _preparar(self, cfg: dict[str, Any], dispositivo: str | None):
        """Comprueba binario, modelo y mmproj y resuelve el dispositivo de la GPU."""
        rutas = self._entorno.rutas_efectivas(cfg)
        for ruta, etiqueta in ((rutas.binario, "binario llama-tts"), (rutas.modelo, "modelo"),
                               (rutas.mmproj, "mmproj")):
            if not es_archivo(ruta):
                raise PeticionInvalida(f"No se encuentra el {etiqueta}. Revisa los ajustes.")
        if not self._entorno.soporta_qwen3tts(rutas.binario):
            raise PeticionInvalida("Tu llama.cpp no admite Qwen3-TTS (falta --mmproj en "
                                   "llama-tts). Actualízalo: winget upgrade ggml.llamacpp")
        modo_disp = str(dispositivo or cfg.get("dispositivo") or "auto")
        if modo_disp == "auto":
            # Cada equipo puede fijar su preferencia (p. ej. «cpu» en una GPU de 4 GB)
            modo_disp = os.environ.get("CLONAR_TTS_DISPOSITIVO") or "auto"
        if modo_disp == "cpu":
            return rutas, ""
        if modo_disp == "auto":
            return rutas, self._entorno.dispositivo_preferido(rutas.binario)
        return rutas, modo_disp

    def generar(self, texto: str | None, voz: str | None, dispositivo: str | None,
                ajustes: dict[str, Any], modo: str = "simple",
                segmentos: list[dict[str, Any]] | None = None, video: bool = False,
                escena: str = "dia", avatar: str | None = None, animado: bool = False,
                motor_animacion: str | None = None,
                opciones_animacion: dict[str, Any] | None = None) -> dict[str, Any]:
        cfg = self._cfg(ajustes)
        rutas, id_dispositivo = self._preparar(cfg, dispositivo)
        if video and escena not in ESCENAS:
            raise PeticionInvalida(f"Escena desconocida: {escena}. Usa: {', '.join(ESCENAS)}.")
        for seg in segmentos or []:
            if seg.get("escena") and seg["escena"] not in ESCENAS:
                raise PeticionInvalida(f"Escena desconocida: {seg['escena']}.")

        texto = (texto or "").strip()
        if segmentos:
            segmentos = [s for s in segmentos if s.get("texto", "").strip()]
            texto = texto or " ".join(s["texto"] for s in segmentos)
        if not texto:
            raise PeticionInvalida("Escribe el texto que quieres sintetizar.")

        # Todo se valida aquí, antes de lanzar la tarea, para responder 400 al momento.
        # El avatar general solo hace falta si algún segmento no trae el suyo.
        necesita_general = video and (not segmentos or any(not s.get("avatar") for s in segmentos))
        general = self._hablante(voz, avatar, necesita_general)
        hablantes = None
        if segmentos:
            # Sin voz ni avatar propios, el segmento lo dice el hablante general
            hablantes = [self._hablante(s.get("voz"), s.get("avatar") or avatar, video, voz)
                         if s.get("voz") or s.get("avatar") else general
                         for s in segmentos]

        escena_video = escena if video else None
        # Lo que se guarda con el proyecto para poder regenerar cualquier segmento
        config_global = {"voz": voz, "dispositivo": dispositivo, "ajustes": ajustes, "modo": modo,
                         "video": video, "escena": escena, "avatar": avatar,
                         "animado": bool(video and animado), "motor_animacion": motor_animacion,
                         "opciones_animacion": opciones_animacion or {}}
        registro: dict[str, Any] = {}
        # Con segmentos (revisados o hechos a mano en la web) se respetan tal cual
        if modo == "emocional" or segmentos:
            planificar = self._planificador_emocional(cfg, texto, segmentos, hablantes, general,
                                                      escena_video, registro, voz, avatar)
        else:
            def planificar(_tarea: Tarea) -> list[Pieza]:
                registro["segmentos"] = [{
                    "texto": texto, "emocion": "neutro", "intensidad": "media", "voz": voz,
                    "avatar": avatar, "escena": escena_video,
                    "ajustes": completar_ajustes(cfg, {"emocion": "neutro"})}]
                h = self._con_imagen(general, escena_video) if escena_video else general
                return piezas_simples(cfg, texto, h, escena_video)
        estimado = len(trocear_texto(texto, int(cfg["chars_por_bloque"])))

        id_tarea = self._tareas.nuevo_id()
        destino = self._salidas.nueva_ruta(id_tarea)
        destino_video = self._salidas.ruta_video_para(destino) if video else None
        # Comprueba el motor antes de lanzar: si no está, error al momento y no tras el audio
        componer = (self._animacion.componedor_meet(motor_animacion, opciones_animacion or {})
                    if video and animado else None)

        def publicar(audio, video_final, partes) -> None:
            self._salidas.publicar(destino, audio, video_final, partes, texto, config_global,
                                   registro.get("segmentos"))

        self._tareas.lanzar(id_tarea, rutas, planificar, estimado, id_dispositivo, destino,
                            destino_video, componer, publicar)
        return {"id": id_tarea, "bloques": estimado, "dispositivo": id_dispositivo or "CPU",
                "archivo": destino.name, "modo": modo,
                "video": destino_video.name if destino_video else None,
                "animado": bool(video and animado)}

    def _planificador_emocional(self, cfg: dict[str, Any], texto: str,
                                segmentos: list[dict[str, Any]] | None,
                                hablantes: list[Hablante] | None, general: Hablante,
                                escena: str | None, registro: dict[str, Any],
                                voz: str | None, avatar: str | None):
        def planificar(tarea: Tarea) -> list[Pieza]:
            if segmentos:
                resultado = {"origen": "usuario", "modelo": "", "segundos": 0.0,
                             "segmentos": segmentos}
                lista = hablantes or [general] * len(segmentos)
            else:
                tarea.emitir("analisis")
                try:
                    resultado = self._analizador.analizar(texto)
                except LLMNoDisponible as exc:
                    # Sin LLM no se pierde la síntesis: sale entera en tono neutro
                    tarea.emitir("log", linea=f"Análisis emocional no disponible ({exc}); "
                                              "se genera en tono neutro.")
                    log.warning("Tarea %s: sin análisis emocional: %s", tarea.id, exc)
                    resultado = {"origen": "neutro", "modelo": "", "segundos": 0.0,
                                 "segmentos": segmento_neutro(texto)}
                lista = [general] * len(resultado["segmentos"])
            tarea.emitir("segmentos", origen=resultado["origen"], modelo=resultado["modelo"],
                         segundos=resultado["segundos"],
                         segmentos=con_ajustes(cfg, resultado["segmentos"]))
            segs = resultado["segmentos"]
            if escena:
                # Cada segmento con su fondo (o el general); se cachean en el avatar
                segs = [{**sg, "escena": sg.get("escena") or escena} for sg in segs]
                lista = [self._con_imagen(h, sg["escena"]) for h, sg in zip(lista, segs)]
            registro["segmentos"] = [{
                "texto": sg["texto"], "emocion": sg.get("emocion"), "intensidad": sg.get("intensidad"),
                "voz": sg.get("voz") or voz, "avatar": sg.get("avatar") or avatar,
                "escena": sg.get("escena"), "ajustes": completar_ajustes(cfg, sg)} for sg in segs]
            return piezas_emocionales(cfg, segs, lista)

        return planificar

    # ---- Regenerar un segmento de un proyecto ------------------------------------
    def proyecto(self, id_proyecto: str) -> dict[str, Any]:
        return self._salidas.proyecto(id_proyecto)

    def regenerar_segmento(self, id_proyecto: str, indice: int,
                           segmento: dict[str, Any]) -> dict[str, Any]:
        """Vuelve a generar solo ese segmento (audio y, si lo hay, video) con la
        configuración nueva y lo une con los demás, que no se tocan."""
        proyecto = self._salidas.proyecto(id_proyecto)
        config = proyecto.get("config")
        if not config:
            raise PeticionInvalida("Este proyecto es anterior a guardar la configuración: "
                                   "no se pueden regenerar sus segmentos.")
        segmentos = proyecto["segmentos"]
        if not 1 <= indice <= len(segmentos):
            raise PeticionInvalida(f"El proyecto tiene {len(segmentos)} segmento(s).")
        texto = (segmento.get("texto") or "").strip()
        if not texto:
            raise PeticionInvalida("El segmento necesita texto.")

        cfg = self._cfg(config.get("ajustes") or {})
        rutas, id_dispositivo = self._preparar(cfg, config.get("dispositivo"))
        video = bool(proyecto.get("video")) and bool(config.get("video"))
        escena = segmento.get("escena") or config.get("escena") or "dia"
        if escena not in ESCENAS:
            raise PeticionInvalida(f"Escena desconocida: {escena}.")
        avatar = segmento.get("avatar") or (config.get("avatar") if video else None)
        hablante = self._hablante(segmento.get("voz"), avatar, video, config.get("voz"))
        seg = {**segmento, "texto": texto, "escena": escena if video else None}
        config_segmento = {
            "texto": texto, "emocion": seg.get("emocion") or "neutro",
            "intensidad": seg.get("intensidad") or "media", "voz": seg.get("voz"),
            "avatar": avatar, "escena": seg["escena"], "ajustes": completar_ajustes(cfg, seg)}
        # Un segmento intermedio conserva su pausa final para encajar con el siguiente
        pausa_final = config_segmento["ajustes"]["pausa_ms"] if indice < len(segmentos) else 0

        def planificar(_tarea: Tarea) -> list[Pieza]:
            h = self._con_imagen(hablante, escena) if video else hablante
            return piezas_emocionales(cfg, [seg], [h], primer_indice=indice)

        componer = (self._animacion.componedor_meet(config.get("motor_animacion"),
                                                    config.get("opciones_animacion") or {})
                    if video and config.get("animado") else None)
        destino = self._salidas.ruta(proyecto["audio"])
        destino_video = self._salidas.ruta_video_para(destino) if video else None

        def publicar(_audio, _video, partes) -> None:
            self._salidas.reemplazar_segmento(id_proyecto, partes[0], config_segmento)

        id_tarea = self._tareas.nuevo_id()
        self._tareas.lanzar(id_tarea, rutas, planificar,
                            len(trocear_texto(texto, int(cfg["chars_por_bloque"]))),
                            id_dispositivo, destino, destino_video, componer, publicar,
                            pausa_final_ms=pausa_final)
        return {"id": id_tarea, "proyecto": id_proyecto, "indice": indice,
                "video": bool(video), "animado": bool(componer)}
