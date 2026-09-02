# Diccionario de datos: qué es exactamente cada cifra que publicamos

**Fecha:** 2026-09-02
**Motivo:** la divergencia del IPC se encontró de casualidad —Simon buscó la
cifra a mano y no coincidió—. Nadie auditaba las series en ninguna parte, así
que cualquier otra podía tener el mismo problema sin que nadie se enterara.

**Criterio:** ADR-001 llevado al ETL. **Una cifra correcta bajo una etiqueta
equivocada es una cifra incorrecta.** No alcanza con que el número sea el que
la fuente devuelve; tiene que ser el que su etiqueta promete.

**Método:** cada fila se verificó contra los metadatos que devuelven las APIs
—no de memoria— el 2026-09-02: `/fred/series` para las tres series de FRED
(campos `title`, `units`, `seasonal_adjustment`, `frequency`, `notes`), y el
payload real de `historical-price-eod/full` de FMP para los dos índices. Lo que
no se pudo verificar así se dice en su fila.

**La trampa que motivó el método.** En el ensayo el desempleo dio 4,1 % y se
declaró «coincide exacto» comparando dígitos contra una cifra vista al pasar.
Eso no es una validación: un número igual con definiciones distintas es una
coincidencia, y es peor que una diferencia visible porque no llama la atención
de nadie. Acá cada fila se cierra por definición, no por dígito.

---

## La tabla

| # | Dato | Fuente | Qué es exactamente | Veredicto |
|---|---|---|---|---|
| 1 | IPC interanual | FRED `CPIAUCNS` | CPI-U, All Items, US City Average, base 1982-84=100, **sin desestacionalizar** | ✅ Corregido: era `CPIAUCSL` |
| 2 | Desempleo | FRED `UNRATE` | **U-3**, 16+, Household Survey (`LNS14000000`), desestacionalizado | ✅ Bien |
| 3 | Treasury 10A | FRED `DGS10` | Rendimiento de mercado a **10 años constant maturity**, nominal, base de inversión | ✅ Bien |
| 4 | Cierre S&P 500 | FMP `^GSPC` | Nivel del índice de **precio** (excluye dividendos). No hay nada que ajustar | ✅ Bien — pero ver el intradía, abajo |
| 5 | Cierre Nasdaq | FMP `^IXIC` | **Nasdaq Composite** (~3.000 empresas), no el Nasdaq-100 | ✅ Corregido: la etiqueta dice «Nasdaq Composite» |
| 6 | Fallback de ambos | AV `SPY` / `QQQ` | Cierres **sin ajustar** (`TIME_SERIES_DAILY`, campo `4. close`) | ❌ **Dos problemas** |
| 7 | Retorno «semanal» | cálculo propio | Ventana móvil de 5 días hábiles anclada a la última sesión **terminada** | ✅ La etiqueta lo respeta |

Estado al cierre del 2026-09-02: **la 1 y la 5 quedaron arregladas**, la 6
sigue abierta, y apareció un problema que no estaba en ninguna fila —el precio
intradía— que se arregló el mismo día. El detalle de cada una, abajo.

---

## 1. IPC interanual — **corregido el 2026-09-02**

**Lo que publicamos:** `CPIAUCSL`, que los metadatos de FRED describen como
*Consumer Price Index for All Urban Consumers: All Items in U.S. City Average*,
unidades *Index 1982-1984=100*, frecuencia mensual, **`seasonal_adjustment:
Seasonally Adjusted`**.

**Lo que le hacemos:** `compute_yoy` compara la última observación contra la
última anterior o igual a doce meses antes —por fecha, no por posición—.

**Lo que da, con datos reales del 2026-09-02:**

| Serie | Última (jul-2026) | Base (jul-2025) | YoY | Lo que imprime el post |
|---|---|---|---|---|
| `CPIAUCSL` (hoy) | 332,813 | 322,169 | 3,3039 % | **+3,3 %** |
| `CPIAUCNS` (la del titular) | 333,918 | 323,048 | 3,3648 % | **+3,4 %** |

La diferencia cae justo en el dígito que se publica.

**Por qué la convención es la sin desestacionalizar.** Al comparar un mes
contra el mismo mes del año anterior el factor estacional se cancela por
construcción: desestacionalizar no aporta nada al interanual y sólo agrega el
ruido de los factores. Por eso el BLS y los medios citan NSA para el interanual
y SA para la variación mensual. Este bloque muestra **sólo** el interanual.

