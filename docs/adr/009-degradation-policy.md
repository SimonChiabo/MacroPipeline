# ADR-009: Política de degradación — qué fallo degrada y qué fallo aborta

**Estado:** Aceptado (2026-08-25). Las tres divergencias que documentaba se
arreglaron el mismo dia; la seccion final las conserva como registro. Hoy son
siete: la cuarta, que quedaba como consecuencia aceptada, se cerro el
2026-08-31 al publicar solo el retorno por la ruta de AV; la quinta y la sexta
salieron de recorrer la tabla contra el codigo el 2026-08-29 y siguen
**abiertas**; la septima salio de recorrer la tabla otra vez el 2026-09-01,
tambien **abierta** y dejada asi a proposito (es dev-only). La historia de
"todas arregladas el mismo dia" ya no vale para la seccion entera.
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
| Aborta trabado | `in_progress` | **No se acepta**: el reintento del mismo `event_id` se salta el cierre, y alerta si el lock lleva más de dos horas o su antigüedad no se puede leer |

Un abort **nunca** debe dejar la fila en `in_progress`. Es el mismo razonamiento
del fix de `publishers_ready` (`5ba7997`): si no se publicó, el estado no debe
afirmar lo contrario ni impedir el reintento.

Esa cuarta forma no se puede eliminar: una muerte no atrapable —SIGKILL, el
runner efímero que se apaga— deja la fila trabada por definición, y ningún
`except` la cubre. Lo que sí se eliminó es el silencio. Desde el 2026-09-01 el
guard de lock avisa por Telegram en tres casos: el lock lleva más de dos horas,
la fila es anterior a la columna `locked_at` y no se sabe desde cuándo, o ese
valor existe pero no se puede leer.

El umbral sale del timeout de aprobación humana (`wait_for_approval`, 3600 s):
una hora de `in_progress` es un estado sano mientras el operador decide, así
que sólo se alerta bastante por encima de eso.

El alcance es más chico de lo que sugiere «no se acepta», y conviene tenerlo
claro para no sobre-reaccionar: el `event_id` lleva la fecha del día, así que la
run de la semana siguiente calcula otro y publica normal. Lo que se pierde es
**ese** cierre, y cada relanzamiento del mismo día se lo vuelve a saltar.

**Alertar no vuelve aceptable a esa forma de abortar: la vuelve visible.** El
lock no se expira solo, y es deliberado — el umbral dice que una run viva es
improbable, no imposible, y auto-expirar un lock ajeno es el camino a publicar
el mismo cierre dos veces. Por el mismo motivo el aviso atrapa `TypeError` y
`ValueError` al leer `locked_at`: dejar subir esa excepción haría que el
manejador general marcara la fila `failed`, que es soltar el lock justo donde
esta política dice que no se toca.

### El tercer eje: el apagado por switch no degrada

La formulación obvia —*un componente sin configurar no alerta*— **no sobrevive
al inventario**, y por eso queda escrita como descartada: para X y LinkedIn una
credencial faltante **sí** alerta. Con el eje puesto en "credencial presente"
eso sería una contradicción.

No lo es:

> Un componente **apagado por su switch** no participa, y no participar no es
> degradar. Un componente **encendido** al que le faltan credenciales es un
> fallo, y alerta.
>
> El switch es lo que el código lee. Las declaraciones de ADR-001 —el LLM fuera
> del path numérico— y de ADR-007 —R2 opcional— no desaparecen: siguen siendo el
> motivo por el que un componente *puede* apagarse, pero dejaron de ser la
> señal. La diferencia importa porque una declaración en un ADR no distingue una
> decisión de una key rotada, y un `false` tipeado por una persona sí.

De acá sale que **FRED sin key, R2 sin key y la capa LLM sin key siguen siendo
la misma cosa** — lo que cambió es cuál: degradan, y las tres gastan una alerta,
que sale del punto de decisión al arrancar. Los que no participan, y por eso no
alertan, son esos mismos tres con su switch en `false`. Los tres fallando
*estando encendidos y configurados* también alertan, cada uno desde donde se
rompe.

Para la capa LLM la declaración sigue siendo **ADR-001**, que la define como
auxiliar: el LLM no toca números y solo redacta un titular a partir de cifras ya
calculadas y validadas. Esta misma política se apoya en esa definición para que
la API caída degrade, y es lo que hace que la key ausente degrade también en vez
de abortar; lo que no participa es `USE_ANTHROPIC=false`. Sin capa LLM el cierre
semanal se publica igual y sigue siendo correcto, porque las cifras las pone el
pipeline — lo que se pierde es redacción, no información.

