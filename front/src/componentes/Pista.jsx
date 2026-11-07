import Tippy from "@tippyjs/react";
import "tippy.js/dist/tippy.css";

/**
 * Explicación que aparece al pasar el ratón, al instante (sin retardo), en
 * lugar de textos fijos bajo cada control. Tema oscuro: .tippy-box[data-theme~="clonar"].
 * Envuelve al hijo en un <span> para que funcione también con controles deshabilitados.
 */
export default function Pista({ texto, children, lado = "top", bloque = false }) {
  if (!texto) return children;
  return (
    <Tippy content={texto} delay={[0, 0]} duration={[120, 80]} placement={lado} theme="clonar"
           maxWidth={340} arrow>
      <span className={bloque ? "pista pista-bloque" : "pista"}>{children}</span>
    </Tippy>
  );
}
