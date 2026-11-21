# Corridas repetidas · Experimento 3 · Seguridad (SEG-01)

Para cuando ya hiciste la instalación del README y quieres correr el experimento otro día o repetir corridas. Todos los comandos van en PowerShell, **desde la carpeta de este proyecto**.

## A. Antes de cada sesión

1. Abre **Docker Desktop** y espera a que diga *Engine running*.
2. Si el otro experimento (o el laboratorio anterior) quedó encendido, apágalo desde **su** carpeta: `docker compose --profile observabilidad down`.
3. Levanta este laboratorio y comprueba:
   ```powershell
   docker compose --profile observabilidad up -d
   docker compose --profile observabilidad ps
   curl.exe -sk https://localhost:8443/salud
   ```
   Debes ver `{"ok":true}`, y `postgres`, `redis-primario` y `broker` como `healthy`.
4. Abre Grafana para ver la corrida en vivo: <http://localhost:3000/d/exp1>.

> k6 y herramientas no aparecen en `ps`: son contenedores de un solo uso que el script crea y borra en cada corrida.

## B. Correr

```powershell
# piloto (≈ 3 min)
powershell -ExecutionPolicy Bypass -File .\correr_corrida.ps1 -Nombre piloto -VUS 100 -Duracion 1m
# 3 corridas del experimento (≈ 7 min cada una)
1..3 | ForEach-Object { powershell -ExecutionPolicy Bypass -File .\correr_corrida.ps1 -Nombre "corrida$_" }
```

El script hace, en orden:
1. Datos limpios.
2. k6 de fondo con 1.000 estudiantes (solo ambiente).
3. A los 45 s, pytest lanza 120 ataques, 20 enumeraciones y 2 controles.
4. Espera a que k6 termine.
5. Reconcilia las 8 métricas contra los Logs de seguridad.

## C. Reglas para que la corrida sea válida

- **Una corrida a la vez**, siempre con un `-Nombre` distinto.
- **No ejecutes otros comandos del experimento mientras corre.** En especial, no ejecutes `generar_datos.py` a mano.
- **No dejes que el equipo se suspenda** y cierra los programas pesados.

## D. Leer el resultado

Cada corrida deja `resultados\seguridad-<nombre>_resumen.txt`, con cada métrica marcada CUMPLE o NO CUMPLE y una línea final que dice si la corrida **RESPALDA** la hipótesis. La hipótesis se respalda si **las 3 corridas** la respaldan.

## E. Al terminar

```powershell
docker compose --profile observabilidad stop
```
