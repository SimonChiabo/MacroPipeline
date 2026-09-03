# Camino a la primera publicación

> Este documento cubre **una sola pregunta**: qué falta para que MacroPipeline
> publique su primer cierre semanal. El roadmap general del proyecto —semanas,
> fases, alcance— sigue en [PLAN.md §7](./PLAN.md). Acá no hay fechas: el orden
> es por riesgo, y la fecha sale cuando los bloqueadores estén cerrados.

**Estado al 2026-09-02.** Todo lo de abajo está verificado contra el código, el
estado real y las APIs ese día. Lo que no se pudo verificar se dice.

---

## Dónde estamos

| | |
|---|---|
| **Publicaciones** | **Cero.** Nunca salió un post. |
| **Corridas del pipeline** | La base no las cuenta, y no puede: una corrida que aborta antes del lock no deja fila (`mark_failed` es un `UPDATE` a secas). Las que sí dejaron fila están todas en `failed`, y ninguna tiene `x_post_id` ni `linkedin_post_id`. El motivo tampoco está en la fila —la tabla no tiene columna para él, `mark_failed` lo manda al log— así que hace falta la salida de esa corrida para distinguir un rechazo humano de una excepción. |
| **Capa LLM** | **Apagada a propósito** (`USE_ANTHROPIC=false`). El titular lo arma el pipeline con las cifras del snapshot. Vuelve en el camino A. |
| **Estado remoto** | `state/state.db` en R2, 12288 bytes. **Es el autoritativo**: el arranque lo baja encima del local, así que una reparación a mano que no lo toque no sobrevive al siguiente arranque. |
| **Credenciales** | Las siete verificadas contra sus APIs (`scripts/check_credentials.py`, código 0). |
| **Trigger programado** | **No existe.** En `.github/workflows/` sólo hay `ci.yml` y `contract-tests.yml`, y ninguno ejecuta el pipeline. No hay Routine creada. Decidido que corra los **sábados** (bloqueador 3). |
| **Punto de entrada** | `python src/macro_pipeline/orchestration/main.py` — el bloque `if __name__ == "__main__"`. |
| **Renderizado** | Playwright con Chromium instalado en la máquina local. |

Las cinco fases del pipeline están escritas y testeadas por separado: datos,
validación, renderizado, LLM, HITL y publicación. **Corrieron juntas y llegaron
al botón de aprobar**, primero con la capa LLM y después sin ella. Eso corrió de
lugar el riesgo central: ya no es que la mecánica falle, sino que las cifras no
signifiquen lo que su etiqueta dice — y ese frente avanzó mucho el 2026-09-02.

**Lo que se arregló ese día, todo con la fase roja verificada:**

| | Antes | Ahora |
|---|---|---|
| IPC interanual | `CPIAUCSL` (desestacionalizada) → +3,3 % | `CPIAUCNS` → **+3,4 %**, la del titular |
| Etiqueta del Nasdaq | «NASDAQ» a secas | **«Nasdaq Composite»** |
| Precio publicado | podía ser **intradía** rotulado «Cierre» | sólo sesiones terminadas |
| Titular | Claude Haiku 4.5 + validator | determinista, desde el snapshot |
| Atribución | ninguna | «Fuentes: …», con la que trajo el dato |
| Alertas del validador | nombraban una serie escrita a mano | nombran la que el ETL pide |

El detalle de cada una está en [`docs/data-dictionary.md`](./docs/data-dictionary.md).

---

## Dos cosas que hay que saber antes de planificar nada

### No hay dry-run, y apagar las dos redes no lo sustituye

**No existe ningún `--dry-run`**: no hay `__main__.py`, no hay `console_scripts`
en `pyproject.toml` y no hay ninguna bandera así en el código. El README llegó a
prometerlo; ya no (bloqueador 4).

Y apagar las dos redes tampoco sirve de ensayo. Con `PUBLISH_X=false` y
`PUBLISH_LINKEDIN=false`, `_publisher_failures()` no devuelve nada —un switch
apagado es una decisión, no un fallo— y la run **sale con 0 en el punto de
decisión** (`main.py:540-544`, log `no_publishers_enabled`), antes de la fase
de datos. No ejercita ni el ETL, ni el renderer, ni el LLM, ni el HITL.

### El ensayo real existe, y es el propio HITL