**Y un motivo que no estaba sobre la mesa: las revisiones.** Los factores
estacionales se recalculan todos los años y revisan la serie SA hacia atrás. La
serie NSA, una vez publicada, no se revisa. Publicando `CPIAUCSL`, la cifra del
post de la semana pasada puede dejar de ser reproducible sin que nadie toque el
pipeline. Con `CPIAUCNS` eso no pasa.

**Hecho:** `CPI_SERIES = "CPIAUCNS"` en `data/macro.py:15`. La aritmética de
`compute_yoy` no cambió: las dos son series de nivel con la misma base.

Lo ancla un test de unidad sobre la llamada —no sobre la constante, que se
cumpliría con sólo renombrarla— y un contract test que le pregunta a FRED si
la serie sigue siendo `Not Seasonally Adjusted`. Ese segundo es el que fija el
*porqué*: sobrevive a un rename y detecta que la serie cambie de definición.

---

## 2. Desempleo — bien, y ahora por definición

Las notas de FRED lo cierran sin ambigüedad: «This rate is also defined as the
U-3 measure of labor underutilization», población de 16 años o más, Current
Population Survey (Household Survey), código fuente `LNS14000000` —la `S` es la
de desestacionalizado— y `seasonal_adjustment: Seasonally Adjusted`.

Es la medida del titular: cuando el BLS y los medios dicen «la tasa de
desempleo», dicen U-3 desestacionalizada. La U-6 es otra serie (`U6RATE`) y no
la usamos.

No le aplicamos ninguna transformación: se publica la última observación tal
cual. El 2026-09-02: **4,1 %, referida a julio de 2026**, publicada el
2026-08-07. La etiqueta del post dice «Desempleo … al 07/2026», que es el mes
correcto.

**Sin acción.** La etiqueta no dice «U-3» ni «desestacionalizada», y para un
post general eso es la convención, no una omisión.

---

## 3. Treasury 10A — bien, con el lag medido

Título completo en FRED: *Market Yield on U.S. Treasury Securities at 10-Year
Constant Maturity, Quoted on an Investment Basis*. Responde las tres preguntas
que la auditoría tenía abiertas: **sí es constant maturity**, es **nominal**
—la versión indexada a inflación es `DFII10`, que no usamos— y se cotiza en
base de inversión. Frecuencia diaria.

**El lag, medido y no estimado:** el 2026-09-02 la última observación era la
del **31/08** (4,75 %), con `last_updated` el 01/09 a las 15:16 CT. Es decir:
FRED publica el dato de un día hábil por la tarde del día hábil siguiente. El
retraso es de **un día hábil**, que en calendario se ve como dos días, y más
después de un fin de semana largo.

Esto corrige lo que decía ROADMAP.md («hasta dos días de retraso»): la unidad
es el día hábil, no el día de calendario. La consecuencia práctica no cambia
—un cierre del viernes muestra el rendimiento del jueves— y el post es honesto,
porque la plantilla rotula cada métrica con su propio `as_of`
(`playwright_engine.py:56-58`).

---

## 4. Cierre S&P 500 — bien, y la pregunta del ajuste no aplica

El payload real de FMP para `^GSPC` trae `open`, `high`, `low`, `close`,
`volume`, `vwap`, `change`, `changePercent`. **No existe un campo `adjClose`**,
y no debería: `^GSPC` es un **índice**, no un instrumento negociable. No hay
splits ni dividendos que ajustar; el nivel del índice es el cierre oficial.

Lo que sí conviene dejar escrito, porque es una distinción real: el S&P 500 es
un índice de **precio** y por lo tanto excluye dividendos. El total return es
otro índice (`SPXTR`). Nuestro post publica un nivel y una variación de precio,
que es exactamente lo que dice la etiqueta.

**Lo único que ninguna API contesta sola** es si el número de FMP coincide con
el cierre oficial del día. Eso se mira a ojo una vez, en el ensayo, contra
cualquier fuente pública.

---

## 5 y 6. El Nasdaq y el fallback — el mismo problema desde dos lados

### ✅ «NASDAQ» a secas no decía cuál — corregido el 2026-09-02

`^IXIC` es el **Nasdaq Composite**, unas 3.000 empresas (nivel del 01/09/2026:
26.099,77). El post lo rotula **«NASDAQ»**, tanto en la tarjeta
(`playwright_engine.py`) como en el titular genérico (`main.py:99`). Para el
público general «el Nasdaq» es el Composite; para bastante gente del mercado es
el Nasdaq-100. La etiqueta no distinguía.

