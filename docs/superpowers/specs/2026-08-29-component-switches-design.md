# Un switch por componente — diseño

**Fecha:** 2026-08-29
**Estado:** Aprobado
**Cierra:** el resto de la limitación (d) de ADR-009, y el coste del tercer eje
anotado en §Consecuencias (`bf0c643`)

---

## Problema

`MacroOrchestrator.__init__` construye cinco cosas cuyo `ValueError` no atrapa
nadie:

```
main.py:88   self.fmp      = FMPClient()          ← sin FMP_API_KEY
main.py:89   self.av       = AlphaVantageClient() ← sin ALPHA_VANTAGE_API_KEY
main.py:130  self.telegram = TelegramBot()        ← sin token o chat_id
main.py:144  x_enabled     = publisher_enabled(PUBLISH_X)       ← valor inválido
main.py:145  linkedin_enabled = publisher_enabled(PUBLISH_LINKEDIN) ←  ídem
```

La excepción sale del constructor, así que la run muere **antes** de
`run_weekly_close` y antes de que exista `event_id` (`main.py:316`): no hay
alerta, no hay fila de estado, y el único rastro es un traceback en stderr. La
semana siguiente pasa exactamente lo mismo. Es el caso invisible-y-repetible
que la regla "toda degradación alerta" existe para evitar, esta vez del lado de
los aborts.

`FREDClient`, la capa LLM y `R2Client` sí están envueltos, y para ellos el
tercer eje de ADR-009 decidió que un opcional sin configurar no participa y no
alerta.

### El segundo problema, que el primero tapa

Ese tercer eje descansa en que la no-configuración sea **deliberada**, y el
código no distingue una decisión de una key rotada o de un `.env` sin copiar.
Quedó anotado en §Consecuencias de ADR-009 el 2026-08-29:

> Cerrarlo pide declarar qué opcionales *deberían* estar activos y avisar
> cuando uno deja de estarlo; hoy eso no existe.

Este diseño es esa declaración. No es un añadido oportunista: sin ella, envolver
los cinco constructores que faltan sólo mueve el silencio de sitio.

## La decisión

**Cada componente tiene un switch booleano en el entorno. Apagado no alerta.
Encendido y fallando, sí.**

El switch reemplaza a "declarado opcional en un ADR" como señal que lee el
código. Las declaraciones de ADR-001 (el LLM fuera del path numérico) y ADR-007
(R2 opcional) no desaparecen: siguen siendo el motivo por el que un componente
**puede** apagarse, pero dejan de ser lo que el código consulta.

Consecuencia querida: "sin configurar" deja de significar silencio. El silencio
pasa a exigir un `false` tipeado por una persona.

## Alcance

**Entra:** los ocho componentes con credenciales (FMP, Alpha Vantage, FRED,
Anthropic, R2, Telegram, X, LinkedIn), el punto de decisión único, el código de
salida, la alerta del `except` general, y la reescritura de ADR-009.

**No entra, a propósito:**

- **Publicar sólo el retorno desde AV** (divergencia 4 de ADR-009). Es otro
  trabajo: toca el ETL, el rotulado y los rangos del validador. Su ausencia
  decide una línea de este diseño —FMP sin key aborta, ver más abajo— y queda
  anotada para cuando se haga.
- **El estado bajo Routines efímero** (punto 11 del backlog).
- **Un segundo canal de aviso.** Sin él, Telegram roto es irreducible.
- **Ampliar `scripts/check_publishers.py`** para que reporte switches. Sigue
  verificando credenciales de publicación y nada más.

---

## Mecanismo

### El módulo

`publishers/flags.py` deja de ser el sitio correcto: su docstring explica que
vive aparte por los dos consumidores que no se importan entre sí (el orquestador
y `scripts/check_publishers.py`), pero ya no es sólo de publicadores. Se mueve a
`src/macro_pipeline/components.py`, con tres funciones:

```python
def component_enabled(var: str) -> bool:
    """El primitivo, que levanta. Lo sigue usando check_publishers.py."""
    # ausente o vacía  -> True
    # 'true' / 'false' -> el booleano
    # cualquier otro   -> ValueError con el valor tipeado

def read_switch(var: str) -> tuple[bool, str | None]:
    """(encendido, motivo). Motivo != None significa valor inválido."""

def build_component[T](
    name: str, factory: Callable[[], T], enabled: bool
) -> tuple[T | None, str | None]:
    """(cliente, None) listo | (None, None) apagado | (None, motivo) roto."""
```

`build_component` es `build_publisher` con otro nombre: mismos tres estados,
mismo `except ValueError` estrecho —cualquier otra excepción sale y mata la run,
porque no es un componente roto sino un bug—.

### Por qué `read_switch` existe

Un valor de switch inválido no cabe en el mismo cajón que una credencial
ausente. Si `USE_FRED=maybe`, no sabemos si FRED debía correr: degradar sería
adivinar, y adivinar es exactamente lo que esa validación existe para no hacer
(el docstring de `test_publisher_flags.py` ya lo argumenta para las dos
banderas actuales).

Por eso el orquestador guarda **dos** diccionarios con políticas distintas:

- `switch_errors: dict[str, str]` — intención ilegible. **Siempre aborta.**
- `component_errors: dict[str, str]` — credencial ausente. Degrada o aborta
  según el componente.

Meterlos en un solo diccionario obligaría a llevar una bandera al lado de cada
motivo, que es el mismo diccionario partido en dos con más pasos.

### Las variables

`USE_FMP`, `USE_AV`, `USE_FRED`, `USE_ANTHROPIC`, `USE_R2`, `USE_TELEGRAM`, y
`PUBLISH_X` / `PUBLISH_LINKEDIN` **se quedan como están**.

Renombrar las dos existentes daría un solo prefijo, pero toca el `.env`, el
comentario largo de `.env.example`, `check_publishers.py` y el plan ya acordado
de aceptar el vencimiento del token de LinkedIn con `PUBLISH_LINKEDIN=false`.
Coste aceptado: dos prefijos para el mismo concepto, documentado en
`.env.example`.

Los seis nuevos se declaran **comentados** en `.env.example`, como
`STATE_DB_PATH` y `LINKEDIN_TOKEN_ISSUED`: entran en `documentadas` sin entrar
en `ejemplo`, así que no estar en el `.env` no dispara deriva — que es lo
correcto, porque ausente significa encendido y no hay nada que copiar.
`PUBLISH_X` y `PUBLISH_LINKEDIN` siguen sin comentar, como hoy.

### La clasificación necesario/opcional no se codifica como dato

Un `NECESARIOS = frozenset({"fmp", "telegram"})` sería mentira: para X y
LinkedIn la necesidad no es una propiedad de cada uno sino del par —"al menos
una viva"—, que es lo que la guarda pre-lock ya expresa. `build_component` se
queda tonto y la política vive explícita en el punto de decisión.

### La matriz, igual para los ocho

| switch | credencial | qué pasa |
|---|---|---|
| `false` | — | no se construye, log, **sin alerta**. Si impide publicar: aborta pre-lock, en silencio |
| ausente o `true` | presente | normal |
| ausente o `true` | ausente | **alerta**, y degrada o aborta según el componente |
| valor inválido | — | **alerta y aborta**, sea cual sea el componente |

---

## El punto de decisión

### El constructor deja de poder morir por configuración

Ocho llamadas a `build_component` y nada más. Lo único que sigue matando
`__init__` es lo que no es una credencial: `StateDB`, `PlaywrightEngine`, o
cualquier excepción que no sea `ValueError`.

### Dónde va

Ocupa el lugar exacto de la guarda de publicadores actual (`main.py:336-356`),
que pasa a ser una de sus ramas:

```
event_id = …
1. guarda de duplicados (is_published)      → return 0
2. PUNTO DE DECISIÓN  ← nuevo, absorbe la guarda de publicadores
3. guarda de lock (is_in_progress)          → return 0
4. mark_in_progress …
```

El duplicado va primero a propósito: si ese cierre ya salió, no hay nada que
reportar y alertar sería ruido sobre una run que no iba a hacer nada.

### Las ramas, en orden

1. **`switch_errors` no vacío** → alerta con las variables y sus valores,
   `return 1`. Si no hay canal, log en su lugar (ver abajo).