La corrida completa con las banderas encendidas se detiene en Telegram y espera
hasta una hora (`wait_for_approval`, `timeout_seconds=3600`). **Rechazar el
borrador es el ensayo de punta a punta**: ejercita las cinco fases contra datos
reales y no publica nada.

Qué deja: la fila queda `failed` con `reason="rejected_by_human"` y la run
devuelve 0 (`main.py:1054-1057`). `mark_in_progress` re-arma el lock sobre una
fila `failed`, así que el mismo día se puede reintentar y publicar. Es
exactamente el margen de seguridad que ADR-004 diseñó.

**El kill switch, mientras tanto, es `USE_TELEGRAM=false`**: pausa el pipeline
entero con código 0 y sin alertar, porque un componente apagado a propósito no
es un fallo (`main.py:488-490`).

---

## Los bloqueadores, en orden de riesgo

### 1. ~~Las cinco fases nunca corrieron juntas~~ — **cerrado el 2026-09-02**

Corrieron, y llegaron al botón. `weekly_close_2026-09-02`: FMP con 1253
registros por índice, validación OK, Playwright 64 874 bytes, titular de Haiku
4.5 aprobado por el validador, borrador entregado en Telegram, **rechazado a
mano** y run terminada con código 0.

Lo que ese único ensayo dejó verificado de punta a punta, y que ningún test
podía dar:

- **La rama de rechazo funciona**: `telegram_draft_rejected` con el `from_id`
  del operador (o sea que el filtro de `TELEGRAM_ALLOWED_USER_ID` dejó pasar al
  correcto), fila marcada `failed` con `reason="rejected_by_human"`, y
  `pipeline_aborted_by_human` con salida 0.
- **El sincronizado de estado escribe de verdad**: dos `state_pushed` en la
  misma corrida —al tomar el lock y al cerrarla—, y el aviso de «primera
  corrida o pérdida de estado» apareció como se esperaba. Esa sorpresa ya no
  cae el viernes.
- **La ruta buena de datos**: `data_source=fmp`, así que se publica el nivel y
  no sólo la variación.

**Qué sigue haciendo falta antes de publicar:** el IPC (abajo) y la auditoría
de datos. La mecánica ya no es el riesgo; el significado de las cifras, sí.

Qué mirar en el **próximo** ensayo —el del IPC, que cambia una cifra que se
publica—:

- **La cifra del IPC contra la que publica el BLS.** Es el motivo del ensayo.
- **El resto de los números del borrador**, contra su fuente y uno por uno. Es
  la única verificación que ningún test puede hacer: los tests fijan el
  formato, no la veracidad. La auditoría de más abajo dice qué comparar con qué.
- **El titular del LLM.** Si dice «Cierre Semanal: Resumen del Mercado» a secas,
  la capa LLM cayó al fallback y hay que mirar por qué.

Dos cosas que ya no hace falta mirar, porque el ensayo las contestó: de qué
fuente salió el cierre (dio `data_source=fmp`) y el aviso de «primera corrida o
pérdida de estado», que apareció y sembró el estado remoto.

### 2. Por qué falló la corrida del 2026-08-27

No se sabe, y no se puede saber desde la base: `published_events` no tiene
columna de motivo, ni la tenía entonces ni la tiene ahora. `mark_failed` recibe
el `reason` y lo manda al log (`event_marked_failed`), no a la tabla, así que
**el motivo de un fallo sólo sobrevive si alguien guardó la salida de esa
corrida**. Para el 2026-08-27 no se guardó. Escribir el motivo en la fila es
trabajo pendiente y no está hecho.

No conviene investigarlo por separado. Seis días de commits pasaron por encima
—entre ellos el sincronizado de estado y cuatro arreglos del arranque—, así que
la causa de agosto puede ya no existir.

### 3. No hay nada que dispare el pipeline

Sin esto no hay publicación semanal, sólo corridas a mano. La decisión está
planteada en ADR-002 (Claude Routines) con GitHub Actions como plan B, y hay
dos cosas ya sabidas que la simplifican:

- **El entorno efímero dejó de ser un problema.** El fichero de estado viaja
  entero por R2 desde el 2026-08-31, así que las dos opciones sirven.
- **Los secrets de R2 van en el paso de pre-chequeo del workflow**, si se elige
  GitHub Actions. Con la regla «R2 configurado → sincroniza», un workflow al
  que se le olviden correría local-only y en silencio, y el mismo cierre podría
  salir dos veces.

