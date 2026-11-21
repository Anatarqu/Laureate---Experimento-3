"""Validación del JWT (táctica: autenticar actores / verificar integridad del mensaje)."""
import jwt

CLAIMS_OBLIGATORIOS = ["exp", "sub", "tenant_id", "rol"]


class TokenInvalido(Exception):
    """El token no existe, está vencido, fue alterado o no trae los datos mínimos."""

    def __init__(self, motivo: str):
        super().__init__(motivo)
        self.motivo = motivo


class ValidadorJWT:
    def __init__(self, ruta_llave_publica: str):
        with open(ruta_llave_publica, encoding="utf-8") as f:
            self._llave = f.read()

    def validar(self, cabecera_authorization: str | None) -> dict:
        if not cabecera_authorization or not cabecera_authorization.startswith("Bearer "):
            raise TokenInvalido("sin_token")
        token = cabecera_authorization.removeprefix("Bearer ").strip()
        try:
            return jwt.decode(
                token,
                self._llave,
                algorithms=["RS256"],
                options={"require": CLAIMS_OBLIGATORIOS},
            )
        except jwt.ExpiredSignatureError:
            raise TokenInvalido("token_vencido")
        except jwt.InvalidSignatureError:
            raise TokenInvalido("firma_invalida")
        except jwt.PyJWTError as e:
            raise TokenInvalido(f"token_invalido:{type(e).__name__}")


def ip_cliente(request) -> str:
    """IP real del cliente (HAProxy agrega X-Forwarded-For)."""
    reenviada = request.headers.get("x-forwarded-for")
    if reenviada:
        return reenviada.split(",")[0].strip()
    return request.client.host if request.client else "desconocida"


def id_peticion(request) -> str:
    """ID único de la petición (X-Request-Id). Se respeta el que llega si es un UUID válido,
    así el cliente y el log de seguridad pueden cruzarse uno a uno; si no, se genera."""
    import uuid
    recibido = request.headers.get("x-request-id", "")
    try:
        return str(uuid.UUID(recibido))
    except ValueError:
        return str(uuid.uuid4())


def tenant_de_ruta(ruta: str) -> str | None:
    """Institución tomada de la ruta /api/instituciones/{tenant}/..., para registrar
    también los intentos sin token."""
    partes = [p for p in ruta.split("/") if p]
    if len(partes) >= 3 and partes[0] == "api" and partes[1] == "instituciones":
        return partes[2]
    return None


# Cuerpo único para todo rechazo: no revela por qué se negó ni si el recurso existe.
CUERPO_401 = b'{"detail":"no_autenticado"}'
CUERPO_403 = b'{"detail":"acceso_denegado"}'
