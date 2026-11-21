"""Reconciliación de una corrida: calcula las métricas y dice si cada una CUMPLE.

Experimento 1 · Latencia (REND-01)
  --experimento latencia --k6 resultados/latencia_corrida1.json
  Métricas: p95 y p99 del guardado (k6) y respuestas perdidas (confirmadas por k6 frente a
  filas en la BD Intentos, cuando el Persistidor termina de escribir).

Experimento 3 · Seguridad (SEG-01)
  --experimento seguridad --ataques resultados/seguridad_corrida1_ataques.xml
                          --registro resultados/seguridad_corrida1_registro.json
  Métricas: ataques rechazados, tiempo de rechazo, enumeración (respuesta idéntica exista o no
  el recurso), y en los Logs de seguridad: cobertura, duplicados, campos completos y demora.

Con --salida, además de mostrarlo, guarda el resumen en ese archivo.
"""
import argparse
import asyncio
import json
import os
import time
import xml.etree.ElementTree as ET

import asyncpg
import redis.asyncio as redis

DB_ADMIN_URL = os.getenv("DB_ADMIN_URL", "postgresql://postgres:postgres@postgres/evaluaciones")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis-primario:6379")
STREAM, GRUPO = "log:respuestas", "persistidores"

# Metas (de los ASR). Se cambian aquí si cambia el ASR.
META_P95_MS, META_P99_MS, META_PERDIDAS = 400, 600, 0
META_RECHAZO_MS, META_DEMORA_LOG_S = 500, 5
TIPOS_ATAQUE = ["credencial_robada", "token_alterado", "token_vencido", "sin_token",
                "endpoint_docente", "llamada_directa"]
CAMPOS_OBLIGATORIOS = ["request_id", "fecha", "servicio", "usuario", "tenant_id", "recurso", "metodo", "motivo"]

LINEAS: list[str] = []


def decir(texto: str = "") -> None:
    LINEAS.append(texto)
    print(texto)


def fila_resumen(asr, metrica, valor, cumple, meta) -> tuple:
    return (asr, metrica, valor, cumple, meta)


def imprimir_resumen(filas) -> None:
    decir("\n=== Resumen de la corrida (para la tabla de resultados) ===")
    for asr, metrica, valor, cumple, meta in filas:
        decir(f"  {asr:<8} {metrica:<34} {valor:>22}   meta {meta:<16} {'CUMPLE' if cumple else 'NO CUMPLE'}")
    total = sum(1 for f in filas if f[3])
    decir(f"\n  {total} de {len(filas)} métricas cumplen -> "
          f"{'la corrida RESPALDA la hipótesis' if total == len(filas) else 'la corrida NO respalda la hipótesis'}")


# ------------------------------ Experimento 1 ------------------------------

def metrica_k6(m: dict, nombre: str) -> dict:
    v = m.get(nombre, {})
    return v.get("values", v)


async def esperar_drenado(limite_s: int = 600) -> None:
    r = redis.from_url(REDIS_URL, decode_responses=True)
    inicio = time.time()
    try:
        while time.time() - inicio < limite_s:
            try:
                grupos = await r.xinfo_groups(STREAM)
            except redis.ResponseError:
                return
            g = next((g for g in grupos if g["name"] == GRUPO), None)
            if g is None:
                return
            pendientes, rezago = g.get("pending", 0) or 0, g.get("lag", 0) or 0
            if pendientes == 0 and rezago == 0:
                return
            print(f"  esperando al Persistidor: rezago={rezago} pendientes={pendientes}")
            await asyncio.sleep(2)
        decir("  ADVERTENCIA: el stream no terminó de drenarse en el tiempo límite")
    finally:
        await r.aclose()


