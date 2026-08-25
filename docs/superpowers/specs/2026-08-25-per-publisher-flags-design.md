# Banderas de publicacion por red

**Fecha:** 2026-08-25
**Estado:** Aprobado, sin implementar
**Cierra:** el punto 8 del backlog — `publishers_ready` es una sola bandera para
los dos clientes, asi que si uno falla al construirse el otro tampoco publica.
**Agrega:** un interruptor deliberado por red, que no estaba en el pendiente
original y que Simon pidio durante el diseno.

---

## Lo que se verifico antes de disenar

1. **Los dos clientes se construyen en el mismo `try`**
   (`orchestration/main.py:89-95`). `XClient` levanta `ValueError` si falta
   alguna de sus **cuatro** credenciales (`publishers/x_client.py:34-38`);
   `LinkedInClient`, si falta alguna de sus **dos**
   (`publishers/linkedin_client.py:26-30`). Un `ValueError` de cualquiera de
   las seis apaga las dos redes.

2. **La guarda pre-lock ya existe y esta bien ubicada**
   (`orchestration/main.py:242`): aborta antes del lock, sin tocar el estado,
   con alerta. Es el fix de `5ba7997`. Lo unico que le falta es granularidad:
   el texto dice "falta alguna credencial de X o LinkedIn" sin decir cual.

3. **La alerta de degradacion de la capa LLM va antes de pedir aprobacion, a
   proposito** (`orchestration/main.py:370-376`): el operador tiene que saber
   que aprueba antes de aprobarlo. Fija donde va la alerta de este diseno.

4. **`mypy --strict`** (`pyproject.toml:60-62`). Un cliente opcional obliga a
   estrechar el tipo con `is not None`, no con una bandera booleana aparte.

5. **`LINKEDIN_TOKEN_ISSUED=2026-08-21` esta cargado**, o sea que el aviso de
   vencimiento de `check_publishers.py` se dispara solo alrededor del
   2026-10-10. Este diseno le da al vencimiento una salida que hoy no tiene.

---

## Las tres decisiones

### 1. Una red rota degrada; solo ninguna red aborta

Es lo que el criterio de ADR-009 ya predice — *se degrada cuando el fallo solo
cuesta contexto, se aborta cuando podria hacer que una cifra publicada sea
incorrecta o cuando impide publicar*. Publicar solo en X no hace que ninguna
cifra sea incorrecta y no impide publicar: impide publicar **en una red**.

La run degradada termina en `mark_as_published`: fue un exito con menos
alcance, no un fallo. Sin fila `failed`, sin reintento.

**Alternativa descartada:** publicar en la red disponible y dejar la run en
`failed`, para que el reintento use la reconciliacion por `post_id` y complete
la red que falto. No sirve porque **`event_id` lleva la fecha**
(`weekly_close_{date.today()}`): el reintento solo reconcilia **el mismo dia**.
Al dia siguiente es otro `event_id`, no hay fila, y X recibiria el cierre por
segunda vez. Un reintento que casi nunca ocurre y que cuando ocurre tarde
duplica la publicacion es peor que no tenerlo.

### 2. Un apagado deliberado es silencioso

ADR-009 dice que *toda degradacion tiene que alertar, o se vuelve invisible y se
repite semanas*. La razon de esa regla es que el lector no distingue una
publicacion degradada de una normal. Una red que apagaste vos no tiene ese
problema: no es invisible, es tu decision.

Alertar cada semana por una pausa deliberada es el ruido que hace que se dejen
de leer las alertas, y entonces la que importa —el token vencido— se pierde
entre las que no. La distincion que se fija es: **si llega una alerta, es porque
algo se rompio.**

Una red apagada se loggea (`publisher_disabled`) y nada mas.

**Consecuencia buscada:** token de LinkedIn vencido + `PUBLISH_LINKEDIN=false`
da una run **verde y silenciosa** publicando solo en X. La bandera es la forma
de aceptar el vencimiento de octubre sin que el pipeline avise todas las semanas
de algo ya sabido.

### 3. `check_publishers.py` mira las banderas

El codigo de salida del script significa "las credenciales de publicacion
sirven". Una red apagada no tiene credenciales que sirvan ni que dejen de
servir: no participa. Sus chequeos se saltan, se imprime que esta apagada, y no
cuenta para el codigo de salida. Con las dos apagadas el script sale 0.

Es el mismo razonamiento que ya se aplico a la deriva de `.env`: un gate que se
pone rojo por una decision tomada a proposito termina desactivado o ignorado.

---

## Diseno

### Las variables

Explicitas en `.env` y en `.env.example`, en un bloque propio:

```
# --- Publicacion por red ---
# true/false estrictos. Una red apagada no se construye, no publica y no
# alerta: es una decision, no un fallo.
PUBLISH_X=true
PUBLISH_LINKEDIN=true
```

Van explicitas y no heredadas del default del codigo por el mismo motivo que
`ALLOW_MOCK_DATA`: deciden si se publica.

### El parseo

Helper `_publisher_enabled(var: str) -> bool` en `orchestration/main.py`:

- Ausente o vacia -> `True`.
- `true` / `false`, sin distinguir mayusculas y ignorando espacios -> lo obvio.
- **Cualquier otro valor levanta `ValueError`** y la run muere en el
  constructor, antes de tocar nada.

El estricto no es celo. Con un parseo laxo `PUBLISH_LINKEDIN=no` significa
"apagada" si se compara contra `"true"`, y "encendida" si se compara contra
`"false"`; las dos lecturas son silenciosas y las dos son malas — una pausa que
no pausa, o una pausa que nadie pidio. Morir con un mensaje claro en la primera
run despues del typo es lo unico que se ve. Mismo patron que `STATE_DB_PATH=` en
blanco abriendo una base temporal sin decirlo.