Una red apagada por bandera termina en el mismo silencio, y hasta el 2026-08-29
llegaba ahí **por otro camino**: los opcionales callaban por una declaración en
otro ADR, las redes por una bandera explícita, y eran dos argumentos distintos
que sólo coincidían en el resultado. El switch por componente es exactamente lo
que los fusionó —`PUBLISH_X` dejó de ser la excepción y pasó a ser el caso
general, con otros siete iguales al lado—. Por eso la tabla ya no tiene que
distinguirlos: no es que se haya dejado de hacer la distinción, es que dejó de
haber dos cosas que distinguir.

### La política, por componente

| Componente | Fallo | Política | Estado que deja |
|---|---|---|---|
| FRED (bloque macro) | Sin key | **Degrada**, con alerta desde el punto de decisión | — |
| FRED (bloque macro) | Apagado con `USE_FRED=false` | **No participa**, sin alerta | — |
| FRED (bloque macro) | API caída, serie corta, dato rancio o cifra fuera de rango | **Degrada** — `macro=None`, con alerta que nombra la causa | — |
| FMP (índices) | Sin key | **Degrada** a Alpha Vantage — el cierre sale sin el nivel, con alerta desde el punto de decisión | — |
| FMP (índices) | Apagado con `USE_FMP=false` | **Aborta** en silencio — pausa deliberada; un switch apagado no se sustituye por el fallback | Ninguna fila |
| FMP (índices) | API caída | **Degrada** a Alpha Vantage — el cierre sale sin el nivel, con alerta in-run antes de pedir aprobación | — |
| Alpha Vantage (índices) | Sin key | **Degrada** — el fallback queda ausente, con alerta que dice que sin él un fallo de FMP deja la run sin cierre | — |
| Alpha Vantage (índices) | API caída | **Aborta** — sin fuente de datos real no se publica | `failed` |
| Mock Data | `ALLOW_MOCK_DATA=false` | **Aborta** — cifras sintéticas no se publican | `failed` |
| Cálculo del retorno | Menos de 6 filas, o sin dato de hace 5 días hábiles | **Aborta** | `failed` |
| Anthropic (capa LLM) | Sin key | **Degrada**, con alerta desde el punto de decisión | — |
| Anthropic (capa LLM) | Apagada con `USE_ANTHROPIC=false` | **No participa**, sin alerta | — |
| Anthropic (generador) | API caída | **Degrada** — bloque genérico, con alerta que nombre la causa real | — |
| Anthropic (validador) | Rechazo del titular | **Degrada** — bloque genérico + alerta | — |
| `ValidationEngine` | Cifra **del cierre semanal** fuera de rango de plausibilidad | **Aborta** — es la última defensa de la invariante de ADR-001. Sin nivel (ruta de AV) no hay rango de nivel que aplicar; los de retorno siguen | `failed` |
| Playwright (render) | Plantilla ausente o render fallido | **Aborta** — no hay imagen que publicar | `failed` |
| Telegram | Sin credenciales | **Aborta** — sin canal ni HITL; log nombrado y salida `1`, **sin alerta posible** | Ninguna fila |
| Telegram | `USE_TELEGRAM=false` | **Aborta** en silencio — pausa deliberada del pipeline entero | Ninguna fila |
| Telegram (aprobación) | Envío fallido | **Aborta** — ADR-004 exige aprobación humana | `failed` |
| Telegram (aprobación) | Timeout de 1h | **Aborta** | `expired` |
| Telegram (`send_alert`) | Envío fallido | **Degrada** — devuelve `False`, nunca levanta | — |
| R2 (snapshot de imagen) | Sin configurar | **Degrada**, con alerta desde el punto de decisión | — |
| R2 (snapshot de imagen) | Apagado con `USE_R2=false` | **No participa**, sin alerta | — |
| R2 (snapshot de imagen) | Subida fallida | **Degrada** — sin snapshot remoto, con aviso | — |
| R2 (estado) | Sin configurar, o `USE_R2=false` | **No participa** — el estado corre solo contra disco local | — |
| R2 (estado) | Bajada fallida al arrancar | **Aborta** antes del lock, con alerta — sin estado confiable no se sabe si este cierre ya salió | Ninguna fila |
| R2 (estado) | No hay estado remoto todavía | **Sigue**, con aviso que dice «primera corrida o pérdida» — desde el código son indistinguibles | — |
| R2 (estado) | Subida fallida tras una escritura | **Aborta** — la excepción sube al manejador general | `failed` |
| R2 (estado) | Subida fallida dentro de `mark_failed` | **Degrada** — se loguea y no levanta, para no reventar el manejador de fallos | `failed` (local) |
| X / LinkedIn | Credenciales ausentes en **una** de las dos | **Degrada** — publica en la otra, con alerta antes de pedir aprobación | `published` |
| X / LinkedIn | Credenciales ausentes en **las dos** | **Aborta** antes del lock, con alerta | Ninguna fila |
| X / LinkedIn | Apagada con `PUBLISH_X` / `PUBLISH_LINKEDIN` en `false` | **No es un fallo** — no se construye, no publica y **no alerta** | — (Ninguna fila si están apagadas las dos) |
| X / LinkedIn | Publicación fallida | **Aborta** — `post_id` de lo que sí salió persistido | `failed` |
| Cualquier switch | Valor que no es `true` ni `false` | **Aborta** con alerta — no se pudo leer la intención | Ninguna fila |
| Cualquier excepción | Dentro de `run_weekly_close` | **Aborta** con alerta que nombra el motivo | `failed` |

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
antes de publicar en ninguna red. Arreglado en `d187d81`. R2 **sin key** es otro
caso pero desde el 2026-08-29 tiene el mismo desenlace: degrada, y avisa desde el
punto de decisión en vez de callarse. El que no participa —y por lo tanto no
lleva aviso— es `USE_R2=false`, que es el único de los dos que expresa una
decisión.

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

