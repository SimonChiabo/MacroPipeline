# El estado sobrevive a un entorno efímero — diseño

**Fecha:** 2026-08-31
**Estado:** Aprobado
**Cierra:** el punto 11 del backlog (la idempotencia de ADR-002 bajo Routines)

---

## Problema

ADR-002 cierra con una implicación de resiliencia:

> El script es idempotente: si se ejecuta dos veces el mismo día, la segunda
> ejecución detecta el `event_id` ya publicado en SQLite y termina sin publicar.

El mismo ADR decide que Routines **clona el repositorio y ejecuta en un entorno
gestionado por Anthropic**. El estado vive en `storage/state.py:16`:

```python
_DEFAULT_DB_PATH = str(Path.home() / ".macropipeline" / "state.db")
```

Un fichero en el disco del runner. Si ese entorno es efímero, cada corrida
arranca con una base recién creada por `_init_db()` —vacía, sin error y sin log
que la distinga de una primera ejecución legítima— y `is_published()` devuelve
False siempre.

**`STATE_DB_PATH` no lo arregla con ningún valor.** Apunta al mismo filesystem
efímero. Las dos escapatorias intuitivas ya están descartadas por escrito: en
blanco es peor (`sqlite3.connect("")` abre una base temporal que se borra al
cerrar), y la mitigación de ADR-007 —"si se cambia de máquina sin migrar el
archivo, se pierde el estado. Mitigado por `STATE_DB_PATH` configurable"— es
sobre **migrar de máquina**, no sobre efimeridad.

### Qué se rompe, de lo más filoso a lo más silencioso

1. **La reconciliación de fallo parcial.** `main.py:547-550` lee `prev_state`
   para saber qué red ya salió; `mark_x_published` / `mark_linkedin_published`
   escriben el `post_id` apenas se publica, justo para eso. Con estado efímero
   `prev_state` viene siempre vacío: si la corrida publica en X y revienta antes
   de LinkedIn, **el reintento vuelve a postear en X**. Duplicado cara al
   público, y toda esa maquinaria queda como código muerto.
2. **La deduplicación del mismo día** (`main.py:515`). Es lo que ADR-002 promete
   literalmente.
3. **La máquina de reintentos de ADR-009.** El `UPDATE ... WHERE status IN
   ('failed','expired')` de `mark_in_progress` existe para re-armar el lock sobre
   la fila que dejó la corrida anterior. Sin fila que sobreviva no matchea nunca.
4. **La reproducibilidad de ADR-007** — y esta muerde aunque todo salga bien
   siempre. SQLite no es solo el candado: guarda `prompt_version`, `headline`,
   `validator_approved`, las cifras y los `post_id`. Se pierde corrida tras
   corrida sin que nada falle.

### El tamaño real

`event_id = f"weekly_close_{date.today()}"` (`main.py:510`) **lleva la fecha
adentro**, así que la deduplicación entre semanas nunca pasó por `is_published()`.
La exposición es **re-ejecuciones y reintentos del mismo día** (duplicados) más
**pérdida permanente de metadatos** (silenciosa). No es "republica todo cada
semana".

### Por qué hoy no muerde — verificado el 2026-08-31

- Ningún workflow invoca `orchestration/main.py`.
- El único `cron:` del repo es `contract-tests.yml:6` (los contract tests).
- No hay config de Routines versionada: `docs/adr/002-claude-routines.md` es el
  único match en el repo.

Es **un problema sin construir, no uno roto**. Se activa el día que el pipeline
corra desatendido.

## Alcance

**Entra:** que el estado sobreviva entre corridas, la semántica de fallo del
sincronizado, y la distinción entre "primera corrida" y "estado perdido".

**No entra:** reescribir la máquina de estados de `StateDB`, cambiar el esquema,
ni tocar el formato del `event_id`.