2. **Telegram apagado** → log `info`, `return 0`. Es la pausa deliberada del
   pipeline entero, y usa la forma que ADR-009 ya acepta: aborta antes del lock,
   en silencio, sin fila.
3. **Telegram roto** → `logger.error("telegram_unavailable_aborting", …)` con
   el cuadro completo de motivos, `return 1`, sin fila, sin intentar alertar.
   Caso irreducible.
4. **Abortos por componente** → FMP roto, o las dos redes rotas → alerta +
   `return 1`, sin tocar el estado. Las dos redes apagadas → `return 0` en
   silencio, como hoy.
5. **Degradaciones de arranque** → **una sola** alerta con una línea por
   componente y su consecuencia, y el pipeline sigue.

**El orden de las dos primeras es obligatorio, no estético.** `read_switch`
devuelve `(False, motivo)` ante un valor inválido, así que con
`USE_TELEGRAM=maybe` el cliente no se construye y `self.telegram` queda en
`None` — indistinguible de un apagado deliberado. Si la rama de "Telegram
apagado" fuera primero, ese caso saldría con `return 0` **en silencio**: el
mismo agujero invisible que este trabajo existe para cerrar, reintroducido por
el orden de dos `if`. Los `switch_errors` se miran antes que nada porque
significan que no se pudo leer la intención del operador, y ninguna otra rama
puede decidir sin esa intención.

Y ese caso concreto —el switch inválido es el de Telegram— es el único de la
rama 1 que no puede alertar, porque el cliente no existe. Cae en el mismo
tratamiento que Telegram roto: log nombrado con el cuadro completo y `return 1`.

### Por qué FMP aborta y AV degrada

Por el criterio de ADR-009: se aborta cuando el fallo impide publicar.

- **FMP roto → aborta.** Su fallback es Alpha Vantage, y la ruta de AV hoy no
  puede publicar: pide `SPY` (~765) donde FMP pide `^GSPC` (~7.657), y
  `sp500_close_min: 2000` hace abortar al validador (divergencia 4). Sin ruta
  viva, abortar en el punto de decisión con el motivo real es mejor que morir
  tres fases después con un rechazo del validador que no nombra la causa.
- **AV roto → degrada.** No impide publicar mientras FMP funcione.

**Anotación por adelantado:** el día que se haga "publicar sólo el retorno desde
AV", FMP sin key deja de ser abort y pasa a ser degradación. Es una línea, y
queda escrito antes de que pase para no volver a deducirlo.

**Consecuencia aceptada:** AV sin key alerta, y lo que anuncia es que se quedó
sin una red de seguridad que hoy tampoco publicaría. El texto de la alerta lo
dice con todas las letras en vez de sugerir que había un fallback sano.

### Qué alerta se muda, y por qué sólo una

**`publisher_degraded` (`main.py:511`) se muda al punto de decisión.** Su causa
siempre es de arranque (`x_error` / `linkedin_error` se escriben en `__init__`),
así que dejarla donde está la haría sonar dos veces.

Las otras tres se quedan, porque son de ejecución y no de arranque:

- `macro_degraded` sólo se dispara con `macro_error` cargado, y FRED sin key lo
  deja en `None` — eso ya es así hoy y no se toca.
- La alerta de la capa LLM nace de una API caída o de un rechazo del validador.
- La de R2 nace de una subida fallida.

La propiedad que ADR-009 le pedía al aviso de publicadores —llegar antes de
pedir aprobación, para que quien aprueba sepa con qué sale el cierre— se
conserva y se refuerza: el punto de decisión es todavía más temprano.

### Código de salida

`run_weekly_close` pasa de `-> None` a `-> int`:

- `0` — corrió: publicó, o deliberadamente no publicó.
- `1` — abortó por configuración rota.

`__main__` hace `sys.exit(orchestrator.run_weekly_close())`. Las excepciones
inesperadas siguen propagando con traceback y salida ≠ 0 por el camino de
Python. La regla: **salida controlada → entero, bug → excepción.**

