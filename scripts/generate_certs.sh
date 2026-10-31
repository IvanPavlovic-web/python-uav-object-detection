#!/usr/bin/env bash
# Pravi testnu PKI infrastrukturu za MARA-1: vlastitu CA, certifikat servera
# (zemaljska stanica) i certifikat klijenta (dron). Samo za razvoj i demonstraciju.
#
# Upotreba: scripts/generate_certs.sh [izlazni_direktorijum]
set -euo pipefail

OUT="${1:-certs}"
DAYS=825
mkdir -p "$OUT"
cd "$OUT"
umask 077

EC_ARGS=(-newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes)

# 1) Certifikacijsko tijelo (CA)
openssl req -x509 "${EC_ARGS[@]}" -keyout ca.key -out ca.crt -days "$DAYS" \
  -subj "/CN=MARA-1 Test CA" \
  -addext "basicConstraints=critical,CA:TRUE" \
  -addext "keyUsage=critical,keyCertSign,cRLSign" \
  -addext "subjectKeyIdentifier=hash" 2>/dev/null

# 2) Server (zemaljska stanica); ime i IP adresa u SAN polju
cat > server.ext <<EOF
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature
extendedKeyUsage=serverAuth
subjectAltName=DNS:localhost,IP:127.0.0.1
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid,issuer
EOF
openssl req -new "${EC_ARGS[@]}" -keyout server.key -out server.csr -subj "/CN=localhost" 2>/dev/null
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out server.crt -days "$DAYS" -extfile server.ext 2>/dev/null

# 3) Klijent (dron MARA-1)
cat > client.ext <<EOF
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature
extendedKeyUsage=clientAuth
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid,issuer
EOF
openssl req -new "${EC_ARGS[@]}" -keyout client.key -out client.csr -subj "/CN=MARA-1" 2>/dev/null
openssl x509 -req -in client.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out client.crt -days "$DAYS" -extfile client.ext 2>/dev/null

rm -f server.csr client.csr server.ext client.ext ca.srl
chmod 600 ./*.key
echo "Certifikati su napravljeni u: $(pwd)"
ls -1 ca.crt server.crt server.key client.crt client.key
echo "Privatni ključ CA (ca.key) čuvajte odvojeno od drona i ne objavljujte ga."
