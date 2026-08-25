# Alerta cuando el bloque macro se cae, y el eje de lo opcional

**Fecha:** 2026-08-25
**Estado:** Aprobado, sin implementar
**Cierra:** la limitacion (c) de ADR-009 — la regla "toda degradacion alerta" no
se cumple para FRED, y quedo anotada como pregunta abierta para Simon.
**Agrega:** un segundo eje a ADR-009, porque la respuesta correcta para FRED
resulto ser un caso de una regla mas general que hoy vive repartida en tres
decisiones locales que nadie escribio.

---

## Lo que se verifico antes de disenar

1. **No hay un camino silencioso a `macro=None`, hay tres**, y no son la misma
   clase de cosa (`orchestration/main.py`, `_fetch_macro_snapshot`):
   - `self.fred is None` — no hay key. Es una configuracion.
   - `safe_build_macro_snapshot` devuelve `None` — API caida, serie corta o
     dato rancio (`data/macro.py`, el `except Exception` de esa funcion).
   - El `ValidationEngine` rechaza la cifra por fuera de rango.

   Las tres devuelven `None` sin distincion, y las tres solo hacen
   `logger.warning`. La pregunta como la dejo escrita ADR-009 —carve-out o
   alerta— es binaria y por eso queda gruesa.

2. **La key de FRED esta cargada**, en `.env` y en los secrets del nightly
   (`.github/workflows/contract-tests.yml`). El primer camino es hoy hipotetico.

3. **Los umbrales de frescura son generosos y estan calibrados contra el
   calendario real de publicacion** (`validators/rules.yaml`): IPC 90 dias,
   desempleo 75, DGS10 10. Una semana normal no dispara el camino de dato
   rancio. Un shutdown del gobierno si, porque BLS deja de publicar — y ese es
   justamente un caso en el que se quiere saber.

4. **Con `macro=None` la franja macro no se renderiza**
   (`render/playwright_engine.py`, `_build_macro_block` devuelve cadena vacia).
   La degradacion es visible en la imagen que se aprueba por Telegram, pero como
   una **ausencia**, y las ausencias son lo que no se nota.

5. **R2 sin configurar tambien degrada en silencio.** `orchestration/main.py`
   entra al bloque de subida solo `if self.r2_ready`, asi que con R2 sin
   configurar no hay aviso ninguno; solo la subida *fallida* alerta. La fila del
   ADR dice "Sin configurar **o** subida fallida → Degrada, con aviso". **La
   fila miente hoy.**

6. **`safe_build_macro_snapshot` tiene un solo caller** y tres tests unitarios
   (`tests/unit/test_macro.py`), asi que cambiarle la firma es mecanico.

---

## Las decisiones

### 1. Se alerta por lo roto; el opcional sin configurar es silencio

Sin key no alerta nunca. API caida, serie corta, dato rancio o cifra rechazada
si, nombrando la causa real.

Lo que decide es **cual de las tres causas es un fallo**. Las dos ultimas lo
son. La primera no: es la misma forma que `PUBLISH_X=false`, y avisar todas las
semanas de una configuracion permanente es el ruido que hace que se deje de leer
el aviso que importa.

De las tres, la que mas pesa en la decision es **la del validador**: significa
que FRED devolvio una cifra fuera de rango de plausibilidad, que es la clase de
dato que no se quiere cerca de una publicacion financiera, y es hoy la mas
silenciosa de las tres.

**Descartado — alertar siempre, incluido sin key.** Es lo mas simple y lo mas
fiel a la letra de la regla, y reintroduce exactamente el aviso semanal por una
decision propia que se saco del sistema el 2026-08-25.

### 2. El eje nuevo: "declarado opcional", no "sin configurar"

La formulacion obvia —*no configurado ⇒ silencio*— **no sobrevive al
inventario**, y eso es lo que la hace util descartarla por escrito.

Para X y LinkedIn una credencial faltante **si alerta**: quedo fijado como fallo
en ADR-009 el 2026-08-25. Para FRED y R2 una credencial faltante es silencio.
Con el eje puesto en "credencial presente" eso seria una contradiccion.

No lo es, y el motivo es lo que hay que escribir:

> **Un componente declarado opcional, cuando no esta configurado, no participa —
> y no participar no es degradar.** Un componente necesario al que le faltan
> credenciales es un fallo.

"Declarado opcional" no es una opinion sobre cada componente: **ADR-007 lo dice
de R2 y ADR-001 lo dice del bloque macro**; publicar, en cambio, es el proposito
del pipeline. El eje se apoya en declaraciones que ya existen, y por eso es
verificable en vez de retorico — se puede preguntar de un componente nuevo
"¿algun ADR lo declara opcional?" y obtener una respuesta que no depende de
quien conteste.

Hoy la regla se cumple en tres componentes por tres decisiones locales
distintas: los publicadores (fijada explicitamente), R2 (que la cumple sin que
nadie la escribiera) y FRED (que la va a cumplir con este cambio). Es la misma
situacion que motivo ADR-009: la respuesta correcta repartida en decisiones que
nadie escribio como politica.

---

## Diseno

### `safe_build_macro_snapshot` devuelve el motivo

Pasa de `MacroSnapshot | None` a `tuple[MacroSnapshot | None, str | None]`, la
misma forma tri-estado que `build_publisher` (`publishers/flags.py`), ya
revisada y en uso.

El motivo se arma donde se atrapa la excepcion, que es el unico lugar donde
existe. La alternativa —dejar la funcion como esta y que
`_fetch_macro_snapshot` llame a `build_macro_snapshot` con su propio
`try/except`— deja `safe_build_macro_snapshot` sin callers y con tres tests
unitarios ejercitando codigo muerto.

### `_fetch_macro_snapshot` mapea las tres causas