Hace falta porque con el constructor total el caso de Telegram roto saldría con
0 si no. Hoy sale ≠ 0 sólo porque el traceback mata el proceso. `mypy --strict`
obliga a que todos los `return` del método sean explícitos.

### El `except` general

Queda: alerta si hay canal → `mark_failed` → `raise`.

Sin riesgo de avisar dos veces: las degradaciones alertan y siguen sin llegar
nunca ahí, y los aborts del punto de decisión hacen `return`, no `raise`. Cierra
la mitad en caliente de la misma invisibilidad: hoy AV caída, una cifra fuera de
rango o un render fallido dejan una fila `failed` que nadie mira.

---

## ADR-009

Es parte del trabajo, no su epílogo.

- **El tercer eje se reescribe.** De "un componente *declarado opcional* que no
  está configurado no participa" a: *un componente **apagado por su switch** no
  participa, y no participar no es degradar; un componente encendido al que le
  faltan credenciales es un fallo.*
- **§Consecuencias**: la frase sobre declarar qué opcionales deberían estar
  activos pasa de "hoy eso no existe" a describir el switch.
- **La limitación (d) se cierra**, y deja como límite nuevo y explícito el único
  caso irreducible: Telegram encendido y sin credencial no puede avisar de sí
  mismo, y lo único que cruza el borde del proceso es el código de salida.
- **La tabla de catorce filas**: cambian las tres de "sin key" de FRED,
  Anthropic y R2; entran filas para FMP, AV, Telegram y switch inválido.
- **La divergencia 4** se anota con la línea que cambiará cuando se haga el
  retorno desde AV.

---

## Tests

Cada test justificado por la mutación que lo hace caer, que es la convención del
repo desde el 2026-08-26.

**`tests/unit/test_components.py`** (renombra `test_publisher_flags.py`):
`component_enabled` (los casos que ya existen), `read_switch` (válido e
inválido) y los tres estados de `build_component`.

**Constructor total**: parametrizado por componente — borrar su credencial no
levanta y deja el motivo en `component_errors`.

**Punto de decisión**, con `StateDB` real, como
`tests/integration/test_orchestrator_exit_states.py`:

| caso | espera |
|---|---|
| FMP roto | una alerta, `1`, **sin fila** |
| Telegram roto | `1`, `send_alert` **no** se llama, sin fila, log con el cuadro completo |
| Telegram apagado | `0`, silencio |
| switch inválido | alerta, `1` |
| `USE_TELEGRAM` inválido | `1`, **sin** alerta, log nombrado — no se lee como apagado |
| dos degradaciones | **una sola** llamada a `send_alert`, que nombra las dos |
| todo sano | el punto de decisión no alerta |

**Las mutaciones que los justifican**, cada una tiene que hacer caer un test
nombrado y sólo ése:

- Fijar el texto de la alerta en vez de componerlo → cae el de dos
  degradaciones.
- Clasificar FMP como degradación → cae el de FMP.
- Quitar la alerta del `except` general → cae su test.
- Hacer que el switch ausente signifique apagado → caen varios.
- Poner la rama de "Telegram apagado" antes que la de `switch_errors` → cae el
  de `USE_TELEGRAM` inválido, y sólo ése.

---

## Ficheros

- `src/macro_pipeline/components.py` — nuevo; `publishers/flags.py` se borra
- `src/macro_pipeline/orchestration/main.py` — constructor, punto de decisión,
  `-> int`, `__main__`
- `scripts/check_publishers.py` — sólo el import
- `.env.example` — seis switches comentados y el comentario del doble prefijo
- `docs/adr/009-degradation-policy.md`
- `tests/unit/test_publisher_flags.py` → `tests/unit/test_components.py`, más
  casos en los tests del orquestador y en `tests/integration/`

## Riesgo

El mismo del 2026-08-28: el constructor se reescribe casi entero y una guarda se
muda de sitio, así que el diff mezcla cambio real con reindentado. Los controles
que funcionaron entonces:

- contar los hunks del diff completo,
- cuadrar el conteo de líneas,
- diffear el bloque movido **desindentado**, porque `git diff -w` esconde
  también ruido fuera del bloque,
- y correr todo con `./.venv/Scripts/python.exe`, no con el Python global de la
  máquina.
