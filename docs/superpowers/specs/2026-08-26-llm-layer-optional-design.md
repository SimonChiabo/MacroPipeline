# La capa LLM sin configurar no participa — diseño

**Fecha:** 2026-08-26
**Estado:** Aprobado
**Cierra:** la mitad de Anthropic de la limitación (d) de ADR-009

---

## Problema

`MacroOrchestrator.__init__` construye `LLMClient()` sin protección, en el mismo
bloque donde `FREDClient` y `R2Client` sí la tienen. Sin `ANTHROPIC_API_KEY` el
constructor levanta `ValueError` y **la run muere antes de entrar en
`run_weekly_close`**: no hay
alerta, no hay fila de estado, y la semana siguiente pasa exactamente lo mismo
en silencio.

Eso contradice la tabla de ADR-009, que declara que la capa LLM **degrada**:
con la API caída el pipeline publica el bloque genérico con las cifras reales.
Lo que diverge no es un aviso que falte, sino que el constructor trate como
fatal a un componente que la política declara prescindible.

ADR-009 anotó el caso en su limitación (d) y lo dejó explícitamente **"sin
decidir"**. Esto lo decide.

## Alcance

**Entra:** solo Anthropic.

**No entra:** el resto de la limitación (d) —FMP, Alpha Vantage, Telegram y las
banderas `PUBLISH_X`/`PUBLISH_LINKEDIN` con un valor que no es `true` ni
`false`—. Ese caso es distinto y más difícil: son componentes *necesarios*, así
que el eje pide alertar, y `FMPClient` y `AlphaVantageClient` se construyen
**antes** que `TelegramBot`, o sea que cuando revientan el canal de aviso
todavía no existe. Sigue abierto.

## La declaración que habilita el tercer eje

El tercer eje de ADR-009 dice:

> Un componente **declarado opcional**, cuando no está configurado, no participa
> — y no participar no es degradar. Un componente necesario al que le faltan
> credenciales es un fallo.

Y exige que la declaración exista de antes, para que la respuesta no dependa de
quién conteste. ADR-001 dice **"auxiliar"**, no "opcional": el LLM no toca
números, solo redacta un titular de 120 caracteres a partir de cifras ya
calculadas y validadas. ADR-009 ya se apoya en esa definición para justificar
que la capa LLM degrade («ADR-001 define la capa LLM como auxiliar y esto es la
consecuencia de esa definición»).

**Auxiliar es la declaración.** Sin capa LLM el cierre semanal se publica igual
y sigue siendo correcto: lo que se pierde es redacción, no información, porque
las cifras las pone el pipeline. Es la misma forma que tiene la declaración de
R2 en ADR-007.

Queda escrito en ADR-009, que es donde vive el eje, y **no** en ADR-001: el eje
ya cita a ADR-007 para R2 sin reescribir ADR-007, y ADR-001 es un ADR aceptado
de 2026-05-14 al que no le corresponde resolver una pregunta de agosto.

De ahí sale que **Anthropic sin key, FRED sin key y R2 sin configurar son la
misma cosa**: no participan, y no gastan una alerta.

## La asimetría, y por qué es correcta

| Situación | Qué pasa | Alerta |
|---|---|---|
| `ANTHROPIC_API_KEY` ausente | No participa: titular genérico con las cifras reales | **No** |
| Key presente, API caída | Degrada: `FALLBACK_HEADLINE` → bloque genérico | **Sí** (`generador_caido`) |
| Key presente, validador no responde | Degrada: bloque genérico | **Sí** (`validador_no_respondio`) |
| Key presente, titular rechazado | Degrada: bloque genérico | **Sí** (`titular_rechazado`) |

Es exactamente la asimetría que FRED ya tiene desde el 2026-08-26. Roto y no
configurado no son lo mismo: avisar cada semana de una configuración permanente
es el ruido que hace que se deje de leer el aviso que importa, y sostiene la
distinción que ADR-009 fija — **si llega una alerta, es porque algo se rompió**.

## Diseño

### 1. La guarda en `__init__`

Entre `self.renderer` y `self.telegram`, misma forma que FRED y R2:

```python
self.llm: LLMClient | None
self.validator_agent: ValidatorAgent | None
try:
    self.llm = LLMClient()
    self.validator_agent = ValidatorAgent(self.llm)
except ValueError as e:
    logger.warning("llm_not_configured", reason=str(e))
    self.llm = None
    self.validator_agent = None
```

Los dos se apagan juntos y dentro del mismo `try`: `ValidatorAgent` recibe el
cliente, así que sin generador no hay validador que construir.

**Sin bandera aparte.** La guarda lee `self.llm is None` directo. Es la lección
de `x_ready`/`linkedin_ready`, que son propiedades derivadas del cliente a
propósito: un atributo que se puede desincronizar del cliente es un atributo que
un test puede poner a mano para saltearse el mockeo, que fue como el bug de
`5ba7997` vivió detrás de cuatro tests en verde.

El `except` es estrecho (`ValueError`) y no ancho como el de R2 en
`upload_image`: acá no hay red de por medio. `LLMClient.__init__` solo levanta
`ValueError` cuando falta la key; construir
`Anthropic(api_key=...)` no hace ninguna llamada.

### 2. El titular genérico sale de la rama de degradación

Hoy se construye **dentro** de la rama `if degradation:` de la fase LLM. Pasa a
una función de módulo:

```python
def _generic_headline(data: WeeklyCloseData) -> str:
    """<docstring: por qué vive acá y no en la rama de degradación>"""
    return (
        f"📊 Cierre de Mercado Semanal:\n"
        f"S&P500: {data.sp500_weekly_return * 100:+.2f}%\n"
        f"NASDAQ: {data.nasdaq_weekly_return * 100:+.2f}%"
    )
```

Texto idéntico al de hoy. El motivo de extraerlo es que a partir de ahora lo
usan **dos** caminos que alertan distinto, y la premisa con la que ADR-009
acepta degradar ahí es *"el bloque genérico lleva las cifras reales"*. Con dos
copias esa premisa se puede volver falsa en una sola de ellas, y sería falsa
justo donde nadie mira.

### 3. La fase LLM abre con la guarda

```python
if self.llm is None or self.validator_agent is None:
    logger.info("llm_layer_not_participating")
    headline = _generic_headline(data)
    validator_approved = None
    prompt_version = None
else:
    <la fase actual, sin cambios>
    prompt_version = _PROMPT_VERSION
```

Con la capa apagada no se arma `data_str`, no salen las dos llamadas a la API, y
**no se manda ningún `send_alert`**.

`_PROMPT_VERSION` deja de ir directo a `mark_as_published` y pasa por una
variable local que la rama normal setea. `validator_approved` pasa a
`bool | None`; solo se usa dentro de la fase LLM y en la llamada a
`mark_as_published`, así que el cambio de tipo no arrastra nada.

`headline` sigue siendo `str` en los cuatro consumidores de más abajo —la
petición de aprobación por Telegram, X, LinkedIn y el estado—, así que ninguno
se entera.

### 4. Qué registra la fila

`prompt_version = NULL` y `validator_approved = NULL`.

Escribir `headline=v1.4/validator=v1.1/model=claude-haiku-4-5` afirmaría una
llamada que no ocurrió, y `prompt_version` existe justamente para poder
reproducir un titular histórico: un titular que escribió el pipeline no tiene
versión de prompt que lo reproduzca. `validator_approved=False` se leería como
"el validador lo rechazó", que tampoco pasó.

NULL significa "no ocurrió", que es **exactamente** lo que ya significan las
seis columnas macro cuando FRED no participa. Las dos columnas ya son nullable y
`mark_as_published` **ya tiene escrita** la rama
`int(validator_approved) if validator_approved is not None else None`
— hoy inalcanzable desde el orquestador, porque
`bool(review.get("approved"))` nunca da None. Es código muerto que este cambio
pone a correr, igual que pasó con la reconciliación parcial.

### 5. Qué no se toca