async def latencia(args) -> None:
    with open(args.k6, encoding="utf-8") as f:
        m = json.load(f)["metrics"]
    guardado = metrica_k6(m, "http_req_duration{tipo:guardado}") or metrica_k6(m, "http_req_duration")
    confirmados = int(metrica_k6(m, "guardados_confirmados").get("count", 0))
    errores = int(metrica_k6(m, "errores_guardado").get("count", 0))
    reintentos = int(metrica_k6(m, "reintentos_guardado").get("count", 0))
    iteraciones = int(metrica_k6(m, "iterations").get("count", 0))
    tasa = metrica_k6(m, "iterations").get("rate", 0)
    authz = metrica_k6(m, "autorizacion_ms")

    decir("Esperando a que el Persistidor termine de escribir en la BD Intentos...")
    await esperar_drenado()
    con = await asyncpg.connect(DB_ADMIN_URL)
    filas = await con.fetchval("SELECT count(*) FROM respuestas WHERE answer_id NOT LIKE 'ataque-%'")
    await con.close()
    perdidas = max(confirmados - filas, 0)

    decir("\n=== Experimento 1 · REND-01 · latencia del guardado ===")
    decir(f"  guardados emitidos        : {iteraciones}  ({tasa:.1f} guardados/s)")
    decir(f"  p95 / p99 del guardado    : {guardado.get('p(95)', 0):.1f} ms / {guardado.get('p(99)', 0):.1f} ms")
    decir(f"  confirmados por k6        : {confirmados}")
    decir(f"  filas en la BD Intentos   : {filas}")
    decir(f"  respuestas perdidas       : {perdidas}")
    decir(f"  errores HTTP / reintentos : {errores} / {reintentos}")
    if authz:
        decir(f"  (observación) autorización en el gateway: p95 {authz.get('p(95)', 0):.2f} ms")

    imprimir_resumen([
        fila_resumen("REND-01", "p95 del guardado", f"{guardado.get('p(95)', 0):.0f} ms",
                     guardado.get("p(95)", 1e9) <= META_P95_MS, f"<= {META_P95_MS} ms"),
        fila_resumen("REND-01", "p99 del guardado", f"{guardado.get('p(99)', 0):.0f} ms",
                     guardado.get("p(99)", 1e9) <= META_P99_MS, f"<= {META_P99_MS} ms"),
        fila_resumen("REND-01", "Respuestas perdidas", f"{perdidas} de {confirmados}",
                     perdidas <= META_PERDIDAS, str(META_PERDIDAS)),
    ])


# ------------------------------ Experimento 3 ------------------------------

def leer_junit(ruta: str) -> dict:
    conteo = {"ataque": [0, 0], "enumeracion": [0, 0], "control": [0, 0]}
    for caso in ET.parse(ruta).getroot().iter("testcase"):
        fallo = caso.find("failure") is not None or caso.find("error") is not None
        nombre = caso.get("name", "")
        for clave in conteo:
            if nombre.startswith(f"test_{clave}"):
                conteo[clave][0] += 1
                conteo[clave][1] += 0 if fallo else 1
    return conteo