**Tampoco entra:** alertar sobre una fila `in_progress` vieja. Es una mejora real
—hoy ADR-009:70 dice que ese caso "se salta en silencio"— pero es independiente
de este trabajo y se anota aparte para no inflarlo.

## Dos ideas descartadas, y por qué

**Un booleano que evite re-crear la base.** No puede funcionar: el booleano
tiene que vivir en algún lado. En memoria muere con el proceso; en un fichero al
lado de la base muere con el disco. Es el mismo problema un nivel más abajo — no
se puede usar estado efímero para detectar que el estado efímero se perdió.
Además `_init_db()` es `CREATE TABLE IF NOT EXISTS`, idempotente y no es el bug;
suprimirlo no recupera un dato, solo convierte una respuesta silenciosamente
equivocada en un crash. **El objetivo sí se conserva** (detectar la pérdida en
vez de tratarla como primera corrida) y se cumple gratis abajo.

**Switches de ingreso y salida alrededor de la publicación.** Ya existen: la
columna `status` es el switch (`mark_in_progress` ingreso, `mark_as_published`
salida, `is_in_progress()` lector), y la variante por red también
(`mark_x_published` + `x_already_done`). En la muerte atrapable el diseño actual
hace algo mejor que fallar y alertar: `mark_failed` limpia el switch y la
corrida siguiente reintenta salteándose la red ya publicada. Pero **todo vive en
el mismo SQLite**, así que se evapora con el disco. No sobrevive sin lo de abajo.

## La decisión: sincronizar el fichero entero por R2

Al arrancar la corrida se baja `state.db` de R2; después de cada escritura se
sube. **`StateDB` no cambia su máquina de estados ni su esquema; gana un hook de
escritura** para que el push viva donde viven las escrituras y no en seis sitios
de `main.py` que alguien pueda olvidar de mantener.

**Contra la alternativa** (un objeto por `event_id` con el estado en JSON, que
sustituiría a SQLite en el camino de deduplicación):

1. **`state.py` codifica invariantes que costaron caro**: el `WHERE status IN
   ('failed','expired')` del re-arme, la guarda de `published` en `mark_failed`,
   la reconciliación por red. Reimplementar esa máquina en JSON reexpone cada bug
   ya arreglado y hay que volver a ganarse la confianza con tests nuevos.
2. **La atomicidad por clave resuelve un problema que este pipeline no tiene**:
   un solo escritor, cadencia semanal, y la guarda de `is_in_progress`.
   Last-writer-wins sobre el fichero entero alcanza acá.
3. **El fichero entero preserva la reproducibilidad de ADR-007 gratis**: viajan
   todas las columnas. La alternativa tendría que arrastrar cada una en el JSON
   o romper esa promesa en silencio.

**Una base hosted (Turso y similares) queda descartada por credenciales**, no por
técnica: agrega un vendor y un secreto que nadie vigila, que es exactamente la
clase de deuda que llena este backlog (el token de LinkedIn, el de Telegram, y el
agujero de `check_publishers.py` con una key rotada). R2 ya está configurado.

## Diseño

**El pull va al principio de `run_weekly_close`, no en `StateDB.__init__`.** La
causa raíz del punto 13 fue que el orden de construcción hacía de lógica; no se
reintroduce. Detalle que lo hace posible: cada método de `StateDB` abre su propio
`sqlite3.connect` y no sostiene conexión, así que pisar el fichero al arrancar la
corrida funciona aunque `__init__` ya haya pasado. **Salvedad:** `_init_db` y
`_migrate_db` corrieron sobre el fichero vacío, no sobre el bajado — hay que
volver a correrlos después del pull (son idempotentes a propósito).

**El push va después de cada escritura mutante.** Son seis (`mark_in_progress`,
`mark_x_published`, `mark_linkedin_published`, `mark_as_published`, `mark_failed`,
`mark_expired`) y el fichero es de kilobytes. **No se optimiza a "solo las
críticas"**: un subconjunto elegido a mano es justo la clase de hueco que en este
repo aparece invisible con la suite en verde.

