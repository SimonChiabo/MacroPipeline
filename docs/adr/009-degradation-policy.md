# ADR-009: Política de degradación — qué fallo degrada y qué fallo aborta

**Estado:** Aceptado (documenta lo vigente y deja decididas tres divergencias, cuyo arreglo queda pendiente, 2026-08-25)
**Fecha:** 2026-08-25
**Decisores:** Simon Chiabo

---

## Contexto

El pipeline depende de ocho componentes externos —FMP, Alpha Vantage, FRED, la
API de Anthropic, Playwright, Telegram, R2, y las APIs de X y LinkedIn— y cada
uno puede fallar por su cuenta. Ante cada fallo hay dos respuestas posibles:
**degradar** (publicar igual, con menos) o **abortar** (no publicar esta semana).

La pregunta que motivó este ADR fue "¿en qué momento se decidió que si algo
falla se publica igual?", y la respuesta honesta es que **nunca hubo un
momento**. Había cinco decisiones locales tomadas en commits distintos, cada una
razonable por separado y ninguna escrita como política:

- FRED caído o rancio deja `macro=None` (`orchestration/main.py:90-111`,
  `data/macro.py:129-144`).
- El validador rechazando el titular publica un bloque genérico y avisa
  (`orchestration/main.py:305-326`, commit `2eb1a6c`).
- R2 sin configurar publica sin snapshot remoto (`orchestration/main.py:72-77`,
  ADR-007).
- `send_alert` fallando devuelve un bool y nunca levanta
  (`telegram/bot.py:98-120`).
- Solo el Mock Data aborta (`orchestration/main.py:136-141`).

Y dos comportamientos que no eran decisiones sino accidentes: la API de
Anthropic caída cayendo en `FALLBACK_HEADLINE` (`llm/client.py:127-129`), y la
bandera `publishers_ready` que hasta `5ba7997` marcaba como publicado un evento
que no se publicó en ninguna red.

Con la respuesta repartida en cinco docstrings, nadie —ni su autor— podía
contestar la pregunta sin releer el orquestador entero.

---

## Decisión

### El criterio

**Se degrada cuando el fallo solo cuesta contexto. Se aborta cuando el fallo
podría hacer que una cifra publicada sea incorrecta, o cuando impide publicar.**

El criterio se apoya en la invariante de ADR-001 —**una cifra conserva el
indicador del que salió**— y en su corolario: los índices son el contenido
principal, todo lo demás es contexto. Un cierre semanal sin bloque macro sigue
siendo un cierre semanal correcto; un cierre semanal con el nivel del
instrumento equivocado es una publicación financiera errónea, y una publicación
errónea no se puede corregir una vez emitida.

### El segundo eje: qué estado deja un abort

"Aborta" no es una sola cosa. Un abort que no toca el estado se reintenta solo;
uno que deja la fila trabada exige intervención manual. La política fija **tres**
formas de terminar sin publicar:

| Forma | Estado que deja | Consecuencia |
|---|---|---|
| Aborta antes del lock | Ninguna fila | La próxima run reintenta sola |
| Aborta con estado terminal | `failed` o `expired` | La próxima run reintenta; el motivo queda registrado |
| Aborta trabado | `in_progress` | **No se acepta**: el reintento del mismo `event_id` se salta en silencio |

Un abort **nunca** debe dejar la fila en `in_progress`. Es el mismo razonamiento
del fix de `publishers_ready` (`5ba7997`): si no se publicó, el estado no debe
afirmar lo contrario ni impedir el reintento.

### La política, por componente

