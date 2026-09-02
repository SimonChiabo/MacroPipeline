# Telegram entra al chequeo de credenciales — diseño

**Fecha:** 2026-09-02
**Estado:** Aprobado
**Cierra:** el último componente sin verificación real que dejó abierto el spec
del 2026-09-01 —«Telegram sigue sin chequeo, y es el que apaga el HITL de
ADR-004 si su token muere»—.

---

## Problema

`check_credentials.py` verifica seis componentes contra sus APIs. Telegram no
es uno de ellos, y ahora que R2 está cubierto es el único de los **ocho**
componentes con credenciales que **no tiene ninguna verificación en ninguna
parte**: no hay contract test, no hay secret en CI, no hay fixture. El otro que
tampoco está en la tabla —FMP— sí tiene contract test nocturno, que es
exactamente el motivo por el que el spec de ayer lo dejó afuera y a Telegram lo
dejó anotado. Es lo mismo que ayer hacía de R2 el punto del trabajo.

Lo que se pierde cuando Telegram no sirve no es un bloque del post: es el HITL
entero de ADR-004. Un token revocado desde BotFather deja al pipeline sin canal
de aprobación **y sin canal de alerta**, que es el caso irreducible que ya
tiene nombre en la limitación (d) de ADR-009: no hay forma de avisar de que no
hay con qué avisar. Esa run sale con código `1` y hoy nadie mira ese código,
porque nada corre `main.py` en un schedule.

Y hay dos modos de fallo que autentican perfecto y matan el HITL igual, que es
justo el patrón que este script existe para cazar:

- **Un webhook puesto.** `wait_for_approval` hace long polling con
  `getUpdates`, y Telegram devuelve **409 Conflict** a `getUpdates` mientras
  haya un webhook registrado. El borrador sale, el botón no llega nunca, la run
  se cuelga hasta el timeout y no se publica. Es el `x-access-level: read` de X
  con otra ropa: la credencial es válida, el **modo de uso** está roto.
- **`TELEGRAM_ALLOWED_USER_ID` con el id equivocado.** El operador aprieta
  Aprobar y `wait_for_approval` descarta el callback en silencio. Mismo
  resultado: timeout, sin publicar, sin nada en la salida que lo explique.

## Alcance

Un séptimo componente en la tabla, gobernado por `USE_TELEGRAM` como los otros
seis. Nada de esto cambia el pipeline: es un script de diagnóstico.

**La sonda es de solo lectura, y acá sí se puede.** R2 tuvo que escribir porque
la API de S3 no ofrece alternativa. Telegram sí la ofrece: `getMe`,
`getWebhookInfo` y `getChat` cubren token, modo de uso y alcance del chat sin
mandar un mensaje. Se mantiene entera la promesa del docstring —«no publica
nada»— y no llega un mensaje al teléfono cada vez que se corre el script.

**Lo que queda fuera a propósito:** la entrega de punta a punta. `getChat` en
200 dice que el chat existe y que el bot lo alcanza, no que `sendMessage` vaya
a entregar. Cerrar eso pide mandar un mensaje real, y el coste —ruido en cada
corrida, y romper la promesa de pasividad— no lo paga.

## Estructura: el cliente de producción, con los GET en el script

`check_telegram()` construye un `TelegramBot()` y hace los tres GET con
`requests` en el script.

Construir el cliente no es ceremonia: de ahí salen `base_url` y —lo que
importa— el `allowed_user_id` ya parseado con la regla exacta de producción
(`int(raw) if raw.isdigit() else None`). El chequeo verifica **lo que el
pipeline hace**, no una reimplementación que el día que cambie `bot.py` seguiría
validando la regla vieja sin que lo diga nadie. Es el mismo argumento por el
que `check_anthropic` importa `MODEL` de `llm.client` en vez de copiar el
string.

Los GET van en el script y no como métodos nuevos del cliente porque producción
no los usa: `R2Client.delete_object` ya quedó anotado como residuo por existir
sólo para el chequeo, y tres métodos muertos más es empeorar eso.

`TelegramBot.__init__` levanta `ValueError` si falta token o chat_id, igual que
`R2Client`, y se atrapa igual.

## Diseño del chequeo

`TELEGRAM_VARS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")` — las dos que el
constructor exige. `TELEGRAM_ALLOWED_USER_ID` **no** entra en `_check_present`:
su ausencia es un aviso, no una falla (ver más abajo).

Tres llamadas, en este orden:

**1. `getMe`.** ¿El token autentica? `401` → `NO_LISTO`. En 200 imprime el
`@username` del bot, que además confirma a ojo que es el bot que se cree.

Va primero por la misma razón por la que el put va antes que el get en R2: sin
él, un `400` de `getChat` no distingue «chat mal configurado» de «token
revocado», y el diagnóstico que el script existe para dar sale equivocado.

**2. `getWebhookInfo`.** `result.url` no vacío → `NO_LISTO`, nombrando el 409 y
que el long polling es lo que usa el HITL. Con `url` vacía, una línea de OK: el
long polling tiene la vía libre.

**3. `getChat`** con `TELEGRAM_CHAT_ID`. `400 chat not found` (id equivocado, o
el operador nunca inició conversación con el bot) y `403` (bot bloqueado o
expulsado del grupo) → `NO_LISTO`. En 200 imprime el `type` del chat y:

