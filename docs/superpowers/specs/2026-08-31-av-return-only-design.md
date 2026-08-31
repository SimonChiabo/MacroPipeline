# Publicar sólo el retorno por la ruta de Alpha Vantage — diseño

**Fecha:** 2026-08-31
**Estado:** Aprobado
**Cierra:** el punto 6 del backlog, y la divergencia 4 de ADR-009

---

## Problema

El ETL pide a FMP `^GSPC` y `^IXIC` —índices, del orden de 7.657 y 26.029— y
cae a Alpha Vantage con `SPY` y `QQQ` —los ETF que los siguen, del orden de 765
y 612—. `_fetch_weekly_close` guarda el nivel venga de donde venga, y aguas
abajo se publica rotulado "S&P 500". La ruta de fallback publicaría **el número
del instrumento equivocado bajo la etiqueta del índice**: la invariante de
ADR-001 —*cada cifra conserva el indicador del que salió*— rota en el ETL.

Ningún contract test puede cazarlo: AV devolviendo SPY a 765 cumple su contrato
al pie de la letra. Lo único que hoy lo impide son cuatro rangos de nivel en
`validators/rules.yaml:16-19` (`sp500_close_min: 2000`) aplicados en
`validate_weekly_close`: con SPY a 765 el validador levanta y la run aborta.

Eso convirtió una degradación en un abort. ADR-009 lo aceptó por escrito —"mejor
no publicar que publicar el instrumento equivocado"— y dejó anotada la salida:

> **Publicar sólo el retorno desde AV** —que es invariante de escala entre el
> índice y su ETF, y sería una degradación real en vez de un abort— quedó
> propuesto y sin hacer.

Y dejó escrita de antemano la consecuencia:

> El día que se publique sólo el retorno desde AV, FMP sin key pasa a ser una
> degradación y hay que mover su rama del bloque de abortos al de degradaciones.

Este diseño hace las dos cosas.

### La asimetría que lo hace posible

El **nivel** no sobrevive al cambio de instrumento; el **retorno** sí. `SPY` y
`^GSPC` se mueven prácticamente igual en porcentaje, así que
`(p_t − p_{t−5}) / p_{t−5}` es la misma cifra venga de cual venga. Publicar sólo
el retorno no es una mutilación del producto: es publicar exactamente lo que la
fuente de reemplazo sí puede sostener.

---

## La regla, en una frase

**El nivel de cierre se publica sólo si vino del instrumento que dice la
etiqueta.**

FMP lo cumple. AV no. Mock queda como está —sus cifras son de escala índice y ya
viven detrás de `ALLOW_MOCK_DATA=false`; volverlas `None` mezclaría dos
problemas distintos.

---

## 1. Dónde nace el `None`

```python
sp500_close: float | None = Field(..., gt=0, ...)
nasdaq_close: float | None = Field(..., gt=0, ...)
```

Verificado contra Pydantic antes de escribir esto: con `strict=True`, `gt=0`
sigue aplicándose a la rama `float` de la unión —un `-1.0` se rechaza igual— y
`None` pasa. La restricción no se pierde.

**El campo sigue siendo obligatorio: `...` y no `default=None`.** Con un default
el campo se vuelve opcional y quien se olvide de pasarlo obtiene `None` en
silencio — una degradación por descuido, que es justo lo que este diseño existe
para no tener. Con `...`, omitirlo es un `ValidationError` y el único sitio que
construye el modelo tiene que declarar su intención escribiendo `None`.
Verificado: `None` explícito pasa, omitido se rechaza, negativo se rechaza. Los
tests existentes ya pasan las dos cifras, así que no genera churn.

En `_fetch_weekly_close`, la rama de AV construye el modelo con los dos cierres
en `None`. La rama de FMP no cambia una línea.

### Por qué `None` y no una compuerta por `data_source`

Se evaluaron tres representaciones:

| Opción | Veredicto |
|---|---|
| `float \| None` | **Elegida.** Fail-safe por construcción |
| Cierres poblados + `if data_source == "av"` en cada consumidor | Descartada: fail-unsafe |
| `WeeklyReturnsData` aparte | Descartada: YAGNI |

El argumento decisivo es el modo de fallo cuando alguien olvida el caso. Con
`None`, un consumidor distraído hace `f"{None:,.2f}"`, que levanta `TypeError`
—verificado, no supuesto—, cae al `except` general, manda alerta y deja la fila
en `failed`. Con la compuerta, el consumidor distraído **publica 765 en
silencio**: exactamente el bug que este trabajo existe para matar, reintroducido
por un olvido en uno de cuatro sitios.

