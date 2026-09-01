# El chequeo de credenciales deja de ser solo de las redes — diseño

**Fecha:** 2026-09-01
**Estado:** Aprobado
**Cierra:** el resto del agujero que anotó el spec del 2026-08-31 —que
`check_publishers.py` no autentica FRED, AV, Anthropic ni R2—.

---

## Problema

El pipeline solo comprueba que las variables **existan**. `build_component`
atrapa el `ValueError` que levanta el constructor del cliente cuando falta una
credencial, y eso es todo: una key presente pero rotada, revocada o mal copiada
es indistinguible de una buena hasta que la corrida pega contra la API.

`scripts/check_publishers.py` cierra eso para X y LinkedIn. Para los otros
cuatro componentes con credenciales la cobertura es despareja, y la diferencia
importa más de lo que parece:

**FRED, AV y Anthropic ya se autentican de verdad todas las noches.** Los
contract tests de `tests/contract/` pegan contra las APIs reales y
`require_api_key` falla ruidosamente en CI si falta una key. O sea que para
estos tres el agujero **no** es "nadie los verifica" sino uno más chico y más
concreto: lo que se verifica son los **secrets de CI**, nunca el `.env` local.
La deriva entre los dos es invisible, y el `.env` local es el que usa quien
corre el pipeline a mano.

**R2 es otra cosa.** No tiene contract test, no tiene secret en CI y no tiene
fixture: es el **único de los ocho componentes con credenciales sin ninguna
verificación real en ninguna parte**. Y es el que peor falla. `.env.example` lo
dice en sus propias palabras: el snapshot de imagen es opcional, el fichero de
estado no. Sin sincronizar `state/state.db` la deduplicación se pierde y el
mismo cierre puede salir dos veces (ADR-009, filas «R2 (estado)»).

Encima, R2 tiene el problema que este script ya conoce. El chequeo de X existe
porque un token de solo lectura autentica perfecto contra `/2/users/me` y
recién falla con 403 al publicar. Los tokens de R2 tienen la misma partición
—*Object Read only* contra *Object Read & Write*— y la API de S3 **no tiene**
equivalente a la cabecera `x-access-level`: no hay dry-run ni forma de
confirmar permiso de `PutObject` sin poner un objeto.

**R2 es lo que justifica este trabajo. Los otros tres son cobertura del `.env`.**

## Alcance

**Entra:** cuatro chequeos nuevos (FRED, AV, Anthropic, R2), el rename del
script, el blindaje del paso que ya lo corre en el nightly, y la documentación.

**No entra: FMP ni Telegram**, los otros dos de los ocho. FMP tiene contract
test nocturno, igual que FRED y AV. Telegram no tiene ninguno y es un hueco
real —sin `TELEGRAM_BOT_TOKEN` válido no hay aprobación humana y ADR-004 se
queda sin piso—, pero cae fuera del alcance pedido. Queda anotado acá y no en
otro lado.

**Tampoco entra: llevar estos cuatro chequeos al nightly.** Decidido a
conciencia (ver §Costes aceptados). El chequeo sigue siendo a mano, y lo único
que este diseño le debe al nightly es no romperle el paso que ya tiene.

## El gate: los `USE_*` mandan, igual que las redes

`component_enabled` ya existe, ya cubre los ocho componentes desde ADR-009 y ya
levanta `ValueError` con un valor que no sea `true`/`false`. No hace falta
mecanismo nuevo:

- `USE_FRED=false` (o AV, o Anthropic, o R2) → **no se chequea y no cuenta para
  el código de salida**. Apagar es una decisión, no un fallo: es el tercer eje
  de ADR-009 y es la misma regla que ya rige para `PUBLISH_X` y
  `PUBLISH_LINKEDIN`.
- Un valor que no se entiende → diagnóstico y `return 1`, no traceback.
  `main()` ya lo hace para las dos redes; se extiende a los cuatro.

## La regla del "no se pudo verificar", y su única excepción

Hay tres resultados posibles por componente y no dos, así que hay que decir
explícitamente cuál de ellos pone el script en rojo.

**Regla: no haber podido verificar pone rojo.** Una API que no responde, un
timeout, un corte de red: **FALLA**, con un texto que nombra el transporte y no
la credencial —igual que el `LINKEDIN_UNREACHABLE` de hoy, que existe justo para
que la alerta no grite "token muerto" por una caída ajena—. El argumento ya está
escrito en el fixture de `test_av_contract.py`: un verde que no verificó nada es
peor que un rojo.