**Lo confirmó el propio autor del proyecto**, que miró el borrador, comparó
contra 29.077,22 en su terminal y concluyó que el pipeline calculaba mal. Ese
número dividido por `QQQ` (707,64) da 41,09 —el ratio conocido del Nasdaq-100—
y el nuestro da 36,88, que no corresponde a nada. El método se calibró primero
contra un caso conocido: S&P 7.631,47 sobre `SPY` 761,78 da 10,02, que es la
relación SPX/SPY de manual.

Desde el 2026-09-02 la tarjeta y el copy dicen **«Nasdaq Composite»**. Se
evaluó publicar el Nasdaq-100 en su lugar y se descartó: FMP devuelve **402
Payment Required** para `^NDX` en el plan actual, y el Composite es además el
que los medios llaman «el Nasdaq».

### ❌ Y el fallback publica otro índice bajo la misma etiqueta

Acá está el hallazgo que no estaba registrado en ninguna parte:

- `SPY` sigue al **S&P 500** — el mismo índice que `^GSPC`. El cambio de
  instrumento conserva la referencia.
- `QQQ` sigue al **Nasdaq-100** — que **no** es el Composite de `^IXIC`.

Es decir: cuando FMP falla y entra Alpha Vantage, la tarjeta rotulada «NASDAQ»
pasa a mostrar el retorno de **un índice distinto**, sin que nada lo diga. Los
dos se mueven parecido, pero un retorno semanal a dos decimales los separa con
frecuencia.

ADR-009 divergencia 4 sacó el **nivel** de la ruta de AV con el argumento de
que «el retorno sobrevive al cambio de instrumento; el nivel no». La auditoría
precisa ese argumento: **el retorno sobrevive en el S&P y no sobrevive en el
Nasdaq.** Cerrar el nivel no cerró el retorno.

### ❌ Y además el fallback no ajusta por dividendos

`av_client.py:61` pide `TIME_SERIES_DAILY` y lee el campo `"4. close"`: cierres
**sin ajustar**. Sobre un ETF eso importa y sobre un índice no: en la semana en
que `SPY` o `QQQ` cotizan ex-dividendo, el precio cae por el reparto y el
retorno de cinco días queda por debajo del del índice. Reparten
trimestralmente, así que son unas cuatro semanas al año por ETF, con una
magnitud del orden del reparto (≈0,3 % en `SPY`, ≈0,1 % en `QQQ`) — visible a
los dos decimales que publica el post.

### Qué hacer con esto

Ninguna de las dos bloquea publicar **hoy**, porque sólo aparecen si FMP falla.
Pero es justamente la ruta degradada, que es donde nadie mira. Las salidas, sin
decidir todavía:

- Rotular «Nasdaq Composite» en el camino bueno, y en el fallback publicar la
  etiqueta del instrumento que efectivamente se usó.
- O cambiar el fallback del Nasdaq a `ONEQ`, que sí sigue al Composite.
- Y en cualquier caso, usar la variante ajustada de Alpha Vantage para el
  retorno, o documentar el sesgo de las semanas ex-dividendo.

---

## 7. El retorno «semanal» — la etiqueta se sostiene

`_fetch_weekly_close` (`main.py:377-393`) toma la última sesión disponible y la
compara contra la última sesión anterior o igual a esa fecha menos cinco días
hábiles. Medido el 2026-09-02 sobre datos reales de `^GSPC`:

| | |
|---|---|
| Última sesión | 2026-09-01 (martes), 7.631,47 |
| Corte `BDay(5)` | 2026-08-25 |
| Base tomada | 2026-08-25 (martes), 7.677,28 |
| Distancia | **7 días de calendario, 5 sesiones** |

Martes contra martes: una semana legítima, no media semana. **No** es de lunes
a viernes ni una semana calendario fija: es una ventana móvil anclada al último
dato.

Un detalle sobre feriados: `BDay(5)` cuenta días hábiles de calendario, no
sesiones de mercado. Si en la ventana cae un feriado, el corte aterriza en un
día sin sesión y el `<=` toma la anterior: la ventana sigue midiendo siete días
de calendario, con una sesión menos adentro. Sigue siendo «una semana», que es
lo que la etiqueta promete.

**Dónde sí hay una grieta, y no es del ETL.** El encabezado del post dice
«Cierre Semanal de Mercado» y lleva la fecha del último dato —correcto, porque
`WeeklyCloseData.date` sale de la última observación y no de `date.today()`
(`main.py:407-411`)—. Pero el último dato disponible es siempre el de la sesión
anterior: el 2026-09-02 (miércoles) lo más nuevo era el 01/09. **Si la corrida
del viernes ocurre antes del cierre del viernes, el post titulado «Cierre
Semanal» lleva el cierre del jueves.**

