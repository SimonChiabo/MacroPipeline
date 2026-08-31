# El aviso de vencimiento del token de LinkedIn gana un canal — diseño

**Fecha:** 2026-08-31
**Estado:** Aprobado
**Cierra:** el punto 7 del backlog (el token de LinkedIn de octubre)

---

## Problema

El token de LinkedIn dura ~60 días y se reemite a mano desde el token generator
del portal. Con este montaje (`w_member_social`, sin programa de partner) no hay
refresh programático: **rotar es coste externo, no deuda nuestra**, y lo máximo
que el repo puede hacer es avisar a tiempo.

El aviso existe y está bien escrito. `scripts/check_publishers.py:244-251`
calcula la edad contra `LINKEDIN_TOKEN_ISSUED` y marca `[AVISO]` pasado el día
50. El problema es que **no le llega a nadie**: es un `print` de un script que
solo corre a mano, mencionado en `README.md:100` y en ningún workflow. Se
dispara únicamente si alguien lo ejecuta entre el día 50 y el 60.

Con `LINKEDIN_TOKEN_ISSUED=2026-08-21`, esa ventana es del **2026-10-10 al
2026-10-20**. Si esos diez días nadie corre el script, el primer síntoma es una
publicación fallando en vivo.

La mitigación anotada en el backlog no es una alerta: es un recordatorio que hay
que acordarse de ir a buscar.

## Alcance

**Entra:** darle canal de entrega al aviso, un chequeo real de la credencial en
el nightly, y una guarda contra el apagado del cron por inactividad.

**No entra:** el endpoint `introspectToken` de LinkedIn, que devolvería el
vencimiento real en vez de derivarlo de una fecha mantenida a mano. Queda como
upgrade; su ventaja es que no deriva nunca, y este diseño ya cubre la revocación
por la vía de autenticar de verdad.

**Tampoco entra:** el resto del agujero anotado en el backlog —que
`check_publishers.py` no autentica FRED, AV, Anthropic ni R2—. Esto lo cierra
solo para LinkedIn.

## El eje de ADR-009 que gobierna la bandera

El tercer eje dice que un componente **declarado opcional** que no está
configurado no participa, y no participar no es degradar. `PUBLISH_LINKEDIN=false`
es la salida documentada para aceptar el vencimiento de octubre sin trabajo, así
que **una red apagada no genera aviso de vencimiento**: sería avisar todas las
semanas de algo ya decidido, que es justo lo que la bandera existe para evitar.

El coste que ADR-009 ya anotó en §Consecuencias —"quien apaga un componente
tiene que acordarse de volver a encenderlo, y nada se lo recuerda"— se cubre
solo acá, sin maquinaria: al reencender, `LINKEDIN_TOKEN_ISSUED` sigue siendo la
fecha vieja, la edad da meses, y el primer nightly con la bandera en `true`
alerta de inmediato.

## Qué cubre cada pieza, y qué no

**El chequeo de edad es inmune al blackout.** Es aritmética contra una fecha, no
estado acumulado por las corridas. Si el cron duerme 40 días y vuelve, la primera
corrida calcula la edad real y dispara la alarma diaria de ≥60 al toque. El
vencimiento *durante* un apagón se cura solo.

Lo que el blackout cuesta es **latencia**: mientras el cron está apagado no avisa
nada. Y un chequeo disparado por reconexión tendría exactamente la misma
latencia, porque también corre solo cuando el cron corre. Por eso la pieza que
ataca el problema no es detectar la reconexión sino **impedir que el cron quede
apagado sin que nadie se entere**.

**Verificado contra la doc de GitHub:** en repos públicos los workflows
programados se deshabilitan solos tras 60 días sin actividad en el repo, y
**re-habilitarlos es manual** (UI, `gh workflow enable`, o REST API). Ninguna
actividad posterior los reactiva. O sea que "cuando el nightly vuelva" puede no
pasar nunca: se vuelve al repo, se commitea, se asume que la alarma está
encendida, y está apagada en silencio. La coincidencia es fea: los 60 días de
inactividad son la misma ventana que los 60 días del token.

