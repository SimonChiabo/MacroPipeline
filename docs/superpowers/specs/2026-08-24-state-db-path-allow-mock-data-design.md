# Decidir STATE_DB_PATH y ALLOW_MOCK_DATA

**Fecha:** 2026-08-24
**Estado:** Aprobado, sin implementar
**Motivo:** son las dos unicas variables que `python scripts/check_publishers.py`
reporta como deriva, y un chequeo con ruido es un chequeo que se termina ignorando.

---

## Lo que se verifico antes de decidir

Tres hechos que cambian el planteo con el que empezo la tarea:

1. **El `macropipeline.db` de la raiz no lo causa la falta de `STATE_DB_PATH`.**
   Su esquema tiene tres columnas (`event_id`, `published_at`, `image_url`): es
   el formato anterior a la migracion, creado por el codigo viejo que tenia
   `db_path="macropipeline.db"` relativo al CWD (`revision_v2_resultado.md:212`).
   Definir `STATE_DB_PATH` hoy no lo habria evitado ni lo elimina. Son dos
   acciones independientes.

2. **`~/.macropipeline/state.db` no existe.** `Path.home()/'.macropipeline'`
   devuelve `exists: False`. No hay estado real en ninguna parte: la fila
   `weekly_close_2026-05-14` del archivo huerfano es el unico registro de
   publicacion que existe en la maquina.

3. **Los dos defaults del codigo ya son el valor correcto.**
   `ALLOW_MOCK_DATA` cae en `"false"` (`orchestration/main.py:88`), igual que el
   ejemplo. `STATE_DB_PATH` cae en `~/.macropipeline/state.db`
   (`storage/state.py:16`), que ya es absoluta y ajena al CWD — eso es lo que
   cierra el bug de la base relativa al directorio de trabajo. ADR-007 pide
   aparte que la variable sea configurable, para poder apuntar a un fichero
   migrado si se cambia de maquina; es una promesa distinta, y le alcanza con
   que la variable exista. Escribir cualquiera de las dos en `.env` con
   su valor actual no cambia ninguna linea de comportamiento.

De ahi que esto no sea una decision de configuracion sino una sobre el
vocabulario del chequeo de deriva: como tratar una variable declarada, opcional
y corriendo a proposito con su default. El mecanismo ya existe —
`_commented_names()`, escrito para `LINKEDIN_TOKEN_ISSUED`.

## Decision

Las dos variables no son simetricas y no reciben la misma respuesta.

**`STATE_DB_PATH`: comentada en `.env.example`, sin poner en `.env`.**
El default ya es correcto, y una ruta especifica de esta maquina en el `.env` es
una cosa mas que puede quedar mal cuando cambie algo. Queda declarada como
opcional, con el porque al lado.

**`ALLOW_MOCK_DATA`: sigue sin comentar en `.env.example` y se pone explicita en
`.env` como `false`.**
Es la bandera que decide si se puede publicar con datos sinteticos. Que su valor
dependa de un default en el codigo es un eslabon de mas: un valor explicito
sobrevive a que alguien cambie ese default. Misma defensa en profundidad que
dejo la limpieza de asteriscos de `734e2b4`.

**El `macropipeline.db` de la raiz se borra.** Esquema viejo, de codigo que ya no
existe, con una fila cuyo `event_id` lleva la fecha dentro y por lo tanto no
puede repetirse: su valor de deduplicacion es cero. Lo que documenta esta en git
y en R2. Se muestra el contenido completo antes de borrar.

## Cambios

1. `.env.example`: `STATE_DB_PATH` pasa a declaracion comentada, con el comentario
   reescrito para decir que se corre con el default a proposito.
2. `.env.example`: `ALLOW_MOCK_DATA` se queda como esta (sin comentar).
3. `.env`: agregar `ALLOW_MOCK_DATA=false`.
4. Borrar `macropipeline.db` de la raiz.
5. `tests/unit/test_check_publishers.py`: reescribir el docstring de
   `test_runs_against_the_real_repo_files` —hoy dice que la decision esta
   pendiente— y agregar dos asserts junto al de `TELEGRAM_ALLOWED_USER_ID`
   fijando que ninguna de las dos vuelva a aparecer como hallazgo.

## Verificacion

`compare_env_files()` sobre un par de ficheros temporales con las dos
declaraciones comentadas, ejecutado contra la funcion real antes de decidir:

```
comentadas, sin poner  -> []
comentadas, una puesta -> []
_commented_names       -> {'STATE_DB_PATH', 'ALLOW_MOCK_DATA'}
```

Comentar silencia las dos direcciones del chequeo, y una variable que despues se
ponga en el `.env` tampoco salta como "sin documentar". Cero cambios de codigo en
`scripts/check_publishers.py`.

Al terminar, `python scripts/check_publishers.py` no debe imprimir ningun
`[AVISO]` en la seccion `.env vs .env.example`.

## Fuera de alcance

**No se convierte `test_runs_against_the_real_repo_files` en un assert de deriva
cero.** Es la misma trampa que ya se evito en el codigo de salida del script: la
primera variable que se agregue al `.env` antes de documentarla rompe los tests
locales, y asi es como alguien termina desactivando el chequeo. Informa, no
bloquea.

**Persistencia del estado en produccion.** Hoy nada corre
`orchestration/main.py` en un schedule: el unico cron del repo es el nightly de
contract tests, y no hay configuracion de Routines versionada. Si Routines clona
el repo en un entorno efimero como describe ADR-002, ningun valor de
`STATE_DB_PATH` conserva el estado entre runs, y la idempotencia que ese ADR
promete ("la segunda ejecucion detecta el `event_id` ya publicado en SQLite") no
se sostiene ahi. Es un problema sin construir, no uno roto, y va al backlog como
punto aparte.
