import { useRef, useState } from "react";
import { ESCENAS } from "../constantes.js";

export const POSICION_DEFECTO = { ancho: 0.65, izquierda: 0.25 };
const ANCHO_MIN = 0.1;

const limitar = (v, min, max) => Math.min(max, Math.max(min, v));
const redondear = (v) => Math.round(v * 1000) / 1000;

/** Normaliza para que el avatar quepa entero a lo ancho. */
export function ajustarPosicion({ ancho, izquierda }) {
  const a = limitar(ancho, ANCHO_MIN, 1);
  return { ancho: redondear(a), izquierda: redondear(limitar(izquierda, 0, 1 - a)) };
}

/**
 * Coloca el avatar sobre su fondo tal y como lo compondrá ffmpeg: fotograma
 * 16:9, fondo recortado al centro, avatar siempre alineado abajo y con el alto
 * proporcional a su ancho. Solo se mueve en horizontal y se escala.
 */
export default function PosicionAvatar({ avatarUrl, fondos, posicion, alCambiar, deshabilitado }) {
  const disponibles = ESCENAS.filter((e) => fondos[e.id]);
  const [escena, setEscena] = useState(disponibles[0]?.id ?? "dia");
  const lienzo = useRef(null);
  const arrastre = useRef(null);
  const { ancho, izquierda } = posicion;
  const fondo = fondos[escena] ?? fondos[disponibles[0]?.id];

  const empezar = (modo) => (e) => {
    if (deshabilitado) return;
    e.preventDefault();
    e.stopPropagation();
    e.currentTarget.setPointerCapture?.(e.pointerId);
    arrastre.current = { modo, x0: e.clientX, ancho, izquierda,
                         total: lienzo.current.getBoundingClientRect().width };
  };
  const mover = (e) => {
    const a = arrastre.current;
    if (!a) return;
    const dx = (e.clientX - a.x0) / a.total;
    if (a.modo === "mover") {
      alCambiar(ajustarPosicion({ ancho: a.ancho, izquierda: a.izquierda + dx }));
    } else {
      // El tirador de la esquina cambia el ancho; el borde izquierdo queda fijo
      alCambiar(ajustarPosicion({ ancho: Math.min(a.ancho + dx, 1 - a.izquierda), izquierda: a.izquierda }));
    }
  };
  const soltar = () => { arrastre.current = null; };

  const porcentaje = (v) => `${Math.round(v * 100)} %`;

  return (
    <div className="posicion-avatar">
      <div className="posicion-cabecera">
        <span className="etiqueta-campo">Posición sobre el fondo</span>
        {disponibles.length > 1 && (
          <div className="selector-escena">
            {disponibles.map((e) => (
              <button key={e.id} className={escena === e.id ? "activa" : ""} onClick={() => setEscena(e.id)}
                      title={`Ver sobre el fondo de ${e.etiqueta.toLowerCase()}`}>{e.emoji}</button>
            ))}
          </div>
        )}
      </div>

      <div className={`lienzo-posicion ${deshabilitado ? "deshabilitado" : ""}`} ref={lienzo}
           onPointerMove={mover} onPointerUp={soltar} onPointerCancel={soltar}>
        {fondo ? <img className="lienzo-fondo" src={fondo} alt="" draggable={false} />
          : <div className="lienzo-sin-fondo">Elige un fondo para ver la posición</div>}
        {avatarUrl && (
          <div className="lienzo-avatar" style={{ left: `${izquierda * 100}%`, width: `${ancho * 100}%` }}
               onPointerDown={empezar("mover")} title="Arrastra para moverlo a izquierda o derecha">
            <img src={avatarUrl} alt="Avatar" draggable={false} />
            <span className="tirador" onPointerDown={empezar("escalar")} title="Arrastra para cambiar el ancho" />
          </div>
        )}
        <span className="lienzo-guia">alineado abajo · alto proporcional</span>
      </div>

      <div className="campos posicion-controles">
        <label>
          <span>Ancho <b>{porcentaje(ancho)}</b></span>
          <input type="range" min={ANCHO_MIN} max={1} step={0.005} value={ancho} disabled={deshabilitado}
                 onChange={(e) => alCambiar(ajustarPosicion({ ancho: Number(e.target.value),
                                                               izquierda: Math.min(izquierda, 1 - Number(e.target.value)) }))} />
        </label>
        <label>
          <span>Separación izquierda <b>{porcentaje(izquierda)}</b></span>
          <input type="range" min={0} max={redondear(1 - ancho)} step={0.005} value={izquierda}
                 disabled={deshabilitado || ancho >= 1}
                 onChange={(e) => alCambiar(ajustarPosicion({ ancho, izquierda: Number(e.target.value) }))} />
        </label>
      </div>
      <div className="posicion-acciones">
        <button className="btn-enlace" disabled={deshabilitado}
                onClick={() => alCambiar(ajustarPosicion({ ancho, izquierda: (1 - ancho) / 2 }))}>⇔ Centrar</button>
        <button className="btn-enlace" disabled={deshabilitado}
                onClick={() => alCambiar({ ...POSICION_DEFECTO })}>↺ Restablecer (65 % · 25 %)</button>
      </div>
    </div>
  );
}