**Semántica de fallo, derivada del criterio de ADR-009** (se degrada cuando el
fallo solo cuesta contexto; se aborta cuando podría hacer que lo publicado sea
incorrecto):

| Caso | Qué hace | Por qué |
|---|---|---|
| Pull falla por transporte | Aborta **antes del lock**, con alerta | Publicar a ciegas arriesga un duplicado |
| Pull devuelve `NoSuchKey` | Sigue con base vacía, **avisa** "estado remoto ausente: primera corrida o pérdida" | Es donde aterriza la idea del booleano, en su límite honesto: los dos casos son indistinguibles |
| Push falla después de publicar | Alerta + `exit 1`, y **la alerta nombra el riesgo de duplicado** para la corrida siguiente | Es el único momento en que el estado y la realidad divergen |

El abort pre-lock no deja fila, que es la primera forma de abort de ADR-009: la
corrida siguiente reintenta sola.

## Lo que decidió Simon — 2026-08-31

**1. R2 deja de ser opcional en el camino que sincroniza. Aceptado.** Hoy está
declarado opcional y ADR-009 lo apoya en su tercer eje (lo declarado opcional no
participa, y no participar no es degradar); su fila de la tabla dice que R2 caído
degrada y no alerta. Si R2 sostiene la deduplicación, caído ya no cuesta un
snapshot: cuesta un duplicado en X. **Hay que actualizar esa fila de la tabla de
ADR-009**, y el tercer eje deja de aplicarle en el camino que sincroniza.

Ojo con el alcance: R2 sigue siendo opcional **para el snapshot de imagen**. Lo
que cambia de estatus es el estado, no la imagen. Si la tabla no distingue las
dos cosas, la fila nueva tiene que hacerlo.

**2. El sincronizado participa siempre que R2 esté configurado.** Regla uniforme,
sin bandera nueva y sin detección de entorno. Descartados el switch dedicado (otra
bandera con su semántica de valor inválido, el terreno del punto 13) y el "solo en
el camino desatendido" (una condición implícita más).

**Consecuencia buscada:** una sola base lógica compartida entre la máquina de
Simon y la nube. **Consecuencia aceptada:** una corrida local con R2 configurado
ahora escribe en el estado compartido. No es un efecto colateral, es el punto.

## Pregunta abierta

**Si Routines persiste el workspace entre corridas.** No bloquea: fija la
urgencia, no el arreglo. Routines está en research preview por decisión explícita
de ADR-002 ("puede cambiar, degradarse o desaparecer"), así que una persistencia
observada no es un contrato; y el Plan B del propio ADR-002 es GitHub Actions con
cron, efímero por construcción. En las dos ramas el estado tiene que salir del
disco local.

## Riesgos

- **El push es el punto de divergencia.** Entre publicar y subir el estado hay
  una ventana en la que una muerte dura deja la realidad adelantada al registro.
  Se estrecha (push inmediato tras cada `post_id`), no se cierra. Es el mismo
  residuo que ADR-009 ya reconoce para la muerte no atrapable.
- **La suite actual no ejercita ninguna corrida sin estado previo en disco.** El
  camino nuevo necesita test propio, y la verificación por mutación es la que ha
  rendido en este repo: con el push desactivado tiene que caer un test y solo uno.
- **La decisión 2 trae un fail-silent que este trabajo no puede cerrar.** Con la
  regla "R2 configurado → sincroniza", un workflow programado al que se le
  **olviden los secrets de R2** corre solo contra disco local, sin sincronizar y
  sin quejarse: duplicados con todo en verde. Hoy no existe ningún workflow que
  corra el pipeline, así que no hay dónde arreglarlo. **Cuando se escriba ese
  workflow, los secrets de R2 van en su paso de pre-chequeo**, igual que las seis
  keys del nightly. Queda anotado acá y en la fila de ADR-009.
