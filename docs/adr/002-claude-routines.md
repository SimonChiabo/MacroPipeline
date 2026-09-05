# ADR-002: Claude Routines como orquestador principal

**Estado:** Aceptado (con plan B documentado)  
**Fecha:** 2026-05-14  
**Decisores:** Simon Chiabo

---

## Contexto

El pipeline necesita un trigger programado (cron) que ejecute el script Python una vez por semana, al cierre del mercado. (Cuando se escribió esto el día era el viernes; es el sábado desde el 2026-09-02, ver la corrección en Consecuencias.) Las opciones evaluadas fueron: servidor propio con cron, GitHub Actions scheduled workflow, o Claude Routines (feature de Anthropic en research preview).

---

## Decisión

Usar **Claude Routines** como orquestador principal. Routines clona el repositorio y ejecuta el script `src/macro_pipeline/orchestration/main.py` en un entorno gestionado por Anthropic. La llamada a Claude API se hace desde dentro del script Python, no desde Routines directamente.

---

## Consecuencias

**Positivas:**
- Cero infraestructura propia para el scheduler.
- Entorno gestionado (patches de seguridad, uptime) sin coste adicional.
- Encaja con la suscripción de Anthropic existente.

**Negativas:**
- Routines está en research preview: puede cambiar, degradarse o desaparecer.
- El daily cap de Routines (15 ejecuciones/día) es suficiente para el volumen actual pero debe monitorizarse.
- Dependencia de vendor para la función de scheduling.

**Plan B (documentado, no implementado):**  
Si Routines cambia en incompatible con el proyecto, migrar a GitHub Actions scheduled workflow:
```yaml
on:
  schedule:
    - cron: "30 21 * * 6"  # Sábado. La hora está sin decidir; ver corrección de abajo
```
El script Python no necesita cambios: acepta cualquier trigger externo.

**Corrección del 2026-09-05 — el día es sábado, no viernes.**  
Este ADR decía «cada viernes al cierre del mercado» en su contexto, y su Plan B
proponía `30 21 * * 5`. Ambas cosas quedaron obsoletas el **2026-09-02** y este
documento no se enteró hasta hoy: el día de publicación ya es el sábado, escrito
en `docs/data-dictionary.md` §8 y en ROADMAP.md (bloqueador 3).

**El motivo real no es de calendario sino del dato.** El endpoint
`historical-price-eod/full` de FMP devuelve también una fila para la sesión *en
curso*, con el último precio negociado en el campo `close`; una corrida del
viernes por la mañana publicaba un precio intradía rotulado «Cierre». El arreglo
fue que `_fetch_weekly_close` descarte las filas con fecha de hoy —sólo sesiones
terminadas—, y **es esa guarda la que empuja al sábado**: un viernes, descartar
la sesión en curso deja el post con el cierre del jueves. El sábado la guarda no
cuesta frescura y además deja la ventana semanal en viernes contra viernes, que
es la más limpia que da el cálculo de `BDay(5)`.

**El costo, aceptado explícitamente el 2026-09-05:** se llega más tarde que
nadie, y una corrida del viernes por la noche seguiría publicando el jueves
aunque el dato del viernes ya fuera definitivo. Para el MVP vale más el dato con
criterio que ser el primero en publicar — que en este proyecto no es un objetivo
secundario sino directamente no es un objetivo: el sistema existe para demostrar
ingeniería, no para divulgar (PLAN.md §1).

El día cambió; **la hora del Plan B no está decidida**. Las 21:30 UTC eran el
cierre de mercado USA del viernes, que un sábado no significa nada. El `30 21`
del snippet es el valor heredado, no una decisión: hay que fijarlo el día que
este Plan B se implemente.

Nada de esto toca el resto del ADR: la idempotencia y su dependencia del
sincronizado por R2 valen igual con cualquier día y cualquier trigger.

**Consecuencia inmediata, ya aplicada:** el nightly de contract tests
(`contract-tests.yml`) corría `1-5`, así que el sábado —el único día que importa
para publicar— era justo el único sin verificación de contratos ni de
credenciales, y dentro de ese job viven el check de los siete componentes y la
alarma de vencimiento del token de LinkedIn. Su cron pasó a `1-6` el 2026-09-05.

**Implicación de resiliencia:**  
El script es idempotente: si se ejecuta dos veces el mismo día, la segunda ejecución detecta el `event_id` ya publicado en SQLite y termina sin publicar.

**Corrección del 2026-08-31 — de qué depende esa idempotencia.**  
Tal como estaba escrita, la frase de arriba daba por sentado que el fichero SQLite sobrevive de una ejecución a la siguiente, y esta misma decisión dice que Routines **clona el repositorio y ejecuta en un entorno gestionado**. Si ese entorno es efímero, el fichero no sobrevive con **ninguna** ruta —`STATE_DB_PATH` apunta al mismo filesystem— y `is_published()` devuelve False siempre, sin error y sin log que lo distinga de una primera ejecución legítima.

Lo más grave no es perder registros sino **publicar dos veces**: con el estado vacío, un reintento posterior a haber publicado en X y haber reventado antes de LinkedIn vuelve a postear en X.

Desde el 2026-08-31 el fichero viaja por R2 (`storage/state_sync.py`): se baja al arrancar `run_weekly_close` y se sube después de cada escritura. **La idempotencia de este ADR descansa ahora en ese sincronizado**, y por eso una bajada fallida aborta la corrida antes del lock en vez de seguir a ciegas. Ver ADR-009, filas «R2 (estado)».

Sigue abierta la pregunta de si Routines persiste el workspace entre ejecuciones. No cambia el arreglo: el Plan B de más arriba es GitHub Actions, efímero por construcción.