### El estado de R2 rompe el tercer eje a propósito (2026-08-31)

El tercer eje dice que un componente **declarado opcional** que no está
configurado no participa, y no participar no es degradar. R2 era el ejemplo
canónico. Desde que el fichero de `StateDB` viaja por R2 —porque no sobrevive a
un entorno efímero, que es lo que ADR-002 decide para el pipeline— **eso deja
de valer para la mitad de estado**: R2 caído ya no cuesta un snapshot
prescindible, cuesta no saber si este cierre ya se publicó. Por eso las filas
están partidas: la imagen sigue bajo el tercer eje, el estado no.

**El aviso de «no había estado remoto» solo se debe en las corridas que llegan a
publicar.** Vive al final del punto de decisión, después de todas las ramas de
aborto. Una corrida que aborta a propósito —el pipeline en pausa, la única
fuente apagada— no publica nada, así que no puede duplicar nada y el estado
perdido no le cuesta nada *a esa corrida*; avisar ahí sería ruido sobre una
decisión propia. En el caso extremo (`USE_TELEGRAM=false`) ni siquiera hay
canal, porque esa pausa implica `self.telegram is None`.

Esto salió de recorrer esta tabla contra el código: el aviso estaba antes de las
ramas de aborto, así que una corrida deliberadamente apagada mandaba un Telegram
que la fila «FMP apagado → aborta **en silencio**» dice que no manda. Hay test
que lo fija.

El precio, anotado: una pérdida de estado ocurrida durante un período de pausa
no se ve hasta la primera corrida que reanude.

**Residuo conocido, no cerrado:** cuando la excepción que mata la corrida **no**
es del sincronizado, el push de `mark_failed` es el que registra la fila
`failed` en el remoto, y ese push se traga su propio error a propósito (si
levantara, reventaría el manejador de fallos y taparía la causa original). Un
crash cualquiera coincidiendo con un corte de R2 deja el remoto diciendo
`in_progress` y el local `failed`; en un runner efímero, la corrida siguiente
baja ese `in_progress` y **se salta el cierre**, que es la forma que la tabla de
arriba no acepta. Desde el aviso de lock viejo ese salto deja de ser mudo **sólo
si el relanzamiento llega más de dos horas después**: uno más rápido ve un lock
de minutos y se calla, a propósito, porque el umbral no puede distinguirlo de
una run viva. La cadencia semanal lo hace raro y cerrarlo pediría un segundo
canal de escritura, pero queda escrito porque es el tipo de promesa que esta
tabla ya ha hecho de más dos veces.