Resuelto el mismo día, y resultó ser la punta de algo peor: el último dato
disponible durante el horario de mercado no es el de la sesión anterior sino la
sesión **en curso** (sección 8). Desde entonces sólo se publican sesiones
terminadas, y la publicación pasa a los sábados: viernes contra viernes, que es
la ventana más limpia que da este cálculo.

---

## 8. El precio intradía — el que no estaba en ninguna fila

**Encontrado el 2026-09-02, después de escribir la tabla de arriba**, y por el
único método que sirve para esto: Simon miró el borrador y no le cerró un
número. Es la segunda vez en el día que ese método encuentra algo que ninguna
verificación automática podía encontrar.

El endpoint `historical-price-eod/full` de FMP —el que el nombre promete que
son cierres— **devuelve también una fila para la sesión en curso**, y su campo
`close` es el último precio negociado. Medido sobre `^IXIC` ese día:

| Momento (UTC) | Fila más reciente | `close` |
|---|---|---|
| 11:36 | 2026-09-01 | 26.099,77 |
| 14:40 | **2026-09-02** | 26.211,996 |
| 14:59 | **2026-09-02** | 26.196,812 |

Misma fecha, valor distinto con 19 minutos de diferencia. El volumen lo
confirma: 2.031 millones a las 10:59 ET contra los 7.679 de la sesión completa
del 31/08. Los tres decimales de `26211.996` eran la pista a simple vista.

El ensayo de las 14:40 publicó ese primer número rotulado «Cierre».

**Por qué nada lo detectaba.** `rules.yaml` valida rangos y frescura del bloque
macro, pero sobre el cierre semanal sólo mira que el nivel y el retorno caigan
en rango — y un precio intradía cae en rango por definición, porque es un
precio real de ese instante. Es el caso más puro del criterio de este
documento: la cifra es correcta y la etiqueta miente.

**El arreglo, en dos capas.** `_fetch_weekly_close` descarta las filas cuya
fecha sea la de hoy: sólo se publican sesiones terminadas. El corte es por
fecha local y no por calendario de mercado, y alcanza porque el operador va por
delante de ET. Encima de eso, la publicación pasa a los **sábados**, que hace
que la guarda no cueste frescura y de paso deja la ventana semanal en viernes
contra viernes (ROADMAP.md, bloqueador 3).

El costo aceptado: una corrida el viernes por la noche publicaría el jueves
aunque el dato del viernes ya sea definitivo.

**Lo que este hallazgo deja como advertencia general:** el nombre de un endpoint
no es su contrato. `historical-price-eod` dice «end of day» y devuelve una fila
que no lo es. Las otras fuentes merecen la misma desconfianza — y el modo de
verificarlo no es leer la documentación, es pedir el dato dos veces separadas en
el tiempo y comparar.


## Lo que esta auditoría deja pendiente a propósito

- **Que el cierre de FMP coincida con el cierre oficial.** Ninguna API lo
  contesta sola; se mira a ojo una vez en el ensayo.
- **La fila 6: el fallback de Alpha Vantage.** Sigue abierta, con las tres
  salidas planteadas y ninguna elegida. Es la única fila de la tabla sin
  resolver.
- **La cobertura del resto de `_fetch_weekly_close`.** La guarda de sesiones
  terminadas trajo esa función a tener sus dos primeros tests directos; la
  ventana de cinco días hábiles y la supresión del nivel por fuente siguen
  ejercitándose sólo de refilón desde integración.

**Cerrado desde que se escribió esto:**

- **El post ya cita sus fuentes.** El titular determinista termina en
  «Fuentes: …», y nombra la que efectivamente trajo el dato: por la ruta de
  Alpha Vantage dice Alpha Vantage, y si FRED no aportó no lo menciona. El pie
  de la imagen sigue sin citarlas.
- **Los tests que fijan identidad.** Eran el agujero por el que se coló el IPC:
  `rules.yaml` fija rangos y frescura, y 3,30 y 3,36 caen los dos dentro de
  `cpi_yoy_min/max`. Hoy un contract test le pregunta a FRED el
  `seasonal_adjustment` y la frecuencia de las tres series, y otro test fija que
  el ETL pida la serie por su id y no por el nombre de una constante.