| Componente | Fallo | Política | Estado que deja |
|---|---|---|---|
| FRED (bloque macro) | Sin key, API caída, serie corta, dato rancio o fuera de rango | **Degrada** — `macro=None` | — |
| FMP (índices) | API caída | **Degrada** a Alpha Vantage… que hoy aborta (ver divergencia 4) | — |
| Alpha Vantage (índices) | API caída | **Aborta** — sin fuente de datos real no se publica | `failed` |
| Mock Data | `ALLOW_MOCK_DATA=false` | **Aborta** — cifras sintéticas no se publican | `failed` |
| Cálculo del retorno | Menos de 6 filas, o sin dato de hace 5 días hábiles | **Aborta** | `failed` |
| Anthropic (generador) | API caída | **Degrada** — bloque genérico, con alerta que nombre la causa real | — |
| Anthropic (validador) | Rechazo del titular | **Degrada** — bloque genérico + alerta | — |
| `ValidationEngine` | Cifra fuera de rango de plausibilidad | **Aborta** — es la última defensa de la invariante de ADR-001 | `failed` |
| Playwright (render) | Plantilla ausente o render fallido | **Aborta** — no hay imagen que publicar | `failed` |
| Telegram (aprobación) | Envío fallido | **Aborta** — ADR-004 exige aprobación humana | `failed` |
| Telegram (aprobación) | Timeout de 1h | **Aborta** | `expired` |
| Telegram (`send_alert`) | Envío fallido | **Degrada** — devuelve `False`, nunca levanta | — |
| R2 | Sin configurar **o** subida fallida | **Degrada** — sin snapshot remoto, con aviso | — |
| X / LinkedIn | Credenciales ausentes | **Aborta** antes del lock, con alerta | Ninguna fila |
| X / LinkedIn | Publicación fallida | **Aborta** — `post_id` de lo que sí salió persistido | `failed` |

La columna de estado dice lo que la política exige, **no lo que el código hace
hoy**. El único abort que hoy sale realmente antes del lock es el de
credenciales de publicación (`orchestration/main.py:229`, y `mark_in_progress`
está en la línea 248). El único que hoy deja un estado terminal correcto es el
timeout de Telegram, que llama a `mark_expired` (`orchestration/main.py:344`).
Los otros siete aborts de la tabla —incluidos los tres de la fase de datos, que
ocurren en la línea 261— salen por excepción y dejan la fila trabada en
`in_progress`: es la divergencia 3, y no alcanza solo a la fase de publicación.

Tres casos merecen la razón explícita, porque son los que se decidieron hoy:

**La API de Anthropic caída degrada.** El bloque genérico lleva las cifras
reales —las pone el pipeline, no el modelo—, así que lo que se pierde es
redacción, no información. ADR-001 define la capa LLM como auxiliar y esto es
la consecuencia de esa definición. **Pero la alerta tiene que decir la verdad:**
hoy dice "el validador rechazó el titular" también cuando lo que murió fue la
API, porque el `except` del validador devuelve `approved=False` igual que un
rechazo real (`llm/validator.py:151-155`).

**R2 fallando degrada igual que R2 sin configurar.** ADR-007 ya dice que "el
pipeline funciona sin R2, solo sin snapshots remotos". Que el componente
declarado opcional sea fatal *justo cuando está configurado* es la política al
revés, y hoy además aborta en el peor momento: después de que el humano aprobó
y antes de publicar en ninguna red.

**Toda excepción marca `failed`.** Es lo que hace alcanzable la reconciliación
parcial que el orquestador promete en su docstring (`orchestration/main.py:42-43`).
El coste aceptado: un reintento tras una publicación a medias se apoya en los
`post_id` persistidos para no republicar en X lo que ya salió. Ese mecanismo ya
existe (`orchestration/main.py:251-253`, `363-383`); hoy es código muerto.

---

## Consecuencias

**Positivas:**

- La pregunta "¿qué pasa si falla X?" tiene una respuesta en un solo lugar, y
  un criterio que permite responderla para un componente que todavía no existe.
- Las tres divergencias que el inventario destapó pasan a ser trabajo con
  nombre, en vez de comportamiento que nadie decidió.
- La regla "un abort nunca deja `in_progress`" es verificable con un test por
  cada camino de salida, cosa que "degrada o aborta" a secas no era.

**Negativas:**

- Degradar es publicar con menos, y el lector no ve la diferencia: un bloque
  genérico se parece bastante a un cierre normal. **Toda degradación tiene que
  alertar**, o se vuelve invisible y se repite semanas. Es lo que pasó con el
  reetiquetado que encontró el contract test (ADR-001): cuatro gates en verde y
  el texto genérico publicándose 9 de cada 10 semanas.
- La política no se hace cumplir sola. Hoy vive en esta tabla y en el código;
  nada impide que la próxima decisión local vuelva a divergir.

**Lo que esta política no cubre:** un componente que falla *silenciosamente*
devolviendo datos plausibles pero equivocados. Ninguna rama de degradar/abortar
se activa, porque desde el pipeline no hay fallo. Ese riesgo lo cubren los
contract tests (ADR-008) y los rangos de plausibilidad, no este ADR.

