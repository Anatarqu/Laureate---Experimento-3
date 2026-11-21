"""Configuración de la suite de ataques del experimento 3 (SEG-01).

Cada petición de ataque se envía con su propio X-Request-Id y se anota en un registro
(tipo, caso, código, cuerpo, cabeceras y tiempo de respuesta). Al terminar, el registro se
guarda en el archivo indicado por REGISTRO_ATAQUES. La reconciliación lo cruza, uno a uno,
con la tabla log_seguridad para medir cobertura, duplicados, campos completos y demora.
"""
import json
import os
import time
import uuid

import httpx
import pytest

AQUI = os.path.dirname(os.path.abspath(__file__))
GATEWAY_URL = os.getenv("GATEWAY_URL", "https://balanceador:8443")
RESPUESTAS_DIRECTO_URL = os.getenv("RESPUESTAS_DIRECTO_URL", "http://respuestas:8081")
DB_APP_URL = os.getenv("DB_APP_URL", "postgresql://app_eval:app_eval@postgres/evaluaciones")
REGISTRO_ATAQUES = os.getenv("REGISTRO_ATAQUES")

REGISTRO: list[dict] = []


def cargar_datos() -> dict:
    with open(os.path.join(AQUI, "datos_ataques.json"), encoding="utf-8") as f:
        return json.load(f)


DATOS = cargar_datos()


def enviar(cliente: httpx.Client, metodo: str, url: str, tipo: str, caso: int, **kwargs) -> httpx.Response:
    """Envía una petición con X-Request-Id propio y la anota en el registro."""
    rid = str(uuid.uuid4())
    cabeceras = dict(kwargs.pop("headers", {}) or {})
    cabeceras["X-Request-Id"] = rid
    t_envio = time.time()
    t0 = time.perf_counter()
    r = cliente.request(metodo, url, headers=cabeceras, **kwargs)
    ms = (time.perf_counter() - t0) * 1000
    REGISTRO.append({
        "request_id": rid,
        "tipo": tipo,
        "caso": caso,
        "estado": r.status_code,
        "ms": round(ms, 3),
        "cuerpo": r.text,
        "content_type": r.headers.get("content-type"),
        "content_length": r.headers.get("content-length"),
        "t_envio": t_envio,
    })
    return r


def pytest_sessionfinish(session, exitstatus):
    if REGISTRO_ATAQUES:
        os.makedirs(os.path.dirname(os.path.abspath(REGISTRO_ATAQUES)), exist_ok=True)
        with open(REGISTRO_ATAQUES, "w", encoding="utf-8") as f:
            json.dump(REGISTRO, f, ensure_ascii=False, indent=0)


@pytest.fixture(scope="session")
def gateway():
    with httpx.Client(base_url=GATEWAY_URL, verify=False, timeout=10.0) as c:
        yield c


@pytest.fixture(scope="session")
def directo():
    with httpx.Client(base_url=RESPUESTAS_DIRECTO_URL, timeout=10.0) as c:
        yield c