Y una que hay que decidir: un workflow programado necesita los ~15 secrets del
`.env` cargados en el repo, incluidos los cuatro de R2 con permiso de escritura.

**Cuándo: los sábados.** Decidido el 2026-09-02, y no es una preferencia de
agenda — sale de un defecto que se encontró ese día.

El endpoint `historical-price-eod/full` de FMP **devuelve una fila para la
sesión en curso**, y su campo `close` es el último precio negociado. Medido
sobre `^IXIC` el 2026-09-02: 26.211,996 a las 14:40 UTC y 26.196,812 a las
14:59, con la misma fecha, y un volumen de 2.031 millones contra los 7.679 de
una sesión completa. El ensayo de esa tarde publicó el primero de esos números
rotulado «Cierre».

Con el mercado cerrado eso no puede pasar: la fila más reciente sólo puede ser
una sesión terminada. Y hay un segundo motivo que el sábado resuelve solo: la
última sesión es el viernes, y `BDay(5)` hacia atrás cae en el viernes anterior
— **viernes contra viernes**, la ventana semanal más limpia que da ese cálculo.
Un viernes por la mañana daría jueves contra jueves.

Lo que el sábado no arregla: el Treasury sigue mostrando el jueves, porque FRED
publica el dato de un día hábil la tarde del siguiente. Está rotulado con su
propio `as_of`, así que es honesto.

**El horario no es la garantía, es la conveniencia.** Un cron es una
convención, y una corrida a mano un miércoles volvería a publicar un intradía.
Desde el 2026-09-02 la regla vive en el código: `_fetch_weekly_close` descarta
las filas cuya fecha sea la de hoy, así que sólo publica sesiones terminadas
corra el día que corra. El sábado es lo que hace que esa regla no cueste nada
de frescura.

**Y una restricción que el lock dejó a la vista.** Si una corrida muere sin
cerrar su fila, el guard del lock hace que *ninguna* corrida futura publique
ese cierre, y lo único que avisa es un mensaje de Telegram. Con trigger
automático y nadie mirando, un sábado puede pasar sin publicación y sin que
nada más lo note. Pasó el 2026-09-02 en el ensayo, y la reparación tiene que
tocar **R2**, no sólo la base local: el estado remoto es el autoritativo y el
arranque lo baja encima del local.

### 4. ~~El README promete un comando que no existe~~ — **cerrado el 2026-09-02**

El comando ya no está: el README documenta el punto de entrada real
(`python src/macro_pipeline/orchestration/main.py`) con la advertencia de que
publica de verdad y se detiene en Telegram. Este documento afirmaba lo
contrario hasta que se fue a corregirlo y no había nada que corregir.

Sí había otras cuatro afirmaciones falsas, arregladas el mismo día:

- «Cada viernes» → **cada sábado**, con el motivo escrito.
- El paso 4 decía que el titular lo genera Claude. Hoy lo arma el pipeline.
- «Toda la ejecución se observa con OpenTelemetry y Grafana Cloud» → las trazas
  no llegan a ningún lado (bloqueador 5), y ahora el README lo dice.
- «Los datos crudos de cada run se archivan como snapshots inmutables en R2» →
  **eso nunca existió.** R2 guarda el fichero de estado y la imagen publicada,
  nada más. Verificado siguiendo los dos únicos llamadores de `upload_object`.

**Lo que sigue abierto es el CLI**, que era la otra mitad de este bloqueador:
`__main__.py` con `run weekly-close` y un `--dry-run` de verdad, que corra las
cinco fases y se detenga antes de publicar. Hoy el único ensayo posible es
correr entero y rechazar a mano en Telegram, que funciona pero no sirve para
CI. Después de la primera publicación.

### 5. Grafana no existe: la cuenta, no sólo el dashboard — *no bloquea publicar*

Corregido el 2026-09-02, después de afirmar lo contrario en este mismo
documento. **No hay cuenta de Grafana Cloud.** El `.env` tiene el
`OTEL_EXPORTER_OTLP_ENDPOINT` **copiado literal de `.env.example`**
(`https://otlp-gateway-prod-us-east-0.grafana.net/otlp`), que es lo que hizo
creer que estaba configurado.

La consecuencia se vio en el ensayo: el exportador de OTel tira
`Failed to export span batch code: 401, reason: Unauthorized` tres veces por
corrida. **No llega ni una traza.** No frena nada —los spans son best-effort—
y por eso nunca se supo: lo imprime el SDK por stderr, no el logger, y ningún
chequeo lo mira. `check_credentials.py` tampoco: el chequeo de deriva sólo
marca placeholder los valores que empiezan con `your_`, y éste es una URL
válida copiada del ejemplo.