La opción del modelo aparte hace imposible el error por tipos, pero duplica
esquema y camino de render y obliga a todo lo de aguas abajo a aceptar dos
tipos, para una sola ruta degradada.

### Dos piezas que no estaban en el pedido

**Un `model_validator` que rechace el par a medias.** Un `sp500_close` poblado
junto a un `nasdaq_close` en `None` no sale de ninguna fuente real: las dos
cifras vienen siempre del mismo cliente en la misma rama. Si aparece, es un bug,
y tiene que reventar en el esquema y no tres capas más abajo. Es lo que habilita
a que todo el resto del código pregunte una sola cosa —`data.sp500_close is
None`— sin volver a razonar la correlación.

**Una guarda `if self.fmp is None` al principio del `try`.** AV ya tiene la
suya, agregada precisamente porque sin ella salía un `AttributeError` y la
alerta del `except` general culpaba a un bug en vez de nombrar al componente.
Hoy la de FMP sería inalcanzable —la rama 4 aborta antes de llegar—, pero **en
cuanto FMP pasa a degradar deja de serlo**. Sin la guarda, este mismo cambio
reintroduce el fallo que ya se arregló una vez, en el componente de al lado.

---

## 2. Los cuatro consumidores

| Consumidor | Con cierre (FMP) | Sin cierre (AV) |
|---|---|---|
| `validate_weekly_close` | sin cambios | saltea los 4 rangos de nivel |
| `PlaywrightEngine` + plantilla | sin cambios | el retorno ocupa el lugar del cierre |
| Capa LLM | sin cambios | no participa |
| `mark_as_published` | sin cambios | escribe `NULL` |

### Validador

Saltea los rangos de nivel **sólo ante `None`**, que se produce en un único
sitio y ya está protegido por el `model_validator`. Un cierre *poblado* fuera de
rango sigue abortando: la última defensa de ADR-001 no se debilita, se vuelve
condicional a que haya algo que defender.

El test de `test_validators.py:111` —el `sp500_close=765.72`, el SPY real del
2026-08-21— **no pierde cobertura, cambia de significado**: pasa a probar que un
765 que se coló poblado sigue siendo un abort.

Y algo que hay que decir en voz alta porque cambia el perfil de riesgo de esa
ruta: **los rangos de retorno (`±0.25` / `±0.30`) siguen aplicando, y en la ruta
de AV pasan a ser la única defensa numérica que queda.**

### Renderer y plantilla

Una sola plantilla. El par de tarjetas se arma en Python y se inyecta como
`{metrics_grid}`, que es exactamente el patrón que `_build_macro_block` ya usa
para `{macro_block}` en ese mismo fichero. Sin cierre, el retorno sube al lugar
de los 64px de `.metric-value` y la tarjeta lleva un `variación semanal` chico
debajo.

Se descartó una plantilla aparte (`weekly_close_returns.html`): duplica ~145
líneas de CSS en dos ficheros que van a derivar, y pone la copia que nadie va a
mirar justo en la ruta que casi nunca corre. Se descartó también pasar
`sp500_close=""`: `.metric-value` mide 64px con `margin: 20px 0`, así que deja
dos rectángulos vacíos — se lee como un render roto, y es la imagen que un humano
tiene que aprobar por Telegram.

### Capa LLM

Con `data.sp500_close is None` la capa **no participa**: `_generic_headline`
—que ya publica sólo retornos, así que el texto que sale a X y LinkedIn no
cambia—, `prompt_version=None`, `validator_approved=None`. Es la misma rama que
ya existe para la capa apagada o sin key.

Lo importante es el mecanismo, no la política: **el `data_str` no se construye**.
La cifra mal rotulada nunca llega a existir para el LLM, así que la invariante de
ADR-001 en la prosa se sostiene por construcción y no por una instrucción del
prompt.

Esto suma una **cuarta causa** de "bloque genérico". Las otras tres ya se
distinguen en las alertas por decisión explícita —era la diferencia entre tocar
código y relanzar—, así que la nueva tiene que distinguirse también: el log
`llm_layer_not_participating` lleva la causa.

### Persistencia

`mark_as_published` ya declara los cuatro parámetros como `float | None` y las
columnas son `REAL`, que acepta `NULL`. **No hace falta migración.** El único
lector, `get_publication_state`, se consume por `x_post_id`/`linkedin_post_id`
(`main.py:651-652`): ningún lector se rompe.

---

## 3. Los avisos: espejo de FRED

El código ya declara el principio por escrito, en el comentario de la rama LLM:
*«Avisar de nuevo sería el mismo fallo contado dos veces»*. Y FRED ya lo
implementa: `macro_error` se carga **sólo** cuando el bloque macro se rompió en
ejecución, porque FRED sin key ya lo avisó el punto de decisión. La ruta de AV
cae en la misma forma y se resuelve igual.

