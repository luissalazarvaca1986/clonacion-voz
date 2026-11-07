import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../util.js";
import { avisarError, avisoBreve, confirmar } from "../avisos.js";
import { ESCENAS } from "../constantes.js";
import { Modal } from "./DetalleGeneracion.jsx";
import PosicionAvatar, { POSICION_DEFECTO } from "./PosicionAvatar.jsx";

export { ESCENAS };

const IMAGENES = [
  { id: "avatar", etiqueta: "Avatar", ayuda: "PNG con fondo transparente" },
  ...ESCENAS.map((e) => ({ id: e.id, etiqueta: `${e.emoji} ${e.etiqueta}`, ayuda: "Fondo" })),
];

const patch = (id, datos) => api(`/api/avatares/${id}`, {
  method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(datos),
});

function ZonaImagen({ tipo, archivo, alElegir, actual, ocupado }) {
  const [encima, setEncima] = useState(false);
  const [vista, setVista] = useState(null);
  const entrada = useRef(null);

  useEffect(() => {
    if (!archivo) { setVista(null); if (entrada.current) entrada.current.value = ""; return undefined; }
    const url = URL.createObjectURL(archivo);
    setVista(url);
    return () => URL.revokeObjectURL(url);
  }, [archivo]);

  const arrastre = (e) => {
    e.preventDefault();
    setEncima(e.type === "dragover");
    if (e.type === "drop" && e.dataTransfer.files[0]) alElegir(e.dataTransfer.files[0]);
  };

  const imagen = vista ?? actual;
  return (
    <label className={`zona-imagen ${encima ? "encima" : ""} ${imagen ? "con-imagen" : ""} ${tipo.id === "avatar" ? "es-avatar" : ""} ${ocupado ? "ocupado" : ""}`}
           onDragOver={arrastre} onDragLeave={arrastre} onDrop={arrastre}>
      <input ref={entrada} type="file" accept="image/*" hidden
             onChange={(e) => e.target.files[0] && alElegir(e.target.files[0])} />
      {imagen ? <img src={imagen} alt={tipo.etiqueta} /> : <span className="zona-imagen-mas">+</span>}
      <span className="zona-imagen-etiqueta">{ocupado ? "Subiendo…" : tipo.etiqueta}</span>
      {!imagen && <small>{tipo.ayuda}</small>}
      {actual && !ocupado && <span className="zona-imagen-cambiar">Cambiar</span>}
    </label>
  );
}

// URLs locales de los archivos elegidos, para la vista previa de la posición
function useUrlsLocales(archivos) {
  const urls = useMemo(() => Object.fromEntries(
    Object.entries(archivos).filter(([, f]) => f).map(([k, f]) => [k, URL.createObjectURL(f)])), [archivos]);
  useEffect(() => () => Object.values(urls).forEach((u) => URL.revokeObjectURL(u)), [urls]);
  return urls;
}

function NuevoAvatar({ voces, alCrear, alCerrar }) {
  const [nombre, setNombre] = useState("");
  const [voz, setVoz] = useState("");
  const [archivos, setArchivos] = useState({});
  const [posicion, setPosicion] = useState({ ...POSICION_DEFECTO });
  const [creando, setCreando] = useState(false);
  const urls = useUrlsLocales(archivos);

  const faltan = IMAGENES.filter((t) => !archivos[t.id]);
  const listo = nombre.trim() && !faltan.length && !creando;

  const elegir = (tipo) => (archivo) => {
    setArchivos((a) => ({ ...a, [tipo]: archivo }));
    // «luis-avatar.png» sugiere el nombre «Luis»
    if (tipo === "avatar" && !nombre) {
      const base = archivo.name.replace(/\.[^.]+$/, "").replace(/[-_]?avatar[-_]?/i, "");
      if (base) setNombre(base.charAt(0).toUpperCase() + base.slice(1));
    }
  };

  const crear = async () => {
    setCreando(true);
    try {
      const datos = new FormData();
      datos.append("nombre", nombre.trim());
      datos.append("voz", voz);
      datos.append("ancho", posicion.ancho);
      datos.append("izquierda", posicion.izquierda);
      for (const t of IMAGENES) datos.append(t.id, archivos[t.id]);
      await alCrear(datos);
      avisoBreve(`Avatar «${nombre.trim()}» creado`);
      alCerrar();
    } catch (err) {
      avisarError("No se pudo crear el avatar", err);
    } finally {
      setCreando(false);
    }
  };

  return (
    <Modal titulo="Nuevo avatar" alCerrar={alCerrar}
           pie={<button className="btn btn-primario" disabled={!listo} onClick={crear}>
             {creando ? "Procesando imágenes…"
               : faltan.length ? `Faltan: ${faltan.map((t) => t.etiqueta.replace(/^\S+ /, "")).join(", ")}`
                 : "Crear avatar"}
           </button>}>
      <div className="form-avatar">
        <div>
          <p className="ayuda">
            Un avatar necesita su imagen (mejor PNG con fondo transparente) y <b>los tres fondos</b>:
            día, noche y lluvia. Arrastra cada archivo a su casilla.
          </p>
          <div className="rejilla-imagenes">
            {IMAGENES.map((t) => (
              <ZonaImagen key={t.id} tipo={t} archivo={archivos[t.id]} alElegir={elegir(t.id)} />
            ))}
          </div>
          <input type="text" placeholder="Nombre del avatar" maxLength={60}
                 value={nombre} onChange={(e) => setNombre(e.target.value)} />
          <select value={voz} onChange={(e) => setVoz(e.target.value)}>
            <option value="">Sin voz asociada</option>
            {(voces ?? []).map((v) => <option key={v.id} value={v.id}>🎙 Habla con la voz «{v.nombre}»</option>)}
          </select>
        </div>
        <PosicionAvatar avatarUrl={urls.avatar} fondos={urls} posicion={posicion} alCambiar={setPosicion}
                        deshabilitado={creando} />
      </div>
    </Modal>
  );
}