Falta entonces: crear la cuenta free tier, poner endpoint y token de verdad, y
recién ahí el dashboard (PLAN.md §7, semana 4).

---

## La corrida del 2026-09-03

Llegó al gate de Telegram y se rechazó a mano. Trece segundos desde el arranque
hasta el botón; salida `0`; `weekly_close_2026-09-03` quedó en `failed`, sin
`x_post_id`, sin `linkedin_post_id` y sin `image_url`.

Es la primera corrida completa con el pipeline determinista y con los arreglos
de datos del 2026-09-02 puestos, así que ejercitó contra datos reales cosas que
hasta ahora sólo habían pasado por tests:

- **El ciclo de estado contra R2 entero**: `state_pulled` (12288 bytes) al
  arrancar, `state_pushed` al tomar el lock y otro al cerrar la fila.
- **La ruta de FMP**: 1253 registros por índice, `data_source=fmp`, así que se
  publica el nivel de cierre y no sólo la variación.
- **El corte de la sesión en curso**: el cierre publicado es el del 2026-09-02,
  no el precio intradía del día de la corrida.
- **El IPC desde `CPIAUCNS`**: 0,033648 interanual. Ese cambio se había
  verificado con tests y con un contract test, nunca con una corrida.
- **El titular determinista**: `llm_layer_not_participating` con causa
  `capa_no_disponible`, o sea la capa apagada por `USE_ANTHROPIC=false`.
- **El render**: Playwright, 69509 bytes.
- **El filtro de operador**: el `from_id` del callback coincidió con
  `TELEGRAM_ALLOWED_USER_ID`. Si no hubiera coincidido, el botón se descartaba
  en silencio y la corrida se colgaba hasta el timeout de una hora.
- **La rama de rechazo**: `mark_failed` con el motivo al log, push del estado y
  salida `0`.

Lo que **no** ejercitó, y sigue sin ejercitarse nunca:

- **La rama de publicación.** `post_tweet` y `post_text` siguen con cero
  llamadas reales; sólo corren con una aprobación detrás.
- **La subida de la imagen a R2.** Vive dentro del bloque `if approved`
  (`orchestration/main.py:1030`), así que un rechazo no la alcanza:
  `image_url` quedó en `None`. Lo único que viajó a R2 fue el fichero de
  estado.
- **El fallback a Alpha Vantage.** FMP respondió, así que la cascada no se
  entró.
- **La capa LLM.** Apagada por configuración.

Consumo: dos llamadas a FMP y tres a FRED. Cero a Alpha Vantage y a Anthropic.

El exportador de OpenTelemetry devolvió `401 Unauthorized` dos veces, que es lo
que el bloqueador 5 ya describe.

---

## Lo que encontró el ensayo del 2026-09-02

Las cinco fases corrieron juntas por primera vez y llegaron al botón de
aprobar. El borrador se rechazó, no se publicó nada. Tres hallazgos, uno por
categoría:

### ~~El IPC que publicamos no es el que publica todo el mundo~~ — arreglado

`macro.py` usaba **`CPIAUCSL`**, la serie **desestacionalizada**. La cifra que
citan los medios y el BLS como «IPC interanual» sale de **`CPIAUCNS`**, la sin
desestacionalizar. Con el dato de julio de 2026 la diferencia caía justo en el
dígito que se publica: **3,3039 %** contra **3,3648 %**, o sea +3,3 % contra
+3,4 %.

Cualquiera que comparara el post contra una noticia iba a ver 3,3 donde el
mundo dice 3,4 y concluir que el pipeline calcula mal. Es la misma familia que
la divergencia 4 de ADR-009: la etiqueta correcta sobre el instrumento
equivocado.

**Corregido el 2026-09-02**, con dos tests que lo anclan y un motivo extra que
apareció al escribirlo: la serie SA se revisa hacia atrás cada año cuando el BLS
recalcula los factores estacionales, así que publicarla hacía que la cifra de un
post viejo pudiera dejar de ser reproducible sin que nadie tocara el pipeline.
El detalle en [`docs/data-dictionary.md`](./docs/data-dictionary.md).

### El Treasury a 10 años llega con un día hábil de retraso

