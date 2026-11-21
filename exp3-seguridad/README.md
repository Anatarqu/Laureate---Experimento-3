# Experimento 3 · Seguridad del acceso (SEG-01)

Laboratorio en Docker Desktop con los componentes de la **vista de seguridad**:
- **Gateway:** Validador JWT, Extractor del sujeto, Cliente PDP y Aplicar decisión.
- **Motor de Autorización:** Repositorio de políticas, Pipeline y Servicio de Decisiones (OPA como sidecar).
- **Servicio de Intentos y respuestas** y **BD con Row-Level Security**.
- **Respuesta de denegación → Broker de eventos → Logs de seguridad.**

k6 corre de fondo solo para recrear la operación normal; pytest lanza los ataques.

La hipótesis, el diseño y el criterio de aceptación están en **EXPERIMENTO.md**. Para repetir corridas otro día, sigue **CORRIDAS.md**.

| Vista SEG-01 | Contenedor |
|---|---|
| Portal estudiante o docente | `herramientas` (pytest: ataques y controles) y `k6` (ambiente) |
| Balanceador | `balanceador` (HAProxy: TLS 8443 y límite de tasa) |
| Gateway (Validador JWT, Extractor del sujeto, Cliente PDP, Aplicar decisión) | `gateway` |
| Servicio de Decisiones · Repositorio · Pipeline | `opa-sidecar`, `opa/politicas/`, `pipeline-politicas` |
| Proveedor de identidad | `idp` |
| Servicio de Intentos y respuestas | `respuestas` (2 réplicas) |
| BD con Row-Level Security | `postgres` |
| Respuesta de denegación → Broker de eventos | publicador en `gateway` y `respuestas` → `broker` (RabbitMQ) |
| Logs de seguridad | `logs-seguridad` → tabla `log_seguridad` (un registro por `request_id`) |
| Soporte del guardado (ambiente) | `redis-primario`, `redis-replica`, `persistidor` |

## 1. Requisitos

- **Docker Desktop** 4.30 o superior, con **al menos 4 CPU y 8 GB de memoria** (*Settings → Resources*).
- Puertos libres: **8443**, 9090 (Prometheus) y 3000 (Grafana) y 15672 (RabbitMQ).
- Carpeta del proyecto **fuera de OneDrive** (por ejemplo `C:\exp3-seguridad`).
- **No levantes este proyecto a la vez que el del otro experimento** (ni el laboratorio anterior): usan los mismos puertos. Antes de empezar, en la carpeta del otro proyecto ejecuta `docker compose --profile observabilidad down`.

## 2. Instalación (una sola vez)

En PowerShell, desde la carpeta del proyecto:

```powershell
docker compose --profile herramientas build herramientas
docker compose run --rm herramientas python herramientas/preparar_llaves.py
docker compose --profile pipeline run --rm pipeline-politicas
docker compose --profile observabilidad up -d --build
curl.exe -sk https://localhost:8443/salud
```

1. Construye la imagen de herramientas.
2. Genera el certificado TLS y las llaves del JWT.
3. Valida la política (6 de 6 pruebas).
4. Levanta el laboratorio.
5. Comprueba que responde `{"ok":true}`.

## 3. Piloto (unos 3 minutos)

```powershell
powershell -ExecutionPolicy Bypass -File .\correr_corrida.ps1 -Nombre piloto -VUS 100 -Duracion 1m
```

En pytest debe aparecer `142 passed` (120 ataques, 20 enumeraciones y 2 controles). Al final, el resumen debe tener **8 de 8 métricas que cumplen**.

## 4. Corridas del experimento (3, unos 7 minutos cada una)

```powershell
1..3 | ForEach-Object { powershell -ExecutionPolicy Bypass -File .\correr_corrida.ps1 -Nombre "corrida$_" }
```

| Parámetro | Por defecto | Uso |
|---|---|---|
| `-Nombre` | `corrida1` | nombre de la corrida |
| `-VUS` | `1000` | estudiantes de fondo (ambiente) |
| `-Duracion` | `5m` | carga de fondo, después de la rampa de 30 s |
| `-EsperaAtaques` | `45` | segundos antes de lanzar los ataques |

Cada corrida deja en `resultados\` estos archivos:
- `seguridad-corridaN_ataques.xml`: los 142 casos de pytest.
- `seguridad-corridaN_registro.json`: cada ataque con su `request_id`, código, cuerpo y tiempo.
- `seguridad-corridaN_resumen.txt`: **las 8 métricas con CUMPLE o NO CUMPLE**.
- La salida de k6.

Grafana: <http://localhost:3000/d/exp1>. RabbitMQ: <http://localhost:15672> (guest / guest), cola `seguridad.acceso_denegado`.

## 5. Apagar

```powershell
docker compose --profile observabilidad stop       # conserva los datos
docker compose --profile observabilidad down -v    # borra también los volúmenes
```

## Solución de problemas

- **"port is already allocated":** está arriba otro experimento o el laboratorio anterior. Apágalo con `docker compose --profile observabilidad down` en su carpeta.
- **"Ataques registrados" < 100 %:** `broker` debe estar `healthy`. Revisa `docker compose logs logs-seguridad`.
- **Enumeración con respuestas distintas:** estás usando una imagen vieja. Ejecuta `docker compose --profile observabilidad up -d --build`.
- **503 `no_disponible`:** `docker compose up -d --force-recreate gateway opa-sidecar`.