- **`.env.example` y `scripts/check_publishers.py`.** Verificado: `FRED_API_KEY`
  está declarada **sin comentar** en `.env.example` pese a que FRED es opcional.
  Que un componente tolere la ausencia de su key no cambia que se espere
  tenerla puesta; lo que se declara comentado son las variables que se decidió
  *no* poner (`STATE_DB_PATH`, `LINKEDIN_TOKEN_ISSUED`). `ANTHROPIC_API_KEY` se
  queda donde está, y el chequeo de deriva no cambia.
- **`LLMClient` y `ValidatorAgent`.** El cambio es del orquestador. Hacer que
  `LLMClient.__init__` no levante fue considerado y descartado: devolvería
  `FALLBACK_HEADLINE`, que dispara la rama `generador_caido` y **alertaría todas
  las semanas** por una configuración permanente, además de borrar la distinción
  entre "no configurado" y "roto" que es la que hace correcto el silencio.
- **Los tres textos de degradación existentes**, que siguen igual.

## Tests

Tres nuevos, más un par en `__init__` y un control negativo que **ya existe**.
Los de construcción en `tests/unit/test_orchestrator_llm.py` (nuevo); los de la
run en `tests/integration/test_orchestrator_exit_states.py`, que es el fichero
con `StateDB` **real** — `test_orchestrator_persistence.py` lo mockea, así que
no puede verificar qué quedó escrito en la fila.

1. **`__init__` sin `ANTHROPIC_API_KEY` no levanta** y deja `self.llm is None` y
   `self.validator_agent is None`.
2. **Una run sin capa LLM publica**, y el titular lleva los dos retornos reales
   —no `FALLBACK_HEADLINE`, que no lleva ninguna cifra—.
3. **No manda ninguna alerta.** Es el assert que fija el tercer eje.
4. **La fila queda con `prompt_version` y `validator_approved` en NULL**, con un
   `StateDB` real, como `test_orchestrator_exit_states.py`.
5. **Control negativo: con la key presente y la API caída, sí alerta.** No hay
   que escribirlo: `test_a_dead_generator_alerts_even_if_the_validator_approves`
   y `test_a_dead_generator_publishes_the_generic_block_with_the_real_figures`
   ya existen en `test_orchestrator_persistence.py`. Lo que hay que hacer es
   **verificar que siguen pasando**: son lo que impide que apagar el silencio de
   más apague también la alerta real, y el segundo es el que fija el texto del
   bloque genérico a través del camino de degradación, así que protege la
   extracción de `_generic_headline` sin que haya que duplicarlo.

   Además, los dos tests de `__init__` se escriben **por dirección** —con key
   construye, sin key no—: dos asserts ciegos a la dirección fueron exactamente
   lo que dejó a la alerta de publicadores mintiendo con 148 tests en verde.

**Verificación por mutación antes de commitear**, no después:

- Quitar el `try/except` de `__init__` → vuelve a morir la run (cae el test 1).
- Meter un `send_alert` en la rama nueva → cae **exactamente** el test 3.
- Dejar `prompt_version = _PROMPT_VERSION` en la rama nueva → cae el test 4.
- Reemplazar `_generic_headline(data)` por `FALLBACK_HEADLINE` → cae el test 2.

## Consecuencias

**Positiva:** el código deja de tratar como fatal a un componente que la
política declara prescindible, y una key ausente pasa de matar la run en
silencio a publicar un cierre correcto con titular genérico.

**Negativa, y es real:** una run sin capa LLM publica el bloque genérico
**todas las semanas sin decir nada**. Es la consecuencia aceptada del tercer
eje, la misma que FRED sin key, y descansa en que sea una configuración
deliberada y no un accidente. Si `ANTHROPIC_API_KEY` desapareciera del entorno
por error, el pipeline seguiría publicando y nadie se enteraría por Telegram.
El log `llm_not_configured` es el único rastro.

**Lo que sigue abierto:** el resto de la limitación (d). FMP, Alpha Vantage y
Telegram son componentes necesarios y su caso no lo resuelve este eje.

## Verificación

`./.venv/Scripts/python.exe -m pytest` y `./.venv/Scripts/python.exe -m mypy src`.
El Python global de la máquina no tiene el paquete en editable y le faltan
`anthropic`, `playwright` y `boto3`: con él mypy inventa errores `no-any-return`
y los tests del orquestador ni colectan.