No es un fallo: `DGS10` es diaria pero FRED la publica con lag. El 2026-09-02
la última observación disponible era la del **2026-08-31** (4.75 %), mientras
el mercado cotizaba 4.81 %. La plantilla ya lo dice —cada métrica lleva su
propio `as_of` y el 10 años se rotula «al 31/08/2026» (`playwright_engine.py:56-58`)—
así que el post es honesto. Sólo hay que saberlo: un cierre del viernes va a
mostrar el rendimiento del miércoles o el jueves, nunca el del día.

### La observabilidad estaba muda

Ver el bloqueador 5, arriba.

---

## ~~Auditoría pendiente~~ — hecha el 2026-09-02

Vive en **[`docs/data-dictionary.md`](./docs/data-dictionary.md)**: qué mide
exactamente cada una de las siete cifras que publicamos, qué transformación le
aplicamos, con cuánto retraso llega y si la etiqueta del post se corresponde
con eso. Cada fila se cerró contra los metadatos que devuelven las APIs, no de
memoria.

**Cómo quedó:** cuatro filas ya estaban bien y ahora se sabe *por qué*; dos se
arreglaron —el IPC y la etiqueta del Nasdaq—; una sigue abierta. Y apareció una
octava cosa que no estaba en ninguna fila, el precio intradía, que también se
arregló.

**Lo único que sigue abierto es la fila 6, el fallback de Alpha Vantage**, con
dos problemas y ninguna salida elegida:

- **`QQQ` sigue al Nasdaq-100, que no es el Composite de `^IXIC`.** Si FMP se
  cae, la tarjeta rotulada «Nasdaq Composite» pasa a mostrar otro índice. Los
  dos difieren 11,4 % en nivel, medido el 2026-09-02.
- **`TIME_SERIES_DAILY` devuelve cierres sin ajustar.** Sobre un ETF eso sesga
  el retorno en las semanas ex-dividendo, unas cuatro al año por instrumento.

No bloquea publicar, porque sólo aparece si FMP falla. Pero es exactamente la
ruta degradada, que es donde nadie mira.

**Lo que la auditoría dejó como método, más allá de sus respuestas.** Las dos
cosas que encontró de verdad —la divergencia del IPC y el precio intradía— las
encontró un humano mirando un borrador y desconfiando de un número. Ningún test
las podía encontrar: las dos eran cifras correctas bajo etiquetas equivocadas, y
las dos caían dentro de todos los rangos de `rules.yaml`. Lo que sí puede hacer
el código es **impedir que vuelvan**, y eso es lo que ahora fijan los tests de
identidad: un contract test que le pregunta a FRED qué es cada serie, y la
guarda de sesiones terminadas.

---

## Rediseño de la capa LLM — decidido el 2026-09-02, sesión propia

**También necesita una sesión propia**, y es independiente de publicar: el
pipeline puede salir a producción con la capa LLM tal como está.

### El problema

Simon miró el primer borrador real y preguntó si hace falta un LLM para
generarlo. La respuesta honesta es **no**: un template determinista produciría
ese mismo titular. Y hay algo peor cuando se mira de cerca — **el validador
existe para cazar cifras inventadas en el titular, un riesgo que sólo existe
porque un LLM escribe el titular**. La mitad de la capa resuelve un problema
que introdujo la otra mitad.

Bajo el objetivo que PLAN.md §1 fija desde hoy —demostrar ingeniería de
pipelines, integración de herramientas y manejo de APIs de modelos frontier—
eso no se sostiene: lo que la capa LLM demuestra hoy (structured outputs con
schema estricto, prompts versionados, temperatura 0, detección de retiro de
modelo, ADR-001) vive en el repo y es **todo defensivo**. Ninguna de esas
decisiones produce algo que se vea.

### La decisión: camino A

**Darle al LLM un trabajo que sólo él puede hacer, y que se vea.** Sumar una
fuente de **texto** —noticias del cierre, o el calendario de publicaciones
macro— y que el LLM haga lo que un template no puede: extracción estructurada
de texto no estructurado, clasificación y síntesis con citas. El post pasa de
«tres números» a «tres números y por qué se movieron».

Ataca las tres cosas del objetivo a la vez: **una fuente más** en el pipeline,
**integración de herramientas**, y una **API frontier haciendo algo no
trivial**. Y el validador deja de ser redundante: pasa a verificar que cada
cifra del texto exista en el snapshot, que es un trabajo real.

