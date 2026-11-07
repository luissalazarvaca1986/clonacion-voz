export async function api(url, opciones) {
  const r = await fetch(url, opciones);
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try { msg = (await r.json()).detail || msg; } catch { /* respuesta sin JSON */ }
    throw new Error(msg);
  }
  return r.status === 204 ? null : r.json();
}

export const postJson = (url, datos) => api(url, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(datos),
});

export const recortar = (t, n) => (t.length > n ? t.slice(0, n) + "…" : t);

export function tamano(n) {
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${i === 0 ? Math.round(n) : n.toFixed(1)} ${u[i]}`;
}

export function reloj(segundos) {
  if (!isFinite(segundos) || segundos <= 0) return "—";
  const m = Math.floor(segundos / 60), s = Math.round(segundos % 60);
  return m ? `${m} min ${s.toString().padStart(2, "0")} s` : `${s} s`;
}
