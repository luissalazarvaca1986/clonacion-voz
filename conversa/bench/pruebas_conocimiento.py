# -*- coding: utf-8 -*-
"""Set de pruebas del conocimiento de la sala (conversa): cada pregunta va por /api/turno
y se revisa que la respuesta hable del producto correcto, con sus conceptos clave, y que
no confunda productos ni arrastre el tema anterior de la sesión.

    python3 conversa/bench/pruebas_conocimiento.py [etiqueta]   # contra http://localhost:9090
"""
import json, re, sys, time, unicodedata, urllib.parse, urllib.request

CONVERSA = "http://localhost:9090"

def norm(t):
    t = unicodedata.normalize("NFD", t.lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")

# producto: (alias con que se reconoce, conceptos de los que al menos uno debe aparecer)
PRODUCTOS = {
    "NinjaOne": (["ninjaone", "ninja one"], ["endpoint", "parche", "rmm", "monitoreo remoto", "gestion unificada"]),
    "Prompt Security": (["prompt security"], ["inteligencia artificial", " ia", "prompt", "genai", "generativa"]),
    "SentinelOne": (["sentinelone", "sentinel one"], ["endpoint", "edr", "xdr", "ciberseguridad", "amenaza"]),
    "Dynatrace": (["dynatrace"], ["observabilidad", "monitoreo", "aplicacion"]),
    "Okta": (["okta"], ["identidad", "acceso", "autenticacion"]),
    "Armis": (["armis"], ["activo", "visibilidad", "dispositivo", "iot"]),
    "Arexdata": (["arexdata"], ["dato", "dspm", "clasific"]),
    "ServiceNow": (["servicenow"], ["servicio", "flujo", "itsm", "plataforma", "activo"]),
    "Vali Cyber": (["vali cyber", "valicyber", "zerolock"], ["hipervisor", "ransomware", "linux", "servidor"]),
    "Claude": (["claude"], ["anthropic", "inteligencia artificial", "modelo"]),
    "Gemini": (["gemini"], ["google", "inteligencia artificial", "modelo"]),
}
SIMPLES = [(f"¿Qué es {p}?", [p]) for p in PRODUCTOS]
DOBLES = [("¿Qué es NinjaOne y qué es Prompt Security?", ["NinjaOne", "Prompt Security"]),
          ("¿En qué se diferencian SentinelOne y NinjaOne?", ["SentinelOne", "NinjaOne"])]
# Misma sesión, varias preguntas seguidas (prueba del arrastre del historial)
SECUENCIA = ["SentinelOne", "NinjaOne", "Prompt Security", "Okta", "Dynatrace"]


def turno(pregunta, sesion):
    r = urllib.request.Request(f"{CONVERSA}/api/turno",
                               data=f"texto={urllib.parse.quote(pregunta)}&sesion={sesion}".encode(),
                               headers={"Content-Type": "application/x-www-form-urlencoded"})
    t = json.load(urllib.request.urlopen(r, timeout=30))
    textos, fuentes, ini = [], [], time.time()
    with urllib.request.urlopen(f"{CONVERSA}/api/turno/{t['id']}/eventos", timeout=600) as s:
        for l in s:
            l = l.decode().strip()
            if not l.startswith("data:"):
                continue
            ev = json.loads(l[5:])
            if ev.get("tipo") == "contexto":
                fuentes = ev.get("fuentes", [])
            elif ev.get("tipo") in ("total", "error", "fin"):
                break
            elif ev.get("texto"):
                textos.append(ev["texto"])
    return " ".join(textos), fuentes, time.time() - ini


def quitar_eco(respuesta, pregunta):
    """Quita el eco de la pregunta y el relleno «Déjame pensarlo…» que la sala dice antes."""
    r = respuesta.strip()
    if r.startswith(pregunta):
        r = r[len(pregunta):].strip()
    return re.sub(r"^D[ée]jame pensarlo[^.!?]*[.!?]\s*", "", r, flags=re.I)


def evaluar(respuesta, esperados):
    n = norm(respuesta)
    fallos = []
    for p in esperados:
        alias, conceptos = PRODUCTOS[p]
        if not any(a in n for a in alias):
            fallos.append(f"no menciona {p}")
        elif not any(c in n for c in conceptos):
            fallos.append(f"{p} sin sus conceptos clave")
    # La primera frase debe hablar del (primer) producto preguntado, no de otro
    primera = norm(re.split(r"(?<=[.!?])\s", respuesta.strip())[0]) if respuesta else ""
    sujeto = next((q for q, (al, _) in PRODUCTOS.items() if any(primera.startswith(a) or f" {a} es" in primera[:60] for a in al)), None)
    if sujeto and sujeto not in esperados:
        fallos.append(f"empieza hablando de {sujeto}")
    if "rattio no es" in n:
        fallos.append("arrastra el tema anterior")
    return fallos


def main():
    etiqueta = sys.argv[1] if len(sys.argv) > 1 else "prueba"
    base = f"{etiqueta}-{int(time.time())}"
    casos = [(p, e, f"{base}-s{i}") for i, (p, e) in enumerate(SIMPLES + DOBLES)]
    casos += [(f"¿Qué es {p}?", [p], f"{base}-secuencia") for p in SECUENCIA]
    ok = 0
    for i, (preg, esp, ses) in enumerate(casos, 1):
        resp, fuentes, seg = turno(preg, ses)
        resp = quitar_eco(resp, preg)
        fallos = evaluar(resp, esp)
        ok += not fallos
        marca = "OK  " if not fallos else "FALLA"
        tipo = "secuencia" if ses.endswith("secuencia") else "nueva"
        print(f"{marca} [{tipo}] {preg} ({seg:.0f} s)")
        print(f"      R: {resp[:230]}")
        print(f"      fuentes: {sorted(set(f.split(' - ')[0] for f in fuentes))}")
        if fallos:
            print(f"      motivo: {', '.join(fallos)}")
    print(f"\nRESULTADO {etiqueta}: {ok}/{len(casos)} correctas")


if __name__ == "__main__":
    main()
