#!/usr/bin/env bash
# Experimento 3 · Seguridad (SEG-01). Igual que correr_corrida.ps1, para macOS / Linux.
#   ./correr_corrida.sh corrida1
#   VUS=100 DURACION=1m ./correr_corrida.sh piloto
set -u
cd "$(dirname "$0")"
NOMBRE="${1:-corrida1}"; VUS="${VUS:-1000}"; DURACION="${DURACION:-5m}"; INTERVALO="${INTERVALO:-10}"; ESPERA="${ESPERA_ATAQUES:-45}"
ID="seguridad-$NOMBRE"; K6="exp-k6-$ID"; mkdir -p resultados
paso() { printf '\n=== %s ===\n' "$1"; }
fallo() { echo "ERROR: $1"; exit 1; }

paso "0. Imágenes y sistema arriba"
docker image inspect exp-seguridad-herramientas:latest >/dev/null 2>&1 || docker compose --profile herramientas build herramientas
docker image inspect grafana/k6:0.52.0 >/dev/null 2>&1 || docker compose --profile pruebas pull k6
docker compose --profile observabilidad up -d || fallo "docker compose up falló"
for i in $(seq 30); do curl -sk https://localhost:8443/salud | grep -q ok && break; sleep 2; done
curl -sk https://localhost:8443/salud | grep -q ok || fallo "el balanceador no responde"

paso "1. Datos limpios para '$ID'"
docker compose run --rm herramientas python herramientas/generar_datos.py || fallo "generar_datos.py falló"

paso "2. k6 de fondo: $VUS estudiantes, rampa 30 s + $DURACION"
rm -f "resultados/$ID.json" "resultados/${ID}_k6.log" "resultados/${ID}_registro.json"; docker rm -f "$K6" >/dev/null 2>&1
docker compose run --rm k6 inspect /trabajo/pruebas/k6/guardado.js >/dev/null 2>&1 || fallo "k6 no encuentra el script"
docker compose run --rm --name "$K6" k6 run -e VUS="$VUS" -e DURACION="$DURACION" -e INTERVALO="$INTERVALO" \
  --summary-export "/trabajo/resultados/$ID.json" /trabajo/pruebas/k6/guardado.js > "resultados/${ID}_k6.log" 2>&1 &
PID_K6=$!

paso "3. Esperando $ESPERA s y lanzando ataques, enumeraciones y controles"
sleep "$ESPERA"
kill -0 "$PID_K6" 2>/dev/null || fallo "k6 terminó antes de tiempo; revisa resultados/${ID}_k6.log"
docker compose run --rm -e "REGISTRO_ATAQUES=resultados/${ID}_registro.json" herramientas \
  pytest pruebas/ataques -q -p no:cacheprovider --junitxml "resultados/${ID}_ataques.xml"

paso "4. Esperando a que k6 termine"
wait "$PID_K6"

paso "5. Reconciliación"
[ -f "resultados/${ID}_registro.json" ] || fallo "pytest no generó resultados/${ID}_registro.json"
docker compose run --rm herramientas python herramientas/reconciliar.py --experimento seguridad \
  --ataques "resultados/${ID}_ataques.xml" --registro "resultados/${ID}_registro.json" --salida "resultados/${ID}_resumen.txt"