**Excepción única: el rate limit de Alpha Vantage, que va en AVISO y verde.**
No es un fallo excepcional sino una condición que **el propio chequeo se
fabrica**: consume una llamada de la cuota diaria cada vez que corre, así que
correrlo dos veces seguidas lo pondría rojo por su propia culpa. Un chequeo que
se pone rojo por usarlo es un chequeo que se desactiva, que es exactamente lo
que el docstring de `report_env_drift` dice haber evitado. Ninguna otra
condición entra por esta puerta.

## El código de salida cambia de significado, y por eso cambia el nombre

Hoy el `1` significa "las credenciales de **publicación** sirven", y así está
escrito en el docstring. Pasa a significar **"algún componente encendido no
tiene credenciales que sirvan"**: las seis piezas cuentan igual.

Por eso `scripts/check_publishers.py` pasa a **`scripts/check_credentials.py`**
(`git mv`, para conservar la historia). Un nombre que miente es peor deuda que
el churn del rename, y el próximo que lea `check_publishers` va a asumir que
solo mira X y LinkedIn.

Referencias vivas a actualizar en el mismo commit:

| Fichero | Línea |
|---|---|
| `README.md` | 100 |
| `.github/workflows/ci.yml` | 128 |
| `.github/workflows/contract-tests.yml` | 179 |
| `src/macro_pipeline/orchestration/main.py` | 552, 572 |
| `src/macro_pipeline/components.py` | 5, 62 (docstrings) |
| `scripts/linkedin_token_alert.py` | 5 (docstring) |
| `tests/unit/test_check_publishers.py` | el fichero se renombra; la fixture lo carga por ruta |

Los `revision_*.md` de la raíz **no se tocan**: son informes fechados de lo que
se vio ese día, no documentación viva.

## Diseño de cada chequeo

Los tres chequeos HTTP usan `requests` a pelo dentro del script, no los
clientes. `components.py` ya documenta por qué este script no puede arrastrar
pandas ni los siete clientes, y `FREDClient`/`AlphaVantageClient` traen pandas.

### a. FRED

`GET https://api.stlouisfed.org/fred/series?series_id=UNRATE&file_type=json`
con la key. Un objeto de metadatos, no observaciones: es la llamada más barata
que igual autentica.

FRED **no contesta 401** con una key inválida: contesta 400 con un
`error_message` en el cuerpo. El código exacto y el nombre del campo se
**confirman en vivo contra la API durante la implementación**, no se deducen de
la doc — es lo mismo que se hizo con `_CODIGOS_DE_AUSENCIA` en `r2_client.py`,
donde la verificación en vivo es lo que hace confiable el comentario.

Se imprime el `error_message`, nunca la key.

### b. Alpha Vantage

`GET https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=SPY` con la
key.

La trampa está documentada en `av_client.py:79`: **Alpha Vantage devuelve 200
OK siempre**, incluso para errores y rate limits. Así que el status code no
decide nada y hay que mirar el cuerpo:

| Cuerpo | Veredicto |
|---|---|
| `Error Message` | **FALLA**: la key no sirve |
| `Information` con `rate limit` | **AVISO**: inconcluso, no pone rojo |
| Datos | **OK** |