Se descartaron: **B**, mover la demostración al repo (evals, prompt caching,
batch API, métricas de tokens) —serio, pero sigue sin verse—; y **C**, sacar el
LLM del camino de publicación —el post gana confiabilidad y el proyecto pierde
el eje de las APIs frontier—.

### La tensión que esa sesión tiene que resolver

**Camino A roza ADR-001.** Una síntesis que explique movimientos anda cerca de
los números, y ADR-001 pone al LLM fuera del path numérico.

La frontera propuesta, a validar en el diseño: **el LLM puede *citar* cifras
que el pipeline le pasa, nunca calcularlas ni inferirlas**, y el validador
verifica esa invariante cifra por cifra contra el snapshot. Eso conserva la
decisión de ADR-001 y encima la vuelve visible, que es exactamente lo que hoy
falta.

Preguntas abiertas para esa sesión: qué fuente de texto (y si tiene API libre),
qué pasa cuando esa fuente falla —¿degrada al post de hoy, o aborta?—, y cómo
se evita que una síntesis plausible pero equivocada pase el validador.

---

## Orden sugerido

**Actualizado el 2026-09-02 al cierre de la sesión.** El paso 1 —la auditoría de
datos con el arreglo del IPC— está hecho, y de paso cayeron la etiqueta del
Nasdaq, el precio intradía y el bloqueador 4.

Siguen las dos pistas de siempre, y no compiten por el mismo riesgo: **publicar**
(donde ya no queda nada de significado sin resolver que bloquee) y **el objetivo
real** de PLAN.md §1, de donde sale el camino A.

### El orden

1. ~~Auditoría de datos, con el arreglo del IPC~~ — **hecha**, en
   [`docs/data-dictionary.md`](./docs/data-dictionary.md).
2. **Primera publicación real** — el próximo sábado, aprobando el botón en una
   corrida igual a la del ensayo. Es el siguiente paso.
3. **Camino A**: el rediseño de la capa LLM, en sesión propia, con el pipeline
   habiendo publicado al menos una vez.
4. **Trigger** (bloqueador 3), sábados, ya con la certeza de que el pipeline
   funciona de punta a punta en producción.
5. **Grafana** (bloqueador 5), que no bloquea nada de lo anterior.

Y sueltos, sin sesión propia: **el fallback de Alpha Vantage** (la única fila
abierta de la auditoría), **el CLI con `--dry-run`** —la mitad del bloqueador 4
que sigue viva— y **la etiqueta del log** `cause="capa_no_disponible"`, que hoy
no distingue una capa apagada a propósito de una sin credencial.

### Por qué la auditoría fue antes que el camino A

Se decidió así y la sesión lo confirmó: camino A hace que el post explique **por
qué se movieron** los números, y montar una síntesis encima de series mal
etiquetadas no arrastra el error, lo amplifica. Dos de las tres cosas que se
arreglaron —el IPC y el intradía— habrían quedado dentro de esa síntesis.

### Lo que hay que saber antes de la primera publicación

- **Sábado, con el mercado cerrado.** El motivo está en el bloqueador 3.
- **`USE_ANTHROPIC=false`.** El titular es determinista; aprobar publica ese
  texto, no uno redactado por un modelo.
- **Aprobar publica de verdad** en X y LinkedIn: las dos banderas están en
  `true`. Rechazar sigue siendo el ensayo seguro.
- **Si una corrida muere sin cerrar su fila**, el guard del lock hace que
  ninguna corrida futura publique ese cierre, y lo único que avisa es un
  Telegram. La reparación tiene que tocar **R2**, no sólo la base local: el
  estado remoto es el autoritativo y el arranque lo baja encima. Pasó el
  2026-09-02 y la primera reparación no sirvió justamente por eso.

---

## Deuda registrada, para después de la primera corrida

Todo lo de acá está diagnosticado y ninguna de las entradas se tocó. El motivo
es el mismo para todas: **cada una toca el camino que ejecuta la corrida del
sábado 2026-09-05, y no hay una corrida buena de referencia contra la que
comparar.** Arreglar y estrenar a la vez deja dos variables moviéndose, y si el
sábado sale mal no se sabe cuál fue. Después de la primera corrida limpia, cada
una se puede mirar por separado.

No hay fechas acá, y no es una omisión: nada de esto está planificado todavía.

### Ejecución