**Fail-silent que este trabajo no puede cerrar:** el sincronizado participa
siempre que R2 esté configurado, así que un workflow programado al que se le
olviden los secrets de R2 correría solo contra disco local, sin sincronizar y
sin quejarse. Hoy no existe ningún workflow que corra el pipeline. **Cuando se
escriba, los secrets de R2 van en su paso de pre-chequeo**, igual que las seis
keys del nightly.

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
- **El tercer eje descansa en que la no-configuración sea deliberada, y el
  código no puede distinguirla de un accidente.** Una key rotada, un `.env`
  que no se copió a una máquina nueva o un secreto que caducó se leen igual
  que una decisión: el componente no participa, el cierre sale con menos y
  **no llega ninguna alerta**. No es una excepción a la primera negativa de
  arriba —no participar no es degradar—, pero lo que el lector observa es lo
  mismo que esa regla existe para evitar. El único rastro es un log:
  `fred_not_configured`, `r2_not_configured` o `llm_not_configured` al
  arrancar, y `llm_layer_not_participating` en cada cierre. Nadie los mira
  todas las semanas. **Cerrado el 2026-08-29 con un switch por componente.**
  La no-configuración dejó de ser la señal: el silencio ahora exige un `false`
  explícito, y una key rotada o un `.env` sin copiar dejan el componente
  encendido y por lo tanto alertando. Lo que queda como coste es más chico y de
  otra clase: quien apaga un componente tiene que acordarse de volver a
  encenderlo, y nada se lo recuerda.

**Cuatro limitaciones que hasta ahora no estaban escritas en ningún lado. La
(c) se cerró con el código del 2026-08-25 y la (d) con el del 2026-08-29 —para
Anthropic estaba decidida desde el 2026-08-26—; las dos quedan como registro.
La (a) y la (b) siguen abiertas:**

**(a) La alerta de degradación promete de más, y desde el 2026-08-29 en más
casos que antes.** Dice "se publica igual si lo aprobás" y después el humano
puede rechazar, la aprobación puede expirar, o la red viva puede haber
publicado ya más temprano el mismo día: en todos ésos la run no publica y la
alerta ya anunció una publicación degradada.

Eran dos casos cuando el aviso vivía justo antes de la fase HITL. Al mudarlo al
punto de decisión —antes del lock, y por lo tanto antes de datos, validación,
render y LLM— se le pusieron delante todas las salidas de esas fases: un fallo
de datos, una cifra que el validador rechaza, un render roto. Todas dejan fila
`failed` y avisan por su cuenta, así que se contradicen a la vista. La que no
se ve es `is_in_progress`: manda el aviso de degradación y después se salta la
run entera en silencio, sin fila nueva y sin segundo mensaje.

Lo que sigue siendo verdad en todos los casos es la mitad accionable: la
credencial está rota. Que la mudanza haya empeorado esta limitación es el
precio de haber arreglado la (d) —el aviso tenía que subir hasta donde existen
a la vez el canal y el `event_id`— y se paga con gusto: prometer de más sobre
una publicación es menos grave que no avisar nunca. La misma propiedad la
tiene la alerta de la capa LLM, que no se mudó.

**(b) Los `post_id` persistidos, en los que se apoya la reconciliación
parcial, se pueden perder.** Si `mark_x_published` falla después de que
`post_tweet` salió bien, el tweet existe y el registro de que existe no, así
que el reintento republica en X. Esta política se apoya en los `post_id` sin
decir que tienen esa ventana.

**(c) La regla "toda degradación alerta" no se cumplía para FRED.** — *Cerrada
el 2026-08-25.* El bloque macro se caía en silencio por tres caminos que
`_fetch_macro_snapshot` no distinguía. Dos de ellos —la API/serie/frescura y el
validador rechazando la cifra— son fallos y ahora alertan con la causa real; el
tercero, FRED sin key, no era un fallo sino un opcional sin configurar, y seguía
en silencio por el tercer eje de arriba — hasta el 2026-08-29: desde el switch
por componente, una key ausente es un componente encendido y roto, así que
alerta, y el silencio queda para `USE_FRED=false`.

Lo que la pregunta destapó fue más grande que FRED: la respuesta correcta ya se
cumplía en R2 y en los publicadores por decisiones locales que nadie había
escrito como política, y dos filas de esta misma tabla mentían sobre el código
—la de R2 afirmaba un aviso que no se manda, y la del `ValidationEngine`
prometía un abort para la cifra macro que el código, con razón, degrada—. Es el
mismo patrón que motivó este ADR.

**(d) A un componente necesario sin credenciales no se entera nadie.** —
*Cerrada el 2026-08-29.* Ningún componente con credenciales puede matar
`MacroOrchestrator.__init__`: los ocho pasan por `build_component`, que anota
el motivo en vez de dejar salir el `ValueError`. Todo lo que quedó roto o
apagado al arrancar se reporta desde un punto de decisión único al principio
de `run_weekly_close`, que es el primer sitio donde existen a la vez el canal
de aviso y el `event_id`. El orden de construcción dejó de ser lógica, que era
la raíz del problema y no su síntoma: FMP y Alpha Vantage se construían antes
que Telegram, así que cuando reventaban no había con qué avisar.

