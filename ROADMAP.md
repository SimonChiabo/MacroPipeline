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
| **Corridas del pipeline** | **Una.** `weekly_close_2026-08-27`, terminó en `failed`. La fila no guarda motivo: `mark_failed` todavía no lo registraba. |
| **Estado remoto** | Ausente. `state/state.db` no existe en R2 — esa corrida es anterior al sincronizado, que se escribió el 2026-08-31. |
| **Credenciales** | Las siete verificadas hoy contra sus APIs (`scripts/check_credentials.py`, código 0). |
| **Trigger programado** | **No existe.** En `.github/workflows/` sólo hay `ci.yml` y `contract-tests.yml`, y ninguno ejecuta el pipeline. No hay Routine creada. |
| **Punto de entrada** | `python src/macro_pipeline/orchestration/main.py` — el bloque `if __name__ == "__main__"` (`main.py:1103`). |
| **Renderizado** | Playwright con Chromium instalado en la máquina local. |

Las cinco fases del pipeline están escritas y testeadas por separado: datos,
validación, renderizado, LLM, HITL y publicación (`main.py:746-964`). **Nunca
corrieron las cinco juntas contra datos reales.** Ése es el riesgo central de
todo lo que sigue.

---

## Dos cosas que hay que saber antes de planificar nada

### No hay dry-run, y apagar las dos redes no lo sustituye

El README ofrece `python -m macro_pipeline run weekly-close --dry-run`. **Ese
comando no existe**: no hay `__main__.py`, no hay `console_scripts` en
`pyproject.toml` y no hay ninguna bandera `--dry-run` en el código.

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

### 1. Las cinco fases nunca corrieron juntas — *el bloqueador real*

Todo lo demás es logística. Esto es lo único que no se sabe: si el pipeline
completo, contra datos reales de un viernes, llega hasta el botón de aprobar.

**Qué hacer:** correr `python src/macro_pipeline/orchestration/main.py` a mano,
con las banderas de publicación **encendidas**, y **rechazar** el borrador en
Telegram. Repetir hasta que la corrida llegue limpia al botón.

Qué mirar en esa corrida, además de que no reviente:

- **Los números del borrador**, contra la fuente. Es la única verificación que
  ningún test puede hacer: los tests fijan el formato, no la veracidad.
- **De qué fuente salió el cierre.** Si vino por Alpha Vantage, el nivel no se
  publica y el renderer sube la variación semanal en su lugar (ADR-009,
  divergencia 4). Es correcto, pero conviene verlo una vez con los ojos.
- **El titular del LLM.** Si dice «Cierre Semanal: Resumen del Mercado» a secas,
  la capa LLM cayó al fallback y hay que mirar por qué.
- **Que llegue el aviso de «primera corrida o pérdida de estado».** Es esperado
  y no es un fallo: no hay estado remoto todavía y esa corrida lo siembra.

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

### 4. El README promete un comando que no existe

Dos salidas, y hay que elegir una:

- **Corregir el README** para que documente el punto de entrada real. Diez
  minutos, cero código nuevo.
- **Construir el CLI** (`__main__.py` con `run weekly-close` y un `--dry-run`
  de verdad, que corra las cinco fases y se detenga antes de publicar). Es más
  trabajo, pero da el ensayo repetible que hoy no existe y que haría falta cada
  vez que se toque el ETL o el renderer.

Recomendación: corregir el README ahora —es una mentira en la primera página
del repo— y decidir el CLI después del bloqueador 1, cuando se sepa cuántas
veces hizo falta ensayar.

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

### El IPC que publicamos no es el que publica todo el mundo

`macro.py:15` usa **`CPIAUCSL`**, la serie **desestacionalizada**. La cifra que
citan los medios y el BLS como «IPC interanual» sale de **`CPIAUCNS`**, la
serie **sin desestacionalizar**. Con el dato de julio de 2026, la diferencia es
real y visible:

| Serie | YoY |
|---|---|
| `CPIAUCSL` (la que publicamos) | **3.30 %** |
| `CPIAUCNS` (la del titular) | **3.36 %** → se redondea a **3.4 %** |

Cualquiera que compare el post contra una noticia va a ver 3.3 donde el mundo
dice 3.4, y va a concluir que el pipeline calcula mal. Es la misma familia que
la divergencia 4 de ADR-009 —la etiqueta correcta sobre el instrumento
equivocado—, y por lo tanto **hay que arreglarlo antes de publicar**. La
convención es: desestacionalizada para la variación mensual, sin desestacionalizar
para la interanual, que es la que este bloque muestra.

### El Treasury a 10 años llega con hasta dos días de retraso

No es un fallo: `DGS10` es diaria pero FRED la publica con lag. El 2026-09-02
la última observación disponible era la del **2026-08-31** (4.75 %), mientras
el mercado cotizaba 4.81 %. La plantilla ya lo dice —cada métrica lleva su
propio `as_of` y el 10 años se rotula «al 31/08/2026» (`playwright_engine.py:56-58`)—
así que el post es honesto. Sólo hay que saberlo: un cierre del viernes va a
mostrar el rendimiento del miércoles o el jueves, nunca el del día.

El desempleo (4.1 %) coincidió exacto con la fuente.

### La observabilidad estaba muda

Ver el bloqueador 5, arriba.

---

## Orden sugerido

1. **Ensayo con rechazo** (bloqueador 1). Repetir hasta que llegue limpio al
   botón.
2. **Primera publicación real**, aprobando el botón en una corrida igual a la
   anterior.
3. **Trigger** (bloqueador 3), ya con la certeza de que el pipeline funciona.
4. **README/CLI** (bloqueador 4) y **Grafana** (5), en cualquier orden.

**El ensayo del paso 1 no necesita esperar al viernes**, y conviene hacerlo
antes. Verificado en el código el 2026-09-02:

- El retorno **no** es de lunes a viernes: es una ventana móvil de cinco días
  hábiles. `_fetch_weekly_close` toma el último cierre disponible y lo compara
  contra el primero que sea anterior o igual a ese día menos `BDay(5)`
  (`main.py:377-393`), con el corte por fecha real para no sesgarse con los
  feriados. Un miércoles da miércoles contra miércoles: un retorno semanal
  legítimo, no media semana.
- El bloque macro es una ventana hacia atrás desde hoy (`macro.py:95-96`) y la
  plantilla imprime la fecha del propio dato (`playwright_engine.py:137`).
  Ninguna de las cinco fases pregunta qué día de la semana es.
- **El `event_id` lleva la fecha** (`weekly_close_2026-09-02` contra
  `weekly_close_2026-09-04`), así que un ensayo entre semana no ocupa la fila
  del viernes ni puede interferir con la corrida real.

De regalo, el ensayo siembra el estado remoto en R2, así que el viernes ya no
llega el aviso de «primera corrida o pérdida de estado» y una sorpresa menos
cae en el día que importa.

El paso 2 —la publicación de verdad— sí conviene que sea un viernes, que es la
cadencia que el proyecto eligió.

---

## Lo que este documento no cubre

- **Cobertura de CI para R2 y Telegram.** El nightly apaga los cinco
  componentes no publicadores, así que sus credenciales sólo se verifican
  corriendo el chequeo a mano. No bloquea publicar.
- **Las divergencias 5, 6 y 7 de ADR-009**, abiertas a propósito.
- **El residuo del sincronizado**: un crash con un corte de R2 encima puede
  dejar el remoto en `in_progress` y el local en `failed`. Desde el
  2026-09-01 eso alerta si el relanzamiento llega más de dos horas después.