---

## Divergencias entre esta política y el código (2026-08-25)

Las cuatro están verificadas contra el código en la fecha del ADR. Las tres
primeras son trabajo pendiente; la cuarta es una consecuencia aceptada.

**1. La alerta miente cuando muere la API de Anthropic.**
`orchestration/main.py:316-321` emite siempre "El validador rechazó el titular
generado". Con la API caída el generador devuelve `FALLBACK_HEADLINE`
(`llm/client.py:127-129`) y el validador devuelve `approved=False` con
`API_ERROR_REASON_PREFIX` (`llm/validator.py:151-155`): la degradación es
correcta, el diagnóstico no. Las constantes para distinguirlo ya existen y son
públicas justamente para esto — el contract test las usa.

**2. R2 configurado y fallando aborta después de la aprobación humana.**
`orchestration/main.py:352-354` llama a `upload_image` sin protección y
`storage/r2_client.py:68-70` levanta `R2ClientError`. La excepción sale por el
`except` general y la run muere con el humano ya habiendo aprobado y sin nada
publicado. Debe degradar y avisar.

**3. Toda excepción posterior a `mark_in_progress` deja la fila trabada.**
`mark_x_published` y `mark_linkedin_published` (`storage/state.py:135`, `144`)
actualizan solo la columna del `post_id`, **no `status`**. El `except` general
del orquestador (`orchestration/main.py:404-408`) loggea y re-levanta sin tocar
el estado. Resultado: la fila queda en `in_progress` para siempre y el reintento
del mismo `event_id` muere en el guard de `orchestration/main.py:242-246` con un
`pipeline_already_running_skipping`. **La idempotencia parcial que promete el
docstring de `orchestration/main.py:42-43` es hoy inalcanzable**, y el código de
reconciliación de las líneas 251-253 y 363-383 nunca se ejecuta.

No es un problema de la fase de publicación: el lock se toma en la línea 248 y
la fase de datos empieza en la 261, así que el abort por Alpha Vantage caída, el
del Mock Data bloqueado y el de datos insuficientes para el retorno también
dejan la fila trabada. La única salida que hoy marca bien es el timeout de
Telegram (`mark_expired`, línea 344).

Un detalle del arreglo, para no tropezar con él: `mark_in_progress` es
`INSERT OR IGNORE` (`storage/state.py:105-112`), así que sobre una fila que ya
existe con estado `expired` o `failed` **el lock no se re-arma** — el reintento
corre sin lock. Marcar `failed` en el `except` cierra el bloqueo pero deja esa
segunda mitad abierta.

**4. Consecuencia aceptada: la ruta de Alpha Vantage ya no puede publicar.**
El ETL pide a FMP `^GSPC` (índice, ~7.657) y cae a AV con `SPY` (ETF, ~765). El
nivel se guarda venga de donde venga y se publica rotulado "SP500: Cierre", así
que la ruta de fallback publicaría el número del instrumento equivocado — la
misma invariante de ADR-001, esta vez en el ETL. El control son los rangos de
`validators/rules.yaml:16-19` (`sp500_close_min: 2000`) aplicados en
`validate_weekly_close`: con SPY a 765 el validador levanta y la run aborta.

Por eso la tabla dice que FMP "degrada a AV… que hoy aborta". Es deliberado:
mejor no publicar que publicar el instrumento equivocado. **Publicar solo el
retorno desde AV** —que es invariante de escala entre el índice y su ETF, y
sería una degradación real en vez de un abort— quedó propuesto y sin hacer.

---

## Relación con otros ADR

- **ADR-001** aporta el criterio: la invariante de las cifras es lo que separa
  degradar de abortar.
- **ADR-004** es la razón de que el fallo de Telegram en la fase de aprobación
  aborte y no degrade: sin humano no hay publicación durante el primer mes.
- **ADR-007** declara R2 opcional; la divergencia 2 es el código no cumpliendo
  esa declaración.
- **ADR-008** cubre el hueco que esta política deja: los fallos silenciosos.
- **ADR-002** queda tocado de refilón: la idempotencia que promete depende de
  que el fichero SQLite sobreviva entre runs, cosa que un entorno efímero de
  Routines no garantiza. Hoy no muerde —nada corre el orquestador en un
  schedule— pero la tabla de estados de este ADR no significa nada si el estado
  no persiste.