function EditarAvatar({ avatar, alCambiar, alCerrar }) {
  const [nombre, setNombre] = useState(avatar.nombre);
  const [subiendo, setSubiendo] = useState(null);
  const [version, setVersion] = useState(Date.now());   // rompe la caché del navegador
  const [posicion, setPosicion] = useState(avatar.posicion ?? POSICION_DEFECTO);
  const [guardando, setGuardando] = useState(false);
  const cambiada = posicion.ancho !== avatar.posicion?.ancho || posicion.izquierda !== avatar.posicion?.izquierda;

  const url = (tipo) => `/api/avatares/${avatar.id}/imagenes/${tipo}?v=${version}`;
  const fondos = Object.fromEntries(ESCENAS.map((e) => [e.id, url(e.id)]));

  const reemplazar = (tipo) => async (archivo) => {
    setSubiendo(tipo);
    try {
      const datos = new FormData();
      datos.append("archivo", archivo);
      await api(`/api/avatares/${avatar.id}/imagenes/${tipo}`, { method: "PUT", body: datos });
      setVersion(Date.now());
      await alCambiar();
    } catch (err) {
      avisarError(`No se pudo cambiar «${tipo}»`, err);
    } finally {
      setSubiendo(null);
    }
  };

  const renombrar = async () => {
    if (!nombre.trim() || nombre.trim() === avatar.nombre) return;
    await patch(avatar.id, { nombre: nombre.trim() });
    await alCambiar();
  };

  const guardarPosicion = async () => {
    setGuardando(true);
    try {
      await patch(avatar.id, posicion);
      await alCambiar();
      avisoBreve("Posición guardada");
    } catch (err) {
      avisarError("No se pudo guardar la posición", err);
    } finally {
      setGuardando(false);
    }
  };

  const cerrar = async () => {
    if (cambiada && !await confirmar("¿Descartar la posición nueva?",
      "Has movido el avatar sin guardar la posición.", { aceptar: "Descartar", peligro: true })) return;
    alCerrar();
  };

  return (
    <Modal titulo={`Editar avatar · ${avatar.nombre}`} alCerrar={cerrar}
           pie={<button className="btn btn-primario" disabled={!cambiada || guardando} onClick={guardarPosicion}>
             {guardando ? "Guardando…" : cambiada ? "Guardar posición" : "Posición guardada"}
           </button>}>
      <div className="form-avatar">
        <div>
          <p className="ayuda">Elige o arrastra una imagen nueva sobre la que quieras cambiar: se aplica al momento.</p>
          <div className="rejilla-imagenes">
            {IMAGENES.map((t) => (
              <ZonaImagen key={t.id} tipo={t} alElegir={reemplazar(t.id)} ocupado={subiendo === t.id}
                          actual={url(t.id)} />
            ))}
          </div>
          <label className="campo-nombre">
            <span className="etiqueta-campo">Nombre</span>
            <input type="text" maxLength={60} value={nombre} onChange={(e) => setNombre(e.target.value)}
                   onBlur={renombrar} onKeyDown={(e) => e.key === "Enter" && renombrar()} />
          </label>
        </div>
        <PosicionAvatar avatarUrl={url("avatar")} fondos={fondos} posicion={posicion} alCambiar={setPosicion}
                        deshabilitado={guardando} />
      </div>
    </Modal>
  );
}

