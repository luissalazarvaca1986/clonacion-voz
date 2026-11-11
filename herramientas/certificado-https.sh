#!/usr/bin/env bash
# Certificado HTTPS de la sala (nginx, puerto 9443) válido en cualquier red.
#
# El navegador solo deja usar el micrófono en HTTPS (o localhost). Un certificado hecho
# para una IP deja de valer al cambiar de red, así que:
#   1. Una CA propia, creada una sola vez (nginx/ca/, fuera de git y fuera del contenedor).
#      Su certificado (nginx/certs/ca.crt) se instala UNA VEZ en el celular y desde ahí
#      confía en todo lo que ella firme.
#   2. El certificado del servidor se rehace con las IP que tenga la máquina en ese momento,
#      su nombre (<host>.local, por mDNS) y localhost, firmado por esa CA.
# Al llegar a otra red (otra IP), ejecutarlo una vez; recarga nginx solo:
#   herramientas/certificado-https.sh            # rehace el certificado si cambió la IP
#   herramientas/certificado-https.sh --forzar   # lo rehace siempre
# El celular descarga la CA en http://<IP>:8094/ca.crt
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIR_CA="$RAIZ/nginx/ca"
DIR_CERTS="$RAIZ/nginx/certs"
NOMBRE_CA="Rattio Second Brain - CA local"
CONTENEDOR="clonar-voz-front"
FORZAR="${1:-}"

mkdir -p "$DIR_CA" "$DIR_CERTS"
chmod 700 "$DIR_CA"

# ---- 1. CA (una sola vez, 20 años) --------------------------------------------------
if [[ ! -f "$DIR_CA/ca.key" || ! -f "$DIR_CA/ca.crt" ]]; then
    openssl req -x509 -newkey rsa:3072 -nodes -days 7300 -sha256 \
        -keyout "$DIR_CA/ca.key" -out "$DIR_CA/ca.crt" -subj "/CN=$NOMBRE_CA" \
        -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
        -addext "keyUsage=critical,keyCertSign,cRLSign" 2>/dev/null
    chmod 600 "$DIR_CA/ca.key"
    FORZAR="--forzar"
    echo "CA creada: $DIR_CA/ca.crt (instálala una vez en el celular)"
fi
cp "$DIR_CA/ca.crt" "$DIR_CERTS/ca.crt"

# ---- 2. Nombres e IP actuales --------------------------------------------------------
HOST="$(hostname)"
# IPv4 de las interfaces reales (sin Docker ni loopback)
mapfile -t IPS < <(ip -4 -o addr show scope global \
    | awk '$2 !~ /^(docker|br-|veth|virbr|cni|flannel|tailscale)/ {split($4, a, "/"); print a[1]}' | sort -u)
SAN="DNS:localhost,DNS:$HOST,DNS:$HOST.local,IP:127.0.0.1"
for ip in "${IPS[@]}"; do SAN+=",IP:$ip"; done

# ¿Hace falta rehacerlo? (cambió la lista de nombres/IP, no existe, o vence en < 30 días)
if [[ "$FORZAR" != "--forzar" && -f "$DIR_CERTS/conversa.crt" && -f "$DIR_CERTS/.san" ]] \
    && [[ "$(cat "$DIR_CERTS/.san")" == "$SAN" ]] \
    && openssl x509 -checkend 2592000 -noout -in "$DIR_CERTS/conversa.crt" >/dev/null \
    && openssl verify -CAfile "$DIR_CA/ca.crt" "$DIR_CERTS/conversa.crt" >/dev/null 2>&1; then
    echo "Certificado al día: $SAN"
    exit 0
fi

# ---- 3. Certificado del servidor (825 días: el máximo que aceptan los navegadores) ----
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
openssl req -newkey rsa:2048 -nodes -keyout "$TMP/conversa.key" -out "$TMP/conversa.csr" \
    -subj "/CN=$HOST.local" 2>/dev/null
cat > "$TMP/ext.cnf" <<EOF
basicConstraints=CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=$SAN
EOF
openssl x509 -req -in "$TMP/conversa.csr" -CA "$DIR_CA/ca.crt" -CAkey "$DIR_CA/ca.key" \
    -CAcreateserial -CAserial "$DIR_CA/ca.srl" -days 825 -sha256 \
    -extfile "$TMP/ext.cnf" -out "$TMP/conversa.crt" 2>/dev/null
# nginx envía el certificado del servidor seguido del de la CA (cadena completa)
cat "$TMP/conversa.crt" "$DIR_CA/ca.crt" > "$DIR_CERTS/conversa.crt"
install -m 644 "$TMP/conversa.key" "$DIR_CERTS/conversa.key"
echo "$SAN" > "$DIR_CERTS/.san"
echo "Certificado nuevo para: $SAN"

# ---- 4. Recargar nginx si está en marcha ---------------------------------------------
if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTENEDOR"; then
    docker exec "$CONTENEDOR" nginx -s reload && echo "nginx recargado"
fi
