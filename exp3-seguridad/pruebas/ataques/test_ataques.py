"""Suite del experimento 3 (SEG-01): se INTENTA el ataque y se verifica que se rechaza.

  6 tipos de ataque x 20 casos = 120 ataques   -> cada uno debe terminar en 401 o 403
  20 pruebas de enumeración                     -> intento inexistente y ajeno: misma respuesta
  2 controles (no son ataques)                  -> docente legítimo entra; RLS aísla instituciones

Componente que debe detener cada ataque (vista de seguridad):
  token de otro estudiante ............... Servicio de Decisiones (OPA) -> Aplicar decisión
  token alterado / vencido / sin token ... Validador JWT
  estudiante en endpoint de docente ...... Servicio de Decisiones (OPA)
  llamada directa sin gateway ............ Servicio de Intentos y respuestas (vuelve a validar)
  enumeración de intentos ................ Servicio de Intentos y respuestas (403 uniforme)
"""
import asyncio
import uuid

import asyncpg
import pytest

from conftest import DATOS, DB_APP_URL, enviar

PAREJAS = DATOS["parejas"]          # 20 parejas (estudiante A de inst-a, estudiante B de inst-b)
CASOS = range(len(PAREJAS))
RECHAZO = {401, 403}


def ruta_guardar(tenant: str, estudiante: str, intento: str, pregunta: str = "q-ataque") -> str:
    return f"/api/instituciones/{tenant}/estudiantes/{estudiante}/intentos/{intento}/respuestas/{pregunta}"


def ruta_de(est: dict) -> str:
    return ruta_guardar(est["tenant"], est["id"], est["intento"])


def cuerpo(i: int, tipo: str) -> dict:
    return {"valor": "X", "answer_id": f"ataque-{tipo}-{i}"}


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def sin_datos(r) -> bool:
    """El rechazo no devuelve datos del recurso: solo el cuerpo genérico."""
    return r.text in ('{"detail":"acceso_denegado"}', '{"detail":"no_autenticado"}')


# ---------------------------- 120 ataques ----------------------------

@pytest.mark.parametrize("i", CASOS)
def test_ataque_credencial_robada(gateway, i):
    """Token válido del estudiante A usado contra el intento del estudiante B (otra institución)."""
    p = PAREJAS[i]
    r = enviar(gateway, "PUT", ruta_de(p["b"]), "credencial_robada", i,
               json=cuerpo(i, "robada"), headers=bearer(p["a"]["token"]))
    assert r.status_code in RECHAZO and sin_datos(r), r.text


@pytest.mark.parametrize("i", CASOS)
def test_ataque_token_alterado(gateway, i):
    """Se cambia tenant_id en el payload sin volver a firmar."""
    p = PAREJAS[i]
    r = enviar(gateway, "PUT", ruta_de(p["b"]), "token_alterado", i,
               json=cuerpo(i, "alterado"), headers=bearer(p["token_alterado"]))
    assert r.status_code in RECHAZO and sin_datos(r), r.text


@pytest.mark.parametrize("i", CASOS)
def test_ataque_token_vencido(gateway, i):
    p = PAREJAS[i]
    r = enviar(gateway, "PUT", ruta_de(p["a"]), "token_vencido", i,
               json=cuerpo(i, "vencido"), headers=bearer(p["token_vencido"]))
    assert r.status_code in RECHAZO and sin_datos(r), r.text


@pytest.mark.parametrize("i", CASOS)
def test_ataque_sin_token(gateway, i):
    p = PAREJAS[i]
    r = enviar(gateway, "PUT", ruta_de(p["a"]), "sin_token", i, json=cuerpo(i, "sin_token"))
    assert r.status_code in RECHAZO and sin_datos(r), r.text


@pytest.mark.parametrize("i", CASOS)
def test_ataque_estudiante_en_endpoint_de_docente(gateway, i):
    est = PAREJAS[i]["a"]
    r = enviar(gateway, "GET", f"/api/instituciones/{est['tenant']}/cursos/curso-1/calificaciones",
               "endpoint_docente", i, headers=bearer(est["token"]))
    assert r.status_code in RECHAZO and sin_datos(r), r.text


@pytest.mark.parametrize("i", CASOS)
def test_ataque_llamada_directa_sin_gateway(directo, i):
    """Llamada directa al Servicio de Intentos y respuestas, con el token de A contra el intento de B."""
    p = PAREJAS[i]
    r = enviar(directo, "PUT", ruta_de(p["b"]), "llamada_directa", i,
               json=cuerpo(i, "directo"), headers=bearer(p["a"]["token"]))
    assert r.status_code in RECHAZO and sin_datos(r), r.text


# ------------------------ 20 pruebas de enumeración ------------------------

@pytest.mark.parametrize("i", CASOS)
def test_enumeracion_no_revela_si_el_intento_existe(gateway, i):
    """Con su propio token y su propia ruta, el estudiante A pide (1) un intento que no existe
    y (2) el intento de otro estudiante de su institución. Si las respuestas difieren, el
    atacante podría descubrir qué IDs existen. Deben ser idénticas: mismo código, cuerpo y tipo."""
    a = PAREJAS[i]["a"]
    ajeno = PAREJAS[(i + 1) % len(PAREJAS)]["a"]["intento"]       # otro estudiante de inst-a
    inexistente = str(uuid.uuid4())
    r1 = enviar(gateway, "PUT", ruta_guardar(a["tenant"], a["id"], inexistente), "enum_inexistente", i,
                json=cuerpo(i, "enum1"), headers=bearer(a["token"]))
    r2 = enviar(gateway, "PUT", ruta_guardar(a["tenant"], a["id"], ajeno), "enum_ajeno", i,
                json=cuerpo(i, "enum2"), headers=bearer(a["token"]))
    assert r1.status_code == r2.status_code == 403, (r1.status_code, r2.status_code)
    assert r1.text == r2.text, (r1.text, r2.text)
    assert r1.headers.get("content-type") == r2.headers.get("content-type")
    assert r1.headers.get("content-length") == r2.headers.get("content-length")


# ---------------------------- 2 controles ----------------------------

def test_control_docente_legitimo_si_entra(gateway):
    """Un docente de su propia institución sí puede consultar (no es un ataque)."""
    doc = DATOS["docentes"]["inst-a"][0]
    r = gateway.get("/api/instituciones/inst-a/cursos/curso-1/calificaciones", headers=bearer(doc["token"]))
    assert r.status_code == 200, r.text


def test_control_row_level_security_aisla_instituciones():
    """Con la sesión fijada en inst-a, la BD no devuelve filas de inst-b aunque se pidan."""

    async def consultar():
        con = await asyncpg.connect(DB_APP_URL)
        try:
            async with con.transaction():
                await con.execute("SELECT set_config('app.tenant_id', 'inst-a', true)")
                ajenas = await con.fetchval("SELECT count(*) FROM intentos WHERE tenant_id = 'inst-b'")
                propias = await con.fetchval("SELECT count(*) FROM intentos WHERE tenant_id = 'inst-a'")
            sin_sesion = await con.fetchval("SELECT count(*) FROM intentos")
            return ajenas, propias, sin_sesion
        finally:
            await con.close()

    ajenas, propias, sin_sesion = asyncio.run(consultar())
    assert ajenas == 0, "RLS dejó ver intentos de otra institución"
    assert propias > 0, "No hay intentos de inst-a: ejecuta generar_datos.py antes de la suite (y no durante)"
    assert sin_sesion == 0, "Sin institución en la sesión la BD no debería devolver filas"
