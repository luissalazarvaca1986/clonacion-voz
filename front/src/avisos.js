// Diálogos con SweetAlert2 en lugar de alert/confirm del navegador, con el
// mismo tema oscuro que el resto de la interfaz (ver .swal-clonar en estilos.css).
import Swal from "sweetalert2";

const tema = Swal.mixin({
  customClass: {
    popup: "swal-clonar",
    confirmButton: "btn btn-primario",
    cancelButton: "btn btn-sec",
    denyButton: "btn btn-peligro",
  },
  buttonsStyling: false,
  reverseButtons: true,
});

/** Aviso informativo o de error (equivale a alert). */
export function avisar(titulo, texto = "", icono = "warning") {
  return tema.fire({ icon: icono, title: titulo, text: texto, confirmButtonText: "Entendido" });
}

export function avisarError(titulo, error) {
  return avisar(titulo, error?.message ?? String(error ?? ""), "error");
}

/** Confirmación (equivale a confirm). Devuelve true si se acepta. */
export async function confirmar(titulo, texto = "", { aceptar = "Aceptar", peligro = false } = {}) {
  const r = await tema.fire({
    icon: peligro ? "warning" : "question",
    title: titulo,
    text: texto,
    showCancelButton: true,
    confirmButtonText: aceptar,
    cancelButtonText: "Cancelar",
    focusCancel: peligro,
    customClass: {
      popup: "swal-clonar",
      confirmButton: peligro ? "btn btn-peligro" : "btn btn-primario",
      cancelButton: "btn btn-sec",
    },
  });
  return r.isConfirmed;
}

/** Notificación breve que se cierra sola. */
export function avisoBreve(titulo, icono = "success") {
  return tema.fire({
    toast: true, position: "top-end", icon: icono, title: titulo,
    showConfirmButton: false, timer: 2200, timerProgressBar: true,
  });
}