**`__main__.py` con `--dry-run`.** El único punto de entrada es el bloque
`if __name__ == "__main__"` de `orchestration/main.py:1161`; no hay
`console_scripts` en `pyproject.toml` ni `__main__.py`. No existe forma de
ejercitar el ETL, la validación y el render sin llegar al gate de Telegram.
Apagar las dos redes no lo sustituye: con `PUBLISH_X=false` y
`PUBLISH_LINKEDIN=false`, `_publisher_failures()` no devuelve nada —un switch
apagado es una decisión, no un fallo— y la corrida sale con `0` en
`orchestration/main.py:601` (`no_publishers_enabled`), **antes** de la fase de
datos. Hoy el único ensayo real es rechazar el borrador en Telegram.

**`mark_failed` no persiste el motivo.** `storage/state.py:310` recibe `reason`
y lo manda al log (`event_marked_failed`); la tabla creada en
`storage/state.py:66` no tiene columna para él. La consecuencia práctica: una
fila en `failed` no distingue un rechazo humano de una excepción, y por eso no
se sabe ni se puede saber por qué falló la corrida del 2026-08-27. Es cambio de
esquema y necesita migración, porque hay una base viva en R2.

**Una fila puede quedar en `failed` con publicaciones vivas.** Encontrado el
2026-09-03 leyendo el bloque `if approved` de `orchestration/main.py:1023`, no
por un fallo observado.

`mark_x_published` (`storage/state.py:215`) y `mark_linkedin_published`
(`storage/state.py:225`) escriben **sólo la columna del `post_id`**: dejan el
`status` en `in_progress`. Y `mark_failed` (`storage/state.py:310`) marca
`failed` toda fila cuyo estado no sea ya `published`. Entre medio hay dos
sitios que pueden levantar después de que X publicó:

- `linkedin.post_text` (`orchestration/main.py:1091`), que levanta
  `LinkedInClientError`.
- el push del estado a R2 dentro del propio `mark_x_published`: `_notify_write`
  (`storage/state.py:57`) **propaga lo que levante el hook**, y corre después
  de que el `UPDATE` local ya commiteó.

Cualquiera de los dos sube al manejador general (`orchestration/main.py:1116`),
que llama a `mark_failed`. Resultado: la fila dice `failed` con un `x_post_id`
poblado y un tweet publicado.

No es una pérdida de datos —el `post_id` queda persistido y la reconciliación
parcial hace que un relanzamiento del mismo día se saltee X y publique sólo
LinkedIn— pero el estado miente sobre lo que salió, y es el mismo campo que
mira quien audita después. Va junto con la columna de motivo: las dos son
cambios de esquema y de estados sobre una base viva.

Lo que **no** es un camino a esto, aunque lo parezca: la subida de la imagen a
R2. Corre **antes** que las dos redes (`orchestration/main.py:1030`), no
después, y su `except Exception` (`orchestration/main.py:1048`) degrada en el
sitio —loguea `r2_upload_failed_degrading`, avisa por Telegram y sigue con
`image_url=None`—, así que no llega al manejador general ni impide publicar.

**La clave de R2 lleva la fecha del día.** `orchestration/main.py:1033` sube la
imagen como `f"{event_id}.png"`, y `event_id` es `weekly_close_<fecha de hoy>`.
`R2Client.upload_image` (`storage/r2_client.py:131`) delega en un `put_object`
sin versionado de objeto ni cabecera condicional, así que dos corridas el mismo
día —el caso normal cuando la primera se rechaza y la segunda se aprueba—
sobrescriben la imagen de la primera.

**La imagen renderizada no se adjunta a las publicaciones.** Va a la preview de
Telegram y a R2. Los dos publicadores mandan sólo texto:
`publishers/x_client.py:49` (`post_tweet(text)`) y
`publishers/linkedin_client.py:40` (`post_text(text)`) no aceptan otra cosa.
Cambiarlo son dos endpoints distintos —el de subida de media de X y el de
imágenes de UGC Posts en LinkedIn—, así que es trabajo real y no un parámetro.

### Cobertura