**Anthropic se cerró tres días antes que los otros siete, y su caso era otro.**
— *Decidido el 2026-08-26.* Sin `ANTHROPIC_API_KEY` el constructor también
levantaba, pero lo que divergía ahí no era un aviso que faltara sino que el
constructor tratara como fatal a un componente que esta política declara
degradable; por eso se pudo arreglar solo, envolviéndolo como ya se envolvía a
`FREDClient` y a `R2Client`, sin esperar al punto de decisión. La fase LLM
publica el bloque genérico con las cifras reales y **sigue sin alertar**: lo que
cambió el 2026-08-29 no es esa fase sino que la key ausente ahora se avisa al
arrancar, como la de los otros siete. Silencio queda sólo con
`USE_ANTHROPIC=false`. La fila queda con `prompt_version` y `validator_approved`
en NULL: no ocurrió ninguna llamada que registrar, y escribir la versión de
prompt afirmaría una que no se hizo.

**Lo que no se puede cerrar:** Telegram encendido y sin credencial. No hay canal
para avisar de que no hay canal, y un segundo canal no existe. Esa run deja un
log nombrado con el cuadro completo de motivos y sale con código `1`; el código
de salida es lo único que cruza el borde del proceso. Hoy nadie lo mira —nada
corre `main.py` en un schedule—, así que este límite se cobra el día que el
pipeline corra desatendido, junto con el punto de la idempotencia bajo un
entorno efímero.

**Lo que esta política no cubre:** un componente que falla *silenciosamente*
devolviendo datos plausibles pero equivocados. Ninguna rama de degradar/abortar
se activa, porque desde el pipeline no hay fallo. Ese riesgo lo cubren los
contract tests (ADR-008) y los rangos de plausibilidad, no este ADR.

---

## Divergencias entre esta política y el código

Las cuatro primeras se verificaron contra el código el 2026-08-25, el día del
ADR. Las tres primeras eran trabajo pendiente y se arreglaron ese mismo día;
quedan escritas porque el modo de fallo que cada una describe es más fácil de
volver a introducir que de encontrar. La cuarta quedó como consecuencia
aceptada hasta que se cerró el 2026-08-31, al publicar solo el retorno por la
ruta de AV. La quinta y la sexta salieron de volver a recorrer la tabla fila
por fila el 2026-08-29, ya contra el código del switch por componente: las dos
son la tabla diciendo de más, no el código haciendo de menos. La séptima salió
de recorrer la tabla otra vez el 2026-09-01, contra el código que cerró la
cuarta: es un matiz alcanzable solo en desarrollo y se dejó abierto a
propósito.

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

**4. Consecuencia aceptada: la ruta de Alpha Vantage ya no podía publicar.** —
*Cerrada el 2026-08-31 (`2475978`…`97421a1`).*
El ETL pide a FMP `^GSPC` (índice, ~7.657) y cae a AV con `SPY` (ETF, ~765). El
nivel se guardaba venga de donde venga y se publicaba rotulado "SP500: Cierre",
así que la ruta de fallback publicaría el número del instrumento equivocado —
la misma invariante de ADR-001, esta vez en el ETL. El control eran los rangos
de `validators/rules.yaml:16-19` (`sp500_close_min: 2000`) aplicados en
`validate_weekly_close`: con SPY a 765 el validador levantaba y la run
abortaba. Por eso la tabla decía que FMP "degrada a AV… que hoy aborta". Era
deliberado: mejor no publicar que publicar el instrumento equivocado, y
**publicar solo el retorno desde AV** —invariante de escala entre el índice y
su ETF, y por lo tanto una degradación real en vez de un abort— quedó
propuesto y sin hacer.

Lo cerró exactamente eso:

- `sp500_close`/`nasdaq_close` pasaron a `float | None` (`2475978`), con un
  `model_validator` que exige que los dos vengan juntos o ninguno — no hay
  forma de que se publique uno sin el otro.
- La ruta de AV construye el modelo con los cierres en `None`
  (`publica_nivel = data_source != "av"`, `f0b6ea5`): el nivel del ETF nunca
  llega a existir bajo la etiqueta del índice.
