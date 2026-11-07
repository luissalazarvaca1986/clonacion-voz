import { useEffect, useRef, useState } from "react";
import { api, postJson, reloj, tamano } from "../util.js";

export default function PanelModelo({ alTerminar }) {
  const [catalogo, setCatalogo] = useState(null);
  const [quantModelo, setQuantModelo] = useState("");
  const [quantMmproj, setQuantMmproj] = useState("");
  const [activa, setActiva] = useState(false);
  const [progreso, setProgreso] = useState(null); // { pct, texto }
  const fuente = useRef(null);

  useEffect(() => {
    api("/api/modelo/catalogo").then((c) => {
      setCatalogo(c);
      setQuantModelo(c.por_defecto.modelo);
      setQuantMmproj(c.por_defecto.mmproj);
    });
    return () => fuente.current?.close();
  }, []);

  const descargar = async () => {
    setActiva(true);
    setProgreso({ pct: 0, texto: "Conectando con Hugging Face…" });

    try {
      await postJson("/api/modelo/descargar", { modelo: quantModelo, mmproj: quantMmproj });
    } catch (err) {
      setActiva(false);
      setProgreso((p) => ({ ...p, texto: "✕ " + err.message }));
      return;
    }

    const f = new EventSource("/api/modelo/progreso");
    fuente.current = f;
    f.onmessage = async (ev) => {
      const d = JSON.parse(ev.data);

      if (d.total) {
        const pct = (d.hecho / d.total) * 100;
        const restante = d.velocidad > 0 ? (d.total - d.hecho) / d.velocidad : 0;
        setProgreso({
          pct,
          texto: `${d.archivo} · ${pct.toFixed(1)}% · ${tamano(d.hecho)} de ${tamano(d.total)}` +
            (d.velocidad ? ` · ${tamano(d.velocidad)}/s · faltan ${reloj(restante)}` : ""),
        });
      }

      if (!d.activa) {
        f.close();
        setActiva(false);
        if (d.error) {
          setProgreso((p) => ({ ...p, texto: "✕ " + d.error }));
        } else if (d.terminada) {
          setProgreso({ pct: 100, texto: "✓ Modelos descargados y verificados." });
          await alTerminar();
        }
      }
    };
    f.onerror = () => { f.close(); setActiva(false); };
  };

  const opciones = (grupo) => catalogo && Object.entries(catalogo.catalogo[grupo])
    .map(([clave, i]) => <option key={clave} value={clave}>{i.etiqueta}</option>);

  return (
    <section className="tarjeta panel-modelo">
      <h2>Primer arranque · descargar el modelo</h2>
      <p className="ayuda">
        La aplicación no incluye los pesos del modelo. Hacen falta <b>dos archivos</b>{" "}
        del repositorio oficial en Hugging Face: el modelo principal y su{" "}
        <i>mmproj</i> (el vocoder que convierte los tokens en sonido).
        Se descargan una sola vez y quedan en la carpeta <code>modelo/</code>.
      </p>

      <div className="campos">
        <label>
          <span>Modelo principal</span>
          <select value={quantModelo} disabled={activa}
                  onChange={(e) => setQuantModelo(e.target.value)}>
            {opciones("modelo")}
          </select>
          <small>Q4_K_M va de sobra para la mayoría de equipos</small>
        </label>
        <label>
          <span>Proyector / vocoder (mmproj)</span>
          <select value={quantMmproj} disabled={activa}
                  onChange={(e) => setQuantMmproj(e.target.value)}>
            {opciones("mmproj")}
          </select>
          <small>Necesario: sin él no hay audio</small>
        </label>
      </div>

      <div className="acciones">
        <button className="btn btn-primario" disabled={activa} onClick={descargar}>
          {activa ? "Descargando…" : "Descargar ahora"}
        </button>
        {activa && (
          <button className="btn btn-peligro"
                  onClick={() => api("/api/modelo/cancelar", { method: "POST" })}>
            Cancelar
          </button>
        )}
        <a className="btn btn-sec" href={catalogo?.url} target="_blank" rel="noopener">
          Ver en Hugging Face
        </a>
      </div>

      {progreso && (
        <div className="progreso">
          <div className="barra"><div style={{ width: progreso.pct.toFixed(1) + "%" }}></div></div>
          <span>{progreso.texto}</span>
        </div>
      )}

      <p className="ayuda pie-modelo">
        ¿Prefieres la terminal? <code>python descargar_modelo.py</code> hace lo mismo,
        y también reanuda una descarga interrumpida.
      </p>
    </section>
  );
}
