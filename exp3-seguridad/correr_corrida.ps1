# Corre UNA corrida completa del Experimento 3 · Seguridad del acceso (SEG-01), en orden:
#   0) sistema arriba  1) datos limpios  2) k6 de fondo (ambiente)  3) ataques con pytest
#   4) espera a k6  5) reconciliación contra los Logs de seguridad
#
# Uso (PowerShell, desde la carpeta del proyecto):
#   powershell -ExecutionPolicy Bypass -File .\correr_corrida.ps1 -Nombre corrida1
#   Piloto:  ... -Nombre piloto -VUS 100 -Duracion 1m
param(
    [string]$Nombre = "corrida1",
    [int]$VUS = 1000,
    [string]$Duracion = "5m",        # ventana de carga de fondo, después de la rampa de 30 s
    [double]$Intervalo = 10,         # segundos entre guardados de cada estudiante (10 -> ~100 guardados/s)
    [int]$EsperaAtaques = 45         # segundos antes de lanzar los ataques
)
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$id = "seguridad-$Nombre"
$k6 = "exp-k6-$id"
New-Item -ItemType Directory -Force resultados | Out-Null

function Paso($texto) { Write-Host "`n=== $texto ===" -ForegroundColor Cyan }
function Fallo($texto) { Write-Host "ERROR: $texto" -ForegroundColor Red; exit 1 }

Paso "0. Imágenes y sistema arriba (incluidos Prometheus y Grafana)"
docker image inspect exp-seguridad-herramientas:latest *> $null
if ($LASTEXITCODE -ne 0) { docker compose --profile herramientas build herramientas }
docker image inspect grafana/k6:0.52.0 *> $null
if ($LASTEXITCODE -ne 0) { docker compose --profile pruebas pull k6 }
docker compose --profile observabilidad up -d
if ($LASTEXITCODE -ne 0) { Fallo "docker compose up falló (¿Docker Desktop está abierto? ¿quedó arriba el otro experimento?)" }
$salud = ""
for ($i = 0; $i -lt 30 -and $salud -notmatch "ok"; $i++) {
    $salud = curl.exe -sk https://localhost:8443/salud
    if ($salud -notmatch "ok") { Start-Sleep -Seconds 2 }
}
if ($salud -notmatch "ok") { Fallo "el balanceador no responde en https://localhost:8443/salud. Revisa: docker compose --profile observabilidad ps" }
Write-Host "Sistema arriba: $salud   Grafana: http://localhost:3000/d/exp1   RabbitMQ: http://localhost:15672"

Paso "1. Datos limpios para '$id' (no ejecutes nada más mientras corre)"
docker compose run --rm herramientas python herramientas/generar_datos.py
if ($LASTEXITCODE -ne 0) { Fallo "generar_datos.py falló" }

Paso "2. k6 de fondo: $VUS estudiantes, un guardado cada $Intervalo s, rampa 30 s + $Duracion"
$resumenK6 = "resultados\$id.json"
Remove-Item $resumenK6, "resultados\${id}_k6.log", "resultados\${id}_k6_errores.log", "resultados\${id}_registro.json" -ErrorAction SilentlyContinue
cmd /c "docker rm -f $k6 >nul 2>&1"
$revision = cmd /c "docker compose run --rm k6 inspect /trabajo/pruebas/k6/guardado.js 2>&1"
if ($LASTEXITCODE -ne 0) {
    $revision | Out-File "resultados\${id}_k6_revision.log" -Encoding utf8
    $revision | Select-Object -Last 15 | ForEach-Object { Write-Host $_ }
    Fallo "k6 no pudo cargar el script (detalle arriba y en resultados\${id}_k6_revision.log)"
}
$argumentos = @("compose", "run", "--rm", "--name", $k6, "k6", "run",
    "-e", "VUS=$VUS", "-e", "DURACION=$Duracion", "-e", "INTERVALO=$Intervalo",
    "--summary-export", "/trabajo/resultados/$id.json", "/trabajo/pruebas/k6/guardado.js")
$procesoK6 = Start-Process docker -ArgumentList $argumentos -NoNewWindow -PassThru `
    -RedirectStandardOutput "resultados\${id}_k6.log" -RedirectStandardError "resultados\${id}_k6_errores.log"
$null = $procesoK6.Handle

Paso "3. Esperando $EsperaAtaques s y lanzando 120 ataques + 20 enumeraciones + 2 controles"
Start-Sleep -Seconds $EsperaAtaques
if ($procesoK6.HasExited) { Fallo "k6 terminó antes de tiempo. Revisa resultados\${id}_k6.log" }
docker compose run --rm -e "REGISTRO_ATAQUES=resultados/${id}_registro.json" herramientas `
    pytest pruebas/ataques -q -p no:cacheprovider --junitxml "resultados/${id}_ataques.xml"

Paso "4. Esperando a que k6 termine"
$procesoK6.WaitForExit()
Write-Host "k6 terminó con código $($procesoK6.ExitCode) (en este experimento k6 es solo el ambiente: el resultado está en el resumen)."

Paso "5. Reconciliación (8 métricas contra los Logs de seguridad)"
if (-not (Test-Path "resultados\${id}_registro.json")) { Fallo "pytest no generó resultados\${id}_registro.json" }
docker compose run --rm herramientas python herramientas/reconciliar.py --experimento seguridad `
    --ataques "resultados/${id}_ataques.xml" --registro "resultados/${id}_registro.json" --salida "resultados/${id}_resumen.txt"
Write-Host "`nListo. Evidencia de '$id' en resultados\ (resumen: ${id}_resumen.txt)" -ForegroundColor Green