**Mantiene su firma actual** (`MacroSnapshot | None`) y escribe el motivo en
`self.macro_error` como efecto. No devuelve una tupla: hoy se consume inline
como `macro=self._fetch_macro_snapshot()` dentro de la construccion de
`WeeklyCloseData` (`orchestration/main.py`), y desarmar esa expresion para
desempaquetar dos valores es mas ruido que el que ahorra. `self.macro_error`
tampoco es estado nuevo de una clase distinta: es exactamente la forma de
`self.x_error` / `self.linkedin_error`.

| Causa | Devuelve | `self.macro_error` | Alerta |
|---|---|---|---|
| `self.fred is None` | `None` | `None` | **No** — opcional sin configurar |
| API caida / serie corta / dato rancio | `None` | motivo de `safe_build_macro_snapshot` | **Si** |
| Validador rechaza la cifra | `None` | `str(e)` del `ValidationError` | **Si** |
| Todo bien | el snapshot | `None` | — |

La ultima fila importa: `_fetch_macro_snapshot` tiene que **limpiar**
`macro_error` en el camino feliz, no solo escribirlo en los malos. Una run que
reintenta dentro del mismo proceso no puede heredar el motivo de la anterior.

### El motivo viaja por `self.macro_error`, no por el valor de retorno

**Es forzado, no preferencia.** `_fetch_macro_snapshot` se llama *dentro* de
`_fetch_weekly_close` (`orchestration/main.py`, en la construccion de
`WeeklyCloseData`), y los dos fixtures de integracion mockean
`_fetch_weekly_close` **entero**
(`tests/integration/test_orchestrator_exit_states.py:74`,
`tests/integration/test_orchestrator_persistence.py:74`). Un motivo devuelto por
esa funcion no llegaria nunca en los tests, y disparar la alerta desde
`data.macro is None` la haria saltar en **todos** ellos, rompiendo los
`assert_not_called()` que se acaban de escribir.

**Consecuencia obligatoria, y es la leccion del Task 3 de la tanda anterior:**
`macro_error` se lee en `run_weekly_close`, asi que **los dos fixtures que
construyen el orquestador con `__new__` tienen que setearlo en `None`** o mueren
con `AttributeError`.

### La alerta

Va **antes de `send_approval_request`**, junto a la de la capa LLM y la de
publicadores, por el mismo motivo que las otras dos: quien aprueba tiene que
saber que ese cierre sale con menos.

**Nombra la causa real**, que es la leccion de `f53a755` — la alerta de la capa
LLM culpaba al prompt tambien cuando lo que moria era la API, y mandaba a
revisar un prompt sano.

El lookup por contenido que endurecio el test de orden la semana pasada
(`"sale solo en"` en `test_the_degraded_run_warns_before_asking_for_approval`)
**sobrevive a una tercera alerta**: fue escrito exactamente para esto. Pero
`test_a_disabled_network_never_warns` usa `assert_not_called()` a secas, que se
acopla a *toda* alerta de la run — verificar que su fixture no dispare tambien
la de macro.

### ADR-009

- Seccion nueva con el segundo eje, con la formulacion de la decision 2.
- Fila de FRED partida en dos: sin key (no participa) / roto (degrada, con
  alerta).
- **Fila de R2 corregida** — hoy afirma un aviso que no existe.
- Las filas de X/LinkedIn no cambian de contenido, pero pasan a leerse como
  instancias del eje nuevo en vez de decisiones sueltas.
- La limitacion (c) se cierra con el principio, no con un parche.
- **Los catorce casos del inventario revisados uno por uno contra el eje
  nuevo.** Fue escribir el inventario lo que destapo las tres divergencias la
  primera vez: el valor esta en la revision, no en la tabla.

**Si aparece una divergencia nueva:** se anota y se decide por separado, salvo
que sea del mismo tipo que la de R2 —una fila que miente sobre algo que el
codigo ya hace bien—, en cuyo caso se corrige la fila y listo.

---

## Tests

**Unitarios de `safe_build_macro_snapshot`**: los tres existentes
(`tests/unit/test_macro.py:113,123,135`) se actualizan a la firma nueva, y se
verifica que el motivo llega en los dos casos de fallo, no solo que el snapshot
es `None`.

**Unitarios de `_fetch_macro_snapshot`**: las tres causas, incluida la
distincion entre `(None, None)` y `(None, motivo)`.

**Integracion** (`tests/integration/test_orchestrator_exit_states.py`):
1. FRED roto -> el cierre se publica, alerta antes de `send_approval_request`,
   con la causa en el texto.
2. FRED sin key -> se publica y **no hay alerta**.
3. La alerta nombra la causa correcta: un caso por validador rechazado y otro
   por API caida, con textos distinguibles.

**Comprobacion por mutacion de la causa.** Es obligatoria, no opcional: la
semana pasada la alerta de degradacion de publicadores nombraba la red caida con
dos ternarios, invertirlos dejaba **los 148 tests en verde**, y es el mismo
patron que la divergencia 1 de ADR-009. Con la causa cambiada por otra, algun
test tiene que fallar.

---

## Lo que este cambio no hace

- **No toca el codigo de R2.** Su silencio con R2 sin configurar es correcto
  bajo el eje nuevo; lo que esta mal es la fila del ADR.
- **No cambia el comportamiento de publicacion.** El cierre sigue saliendo sin
  bloque macro; lo unico que cambia es que ahora se avisa.
- **No agrega una bandera explicita tipo `PUBLISH_X` para FRED o R2.** La
  ausencia de la credencial ya es la senial, y una bandera para apagar algo que
  se apaga solo al no configurarlo es estado duplicado que puede divergir.
- **No decide sobre las limitaciones (a) y (b) de ADR-009**, que siguen abiertas
  y anotadas.
