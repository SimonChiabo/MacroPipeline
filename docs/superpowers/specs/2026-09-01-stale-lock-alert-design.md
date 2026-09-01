# Alertar sobre un lock `in_progress` viejo — diseño

**Fecha:** 2026-09-01
**Estado:** Aprobado
**Cierra:** la mejora chica anotada al margen del trabajo de estado del
2026-08-31 — no un punto del backlog numerado, que quedó sin puntos abiertos

---

## Problema

`ADR-009` §«El segundo eje: qué estado deja un abort» fija cuatro formas de
terminar sin publicar. La cuarta —la fila queda en `in_progress`— está escrita
como **la que no se acepta**:

> | Aborta trabado | `in_progress` | **No se acepta**: el reintento del mismo
> `event_id` se salta en silencio |

El código hace exactamente eso. El guard de lock (`orchestration/main.py:657`)
pregunta `is_in_progress`, escribe `pipeline_already_running_skipping` en el log
y devuelve `0`. **Ese cierre no sale nunca**, y cada relanzamiento del mismo día
se lo vuelve a saltar sin decir nada: la única señal es una línea de log en un
runner efímero que nadie está mirando.

Esa forma de terminar no se puede eliminar —una muerte no atrapable (SIGKILL,
el runner que se apaga) deja la fila trabada por definición, y ningún `except`
la cubre—, así que lo que se puede arreglar no es que ocurra: es que ocurra en
silencio.

### El alcance exacto, que es más chico de lo que parece

El daño **no** se acumula semana a semana. `event_id` es
`f"weekly_close_{date.today()}"` (`main.py:660`), así que la run de la semana
que viene calcula otro `event_id`, `is_in_progress` responde `False` y publica
normal. Una fila trabada bloquea únicamente los relanzamientos del **mismo día
calendario**. Ya está dicho en el propio fichero, en el comentario del manejador
general (`main.py:1044-1046`): «el `event_id` lleva la fecha de hoy y la run es
semanal (ADR-002), asi que la proxima no reintenta este cierre — lo reemplaza».

Que sea más chico no lo vuelve tolerable. El resultado es **una publicación
semanal que falta, y cero señales de que faltó**.

### Por qué el silencio es la mitad mala

Un cierre saltado no es visible por su ausencia. La diferencia entre «esta
semana no había nada que publicar» y «esta semana el cierre se saltó por una
fila que quedó trabada» no se nota mirando Telegram. Al operador le llegan
alertas cuando algo degrada o falla, así que la ausencia de alerta se lee como
que todo anduvo.

---

## La regla, en una frase

**Un lock `in_progress` que no puede pertenecer a una run viva se avisa antes de
saltarse el cierre.**

Avisar, no reparar. Ver «Lo que este diseño no hace».

---

## 1. Qué cuenta como «no puede pertenecer a una run viva»

Una run sana puede estar `in_progress` un rato largo, y no por lentitud: la fase
de aprobación humana espera con `wait_for_approval(msg_id, timeout_seconds=3600)`
(`main.py:878-879`). **Una hora entera de `in_progress` es un estado
perfectamente sano** mientras el operador decide.

De ahí sale el umbral, que no es un número elegido a ojo:

```
umbral = timeout de aprobación (3600 s) + el resto del pipeline, con margen
       = 2 horas
```

Un umbral de una hora alertaría sobre toda run que el humano deja madurar, y una
alerta que a veces es ruido es una alerta que se aprende a ignorar. Dos horas
sólo se alcanzan si nadie está esperando del otro lado.

La constante vive con el porqué escrito al lado y **nombrando el `3600` del que
sale**: si alguien toca el timeout de aprobación, el comentario dice qué más hay
que mirar. Es la relación la que importa, no el valor.

---

## 2. `locked_at`: el dato que hoy no existe

El umbral necesita saber **cuándo se tomó el lock**, y en la base no hay tal
cosa.

`created_at` no sirve, y por un motivo concreto: `mark_in_progress` re-arma el
lock sobre una fila `failed`/`expired` con un `UPDATE ... SET status` a secas
(`state.py:152-156`) que **no toca ningún timestamp**. Una fila nacida hace tres
semanas y reintentada hace cinco minutos sigue diciendo que nació hace tres
semanas. Con `created_at` como reloj, cada reintento de una fila vieja alertaría
sobre una run que está corriendo ahora mismo.

Tampoco se re-significa `created_at`. Es cierto que hoy **no lo lee nadie** —se
escribe en el `INSERT` y no aparece en ninguna consulta, ni en el código ni en
los tests—, así que refrescarlo no rompería nada. Lo que dejaría es una columna
llamada `created_at` que significa «cuándo se tomó el lock»: un nombre que
miente, que es la clase exacta de residuo que este repo viene borrando.

Entonces: **columna nueva `locked_at`**, escrita en las **dos** ramas de
`mark_in_progress` —el `INSERT OR IGNORE` y el `UPDATE` del re-arm—. Refrescarla
en el re-arm es la mitad que hace útil al umbral.

**Tipo `TEXT`, ISO-8601 en UTC.** No `TIMESTAMP` con un objeto `datetime`: ese
camino usa el adaptador por defecto de `sqlite3`, deprecado desde Python 3.12 y
que ya emite `DeprecationWarning` en esta suite (`state.py:234`). Código nuevo no
se suma a un camino deprecado. El resto de la tabla ya guarda fechas como texto
(`cpi_as_of`, `unrate_as_of`, `dgs10_as_of`).