- Si `type == "private"`, el `chat.id` **es** el user id del operador, así que
  se compara con `allowed_user_id`. Distintos → `NO_LISTO`, y el texto nombra
  las dos variables para que se vea cuál corregir.
- Si el chat es un grupo, el id es negativo y distinto por diseño: la
  comparación no aplica y se **dice** en una línea de aviso, en vez de callar.
  Un silencio acá se lee como «comparado y OK».

## Veredictos

`NO_LISTO`: variable ausente o con el placeholder, `401` en `getMe`, webhook
registrado, chat inalcanzable, y el mismatch de ids en un chat privado.

El mismatch es `NO_LISTO` y no un aviso porque el resultado es un HITL muerto:
el bot autentica, manda el borrador y ningún botón se acepta jamás. Eso es
exactamente lo que el código `1` significa —«algo encendido no está listo»— y
es el tipo de fallo silencioso que justifica el script entero.

`LISTO` con `AVISO`: `allowed_user_id` en `None` —ausente o no numérica, que
`isdigit()` trata igual— y el caso del chat de grupo.

El aviso y no la falla, para el id ausente: **el cierre se publica**. El HITL
funciona, sólo que sin restringir quién aprueba. El código de salida significa
«esta credencial no sirve», y ésta sirve. Además la ausencia ya la reporta
`report_env_drift`, que está escrito a propósito para no poner el script en
rojo por una decisión pendiente —«un chequeo que pone el script en rojo por una
decisión pendiente termina desactivado»—. Contradecirlo acá sería tener las dos
políticas a la vez.

**No hay caso `SIN_VERIFICAR`.** Ese veredicto es de la cuota de Alpha Vantage.
Acá una red caída es `NO_LISTO`, como en los otros cinco componentes.

## Un cambio en código existente: `_mensaje_de_error`

Telegram pone el motivo real en `description` (`{"ok": false, "error_code":
400, "description": "Bad Request: chat not found"}`), igual que FRED lo pone en
`error_message`. `_mensaje_de_error` aprende a mirar las dos claves, así que un
400 deja de ser «HTTP 400» a secas. Es la misma función y el mismo motivo por
el que se escribió; no se duplica.

## Blindaje del nightly — en el mismo commit, no después

`USE_TELEGRAM: "false"` en el paso de LinkedIn de `contract-tests.yml`, junto a
los otros cuatro y con el comentario que ya llevan.

Sin eso, ese paso —que corre sólo con los secrets de LinkedIn— ejecutaría el
chequeo de Telegram sin `TELEGRAM_BOT_TOKEN`, saldría con código `1` y la
alerta culparía a LinkedIn de una credencial de Telegram. Es literalmente el
error de planificación de ayer, que dejó el repo roto durante cuatro tasks:
**cuando un cambio amplía lo que un script verifica, el blindaje de quien ya lo
invoca va en el mismo commit.**

## Tests

Unitarios, con `monkeypatch` y respuestas falsas, como el resto del fichero. La
suite no sale a la red.

**Primero, un arreglo que no es opcional:** `_apagar_los_cuatro` pasa a apagar
cinco. Cinco tests llaman a `main()` apoyándose en ese helper, y
`component_enabled` trata la variable ausente como encendida a propósito: sin
tocarlo, esos cinco tests **contactan la API real de Telegram**.

Ocho casos, cada uno anclado a la mutación que lo justifica:

| Caso | Veredicto | Mutación que caza |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` ausente | `NO_LISTO` sin tocar la red | saltarse `_check_present` |
| `401` en `getMe` | `NO_LISTO` | tratar cualquier no-200 como listo |
| `getWebhookInfo` con `url` | `NO_LISTO` | ignorar el webhook y seguir |
| `getChat` `400` | `NO_LISTO` | dar por buena la respuesta sin mirar el status |
| privado, ids iguales | `LISTO` | — (el camino feliz) |
| privado, ids distintos | `NO_LISTO`, el texto nombra las dos variables | el `==` invertido, y el texto fijo en vez de interpolado |
| grupo, ids distintos | `LISTO` | comparar sin mirar el `type` |
| `allowed_user_id` en `None` | `LISTO` + aviso | subirlo a `NO_LISTO` |

Los dos últimos son los que impiden que el chequeo empiece a fallar por casos
legítimos, que es como un chequeo se termina apagando.

## Documentación

«los seis componentes» aparece en el docstring del módulo de
`check_credentials.py`, en el `print` de apertura de `main()` y en el README.
Pasan a siete. El ancho de la columna del resumen (`:<15`) no cambia:
`Telegram:` mide 9.

## Costes aceptados

- **La entrega no se verifica** (arriba, en Alcance). `getChat` es el proxy más
  cercano que no manda nada.
- **En CI no se verifica nada de esto**, igual que R2: el nightly apaga los
  cinco. Cerrarlo pediría dos secrets más, y el chequeo de Telegram en CI
  tendría que apuntar a un chat que no es el del operador.
- **Un webhook registrado por otra herramienta sobre el mismo bot** sale como
  `NO_LISTO` aunque sea deliberado. Es correcto para este pipeline: si hay
  webhook, este HITL no funciona.