## Diseño

### a. Dos variables de repo

`LINKEDIN_TOKEN_ISSUED` (fecha ISO) y `PUBLISH_LINKEDIN` (espejo del `.env`).
Van como **variables**, no secrets: una fecha y un booleano no son secretos, y
como variables se leen del log, que es lo que se quiere para diagnosticar.

### b. Dos secrets nuevos

`LINKEDIN_ACCESS_TOKEN` y `LINKEDIN_PERSON_URN`. No hacen falta los de X:
poniendo `PUBLISH_X=false` en el `env:` del paso, `check_publishers.py` se
saltea X entero (`scripts/check_publishers.py:277-279`).

### c. Paso de edad escalonado en `contract-tests.yml`

Silencio si `vars.PUBLISH_LINKEDIN == 'false'`. Si no, avisa en los días **50,
55, 58 y todos los días pasado el 60**.

```
age == 50, 55, 58  -> aviso
age >= 60          -> aviso todos los dias
resto              -> silencio
```

Es stateless a propósito: la decisión sale de la edad, no de un registro de si
ya avisó. Tres pulsos espaciados evitan el patrón que entrena a ignorar el canal
—y es el mismo canal por el que llegan las alertas de degradación reales, donde
por convención del repo **un mensaje significa que algo se rompió**—, y la cola
diaria pasado el 60 hace imposible perderlo del todo.

Manda **su propio mensaje** de Telegram, con el patrón de `curl` + chequeo de
status code que ya está en ese fichero. **No** va gateado con `if: failure()`:
hacer que un aviso de token ponga el job en rojo lo disfrazaría de fallo de
contract tests.

Si `LINKEDIN_TOKEN_ISSUED` falta o no es una fecha ISO válida, el paso avisa por
Telegram. Es fail-loud a propósito: una fecha ilegible **desarma la alarma**, que
es exactamente lo que este trabajo existe para evitar.

**El orden de las dos guardas importa y no es estético.** La bandera se mira
primero y la fecha después: con LinkedIn apagado y la fecha ilegible el
resultado es silencio, no aviso. Si se invirtieran, apagar la red dejaría de
silenciar en el caso justo en que la fecha quedó sin mantener —que es el caso
más probable después de un apagado largo—, y el aviso volvería a sonar todas las
semanas por algo ya decidido. Es el mismo tipo de trampa que el orden de las dos
primeras ramas de `_startup_exit_code`, y merece el mismo test que lo fije.

### d. Chequeo real de la credencial en cada nightly

Es un **paso aparte** del de la edad: uno hace aritmética sin red, el otro pega
contra la API, y mezclarlos haría que una caída de LinkedIn se llevara puesto el
aviso de vencimiento, que es el que no puede fallar.

`check_publishers.py` con `PUBLISH_X=false`, o sea LinkedIn solo. **Recibe
`PUBLISH_LINKEDIN` desde `vars.PUBLISH_LINKEDIN`**, así que la red apagada lo
silencia igual que al paso de la edad: el script ya se saltea la red apagada
(`scripts/check_publishers.py:281`) y no hay una segunda regla que mantener.
Corre en **todas** las corridas, no solo tras una reconexión: una vez que los secrets
están en CI, hacerlo siempre es estrictamente mejor que gatillarlo por un hueco
detectado —no hace falta detectar el hueco ni plomería con `gh run list`, y un
token **revocado** se caza dentro del día en vez de solo después de un apagón—.
Satisface "al reconectar corre `check_publishers.py`" trivialmente: la primera
corrida tras reconectar lo corre, como todas.

