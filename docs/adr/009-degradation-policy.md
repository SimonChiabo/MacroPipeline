# ADR-009: Política de degradación — qué fallo degrada y qué fallo aborta

**Estado:** Aceptado (2026-08-25). Las tres divergencias que documentaba se
arreglaron el mismo dia; la seccion final las conserva como registro.
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

- FRED caído o rancio deja `macro=None` (`orchestration/main.py`, `_fetch_macro_snapshot`,
  `data/macro.py:safe_build_macro_snapshot`).
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
uno que deja la fila trabada exige intervención manual. La política fija
**cuatro** formas de terminar sin publicar:

| Forma | Estado que deja | Consecuencia |
|---|---|---|
| Aborta antes del lock | Ninguna fila | La próxima run reintenta sola |
| Aborta antes del lock, en silencio | Ninguna fila | La próxima run vuelve a no publicar, también en silencio, hasta que se vuelva a encender la bandera |
| Aborta con estado terminal | `failed` o `expired` | La próxima run reintenta; el motivo queda registrado |
| Aborta trabado | `in_progress` | **No se acepta**: el reintento del mismo `event_id` se salta en silencio |

Un abort **nunca** debe dejar la fila en `in_progress`. Es el mismo razonamiento
del fix de `publishers_ready` (`5ba7997`): si no se publicó, el estado no debe
afirmar lo contrario ni impedir el reintento.

### El tercer eje: lo declarado opcional no degrada

La formulación obvia —*un componente sin configurar no alerta*— **no sobrevive
al inventario**, y por eso queda escrita como descartada: para X y LinkedIn una
credencial faltante **sí** alerta. Con el eje puesto en "credencial presente"
eso sería una contradicción.

No lo es:

> **Un componente declarado opcional, cuando no está configurado, no participa
> — y no participar no es degradar.** Un componente necesario al que le faltan
> credenciales es un fallo.

"Declarado opcional" no es una opinión sobre cada componente: **ADR-007 lo dice
de R2, y del bloque macro lo dice el criterio con el que abre esta Decisión**
—los índices son el contenido principal y un cierre semanal sin bloque macro
sigue siendo un cierre semanal correcto—. Publicar, en cambio, es el propósito
del pipeline. El eje se apoya en declaraciones que ya existen, y por eso es
verificable en vez de retórico — de un componente nuevo se puede preguntar "¿lo
declara opcional algún ADR?" y la respuesta no depende de quién conteste.

De acá sale que **FRED sin key y R2 sin configurar son la misma cosa**: no
participan, y no gastan una alerta. Los dos fallando *estando configurados* sí
alertan.

Una red apagada por bandera termina en el mismo silencio, pero **no por este
eje**: publicar no lo declara opcional ningún ADR, y una red apagada no está sin
configurar sino apagada a propósito. Llega ahí por su propio argumento —*Un
apagado deliberado no alerta*, más abajo—, y que los dos caminos coincidan es lo
que hace que la tabla no tenga que distinguirlos.

### La política, por componente

| Componente | Fallo | Política | Estado que deja |
|---|---|---|---|
| FRED (bloque macro) | Sin key | **No participa** — opcional sin configurar (criterio de este ADR), sin alerta | — |
| FRED (bloque macro) | API caída, serie corta, dato rancio o cifra fuera de rango | **Degrada** — `macro=None`, con alerta que nombra la causa | — |
| FMP (índices) | API caída | **Degrada** a Alpha Vantage… que hoy aborta (ver divergencia 4) | — |
| Alpha Vantage (índices) | API caída | **Aborta** — sin fuente de datos real no se publica | `failed` |
| Mock Data | `ALLOW_MOCK_DATA=false` | **Aborta** — cifras sintéticas no se publican | `failed` |
| Cálculo del retorno | Menos de 6 filas, o sin dato de hace 5 días hábiles | **Aborta** | `failed` |
| Anthropic (generador) | API caída | **Degrada** — bloque genérico, con alerta que nombre la causa real | — |
| Anthropic (validador) | Rechazo del titular | **Degrada** — bloque genérico + alerta | — |
| `ValidationEngine` | Cifra **del cierre semanal** fuera de rango de plausibilidad | **Aborta** — es la última defensa de la invariante de ADR-001 | `failed` |
| Playwright (render) | Plantilla ausente o render fallido | **Aborta** — no hay imagen que publicar | `failed` |
| Telegram (aprobación) | Envío fallido | **Aborta** — ADR-004 exige aprobación humana | `failed` |
| Telegram (aprobación) | Timeout de 1h | **Aborta** | `expired` |
| Telegram (`send_alert`) | Envío fallido | **Degrada** — devuelve `False`, nunca levanta | — |
| R2 | Sin configurar | **No participa** — opcional sin configurar (ADR-007), sin alerta | — |
| R2 | Subida fallida | **Degrada** — sin snapshot remoto, con aviso | — |
| X / LinkedIn | Credenciales ausentes en **una** de las dos | **Degrada** — publica en la otra, con alerta antes de pedir aprobación | `published` |
| X / LinkedIn | Credenciales ausentes en **las dos** | **Aborta** antes del lock, con alerta | Ninguna fila |
| X / LinkedIn | Apagada con `PUBLISH_X` / `PUBLISH_LINKEDIN` en `false` | **No es un fallo** — no se construye, no publica y **no alerta** | — (Ninguna fila si están apagadas las dos) |
| X / LinkedIn | Publicación fallida | **Aborta** — `post_id` de lo que sí salió persistido | `failed` |

