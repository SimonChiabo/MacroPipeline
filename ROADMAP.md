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
| **Corridas del pipeline** | **Cinco.** La del 2026-08-27, `failed` sin motivo guardado; y cuatro el 2026-09-02: el primer ensayo completo (rechazado a mano), uno que murió sin red por un timeout externo, uno que se salteó por el lock huérfano que dejó el anterior, y el ensayo final con el pipeline determinista, rechazado a mano y cerrado con código 0. |
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

No se sabe, y no se puede saber: esa fila no guarda motivo. Desde entonces
`mark_failed` sí lo registra, así que **el bloqueador 1 responde también a
éste**: la próxima corrida deja el motivo escrito si vuelve a fallar.

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

## Lo que este documento no cubre

- **Cobertura de CI para R2 y Telegram.** El nightly apaga los cinco
  componentes no publicadores, así que sus credenciales sólo se verifican
  corriendo el chequeo a mano. No bloquea publicar.
- **Las divergencias 5, 6 y 7 de ADR-009**, abiertas a propósito.
- **El residuo del sincronizado**: un crash con un corte de R2 encima puede
  dejar el remoto en `in_progress` y el local en `failed`. Desde el
  2026-09-01 eso alerta si el relanzamiento llega más de dos horas después.
