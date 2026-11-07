import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { api } from "./util.js";
import Cabecera from "./componentes/Cabecera.jsx";
import Aviso from "./componentes/Aviso.jsx";
import PanelModelo from "./componentes/PanelModelo.jsx";
import PanelVoces from "./componentes/PanelVoces.jsx";
import PanelTexto from "./componentes/PanelTexto.jsx";
import Historial from "./componentes/Historial.jsx";
import Avatares from "./componentes/Avatares.jsx";

// Alto fijo de la cabecera: el resto de la pantalla se reparte desde aquí
export const ALTO_CABECERA = 72;
const ESPACIO = 16;          // relleno y separación de la rejilla
const ANCHO_ESCRITORIO = 950;
const ALTO_LINEA = 52;       // panel plegado: una sola línea (su título)

/** Alto disponible bajo la cabecera (y los avisos, si los hay), recalculado al
 *  redimensionar. En pantallas estrechas no se fija: la página fluye y desplaza. */
function useAltoDisponible(ref, dependencias) {
  const [medidas, setMedidas] = useState({ alto: 0, escritorio: true });
  useLayoutEffect(() => {
    const medir = () => {
      const arriba = ref.current?.getBoundingClientRect().top ?? ALTO_CABECERA;
      setMedidas({ alto: Math.max(360, window.innerHeight - arriba),
                   escritorio: window.innerWidth > ANCHO_ESCRITORIO });
    };
    medir();
    window.addEventListener("resize", medir);
    return () => window.removeEventListener("resize", medir);
  }, dependencias); // eslint-disable-line react-hooks/exhaustive-deps
  return medidas;
}

export default function App() {
  const [sistema, setSistema] = useState(null);
  const [errorConexion, setErrorConexion] = useState("");
  const [voces, setVoces] = useState(null);
  const [vozSeleccionada, setVozSeleccionada] = useState(null);
  const [salidas, setSalidas] = useState(null);
  const [catalogoEmociones, setCatalogoEmociones] = useState(null);
  const [avatares, setAvatares] = useState(null);
  const [motores, setMotores] = useState(null);

  const cargarEstado = useCallback(async () => {
    setSistema(await api("/api/estado"));
  }, []);

  const cargarVoces = useCallback(async () => {
    setVoces(await api("/api/voces"));
  }, []);

  const cargarAvatares = useCallback(async () => {
    setAvatares(await api("/api/avatares"));
  }, []);

  const cargarHistorial = useCallback(async () => {
    setSalidas(await api("/api/salidas"));
  }, []);

  useEffect(() => {
    (async () => {
      try {
        await cargarEstado();
      } catch (err) {
        setErrorConexion("No se pudo contactar con el servidor: " + err.message);
      }
      await cargarVoces();
      await cargarHistorial();
      setCatalogoEmociones(await api("/api/emociones"));
      await cargarAvatares();
      setMotores(await api("/api/animaciones/motores"));
    })();
  }, [cargarEstado, cargarVoces, cargarHistorial, cargarAvatares]);

  const subirVoz = async (archivo, nombre, transcripcion) => {
    const datos = new FormData();
    datos.append("audio", archivo);
    datos.append("nombre", nombre);
    datos.append("transcripcion", transcripcion || "");
    const voz = await api("/api/voces", { method: "POST", body: datos });
    setVozSeleccionada(voz.id);
    await cargarVoces();
    return voz;
  };

  const borrarVoz = async (id) => {
    await api(`/api/voces/${id}`, { method: "DELETE" });
    if (vozSeleccionada === id) setVozSeleccionada(null);
    await cargarVoces();
  };

  const faltaModelo = sistema && (!sistema.modelo_ok || !sistema.mmproj_ok);

  const principal = useRef(null);
  const { alto, escritorio } = useAltoDisponible(principal, [faltaModelo, errorConexion, sistema]);
  useEffect(() => {
    document.documentElement.style.setProperty("--alto-cabecera", `${ALTO_CABECERA}px`);
    document.body.classList.toggle("diseno-fijo", escritorio);
  }, [escritorio]);
  // Columna izquierda en acordeón: de voces, avatares e historial solo uno se
  // despliega y ocupa todo el alto disponible; los otros quedan en una línea.
  const [desplegado, setDesplegado] = useState(() => {
    try { return JSON.parse(localStorage.getItem("panel-desplegado")) ?? "voces"; } catch { return "voces"; }
  });
  const alternar = (panel) => (abrir) => {
    const nuevo = abrir ?? desplegado !== panel ? panel : null;
    setDesplegado(nuevo);
    try { localStorage.setItem("panel-desplegado", JSON.stringify(nuevo)); } catch { /* sin almacenamiento */ }
  };
  const util = alto - 2 * ESPACIO;
  const columna = util - 2 * ESPACIO;
  const altoPanel = (panel) => (desplegado === panel ? columna - 2 * ALTO_LINEA : ALTO_LINEA);
  const fijo = (px) => (escritorio ? { height: `${Math.floor(px)}px` } : undefined);

  return (
    <>
      <Cabecera sistema={sistema} />
      <Aviso sistema={sistema} errorConexion={errorConexion} />
      {faltaModelo && <PanelModelo alTerminar={cargarEstado} />}

      <main className="rejilla" ref={principal}
            style={escritorio ? { height: `${alto}px`, padding: `${ESPACIO}px`, gap: `${ESPACIO}px` } : undefined}>
        <div className="columna" style={escritorio ? { gap: `${ESPACIO}px` } : undefined}>
          <PanelVoces
            estilo={fijo(altoPanel("voces"))}
            abierto={desplegado === "voces"}
            alAlternar={alternar("voces")}
            voces={voces}
            seleccionada={vozSeleccionada}
            alSeleccionar={(id) => setVozSeleccionada((actual) => (actual === id ? null : id))}
            alBorrar={borrarVoz}
            alSubir={subirVoz}
          />
          <Avatares avatares={avatares} voces={voces} alCambiar={cargarAvatares}
                    estilo={fijo(altoPanel("avatares"))}
                    abierto={desplegado === "avatares"} alAlternar={alternar("avatares")} />
          <Historial salidas={salidas} alCambiar={cargarHistorial} catalogo={catalogoEmociones}
                     voces={voces} avatares={avatares} estilo={fijo(altoPanel("historial"))}
                     abierto={desplegado === "historial"} alAlternar={alternar("historial")} />
        </div>
        <PanelTexto
          sistema={sistema}
          catalogoEmociones={catalogoEmociones}
          voces={voces}
          avatares={avatares}
          motores={motores}
          vozSeleccionada={vozSeleccionada}
          alGenerar={cargarHistorial}
          estilo={fijo(util)}
        />
      </main>
    </>
  );
}