function Biblioteca({ avatares, voces, alCambiar, version, alEditar }) {
  if (!avatares) return <div className="lista-avatares"></div>;
  if (!avatares.length) {
    return <div className="vacio">Aún no hay avatares.<br />Crea uno con «➕ Nuevo avatar».</div>;
  }

  const borrar = async (a) => {
    if (!await confirmar(`¿Eliminar el avatar «${a.nombre}»?`, "Se borrarán su imagen y sus tres fondos.",
                         { aceptar: "Eliminar", peligro: true })) return;
    await api(`/api/avatares/${a.id}`, { method: "DELETE" });
    alCambiar();
  };

  return (
    <div className="lista-avatares">
      {avatares.map((a) => (
        <div className="tarjeta-avatar" key={a.id}>
          <img className="miniatura-avatar" alt={a.nombre} loading="lazy"
               src={`/api/avatares/${a.id}/vista/dia?v=${version}-${a.posicion?.ancho}-${a.posicion?.izquierda}`} />
          <div className="tarjeta-avatar-datos">
            <div className="voz-nombre">{a.nombre}</div>
            <div className="voz-meta">
              {ESCENAS.map((e) => (
                <span key={e.id} title={e.etiqueta}
                      className={a.escenas.includes(e.id) ? "" : "falta"}>{e.emoji}</span>
              ))}
              {!a.transparente && <span title="La imagen no tiene fondo transparente"> ⚠</span>}
            </div>
            <select value={a.voz ?? ""}
                    onChange={async (e) => { await patch(a.id, { voz: e.target.value }); alCambiar(); }}>
              <option value="">Sin voz asociada</option>
              {(voces ?? []).map((v) => <option key={v.id} value={v.id}>🎙 {v.nombre}</option>)}
            </select>
          </div>
          <div className="acciones-avatar">
            <button className="icono-btn" title="Editar imagen, fondos, nombre y posición"
                    onClick={() => alEditar(a.id)}>✎</button>
            <a className="icono-btn" href={`/api/avatares/${a.id}/zip`} download title="Descargar en ZIP">⭳</a>
            <button className="icono-btn borrar" title="Eliminar" onClick={() => borrar(a)}>✕</button>
          </div>
        </div>
      ))}
    </div>
  );
}

export default function Avatares({ avatares, voces, alCambiar, estilo, abierto = true, alAlternar }) {
  const [nuevo, setNuevo] = useState(false);
  const [editando, setEditando] = useState(null);
  const [version, setVersion] = useState(0);   // al editar, las miniaturas se recargan
  const refrescar = async () => { await alCambiar(); setVersion((v) => v + 1); };
  const avatarEditado = (avatares ?? []).find((a) => a.id === editando);

  const crear = async (datos) => {
    await api("/api/avatares", { method: "POST", body: datos });
    await alCambiar();
  };

  return (
    <section className={`tarjeta tarjeta-columna plegable ${abierto ? "" : "plegada"}`} style={estilo}>
      <div className="tarjeta-cabecera">
        <h2 className="titulo-con-info">
          <button className="plegar" onClick={() => alAlternar?.()} aria-expanded={abierto}
                  title={abierto ? "Plegar" : "Desplegar"}>{abierto ? "▾" : "▸"}</button>
          <span className="titulo-plegable" onClick={() => alAlternar?.()}>Avatares</span>
        </h2>
        <button className="btn btn-sec btn-pequeno" onClick={() => setNuevo(true)}>➕ Nuevo avatar</button>
      </div>
      <div className={`tarjeta-desplazable ${abierto ? "" : "oculto"}`}>
        <Biblioteca avatares={avatares} voces={voces} alCambiar={alCambiar} version={version}
                    alEditar={setEditando} />
      </div>
      {nuevo && <NuevoAvatar voces={voces} alCrear={crear} alCerrar={() => setNuevo(false)} />}
      {avatarEditado && <EditarAvatar avatar={avatarEditado} alCambiar={refrescar}
                                      alCerrar={() => setEditando(null)} />}
    </section>
  );
}
