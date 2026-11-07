import { useEffect, useRef, useState } from "react";
import { api, postJson } from "../util.js";
import { Modal } from "./DetalleGeneracion.jsx";
import { Segmento } from "./Segmentos.jsx";

// Edita un segmento de un proyecto del historial y lo regenera: solo ese
// segmento se vuelve a generar y se une con los demás, que no cambian.
export default function EditarSegmento({ salida, indice, catalogo, voces, avatares, alCerrar, alRegenerado }) {
  const idProyecto = salida.archivo.replace(/\.wav$/, "");
  const [proyecto, setProyecto] = useState(null);
  const [seg, setSeg] = useState(null);
  const [error, setError] = useState("");
  const [generando, setGenerando] = useState(false);
  const [progreso, setProgreso] = useState("");
  const fuente = useRef(null);
  const tarea = useRef(null);

  useEffect(() => {
    api(`/api/proyectos/${idProyecto}`).then((p) => {
      setProyecto(p);
      const s = p.segmentos.find((x) => x.indice === indice);
      setSeg({ ...s, sugeridos: s.ajustes });
    }).catch((e) => setError(e.message));
    return () => fuente.current?.close();
  }, [idProyecto, indice]);

  const config = proyecto?.config ?? {};
  const base = config.ajustes ?? {};

  const sugerir = async (_i, emocion, intensidad) => {
    const sugeridos = await postJson("/api/emociones/sugerencia", {
      emocion, intensidad, temp: base.temp, top_p: base.top_p, top_k: base.top_k, pausa_ms: base.pausa_ms,
    });
    setSeg((s) => ({ ...s, emocion, intensidad, ajustes: sugeridos, sugeridos }));
  };

  const regenerar = async () => {
    setGenerando(true);
    setError("");
    setProgreso("Preparando…");
    let t;
    try {
      t = await postJson(`/api/proyectos/${idProyecto}/segmentos/${indice}/regenerar`, {
        texto: seg.texto, emocion: seg.emocion, intensidad: seg.intensidad, voz: seg.voz,
        avatar: seg.avatar, escena: seg.escena, ajustes: seg.ajustes,
      });
    } catch (e) {
      setGenerando(false);
      setError(e.message);
      return;
    }
    tarea.current = t.id;
    const f = new EventSource(`/api/tarea/${t.id}/eventos`);
    fuente.current = f;
    f.onmessage = (ev) => {
      const d = JSON.parse(ev.data);
      if (d.tipo === "bloque") setProgreso(`🎙 Generando el audio (bloque ${d.indice} de ${d.total})…`);
      else if (d.tipo === "segmento" && d.etapa === "video" && d.estado === "en_curso") setProgreso("🎬 Generando el video…");
      else if (d.tipo === "progreso") setProgreso(`🎭 ${d.fase}: fotograma ${d.hecho} de ${d.total}`);
      else if (d.tipo === "fin") {
        f.close();
        setGenerando(false);
        alRegenerado();
      } else if (d.tipo === "error" || d.tipo === "cancelada") {
        f.close();
        setGenerando(false);
        setError(d.tipo === "error" ? d.mensaje : "Cancelado.");
        setProgreso("");
      }
    };
    f.onerror = () => { f.close(); setGenerando(false); };
  };

  const cancelar = async () => {
    if (generando && tarea.current) await api(`/api/tarea/${tarea.current}/cancelar`, { method: "POST" });
    else alCerrar();
  };

  const ctx = {
    catalogo, voces, avatares, editable: !generando, emocional: true, textoEditable: true,
    conVideo: Boolean(config.video), animado: Boolean(config.animado), escenaGeneral: config.escena ?? "dia",
    alCambiarCampo: (_i, campo, valor) => setSeg((s) => ({ ...s, [campo]: valor })),
    alCambiarAjuste: (_i, clave, valor) => setSeg((s) => ({ ...s, ajustes: { ...s.ajustes, [clave]: valor } })),
    alSugerir: sugerir,
  };

  return (
    <Modal titulo={`Editar parte ${indice} · ${salida.titulo}`} alCerrar={cancelar}
           pie={<>
             <span className="modal-pie-info">
               {generando ? progreso : "Solo se vuelve a generar esta parte; las demás se conservan."}
             </span>
             <button className="btn btn-primario" disabled={!seg || generando || !seg.texto.trim()}
                     onClick={regenerar}>
               {generando ? "Regenerando…" : "↻ Regenerar segmento"}
             </button>
           </>}>
      {error && <p className="error-analisis">✕ {error}</p>}
      {!seg && !error && <div className="vacio">Cargando la configuración…</div>}
      {seg && (
        <div className="segmentos editar-segmento">
          <Segmento s={seg} i={indice - 1} abierto alAbrir={() => {}} ctx={ctx} />
        </div>
      )}
    </Modal>
  );
}