El texto de la alerta **distingue un fallo de red de un 401**, con el mismo
criterio que `AV_RATE_LIMIT`: una caída de LinkedIn no debe gritar "token
muerto" todos los días. `report_env_drift` ya maneja el `.env` ausente
(`scripts/check_publishers.py:119-121`), así que el script corre en CI sin
tocarlo por ese lado.

### e. Guarda contra el disable por inactividad, en `ci.yml`

Corre en cada push: consulta el estado del workflow programado y, si da
`disabled_inactivity`, lo reactiva y lo dice fuerte.

```sh
gh api repos/:owner/:repo/actions/workflows/contract-tests.yml --jq .state
```

Esto es lo que realmente cierra el agujero del blackout, y va en `ci.yml`
—no en el nightly— porque un workflow deshabilitado no puede reactivarse a sí
mismo.

### f. El bug del 403 en `check_publishers.py`

`check_linkedin()` hace `return not missing` en la rama de 403 antes de llegar
al bloque de vencimiento, así que si al token le faltan los scopes
`openid profile` el aviso de edad **no se imprime nunca**, ni siquiera corriendo
el script a mano. El bloque de edad se mueve delante de los returns tempranos.

### g. Documentación

`.env.example` y README: al rotar el token hay que tocar el `.env` **y** la
variable de repo.

## Errores y modos de fallo

| Situación | Comportamiento |
|---|---|
| Token en día 50/55/58 | Aviso por Telegram, job verde |
| Token en día ≥60 | Aviso diario, job verde |
| `PUBLISH_LINKEDIN=false` | Silencio total: ni edad ni chequeo de credencial |
| `LINKEDIN_TOKEN_ISSUED` ausente o ilegible | Aviso por Telegram (fail-loud) |
| Bandera `false` **y** fecha ilegible | Silencio: la bandera se mira primero |
| LinkedIn devuelve 401 | Aviso nombrando token muerto |
| LinkedIn no responde (red) | Aviso nombrando fallo de red, texto distinto |
| Telegram caído | El aviso queda en el resumen del run; el paso lo grita |
| Cron deshabilitado por inactividad | El siguiente push lo reactiva desde `ci.yml` |

## Tests

- Aritmética de la cadencia: los cuatro umbrales (50, 55, 58, ≥60) y el silencio
  del resto, incluido el borde de 49 y 51.
- El silencio con `PUBLISH_LINKEDIN=false` a cualquier edad.
- La fecha ausente y la fecha ilegible avisan.
- **El orden de las guardas**: bandera `false` con fecha ilegible da silencio.
  Invertir las dos guardas tiene que hacer caer este test.
- El bug del 403: un test que falle hoy, sobre la rama de 403 imprimiendo la
  edad.
- **Verificación por mutación** para la cadencia, que es el control que este repo
  ya usó con éxito: cambiar `age >= 60` por `age > 60` tiene que hacer caer un
  test y solo uno.

## Costes aceptados

- **Dos secrets nuevos en CI, uno con permiso de publicar en el perfil.** La
  recomendación original evitaba credenciales en CI; el requisito de chequeo real
  retiró esa propiedad. Decisión tomada a conciencia, y el repo ya filtró una
  credencial una vez (`9ea687a`, 2026-05-14), así que el riesgo se conoce.
- **`vars.PUBLISH_LINKEDIN` es un espejo a mano del `.env`, y la deriva es
  asimétrica.** Espejo `true` y `.env` `false` sobra ruido: molesto pero visible.
  Al revés —espejo `false`, `.env` `true`— es **fail-silent**: el pipeline
  publica en LinkedIn con un token muriéndose y el nightly calla. Ninguna pieza
  de este diseño lo cierra; queda anotado.
- **La fecha se mantiene a mano.** Rotar sin actualizar la variable deja la
  alerta sonando hasta que se corrija, lo cual es fail-loud y aceptable. Lo que
  no puede pasar es el silencio sobre un token viejo: pediría actualizar la fecha
  *sin* rotar, que no es un movimiento que ocurra.