La columna de estado se cumple desde el 2026-08-25. Cuando se escribió esta
tabla solo dos aborts la respetaban —el de credenciales de publicación, que sale
antes del lock, y el timeout de Telegram, que llama a `mark_expired`—; los otros
siete salían por excepción y dejaban la fila trabada en `in_progress` (era la
divergencia 3). Hoy el `except` general marca `failed`, y cada camino de salida
tiene un test que verifica el estado que deja
(`tests/integration/test_orchestrator_exit_states.py`).

Cinco casos merecen la razón explícita, porque son los que se decidieron hoy:

**La API de Anthropic caída degrada.** El bloque genérico lleva las cifras
reales —las pone el pipeline, no el modelo—, así que lo que se pierde es
redacción, no información. ADR-001 define la capa LLM como auxiliar y esto es
la consecuencia de esa definición. **Pero la alerta tiene que decir la verdad**,
y decía "el validador rechazó el titular" también cuando lo que murió fue la
API, porque el `except` del validador devuelve `approved=False` igual que un
rechazo real (`llm/validator.py:151-155`). Arreglado en `f53a755`.

**R2 configurado y fallando degrada, no aborta.** ADR-007 ya dice que "el
pipeline funciona sin R2, solo sin snapshots remotos". Que el componente
declarado opcional sea fatal *justo cuando está configurado* es la política al
revés, y además abortaba en el peor momento: después de que el humano aprobó y
antes de publicar en ninguna red. Arreglado en `d187d81`. R2 **sin configurar**
es el otro caso y no es el mismo: por el tercer eje no participa, así que no
degrada y su fila no lleva aviso.

**Toda excepción marca `failed`.** Es lo que hace alcanzable la reconciliación
parcial que el orquestador promete en su docstring (`MacroOrchestrator`, «Idempotencia parcial»).
El coste aceptado: un reintento tras una publicación a medias se apoya en los
`post_id` persistidos para no republicar en X lo que ya salió. Ese mecanismo ya
existía y era código muerto; desde `1dc6fac` se ejecuta, y hay un test que lo
recorre de punta a punta con un `StateDB` real.

**Una red de publicación caída degrada; solo aborta si no queda ninguna.**
Es lo que el criterio ya predice: que falte la credencial de LinkedIn no hace
que ninguna cifra sea incorrecta y no impide publicar — impide publicar *en una
red*. Hasta el 2026-08-25 una sola bandera `publishers_ready` cubría los dos
clientes, así que un `ValueError` de cualquiera de las seis credenciales apagaba
las dos. La run degradada termina en `published`, no en `failed`: fue un éxito
con menos alcance. Se descartó publicar en una red y dejar la fila `failed` para
reintentar la otra, porque el `event_id` lleva la fecha y el reintento solo
reconcilia el mismo día: al día siguiente republicaría en la red que sí había
salido.

**Un apagado deliberado no alerta.** `PUBLISH_X=false` y `PUBLISH_LINKEDIN=false`
apagan una red a propósito: no se construye el cliente, no se publica, y no se
manda nada a Telegram. No es una excepción a la regla de que toda degradación
alerta: es que apagar una red no es una degradación. La regla existe porque el
lector no distingue una publicación degradada de una normal, y una decisión
propia no necesita que se la avisen. Un aviso semanal por una pausa que pediste
es el ruido que hace que se deje de leer el aviso que importa. La distinción que
queda fijada es **si llega una alerta, es porque algo se rompió**.

---

## Consecuencias

**Positivas:**

- La pregunta "¿qué pasa si falla X?" tiene una respuesta en un solo lugar, y
  un criterio que permite responderla para un componente que todavía no existe.
- Las tres divergencias que el inventario destapó pasaron a ser trabajo con
  nombre en vez de comportamiento que nadie decidió, y de ahí a arregladas el
  mismo día. Escribir el inventario fue lo que las encontró.