async def seguridad(args) -> None:
    junit = leer_junit(args.ataques)
    with open(args.registro, encoding="utf-8") as f:
        registro = json.load(f)
    ataques = [r for r in registro if r["tipo"] in TIPOS_ATAQUE]
    esperados = {r["request_id"]: r for r in registro if r["estado"] in (401, 403)}
    rechazados = [r for r in ataques if r["estado"] in (401, 403)]
    tiempos = sorted(r["ms"] for r in ataques)
    t_max = tiempos[-1] if tiempos else 0
    t_p95 = tiempos[int(0.95 * (len(tiempos) - 1))] if tiempos else 0

    # Los Logs de seguridad son asíncronos: se espera a que lleguen (sin afectar la medida de
    # demora, que sale de las marcas de tiempo de cada fila).
    con = await asyncpg.connect(DB_ADMIN_URL)
    ids = list(esperados)
    inicio, anterior = time.time(), -1
    while True:
        presentes = await con.fetchval("SELECT count(DISTINCT request_id) FROM log_seguridad WHERE request_id = ANY($1)", ids)
        if presentes == len(ids) or time.time() - inicio > 60:
            break
        if presentes == anterior and time.time() - inicio > 15:
            break
        anterior = presentes
        await asyncio.sleep(1)
    filas = await con.fetch(
        "SELECT request_id, fecha, fecha_registro, servicio, usuario, tenant_id, recurso, metodo, motivo "
        "FROM log_seguridad WHERE request_id = ANY($1)", ids)
    duplicados = await con.fetchval(
        "SELECT count(*) FROM (SELECT request_id FROM log_seguridad GROUP BY request_id HAVING count(*) > 1) d")
    por_motivo = await con.fetch(
        "SELECT motivo, count(*) AS n FROM log_seguridad WHERE request_id = ANY($1) GROUP BY motivo ORDER BY n DESC", ids)
    await con.close()

    por_id = {f["request_id"]: f for f in filas}
    ataques_ids = [r["request_id"] for r in rechazados]
    registrados = sum(1 for i in ataques_ids if i in por_id)
    incompletos = sum(1 for f in filas if any(f[c] in (None, "") for c in CAMPOS_OBLIGATORIOS))
    demoras = sorted((f["fecha_registro"] - f["fecha"]).total_seconds() for f in filas)
    demora_max = demoras[-1] if demoras else float("inf")
    enum_ok, enum_total = junit["enumeracion"][1], junit["enumeracion"][0]

    decir("\n=== Experimento 3 · SEG-01 · resistencia a ataques ===")
    decir(f"  ataques lanzados           : {len(ataques)}  (tipos: {', '.join(TIPOS_ATAQUE)})")
    decir(f"  ataques rechazados         : {len(rechazados)}  (pytest: {junit['ataque'][1]} de {junit['ataque'][0]} casos correctos)")
    decir(f"  tiempo de rechazo          : p95 {t_p95:.1f} ms, máximo {t_max:.1f} ms")
    decir(f"  enumeración                : {enum_ok} de {enum_total} pares con respuesta idéntica")
    decir(f"  controles                  : {junit['control'][1]} de {junit['control'][0]} correctos")
    decir(f"  ataques en los Logs        : {registrados} de {len(rechazados)}")
    decir(f"  rechazos registrados (todos, incl. enumeración): {len(por_id)} de {len(esperados)}")
    decir(f"  duplicados por request_id  : {duplicados}")
    decir(f"  registros con campos vacíos: {incompletos}")
    decir(f"  demora del registro        : máximo {demora_max:.2f} s")
    for f in por_motivo:
        decir(f"    {f['motivo']:<32} {f['n']}")

    n = max(len(ataques), 1)
    imprimir_resumen([
        fila_resumen("SEG-01", "Ataques rechazados", f"{100 * len(rechazados) / n:.1f} % ({len(rechazados)}/{len(ataques)})",
                     len(rechazados) == len(ataques) and junit["ataque"][1] == junit["ataque"][0], "100 %"),
        fila_resumen("SEG-01", "Tiempo de rechazo (máximo)", f"{t_max:.0f} ms", t_max < META_RECHAZO_MS, f"< {META_RECHAZO_MS} ms"),
        fila_resumen("SEG-01", "No revela si el recurso existe", f"{enum_ok}/{enum_total} pares", enum_total > 0 and enum_ok == enum_total, "100 % idénticos"),
        fila_resumen("SEG-01", "Ataques registrados", f"{100 * registrados / max(len(rechazados), 1):.1f} % ({registrados})",
                     registrados == len(rechazados) and len(rechazados) > 0, "100 %"),
        fila_resumen("SEG-01", "Registrados una sola vez", f"{duplicados} duplicados", duplicados == 0, "0"),
        fila_resumen("SEG-01", "Registros con todos sus campos", f"{incompletos} incompletos", incompletos == 0, "0"),
        fila_resumen("SEG-01", "Demora del registro (máximo)", f"{demora_max:.2f} s", demora_max <= META_DEMORA_LOG_S, f"<= {META_DEMORA_LOG_S} s"),
        fila_resumen("SEG-01", "Controles legítimos", f"{junit['control'][1]}/{junit['control'][0]}",
                     junit["control"][0] > 0 and junit["control"][1] == junit["control"][0], "2/2"),
    ])


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--experimento", choices=["latencia", "seguridad"], required=True)
    p.add_argument("--k6", help="resumen de k6 (--summary-export), experimento latencia")
    p.add_argument("--ataques", help="reporte junit de pytest, experimento seguridad")
    p.add_argument("--registro", help="registro de peticiones de pytest (REGISTRO_ATAQUES), experimento seguridad")
    p.add_argument("--salida", help="guarda también el resumen en este archivo")
    a = p.parse_args()
    if a.experimento == "latencia" and not a.k6:
        p.error("--k6 es obligatorio en el experimento latencia")
    if a.experimento == "seguridad" and not (a.ataques and a.registro):
        p.error("--ataques y --registro son obligatorios en el experimento seguridad")
    asyncio.run(latencia(a) if a.experimento == "latencia" else seguridad(a))
    if a.salida:
        with open(a.salida, "w", encoding="utf-8") as f:
            f.write("\n".join(LINEAS) + "\n")
        print(f"\nResumen guardado en {a.salida}")
