import { useState } from "react";
import { api } from "../util.js";
import DetalleGeneracion, { Modal } from "./DetalleGeneracion.jsx";
import EditarSegmento from "./EditarSegmento.jsx";
import { confirmar } from "../avisos.js";

export default function Historial({ salidas, alCambiar, catalogo, voces, avatares, estilo,
                                   abierto = true, alAlternar }) {
  const [abiertaId, setAbiertaId] = useState(null);
  const [editando, setEditando] = useState(null);   // índice de la parte en edición
  // Siempre la versión más reciente de la lista (cambia al regenerar un segmento)
  const abierta = (salidas ?? []).find((s) => s.archivo === abiertaId) ?? null;
  const setAbierta = (s) => setAbiertaId(s?.archivo ?? null);

  const borrar = async (s) => {
    if (!await confirmar("¿Eliminar este proyecto?",
      `«${s.titulo}» se borrará con su video, todas sus partes y su configuración.`,
      { aceptar: "Eliminar", peligro: true })) return;
    await api(`/api/salidas/${s.archivo}`, { method: "DELETE" });
    setAbierta(null);
    alCambiar();
  };

  let contenido = null;
  if (salidas && !salidas.length) {
    contenido = <div className="vacio">Todavía no has generado nada.</div>;
  } else if (salidas) {
    contenido = salidas.map((s) => (
      <button key={s.archivo} className="tarjeta-generacion" onClick={() => setAbierta(s)}
              title="Ver el resultado y sus partes">
        <span className="icono-tipo">{s.video ? "🎬" : "🎙"}</span>
        <span className="tarjeta-generacion-datos">
          <span className="tarjeta-generacion-titulo">{s.titulo}</span>
          <span className="tarjeta-generacion-meta">
            {s.fecha.replace("T", " ")} · {s.duracion} s
            {s.segmentos?.length > 1 ? ` · ${s.segmentos.length} segmentos` : ""}
          </span>
        </span>
      </button>
    ));
  }

  return (
    <section className={`tarjeta tarjeta-columna plegable ${abierto ? "" : "plegada"}`} style={estilo}>
      <div className="tarjeta-cabecera">
        <h2 className="titulo-con-info">
          <button className="plegar" onClick={() => alAlternar?.()} aria-expanded={abierto}
                  title={abierto ? "Plegar" : "Desplegar"}>{abierto ? "▾" : "▸"}</button>
          <span className="titulo-plegable" onClick={() => alAlternar?.()}>Historial</span>
        </h2>
        {salidas?.length > 0 && <span className="contador">{salidas.length}</span>}
      </div>
      <div className={`historial tarjeta-desplazable ${abierto ? "" : "oculto"}`}>{contenido}</div>
      {abierta && (
        <Modal titulo={abierta.titulo} alCerrar={() => setAbierta(null)}
               pie={<>
                 <span className="modal-pie-info">{abierta.fecha.replace("T", " ")}</span>
                 <button className="btn btn-peligro" onClick={() => borrar(abierta)}>Eliminar</button>
               </>}>
          <DetalleGeneracion salida={abierta} alEditar={setEditando} />
        </Modal>
      )}
      {abierta && editando && (
        <EditarSegmento salida={abierta} indice={editando} catalogo={catalogo} voces={voces}
                        avatares={avatares} alCerrar={() => setEditando(null)}
                        alRegenerado={async () => { await alCambiar(); setEditando(null); }} />
      )}
    </section>
  );
}