- La regla "un abort nunca deja `in_progress`" es verificable con un test por
  cada camino de salida, cosa que "degrada o aborta" a secas no era. Esos tests
  existen desde `1dc6fac` (`tests/integration/test_orchestrator_exit_states.py`).

**Negativas:**

- Degradar es publicar con menos, y el lector no ve la diferencia: un bloque
  genérico se parece bastante a un cierre normal. **Toda degradación tiene que
  alertar**, o se vuelve invisible y se repite semanas. Es lo que pasó con el
  reetiquetado que encontró el contract test (ADR-001): cuatro gates en verde y
  el texto genérico publicándose 9 de cada 10 semanas.
- La política no se hace cumplir sola. Hoy vive en esta tabla y en el código;
  nada impide que la próxima decisión local vuelva a divergir.

**Cuatro limitaciones que hasta ahora no estaban escritas en ningún lado.
Ninguna se arregla acá: la (c) se cerró con el código del 2026-08-25 y queda
como registro, y las otras tres siguen abiertas:**

**(a) La alerta de degradación promete de más en dos casos.** Va antes de pedir
aprobación (a propósito: quien aprueba tiene que saber que el cierre sale en
una sola red), así que dice "el cierre se publica igual si lo aprobás" incluso
cuando el humano después rechaza o la aprobación expira, y también cuando la
red viva ya había publicado más temprano el mismo día — en ese caso la run no
publica nada y sin embargo la alerta anunció una publicación degradada. La
mitad accionable del aviso —la credencial está rota, corré
`check_publishers.py`— es verdadera en todos los casos. La misma propiedad la
tiene la alerta de la capa LLM.

**(b) Los `post_id` persistidos, en los que se apoya la reconciliación
parcial, se pueden perder.** Si `mark_x_published` falla después de que
`post_tweet` salió bien, el tweet existe y el registro de que existe no, así
que el reintento republica en X. Esta política se apoya en los `post_id` sin
decir que tienen esa ventana.

**(c) La regla "toda degradación alerta" no se cumplía para FRED.** — *Cerrada
el 2026-08-25.* El bloque macro se caía en silencio por tres caminos que
`_fetch_macro_snapshot` no distinguía. Dos de ellos —la API/serie/frescura y el
validador rechazando la cifra— son fallos y ahora alertan con la causa real; el
tercero, FRED sin key, no es un fallo sino un opcional sin configurar, y sigue
en silencio por el tercer eje de arriba.

Lo que la pregunta destapó fue más grande que FRED: la respuesta correcta ya se
cumplía en R2 y en los publicadores por decisiones locales que nadie había
escrito como política, y dos filas de esta misma tabla mentían sobre el código
—la de R2 afirmaba un aviso que no se manda, y la del `ValidationEngine`
prometía un abort para la cifra macro que el código, con razón, degrada—. Es el
mismo patrón que motivó este ADR.

**(d) A un componente necesario sin credenciales no se entera nadie.** El eje de
arriba dice que un componente necesario al que le faltan credenciales es un
fallo, y para X y LinkedIn el código lo trata como tal: alerta —y si no queda
ninguna red viva, aborta antes del lock—. Para FMP, Alpha Vantage y Telegram —y
para un `PUBLISH_X` o un `PUBLISH_LINKEDIN` con un valor que no es `true` ni
`false`— el `ValueError` sale de `MacroOrchestrator.__init__` sin que nadie lo
atrape: la run muere antes de entrar en `run_weekly_close`, así que no hay
alerta, no hay fila de estado y la semana siguiente vuelve a pasar lo mismo,
también en silencio. Es el caso invisible-y-repetible que la regla "toda degradación
alerta" existe para evitar, esta vez del lado de los aborts. Avisar no sería
gratis: `FMPClient` y `AlphaVantageClient` se construyen antes que
`TelegramBot`, así que cuando revienta uno de ellos el canal de aviso todavía no
existe, y cuando la credencial que falta es la del propio Telegram no hay canal
ninguno — salvo el de las banderas, que revienta después de que `TelegramBot` ya
existe y para el que avisar sale casi gratis. **Anthropic no entra en esta
lista, y su caso es otro:** sin `ANTHROPIC_API_KEY` el constructor también
levanta, pero la tabla de arriba dice que la capa LLM **degrada**
—`generate_headline` atrapa la API caída y publica el bloque genérico con las
cifras reales—, así que lo que diverge ahí no es que falte el aviso sino que el
constructor trate como fatal a un componente que esta política declara
degradable; envolverlo como este mismo `__init__` ya envuelve a `FREDClient` y a
`R2Client` no necesitaría alerta ninguna. **Queda sin decidir.**

**Lo que esta política no cubre:** un componente que falla *silenciosamente*
devolviendo datos plausibles pero equivocados. Ninguna rama de degradar/abortar
se activa, porque desde el pipeline no hay fallo. Ese riesgo lo cubren los
contract tests (ADR-008) y los rangos de plausibilidad, no este ADR.