**Migración:** una línea más en la lista de `_migrate_db` (`state.py:93-110`),
que ya es idempotente y se traga el `OperationalError` de la columna existente.
Sin backfill — ver el punto siguiente.

---

## 3. `locked_at IS NULL` alerta

Una fila `in_progress` sin `locked_at` sólo puede venir de antes de la
migración. Y no va a dejar de estarlo nunca: `mark_in_progress` **no toca las
filas `in_progress`** —su `WHERE` es `status IN ('failed', 'expired')`, y eso es
justo lo que hace que `is_in_progress` sirva de guarda—, así que esa fila no
recibe un `locked_at` en ninguna corrida futura.

Tratar el NULL como «no alertar» preservaría el salto en silencio **para
siempre, y exactamente en la fila que motiva todo este trabajo**. `in_progress`
con antigüedad desconocida es el estado que más necesita un humano, no el que
menos.

Un backfill copiando `created_at` tampoco: escribiría una aproximación —cuándo
nació la fila, no cuándo se tomó el último lock— en una columna cuyo valor
entero está en ser exacta, para ahorrarse una rama de tres líneas.

---

## 4. Dónde va la alerta

En el guard de lock, `main.py:656-661`, antes del `return 0`.

El canal está disponible sin tocar nada: el local `telegram` ya está estrechado
en `main.py:649`, ocho líneas más arriba, con un `RuntimeError` explícito si el
punto de decisión dejó pasar una run sin canal. Y `send_alert` **nunca levanta**
(`telegram/bot.py:98-120`): devuelve `False` y lo registra. Alertar acá no puede
convertir un cierre saltado en una run reventada.

El texto dice la antigüedad, el `event_id` y qué hacer, porque el operador que
lo recibe tiene que decidir una intervención manual y la alerta es todo lo que
tiene. Con `locked_at` en NULL dice que la antigüedad se desconoce, que es un
dato distinto de «hace mucho» y se lee distinto.

### El reparto de responsabilidades

`is_in_progress` **no se toca**: es el guard, tiene tests, y sigue respondiendo
la única pregunta que le corresponde. La antigüedad la sirve un lector nuevo en
`StateDB` que devuelve el `locked_at` de la fila (o `None`), sin interpretarlo.

**La política vive en el orquestador.** El umbral de dos horas sale del timeout
de aprobación, que es una decisión del pipeline y no de la capa de estado; poner
el número en `StateDB` sería obligarla a saber cuánto tarda una run sana.

---

## 5. Lo que este diseño no hace

**No toma el lock. No llama a `mark_expired`. No cambia el `return 0`.**

`mark_expired` está a unas líneas y es la tentación obvia: si la fila está
trabada, expirarla y seguir. Se descarta por escrito.

ADR-009 clasifica la fila trabada como la forma que **exige intervención
manual**, y el motivo sobrevive al detalle: el umbral dice que una run viva es
*improbable*, no que sea *imposible*. Un operador que tardó tres horas en
aprobar, una segunda instancia lanzada a mano, un reloj corrido — cualquiera de
los tres convierte el auto-expirado en dos runs publicando el mismo cierre. El
peor resultado posible del sistema es publicar dos veces, y es exactamente
contra eso que existen el lock y el `AND status != 'published'` de `mark_failed`.

La mejora es que el salto deje de ser silencioso. Que se auto-repare es otro
trabajo, con otro diseño, y probablemente no valga la pena.

---

## 6. Tests

- Lock reciente: no alerta.
- Lock más viejo que el umbral: alerta, y el cierre igual se salta (`return 0`).
- `locked_at IS NULL` sobre una fila `in_progress`: alerta.
- **El re-arm refresca `locked_at`.** Es la que importa: sin ella, un reintento
  legítimo de una fila vieja alertaría, y la mutación que lo introduce —borrar el
  refresco del `UPDATE` y dejar sólo el del `INSERT`— deja el resto de la suite
  en verde. Es el mismo patrón que la mutación del rango de retorno del NASDAQ.
- La migración es idempotente sobre una base que ya tiene la columna.

Sobre `StateDB` real, como el resto de los tests de estado de punta a punta.

---

## 7. ADR-009

La fila `in_progress` de la tabla del segundo eje pasa de:

> **No se acepta**: el reintento del mismo `event_id` se salta en silencio

a decir que se salta **y alerta**, con la nota de que la forma sigue sin
aceptarse: alertar no la vuelve aceptable, la vuelve visible.

---

## Un hilo que esto toca de refilón

El primer residuo del sincronizado de estado —si la excepción que mata la run no
es del sincronizado, un crash con un corte de R2 encima deja el remoto en
`in_progress` y el local en `failed`— termina con la run siguiente en un runner
efímero bajando ese `in_progress` y cayendo justo en el guard de la línea 657.

Esta alerta **no cierra ese residuo**: el cierre se sigue saltando, y cerrarlo
de verdad pide un segundo canal de escritura.

Y le saca el «en silencio» sólo a medias, que conviene no exagerar. Si el
relanzamiento llega más de dos horas después, avisa. Si llega enseguida —el
caso más probable, porque relanzar a mano se hace al ratito— el lock tiene
minutos y el umbral se calla, que es exactamente lo que tiene que hacer: a esa
antigüedad no hay forma de distinguirlo de una run viva.