**`tests/integration/test_orchestrator_startup_gate.py:80` mockea el camino de
datos entero.** El test se llamaba
`test_a_broken_fmp_degrades_to_alpha_vantage_and_publishes` y lo que verifica es
otra cosa: que FMP sin key alerta una sola vez nombrando la consecuencia y deja
que la corrida llegue a publicar. Nunca ejecuta la ruta de Alpha Vantage, porque
el `_orchestrator` de ese fichero reemplaza `_fetch_weekly_close` por un
`MagicMock`. Se le cambió el nombre a
`test_a_broken_fmp_alerts_and_lets_the_run_reach_publication`, que es lo que
hace; el rename no toca ningún otro test. Lo que queda pendiente es la cobertura
en sí, no el nombre: el fallback de verdad está ejercitado en
`tests/unit/test_orchestrator_startup.py:226`
(`test_la_ruta_de_av_no_publica_el_nivel`), que fuerza un `RuntimeError` en FMP
y comprueba que la fuente pasa a ser AV y que el nivel de cierre queda en
`None`.

**El orquestador dice «los seis componentes» y ya son ocho.**
`orchestration/main.py:630` arma la alerta de degradación de arranque con el
texto «`python scripts/check_credentials.py` verifica los seis componentes
contra su API de verdad». Eran seis cuando se escribió; con Telegram
(2026-09-02) pasaron a siete y con FMP (2026-09-03) a ocho, que es lo que hoy
cubre la lista `chequeos` del script y coincide con las ocho constantes
`*_VAR` de `components.py`.

Es una cadena, no lógica: la alerta llega igual y dice lo mismo de fondo. Pero
va a Telegram cuando un componente arranca sin credenciales, o sea justo cuando
alguien la va a leer para decidir si aprueba, y un número que no cuadra con lo
que el script imprime es de las cosas que hacen dudar del resto del mensaje. No
se toca ahora porque `orchestration/main.py` es el fichero que ejecuta la
corrida del sábado.

### Divergencias abiertas de ADR-009

Las tres están escritas en el propio ADR, con la fecha en que se encontraron.
Ninguna es un bug del código: son celdas de la tabla que prometen algo distinto
de lo que el código hace, y las tres se dejaron abiertas a propósito.

- **Divergencia 5** — «cualquier switch aborta con alerta» promete un aviso que
  no siempre llega: con `USE_TELEGRAM` ilegible, el switch roto es el del canal,
  así que se sale por `switch_invalid_no_channel_aborting` con código `1` y sin
  alerta. El código hace lo único que puede; lo que diverge es la celda.
- **Divergencia 6** — una red apagada y la otra rota abortan, y ninguna fila lo
  dice. `PUBLISH_X=false` con LinkedIn sin credenciales aborta con alerta y
  código `1`, que es correcto, pero leyendo la tabla se predice «degrada,
  publica en la otra». Falta una fila, no código.
- **Divergencia 7** — `fmp_runtime_error` puede nombrar la fuente equivocada, y
  sólo en desarrollo. Se carga en el `except` de FMP antes de intentar Alpha
  Vantage.

### Dependencias y código sin uso

**Rangos de runtime abiertos y sin lockfile** (`pyproject.toml`). Las de
desarrollo están fijadas exactas porque son gates de CI; las de runtime van con
`>=` y sin techo salvo `anthropic>=1,<2`. Hoy eso resuelve `pandas` de la serie
3.x bajo un rango escrito para la 2.x. No falla, y por eso mismo es deuda y no
incidente: lo que falta es un lockfile que haga reproducible la corrida.

**Tres piezas escritas, testeadas y sin usar desde ningún punto de entrada.**
Hay que decidir si se usan o se borran; dejarlas sin decidir es lo que las
convierte en ruido para quien lee el repo.

- `render/pillow_engine.py` — `PillowEngine`. ADR-003 le asigna las plantillas
  simples; el orquestador sólo instancia `PlaywrightEngine`. Su único consumidor
  es `tests/unit/test_render_pillow.py`.
- `data/fmp_client.py:93` — `FMPClient.get_earnings_calendar`. Ninguna fase del
  pipeline usa earnings.
- `validators/schemas.py:78` — `MacroReleaseData`, y con él
  `ValidationEngine.validate_macro_release`. No lo invoca ningún camino.

---

## Lo que este documento no cubre

- **Cobertura de CI para R2 y Telegram.** El nightly apaga los cinco
  componentes no publicadores, así que sus credenciales sólo se verifican
  corriendo el chequeo a mano. No bloquea publicar.
- **Las divergencias 5, 6 y 7 de ADR-009**, abiertas a propósito.
- **El residuo del sincronizado**: un crash con un corte de R2 encima puede
  dejar el remoto en `in_progress` y el local en `failed`. Desde el
  2026-09-01 eso alerta si el relanzamiento llega más de dos horas después.