---

## Divergencias entre esta política y el código

Las cuatro se verificaron contra el código el 2026-08-25, el día del ADR. Las
tres primeras eran trabajo pendiente y se arreglaron ese mismo día; quedan
escritas porque el modo de fallo que cada una describe es más fácil de volver a
introducir que de encontrar. La cuarta sigue siendo una consecuencia aceptada.

**1. La alerta mentía cuando moría la API de Anthropic.** — *Cerrada en
`f53a755`.*
`orchestration/main.py` emitía siempre "El validador rechazó el titular
generado". Con la API caída el generador devuelve `FALLBACK_HEADLINE`
(`llm/client.py:127-129`) y el validador devuelve `approved=False` con
`API_ERROR_REASON_PREFIX` (`llm/validator.py:151-155`): la degradación era
correcta, el diagnóstico no, y mandaba a revisar un prompt sano. Ahora se mira
el motivo y hay un texto por causa.

Escribiendo el arreglo apareció un cuarto caso que ningún inventario tenía, y
es el más silencioso de todos: **cuando quien muere es el generador, no había
alerta ninguna.** `FALLBACK_HEADLINE` no lleva ninguna cifra, así que el
validador —que busca cifras inventadas— lo aprueba, no hay rechazo que
detectar, y el pipeline publicaba "Cierre Semanal: Resumen del Mercado" a
secas. Es decir que la premisa con la que este ADR acepta degradar ahí —"el
bloque genérico lleva las cifras reales, las pone el pipeline"— sólo era cierta
por el camino del rechazo. Ahora también por este.

**2. R2 configurado y fallando abortaba después de la aprobación humana.** —
*Cerrada en `d187d81`.*
`orchestration/main.py` llamaba a `upload_image` sin protección y
`storage/r2_client.py:68-70` levanta `R2ClientError`. La excepción salía por el
`except` general y la run moría con el humano ya habiendo aprobado y sin nada
publicado. Ahora degrada y avisa, con un `except` ancho a propósito:
`upload_image` sólo convierte `ClientError`, así que un corte de red llega como
`EndpointConnectionError` de botocore y atrapar sólo lo nuestro habría dejado
abierto justo el fallo más probable.

**3. Toda excepción posterior a `mark_in_progress` dejaba la fila trabada.** —
*Cerrada en `1dc6fac`.*
`mark_x_published` y `mark_linkedin_published` actualizan sólo la columna del
`post_id`, **no `status`**, y el `except` general del orquestador loggeaba y
re-levantaba sin tocar el estado. La fila quedaba en `in_progress` para siempre
y el reintento del mismo `event_id` moría en el guard de duplicados con un
`pipeline_already_running_skipping`. **La idempotencia parcial que promete el
docstring de `MacroOrchestrator` («Idempotencia parcial») era inalcanzable**, y el código de
reconciliación que lee los `post_id` nunca se ejecutaba.

No era un problema de la fase de publicación: el lock se toma antes de la fase
de datos, así que el abort por Alpha Vantage caída, el del Mock Data bloqueado y
el de datos insuficientes para el retorno también dejaban la fila trabada. La
única salida que marcaba bien era el timeout de Telegram.

El arreglo tiene tres piezas, y las dos últimas no son obvias:

- El `except` general llama a `mark_failed`. Como es un `UPDATE` acotado sirve
  para las tres situaciones sin preguntar en cuál estamos: si el abort fue
  anterior al lock no hay fila y no se crea ninguna.
- `mark_in_progress` era `INSERT OR IGNORE`, así que sobre una fila `failed` o
  `expired` el lock **no se re-armaba** y el reintento corría sin lock. Ahora
  re-arma, con un `WHERE` estrecho que no reabre una fila `published` ni pisa
  los `post_id` —que es lo que lee la reconciliación tres líneas después—.
- `mark_failed` ignora las filas `published`. Con "toda excepción marca
  `failed`" el manejador corre también para lo que reviente *después* de
  publicar, y desmarcar ahí sería peor que el fallo original: la run siguiente
  vería `is_published() == False` y publicaría el mismo cierre por segunda vez
  en X y LinkedIn.

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
- **ADR-007** declara R2 opcional; la divergencia 2 era el código no cumpliendo
  esa declaración.
- **ADR-008** cubre el hueco que esta política deja: los fallos silenciosos.
- **ADR-002** queda tocado de refilón: la idempotencia que promete depende de
  que el fichero SQLite sobreviva entre runs, cosa que un entorno efímero de
  Routines no garantiza. Hoy no muerde —nada corre el orquestador en un
  schedule— pero la tabla de estados de este ADR no significa nada si el estado
  no persiste.