**El rate limit no puede poner el chequeo en rojo.** La cuota de la capa
gratuita es chica, el propio chequeo consume una llamada de esa cuota, y un
chequeo que se pone rojo por cuota es un chequeo que se termina desactivando.
Es el criterio que ya está escrito dos veces en el repo: el docstring de
`report_env_drift` ("un chequeo que pone el script en rojo por una decisión
pendiente termina desactivado") y el marcador `AV_RATE_LIMIT` de
`tests/contract/test_av_contract.py`, que existe justo para separar "no pudimos
verificar" de "el contrato cambió".

La salida deja el marcador `AV_RATE_LIMIT`, con el mismo texto y por el mismo
motivo. No colisiona con el del nightly: ese se grepea sobre `pytest-output.txt`.

### c. Anthropic

`GET https://api.anthropic.com/v1/models`, con `x-api-key` y
`anthropic-version`.

Es un endpoint de listado, **no de inferencia: autentica sin gastar tokens**.
Por eso ese y no un mensaje de un token.

- 401 → **FALLA**: la key no sirve.
- 200 → **OK**, y además se mira si `MODEL` (`claude-haiku-4-5`, la constante de
  `llm/client.py`) sigue en la lista. Si no aparece, **AVISO**, no falla: hoy no
  impide publicar, pero es la señal temprana del retiro del modelo, y este repo
  ya se comió uno (`claude-3-haiku-20240307`, retirado el 2026-04-19) por no
  tenerla.

**El listado es paginado, y eso importa acá.** La página por defecto es chica,
así que un chequeo que mire solo la primera respuesta va a avisar de un retiro
inexistente el día que `claude-haiku-4-5` caiga en la segunda página. Un AVISO
falso entrena a ignorar el verdadero, que es el modo de fallo que este mismo
spec cita dos veces. La implementación pagina —o pide un `limit` que alcance— y
**lo confirma en vivo contra la API**, igual que el 400 de FRED.

### d. R2 — sonda de escritura

Es el chequeo que motiva el spec y el único con efectos.

Usa **`R2Client`**, no boto3 crudo. `storage/` no tiene `__init__.py` y
`r2_client.py` solo importa boto3 y structlog, así que entra sin pandas. Y el
punto no es ahorrar líneas: la sonda tiene que ejercitar **el mismo camino que
el pipeline** —`upload_object` y `download_object`, con las dos ramas de
botocore atrapadas— o estaría verificando un endpoint que el pipeline no usa.

Bajo una key dedicada y aleatoria, `healthcheck/check-credentials-<uuid4>.txt`:

1. **`upload_object`** con unos bytes y un timestamp ISO → confirma `PutObject`.
   **Esto es lo único en todo el diseño que no se puede obtener leyendo.**
2. **`download_object`** → confirma `GetObject` y que lo escrito vuelve igual.
3. **`delete_object`** (método nuevo) → limpieza.

**El orden `put` → `get` no es estético y merece un test que lo fije.**
`download_object` traduce `NoSuchBucket` a "ausente" (`None`) a propósito, y ese
comentario explica bien por qué: para el sincronizado de estado, un bucket sin
crear es indistinguible de un objeto que aún no existe. Para un chequeo de
credenciales es engañoso. Arrancando por la lectura, **un bucket inexistente se
leería como "primera corrida" y el chequeo pasaría en verde**. Poniendo el put
primero, el bucket inexistente revienta ahí y se nombra.

**Si el borrado falla: AVISO y verde.** El pipeline nunca borra —`r2_client.py`
solo tiene `put_object` y `get_object`, y `state_sync.py` solo llama a esos
dos—, así que exigir permiso de `DeleteObject` pondría en rojo un token
perfectamente capaz de hacer todo lo que el pipeline necesita. La key es
aleatoria y no fija para que dos corridas simultáneas no se pisen y para que un
huérfano de un borrado fallido no se confunda con el de otra corrida.

Diagnósticos, que es donde está el valor:

| Síntoma | Qué se dice |
|---|---|
| `InvalidAccessKeyId` / `SignatureDoesNotMatch` | Las credenciales no autentican |
| `AccessDenied` en el put, con el get andando | **Token de solo lectura.** Reemitirlo con *Object Read & Write*: es el mismo caso que el `x-access-level: read` de X |
| `NoSuchBucket` en el put | El bucket no existe |
| Fallo de transporte (`BotoCoreError`) | FALLA nombrando el corte de red, no la credencial |
| El get devuelve `None` o bytes distintos tras un put exitoso | FALLA: se escribió y no se pudo releer |

**Código de producción que este trabajo agrega:** `R2Client.delete_object`, con
el mismo manejo de las dos ramas de botocore que sus hermanas. Es el único, y va
en el cliente y no en el script justo para que la sonda no invente su propio
manejo de errores.

## Blindaje del nightly — en el mismo commit, no después

`contract-tests.yml:179` ya corre este script, con `PUBLISH_X: "false"` y
**solo** los secrets de LinkedIn. El paso suma a su `env:`:

```yaml
USE_FRED: "false"
USE_AV: "false"
USE_ANTHROPIC: "false"
USE_R2: "false"
```

Es exactamente el mismo movimiento que el `PUBLISH_X: "false"` que ya tiene, y
por el mismo motivo: ese paso verifica LinkedIn y su alerta habla de LinkedIn.

Sin esto, el primer nightly después del merge cae en la rama genérica y manda
"la verificación de la credencial de LinkedIn falló por otro motivo" **todas las
noches**, culpando a LinkedIn de que falta la key de FRED. El comentario de ese
mismo paso ya nombra ese modo de fallo y por qué es el peor: "una alerta que
señala al componente equivocado es peor que ninguna".

## Errores y modos de fallo

| Situación | Comportamiento |
|---|---|
| `USE_X=false` (cualquiera de los cuatro) | No se chequea, no cuenta para el código de salida |
| `USE_X` con un valor ilegible | Diagnóstico y `1`, sin traceback |
| Key de FRED inválida | FALLA con el `error_message` de FRED |
| AV con `Error Message` | FALLA: la key no sirve |
| AV con rate limit | AVISO + marcador `AV_RATE_LIMIT`, **verde** |
| Anthropic 401 | FALLA |
| Anthropic OK y `MODEL` ausente de la lista | AVISO, verde: el modelo está por retirarse |
| R2 token de solo lectura | FALLA nombrando la reemisión con permiso de escritura |
| R2 bucket inexistente | FALLA en el put, no se disfraza de "primera corrida" |
| R2 borrado fallido | AVISO + un objeto huérfano bajo `healthcheck/`, verde |
| Cualquier API sin responder | **FALLA**, con un texto que nombra el transporte y no la credencial |
| Anthropic OK y `MODEL` en la segunda página del listado | OK, sin aviso: la implementación pagina |
| Todo apagado | Igual que hoy con las dos redes apagadas: `0` |

## Tests

La fase roja se predice **explícitamente para cada test antes de escribir el
código**: en dos planes seguidos de este repo la predicción salió mal, y un test
que no falla antes no ancla nada.

- **El gate**: cada uno de los cuatro apagado no se chequea y no puede poner el
  script en rojo. Plantilla: `test_a_disabled_network_is_not_even_checked` y
  `test_a_disabled_network_cannot_turn_the_script_red`.
- **El switch ilegible** da diagnóstico y `1`. Plantilla:
  `test_a_malformed_flag_gets_a_diagnostic_and_not_a_traceback`.
- **AV**: `Information` con rate limit → AVISO y `main()` en `0`;
  `Error Message` → FALLA y `1`. Es *la* distinción del chequeo de AV.
- **La regla y su excepción, enfrentadas**: un corte de red en cada uno de los
  cuatro pone `1`, y el rate limit de AV no. Los dos casos en el mismo fichero,
  porque la excepción solo se entiende contra la regla. Plantilla:
  `test_un_corte_de_red_deja_el_marcador_que_el_nightly_grepea`.
- **Anthropic**: 401 falla; modelo ausente avisa y sigue verde.
- **R2**, con un doble de `R2Client` y sin red:
  - camino feliz: put, get de los mismos bytes, delete;
  - **borrado fallido → AVISO y verde**;
  - `AccessDenied` en el put → FALLA con el texto del token de solo lectura;
  - **el orden**: un test que fije que el put ocurre antes del get. Invertirlos
    tiene que hacerlo caer — es el bug del `NoSuchBucket` leído como ausencia;
  - **la sonda nunca toca `state/state.db`**: se fija el prefijo `healthcheck/`.
- **El blindaje del nightly**: un test que lea `contract-tests.yml` y exija los
  cuatro `USE_*: "false"` en ese paso. Es el mismo tipo de test que este repo ya
  usa contra `.env.example`
  (`test_the_example_declares_the_six_component_switches_commented`), y es lo
  único que impide que el blindaje se pierda en un merge futuro.
- **Verificación por mutación** sobre las dos reglas que se pueden invertir sin
  que nada se rompa a la vista: la del rate limit de AV (AVISO ↔ FALLA) y la del
  orden put/get. Cada mutación tiene que hacer caer un test y **solo uno**.

## Costes aceptados

- **El script deja de ser pasivo.** Escribe y borra un objeto en un bucket
  privado propio. "No publica nada" se mantiene intacto; **"no toca nada" ya
  no**, y eso va en el docstring y en el README. Es el precio de la única
  propiedad que no se puede obtener leyendo, sobre el componente que
  `.env.example` declara no opcional.
- **`R2Client` gana un `delete_object` que producción no usa.** Un método de
  borrado conviviendo con el código que sincroniza `state.db`. Se acota por el
  prefijo `healthcheck/` y por no llamarlo desde ningún camino de producción,
  pero el método existe y alguien lo puede llamar.
- **Un objeto huérfano por cada borrado fallido**, y con key aleatoria se
  acumulan. Son bytes, y el AVISO lo dice cada vez.
- **Una llamada de la cuota diaria de AV por corrida del chequeo**, la misma
  cuota que usan el pipeline y el contract test.
- **En CI se sigue sin verificar nada de esto.** El chequeo es a mano, así que
  R2 —el único de los ocho componentes sin cobertura automática— sigue sin
  cobertura automática. Este spec no cierra eso: lo deja igual que estaba y le
  pone nombre. Es el candidato obvio al siguiente spec, y el que decida hacerlo
  paga cuatro secrets nuevos en CI, uno con permiso de escritura sobre el
  bucket, y una sonda que escribe en R2 todas las noches desde GitHub Actions.
- **Telegram queda sin chequeo**, y es el que apaga el HITL de ADR-004 si su
  token muere. Fuera del alcance pedido, anotado acá.
