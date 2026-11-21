# Experimento 3 · Seguridad del acceso (SEG-01)

## Hipótesis

> **Si** cada petición debe superar tres controles independientes (el token en el Gateway Estudiantes, la política en el Servicio de Decisiones de OPA y la fila en la BD con Row-Level Security) y cada rechazo se publica por el Broker de eventos hacia los Logs de seguridad, **entonces**, de 120 intentos de acceso a datos ajenos lanzados durante la carga, el 100 % recibirá un rechazo en < 500 ms, sin datos y con la misma respuesta exista o no el recurso, y los Logs de seguridad tendrán un evento por cada intento, sin duplicados por ID de petición y en ≤ 5 s, **porque** cada control verifica por separado quién es el solicitante, qué puede hacer y a qué filas tiene derecho, y la auditoría viaja por un canal asíncrono independiente de la respuesta.

## Tácticas y patrones

- **Resistir ataques:** identificar, autenticar y autorizar actores, verificar la integridad del mensaje, limitar el acceso y la exposición, y separar entidades.
- **API Gateway** como PEP.
- **OPA como sidecar** (PDP).
- **Política como código** (`opa test`).
- **Defensa en profundidad.**
- **Multi-tenant con RLS.**
- **Publicar/suscribir.**
- **Consumidor idempotente** (`request_id` único).

## Diseño

Se despliega la arquitectura de la vista de seguridad. En el Gateway están el Validador JWT, el Extractor del sujeto, el Cliente PDP y Aplicar decisión. El Servicio de Decisiones es OPA, con la política validada por el Pipeline. El Servicio de Intentos y respuestas vuelve a verificar que el recurso sea del solicitante, y la BD aplica Row-Level Security. Cada rechazo sale por la Respuesta de denegación y el Broker de eventos hacia los Logs de seguridad. Mientras k6 mantiene de fondo a 1.000 estudiantes en operación normal, solo para recrear el ambiente, pytest lanza 120 ataques. Son 20 de cada tipo: token de otro estudiante, alterado, vencido, sin token, estudiante en endpoint de docente y llamada directa al servicio. Se agregan una prueba de enumeración, que compara la respuesta a un intento inexistente con la de uno ajeno, y dos controles: un docente legítimo entra y RLS aísla las instituciones. Se hacen un piloto y 3 corridas.

En cada corrida, pytest mide cuántos ataques se rechazan, en cuánto tiempo y si la respuesta revela que el recurso existe. Luego se revisan los Logs de seguridad: que haya un evento por cada ataque, sin duplicados por ID de petición, con todos sus campos y registrado a tiempo. La hipótesis se respalda si en las 3 corridas se cumplen cinco condiciones: el 100 % de los ataques se rechaza en menos de 500 ms, el rechazo no revela si el recurso existe, cada ataque se registra una sola vez, cada registro tiene todos sus campos y cada uno llega en 5 s o menos.

## Métricas y resultado esperado

| Métrica | Instrumento | Resultado esperado |
|---|---|---|
| Ataques rechazados | pytest y registro de peticiones | 100 % (120/120) |
| Tiempo de rechazo (máximo) | registro de peticiones | < 500 ms |
| No revela si el recurso existe | 20 pares de enumeración | 20/20 idénticos |
| Ataques registrados | cruce por `request_id` con `log_seguridad` | 100 % |
| Registrados una sola vez | `request_id` único | 0 duplicados |
| Registros con todos sus campos | request_id, fecha, servicio, usuario, institución, recurso, método, motivo | 0 incompletos |
| Demora del registro (máximo) | `fecha_registro − fecha` | ≤ 5 s |
| Controles legítimos | pytest | 2/2 |
| **Criterio** | | las 3 corridas cumplen |