- `validate_weekly_close` saltea el rango de nivel cuando el nivel es `None`
  (`1ce6b8e`): no hay cifra que defender, así que no hay rango que aplicarle.
  Los rangos de **retorno** siguen aplicándose siempre — el ETF y el índice se
  mueven igual, así que el retorno del ETF es una cifra válida bajo la
  etiqueta del índice, y el renderer sube esa cifra al lugar del cierre con la
  nota "variación semanal" (`973191b`).
- La capa LLM tampoco participa sin nivel: el `data_str` no se construye
  (`8eccebd`), así que la cifra mal rotulada no llega a existir para el
  modelo — ADR-001 se sostiene por construcción, no por una cláusula del
  prompt que alguien puede olvidar actualizar.
- **FMP sin key se mudó del bloque de abortos al de degradaciones** (`97421a1`),
  tal como esta misma divergencia lo dejó escrito de antemano: "el día que se
  publique sólo el retorno desde AV, FMP sin key pasa a ser una degradación".

Publicar el nivel del instrumento equivocado sigue tan prohibido como el día
que se escribió esta divergencia — lo que cambió es que la ruta de AV ahora
tiene algo que publicar sin romper esa regla.

**5. La fila «cualquier switch aborta con alerta» promete un aviso que no
siempre llega.** — *Encontrada el 2026-08-29, abierta.*
La primera rama de `_startup_exit_code` manda la alerta sólo si hay canal. Con
`USE_TELEGRAM=maybe` el switch ilegible es justamente el del canal:
`read_switch` devuelve `(False, motivo)`, `TelegramBot` no se construye y la
rama sale por `switch_invalid_no_channel_aborting` con código `1` y **sin
alerta**. Pasa lo mismo con cualquier otro switch inválido si además Telegram
está roto o apagado. El código hace lo único que puede —es el caso irreducible
que la limitación (d) deja escrito: no hay canal para avisar de que no hay
canal—, así que lo que diverge es la celda. Queda anotada y no metida en la
tabla porque la alerta *es* la regla y la excepción ya tiene su párrafo: una
celda que enumere las dos cosas se lee peor que este.

**6. Una red apagada y la otra rota abortan, y ninguna fila de la tabla lo
dice.** — *Encontrada el 2026-08-29, abierta.*
Las dos filas de publicación parten el mundo en «ausentes en las dos» —aborta— y
«apagada por bandera» —no es un fallo—. El caso mezclado, `PUBLISH_X=false` con
LinkedIn sin credenciales, no cae en ninguna: `x_ready` y `linkedin_ready` son
las dos `False`, así que entra por la rama de publicadores del punto de
decisión, y `_publisher_failures` devuelve el motivo de LinkedIn, así que aborta
**con alerta** y código `1`. Es el comportamiento correcto —no queda ninguna red
viva y el motivo es real—, pero leyendo la tabla se predice «degrada, publica en
la otra». Lo que falta no es código sino una fila, y no se agrega hoy para no
resolver a mano lo que conviene decidir con la tabla entera delante.

**7. `fmp_runtime_error` puede nombrar la fuente equivocada, pero solo en
desarrollo.** — *Encontrada el 2026-09-01, abierta y dejada así a propósito.*
`self.fmp_runtime_error` se carga en el `except` de FMP
(`orchestration/main.py`, dentro de `_fetch_weekly_close`), **antes** de
intentar Alpha Vantage. Si FMP se cae en ejecución y AV también falla, la
fuente termina siendo Mock (`data_source == "mock"`) pero `fmp_runtime_error`
queda cargado igual, así que la alerta in-run de la degradación (`7ebe4de`,
"FMP falló y el cierre sale por Alpha Vantage") diría eso mismo cuando en
realidad el cierre salió por Mock Data, con el nivel poblado y no ausente.

**No es alcanzable en producción**: Mock está bloqueado por
`ALLOW_MOCK_DATA=false`, así que esa misma cascada —FMP roto y AV también—
hace que `_fetch_weekly_close` levante `RuntimeError("Todas las fuentes de
datos fallaron…")` antes de llegar a fabricar datos de Mock, y la run muere
en el manejador general (`failed`) sin que esa alerta llegue a mandarse. La
imprecisión solo se puede provocar con `ALLOW_MOCK_DATA=true`,
que es exclusivamente una bandera de desarrollo. Se decidió deliberadamente
**no** corregirlo en el código —encadenar el motivo a la fuente final
hubiera significado ensanchar el alcance de un plan que ya cerraba la
divergencia 4— y dejarlo escrito acá para que quien lo pise después no
tenga que volver a redescubrirlo.

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