### La construccion

`publishers_ready` desaparece. En `__init__`:

```python
self.x_enabled = _publisher_enabled("PUBLISH_X")
self.linkedin_enabled = _publisher_enabled("PUBLISH_LINKEDIN")

self.x_client: XClient | None = None
if self.x_enabled:
    try:
        self.x_client = XClient()
    except ValueError as e:
        logger.warning("x_not_configured", reason=str(e))
else:
    logger.info("publisher_disabled", publisher="x")
```

Identico para LinkedIn, con `self.linkedin: LinkedInClient | None`.

La disponibilidad **no es un atributo**: es `self.x_client is not None`, expuesta
como propiedad de solo lectura `x_ready` / `linkedin_ready` para que las guardas
se lean. Dos consecuencias buscadas:

- **Una sola fuente de verdad.** `r2_ready` hoy es un atributo aparte que puede
  desincronizarse de `self.r2`; aca no puede. (No se toca `r2_ready` en este
  cambio: queda anotado, no hecho.)
- **Los tests no pueden volver a usar la bandera como atajo para no mockear el
  cliente.** Es literalmente lo que paso con `publishers_ready=False`: los
  cuatro tests de integracion corrian por el camino roto y lo afirmaban
  (`mark_as_published.assert_called_once()`). Con una propiedad de solo lectura,
  declarar un cliente listo obliga a poner un cliente.

En la fase de publicacion las guardas van como `if self.x_client is not None`,
no `if self.x_ready`, porque `mypy --strict` estrecha el tipo con lo primero y
no con lo segundo.

### La guarda pre-lock

Aborta solo si **ninguna** red puede publicar, y distingue por que:

| Situacion | Que hace | Alerta |
|---|---|---|
| Al menos una lista | Sigue | — |
| Ninguna lista, alguna encendida y rota | Aborta antes del lock, sin fila | **Si**, nombrando la rota |
| Las dos apagadas por bandera | Aborta antes del lock, sin fila | **No** — log `no_publishers_enabled` |

Las dos formas de abortar dejan el estado igual que hoy: **ninguna fila**, asi
que la proxima run reintenta sola. No cambia nada del eje de estados de
ADR-009.

### La alerta de degradacion

Cuando una red esta lista y la otra esta **rota** (no apagada), la alerta va
**antes de `send_approval_request`**, junto a la de la capa LLM y por el mismo
motivo: quien aprueba tiene que saber que ese cierre sale en una sola red. El
texto nombra la red caida y el motivo del `ValueError`.

Nunca hay alerta por una red apagada, ni siquiera combinada con una rota: si X
esta apagada y LinkedIn rota no publica nadie, y esa es la fila 2 de la tabla de
arriba — alerta por LinkedIn, silencio sobre X.

### La fase de publicacion

El bloque `if self.publishers_ready:` se parte en dos guardas independientes.
La reconciliacion por `post_id` (`x_already_done`, `linkedin_already_done`) no
cambia: se combina con la disponibilidad, no la reemplaza.

### `check_publishers.py`

`check_x()` y `check_linkedin()` se saltan si su red esta apagada, imprimiendo
que lo esta, y no cuentan para el codigo de salida. Se reescribe el bloque final
(`scripts/check_publishers.py:255-263`), que hoy nombra `publishers_ready`.

### ADR-009

Las dos filas de `X / LinkedIn` de la tabla por componente se reemplazan por
tres: al menos una lista (degrada), ninguna lista (aborta pre-lock), apagada por
bandera (no es un fallo). Mas un parrafo corto con la decision y su derivacion
del criterio, en la seccion de casos que merecen razon explicita. La divergencia
4 no se toca.

---

## Tests

**Unitarios del parseo** (`tests/unit/`): ausente, vacia, `true`, `TRUE`,
` false `, y el valor invalido que levanta.

**Unitarios de construccion**: X apagada no construye `XClient` aunque las
cuatro credenciales esten; LinkedIn con credencial faltante deja
`linkedin_ready` en False sin afectar `x_ready`.

**Integracion con `StateDB` real** (`tests/integration/test_orchestrator_exit_states.py`):

1. Una rota y otra viva -> publica en la viva, alerta antes de la aprobacion,
   fila `published`, y el cliente roto nunca se llama.
2. Las dos apagadas -> sin fila, sin alerta, `send_approval_request` no se llama.
3. Una apagada y otra rota -> sin fila, con alerta que nombra solo la rota.

Los cuatro tests que hoy setean `publishers_ready`
(`test_orchestrator_persistence.py:49,166,189`,
`test_orchestrator_exit_states.py:52,160`) se reescriben contra los clientes
mockeados. **No se recrea el atajo con dos banderas**: es el mecanismo exacto
por el que el bug de `5ba7997` estuvo escondido detras de tests verdes.

---

## Lo que este cambio no hace

- **No toca `r2_ready`**, aunque tiene la misma forma de bandera separable del
  cliente. Fuera de alcance.
- **No separa el estado por red mas alla de los `post_id` que ya existen.** Una
  degradacion no deja rastro en `published_events` distinto de un `post_id`
  ausente.
- **No agrega reintento de la red que falto.** Ver la alternativa descartada de
  la decision 1: el `event_id` con fecha lo vuelve inutil.
- **No cambia el destino de la publicacion en el mensaje de aprobacion.** La
  alerta previa cubre el caso degradado; el mensaje de aprobacion sigue igual.