**Arranque (rama 5).** FMP sin key sale del bloque de abortos y entra al de
degradaciones. Requiere dos cambios que van juntos o no van:

1. Agregar `_CONSECUENCIA["fmp"]` — la rama 5 renderiza `_CONSECUENCIA[c]` sin
   `.get()`.
2. Sacar `"fmp"` del filtro `c not in ("fmp", "telegram")`.

Hacer sólo el segundo produce un `KeyError` **dentro de la propia alerta**: el
aviso de la degradación se convierte en la excepción que mata la run.

**In-run.** Un `self.fmp_runtime_error`, cargado **sólo** cuando FMP se cayó en
ejecución, alertado junto al bloque de `macro_error` y antes de pedir aprobación
—quien aprueba tiene que saber que ese cierre sale con menos—. FMP sin key no
llega ahí: ya avisó el arranque. El nombre no es `fmp_error` a propósito, para no
confundirse con `component_errors["fmp"]`, que es el del arranque.

**Textos que quedan mintiendo.** Tres, y los tres afirman hoy que la ruta de AV
no puede publicar:

- La alerta de la rama 4: *"La ruta de Alpha Vantage no publica: pide `SPY` donde
  FMP pide `^GSPC`, y el validador la rechaza por rango"*.
- `_CONSECUENCIA["av"]`: *"sin fallback si FMP falla, y hoy esa ruta tampoco
  publicaría"*.
- La fila de AV-sin-key de la tabla de ADR-009, que promete literalmente *"una
  alerta que dice que esa ruta tampoco publicaría"*.

**`USE_FMP=false` no se toca.** Sigue siendo pausa deliberada del pipeline y
aborta en silencio. Un switch apagado es una decisión y una key faltante es un
fallo: es el tercer eje de ADR-009 tal como está escrito. Que un switch que hoy
frena todo empiece a publicar por AV sería un cambio de comportamiento
silencioso, y se decidió no hacerlo.

---

## 4. ADR-009

Cuatro filas de la tabla y el cierre de la divergencia 4:

| Fila | Cambio |
|---|---|
| FMP — sin key, o `USE_FMP=false` | Se parte en dos: sin key **degrada** con alerta; `USE_FMP=false` sigue abortando en silencio |
| FMP — API caída | Deja de decir "degrada a AV… que hoy aborta" |
| AV — sin key | El texto prometido cambia: el fallback ausente ya no es "una ruta que tampoco publicaría" |
| `ValidationEngine` | Gana la excepción declarada: sin nivel, no hay rango que aplicar |

Y la divergencia 4 pasa de "propuesto y sin hacer" a cerrada, anotando qué la
cerró.

**Se recorre la tabla entera fila por fila contra el código.** Es obligatorio
cuando se toca la política: lleva seis hallazgos acumulados, uno de ellos un
fallo recién introducido por el propio trabajo que lo encontró.

---

## 5. Pruebas y verificación

TDD. Los tests nuevos cubren, como mínimo:

- La rama de AV produce los dos cierres en `None`; la de FMP no.
- El `model_validator` rechaza el par a medias.
- La guarda de `self.fmp is None` nombra a FMP y no larga un `AttributeError`.
- `validate_weekly_close` pasa con cierres en `None`, y **sigue abortando** con
  un cierre poblado fuera de rango.
- El render sin cierre no contiene el número y sí el retorno.
- La capa LLM no participa cuando no hay cierre, y `data_str` no se construye.
- `mark_as_published` persiste `NULL` y se relee.
- Rama 5: FMP sin key degrada, alerta, y la alerta se renderiza sin `KeyError`.
- Rama 4: `USE_FMP=false` sigue abortando en silencio.
- La alerta in-run se manda con FMP caída en ejecución y **no** con FMP sin key.

**Verificación por mutación** sobre los cuatro puntos donde el diseño se puede
deshacer sin que nadie lo note:

1. Poblar el cierre en la rama de AV.
2. Sacar la guarda de `self.fmp is None`.
3. Dejar `"fmp"` en el filtro de la rama 5.
4. Devolver el `data_str` con cierre a la capa LLM.

Si alguna de esas mutaciones deja la suite verde, ese punto no está cubierto y
falta un test.

**Barra de cierre:** CI verde sobre el HEAD exacto, con el número de run
anotado, y el punto 6 cerrado en memoria con la misma disciplina que los puntos
7 a 13.

---

## Estado al arrancar

HEAD `6c4816a`, 283 tests recolectados (25 deselected), árbol limpio, Codecov
90.97%.
