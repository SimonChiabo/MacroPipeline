# Canal de entrega para el aviso del token de LinkedIn — plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** que el aviso de vencimiento del token de LinkedIn llegue por Telegram sin que nadie tenga que acordarse de correr un script a mano.

**Architecture:** un script de stdlib puro decide si hoy toca avisar (aritmética de fechas, sin red) y el nightly manda el mensaje; un segundo paso del nightly autentica la credencial de verdad contra LinkedIn; y un job nuevo en `ci.yml` impide que GitHub apague el cron por inactividad sin que nadie se entere.

**Tech Stack:** Python 3.12 stdlib, pytest, GitHub Actions, `gh` CLI, API de Telegram.

**Spec:** `docs/superpowers/specs/2026-08-31-linkedin-token-expiry-alert-design.md`

---

## Estructura de ficheros

| Fichero | Responsabilidad | Nuevo |
|---|---|---|
| `scripts/linkedin_token_alert.py` | Decidir si hoy toca avisar y con qué texto. Sin red, sin dependencias. | sí |
| `tests/unit/test_linkedin_token_alert.py` | La cadencia, el orden de las guardas, las fechas rotas. | sí |
| `scripts/check_publishers.py` | El aviso de edad pasa a ser incondicional; dos marcadores para el workflow. | no |
| `tests/unit/test_check_publishers.py` | El test del 403 que hoy no existe. | no |
| `.github/workflows/contract-tests.yml` | Dos pasos: el de edad y el de credencial. | no |
| `.github/workflows/ci.yml` | Job nuevo: el nightly no puede quedar apagado. | no |
| `.env.example`, `README.md` | Que al rotar hay que tocar el `.env` **y** la variable de repo. | no |

**Por qué un script nuevo y no lógica en el YAML:** la cadencia es lógica de verdad —cuatro umbrales, dos guardas ordenadas, dos formas de fecha rota— y en bash dentro del YAML no la testea nadie. Este repo ya se quemó con eso: una alerta que mentía en las dos mitades con 148 tests en verde.

**Por qué stdlib puro y sin importar `macro_pipeline`:** para que el paso pueda correr **antes** de `pip install` y de `setup-python`. La alarma del token no debe depender de que el install funcione. Es la razón por la que este script no usa `component_enabled` aunque exista.

---

## Task 1: el script que decide

**Files:**
- Create: `scripts/linkedin_token_alert.py`
- Test: `tests/unit/test_linkedin_token_alert.py`

- [ ] **Step 1: Escribir los tests que fallan**

Crear `tests/unit/test_linkedin_token_alert.py`:

```python
"""La decisión de avisar del vencimiento del token de LinkedIn.

El aviso viejo era un `print` de `scripts/check_publishers.py`, que solo corre
a mano: se disparaba únicamente si alguien lo ejecutaba entre el día 50 y el
60. Esta es la mitad decidible de darle un canal, separada del envío para que
se pueda testear sin red.
"""

import importlib.util
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def alerta():
    """El script, cargado por ruta: `scripts/` no es un paquete."""
    spec = importlib.util.spec_from_file_location(
        "linkedin_token_alert", ROOT / "scripts" / "linkedin_token_alert.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _hoy_con_edad(edad: int) -> tuple[date, str]:
    """Un par (hoy, fecha_de_emision) que da exactamente `edad` días.

    La fecha de hoy se inyecta en vez de congelar el reloj: `mensaje_de_aviso`
    la recibe como argumento justamente para que estos tests no dependan del
    día en que corran.
    """
    hoy = date(2026, 10, 15)
    return hoy, (hoy - timedelta(days=edad)).isoformat()


@pytest.mark.parametrize("edad", [50, 55, 58])
def test_los_tres_pulsos_avisan(alerta, edad):
    hoy, emitido = _hoy_con_edad(edad)
    mensaje = alerta.mensaje_de_aviso(hoy, "true", emitido)
    assert mensaje is not None
    assert str(60 - edad) in mensaje


@pytest.mark.parametrize("edad", [0, 1, 49, 51, 54, 56, 57, 59])
def test_entre_pulsos_hay_silencio(alerta, edad):
    hoy, emitido = _hoy_con_edad(edad)
    assert alerta.mensaje_de_aviso(hoy, "true", emitido) is None


@pytest.mark.parametrize("edad", [60, 61, 120, 400])
def test_vencido_avisa_todos_los_dias(alerta, edad):
    hoy, emitido = _hoy_con_edad(edad)
    mensaje = alerta.mensaje_de_aviso(hoy, "true", emitido)
    assert mensaje is not None
    assert "vencido" in mensaje


@pytest.mark.parametrize("edad", [10, 50, 55, 58, 60, 400])
def test_la_bandera_apagada_silencia_a_cualquier_edad(alerta, edad):
    """El tercer eje de ADR-009: una red apagada no participa."""
    hoy, emitido = _hoy_con_edad(edad)
    assert alerta.mensaje_de_aviso(hoy, "false", emitido) is None


def test_la_fecha_ausente_avisa(alerta):
    """Fail-loud: una fecha ausente desarma la alarma, que es el problema."""
    mensaje = alerta.mensaje_de_aviso(date(2026, 10, 15), "true", "")
    assert mensaje is not None
    assert "desarmada" in mensaje


def test_la_fecha_ilegible_avisa(alerta):
    mensaje = alerta.mensaje_de_aviso(date(2026, 10, 15), "true", "21/08/2026")
    assert mensaje is not None
    assert "desarmada" in mensaje


def test_la_bandera_se_mira_antes_que_la_fecha(alerta):
    """El orden de las guardas es sustantivo, no estético.

    Con LinkedIn apagado y la fecha ilegible el resultado tiene que ser
    silencio. Si se miraran al revés, apagar la red dejaría de silenciar justo
    cuando la fecha quedó sin mantener —el caso más probable después de un
    apagado largo— y el aviso volvería a sonar todas las semanas por algo ya
    decidido. Invertir las dos guardas tiene que hacer caer este test.
    """
    assert alerta.mensaje_de_aviso(date(2026, 10, 15), "false", "no-es-fecha") is None
    assert alerta.mensaje_de_aviso(date(2026, 10, 15), "false", "") is None


def test_una_bandera_ilegible_no_silencia(alerta):
    """`maybe` no puede ser idéntico a un apagado deliberado.

    Es la misma trampa que el orden de las dos primeras ramas de
    `_startup_exit_code`: el valor que no se entiende tiene que caer del lado
    ruidoso, nunca del silencioso.
    """
    hoy, emitido = _hoy_con_edad(60)
    assert alerta.mensaje_de_aviso(hoy, "maybe", emitido) is not None


def test_la_bandera_ausente_no_silencia(alerta):
    """Ausente = participar, igual que `component_enabled`."""
    hoy, emitido = _hoy_con_edad(60)
    assert alerta.mensaje_de_aviso(hoy, "", emitido) is not None
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

```sh
./.venv/Scripts/python.exe -m pytest tests/unit/test_linkedin_token_alert.py -v
```

Esperado: FAIL en el fixture, con `FileNotFoundError` o
`AttributeError: module has no attribute 'mensaje_de_aviso'`.

**Usar siempre `./.venv/Scripts/python.exe -m ...`.** El Python global de esta
máquina no tiene el paquete en editable y da errores inventados.

- [ ] **Step 3: Escribir el script**

Crear `scripts/linkedin_token_alert.py`:

```python
"""Decide si hoy toca avisar del vencimiento del token de LinkedIn.

El token dura ~60 días y se reemite a mano desde el token generator del portal:
rotar es coste externo y lo máximo que el repo puede hacer es avisar a tiempo.
El aviso ya existía en `scripts/check_publishers.py`, pero es un `print` de un
script que solo corre a mano, así que se disparaba únicamente si alguien lo
ejecutaba dentro de la ventana. Esto es la mitad decidible, separada del envío
para poder testearla sin red.

    python scripts/linkedin_token_alert.py

Imprime el mensaje si toca avisar y no imprime nada si no toca. Sale con 0
siempre que la decisión se haya podido tomar; un crash sale distinto de 0 a
propósito, porque el paso del workflow corre con `set -e` y así un fallo pone
el job en rojo en vez de pasar por silencio.

Solo stdlib, y no importa `macro_pipeline` a propósito: el paso corre antes de
`setup-python` y de `pip install`, para que la alarma no dependa de que el
install funcione.
"""

from __future__ import annotations

import os
import sys
from datetime import date

VIDA_UTIL_DIAS = 60

# Tres pulsos espaciados antes de vencer, y después diario. Avisar los diez
# días seguidos es como se entrena a ignorar un canal — y por éste llegan las
# alertas de degradación, donde por convención del repo un mensaje significa
# que algo se rompió.
PULSOS = (50, 55, 58)

BANDERA = "PUBLISH_LINKEDIN"
EMITIDO = "LINKEDIN_TOKEN_ISSUED"

_COMO_ROTAR = (
    "Reemitirlo desde el token generator del portal y actualizar "
    f"{EMITIDO} en el .env y en las variables del repo."
)


def mensaje_de_aviso(hoy: date, bandera: str, emitido: str) -> str | None:
    """El texto a mandar, o `None` si hoy no toca avisar.

    Las dos guardas van en este orden y no es estético. Con LinkedIn apagado y
    la fecha ilegible el resultado tiene que ser silencio: si se miraran al
    revés, apagar la red dejaría de silenciar justo cuando la fecha quedó sin
    mantener —el caso más probable después de un apagado largo— y el aviso
    volvería a sonar todas las semanas por algo ya decidido.

    Solo el `false` exacto silencia. Un valor que no se entiende cae del lado
    ruidoso, igual que una bandera ausente: es la misma trampa que el orden de
    las dos primeras ramas de `_startup_exit_code`, donde un switch ilegible
    quedaba idéntico a un apagado deliberado.
    """
    if bandera.strip().lower() == "false":
        return None

    crudo = emitido.strip()
    if not crudo:
        return (
            f"[LinkedIn] No puedo avisar del vencimiento del token: falta "
            f"{EMITIDO}. La alarma esta desarmada hasta que se cargue."
        )

    try:
        dia = date.fromisoformat(crudo)
    except ValueError:
        return (
            f"[LinkedIn] No puedo avisar del vencimiento del token: {EMITIDO} "
            f"no es una fecha ISO. La alarma esta desarmada hasta que se "
            f"corrija."
        )

    edad = (hoy - dia).days
    if edad >= VIDA_UTIL_DIAS:
        return (
            f"[LinkedIn] El token esta vencido: emitido hace {edad} dias "
            f"(dura ~{VIDA_UTIL_DIAS}). {_COMO_ROTAR}"
        )
    if edad in PULSOS:
        return (
            f"[LinkedIn] Al token le quedan ~{VIDA_UTIL_DIAS - edad} dias "
            f"(emitido hace {edad}). {_COMO_ROTAR}"
        )
    return None


def main() -> int:
    mensaje = mensaje_de_aviso(
        date.today(),
        os.environ.get(BANDERA, ""),
        os.environ.get(EMITIDO, ""),
    )
    if mensaje:
        print(mensaje)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

**Ojo con el mensaje de la fecha ilegible:** no interpola el valor tipeado. La
lección de `fa96abb` es que un campo que sale de un `except` y termina en un
canal externo hay que redactarlo donde nace; acá el valor es una fecha y no un
secreto, pero el mensaje va a Telegram y no se gana nada mostrándolo.

- [ ] **Step 4: Correr los tests para verificar que pasan**

```sh
./.venv/Scripts/python.exe -m pytest tests/unit/test_linkedin_token_alert.py -v
```

Esperado: PASS, **26 tests** con los parametrizados expandidos
(3 pulsos + 8 entre pulsos + 4 vencidos + 6 de bandera apagada + 5 sueltos).

- [ ] **Step 5: Verificación por mutación**

Es el control que más ha rendido en este repo. Cambiar en el script:

```python
    if edad >= VIDA_UTIL_DIAS:
```

por:

```python
    if edad > VIDA_UTIL_DIAS:
```

Correr los tests. Esperado: cae **`test_vencido_avisa_todos_los_dias[60]`** y
ningún otro. Revertir el cambio.

Segunda mutación: invertir las dos guardas (mover el bloque de la bandera
después del bloque de la fecha). Esperado: cae
**`test_la_bandera_se_mira_antes_que_la_fecha`**. Revertir.

Si alguna de las dos mutaciones deja todo en verde, el test no está fijando lo
que dice fijar y hay que arreglarlo antes de seguir.

- [ ] **Step 6: Lint y tipos**

```sh
./.venv/Scripts/python.exe -m ruff check scripts/ tests/
./.venv/Scripts/python.exe -m ruff format --check scripts/ tests/
./.venv/Scripts/python.exe -m mypy scripts/
```

Esperado: los tres limpios. `line-length = 88` y `mypy strict`: si `ruff format`
se queja, correrlo sin `--check` y volver a mirar el diff.

- [ ] **Step 7: Commit**

```sh
git add scripts/linkedin_token_alert.py tests/unit/test_linkedin_token_alert.py
git commit -m "feat(observability): la cadencia del aviso de LinkedIn, testeable

El aviso de vencimiento vivia como print en un script que solo corre a mano.
Esta es la mitad decidible, sin red y sin dependencias, para que el nightly
pueda mandarla. Tres pulsos (50, 55, 58) y despues diario.

La bandera se mira antes que la fecha a proposito, y solo el 'false' exacto
silencia: un valor ilegible cae del lado ruidoso.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 2: el aviso de edad de `check_publishers.py` deja de depender del 403

**Files:**
- Modify: `scripts/check_publishers.py:200-253`
- Test: `tests/unit/test_check_publishers.py`

**El bug:** `check_linkedin()` tiene el bloque de vencimiento al final, después
de tres `return` tempranos. Con un 403 por scopes —o con la API caída— el aviso
de edad no se imprime nunca, ni siquiera corriendo el script a mano. El
vencimiento se calcula contra una fecha local: no necesita que LinkedIn conteste.

- [ ] **Step 1: Escribir el test que falla**

Agregar al final de `tests/unit/test_check_publishers.py`:

```python
def test_el_aviso_de_vencimiento_no_depende_de_que_linkedin_conteste(
    check_publishers, monkeypatch, capsys
):
    """Un 403 por scopes se comía el aviso de edad.

    El bloque de vencimiento estaba después de tres `return` tempranos, así que
    la rama de 403 —y la API caída, y un PERSON_URN que no coincide— salían sin
    imprimirlo. La edad se calcula contra una fecha local y no necesita que
    LinkedIn conteste.
    """
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "un-token-cualquiera")
    monkeypatch.setenv("LINKEDIN_PERSON_URN", "urn:li:person:abc")
    monkeypatch.setenv("LINKEDIN_TOKEN_ISSUED", "2020-01-01")

    class Respuesta403:
        status_code = 403
        text = ""

    monkeypatch.setattr(
        check_publishers.requests, "get", lambda *a, **k: Respuesta403()
    )

    check_publishers.check_linkedin()

    salida = capsys.readouterr().out
    assert "Token emitido hace" in salida
```

- [ ] **Step 2: Correr el test para verificar que falla**

```sh
./.venv/Scripts/python.exe -m pytest tests/unit/test_check_publishers.py::test_el_aviso_de_vencimiento_no_depende_de_que_linkedin_conteste -v
```

Esperado: FAIL con `assert 'Token emitido hace' in salida` — la salida tiene el
texto del 403 y nada de la edad.

- [ ] **Step 3: Extraer el bloque de edad a su propia función**

En `scripts/check_publishers.py`, agregar antes de `def check_linkedin()`:

```python
def _avisar_vencimiento() -> None:
    """Imprime la edad del token. Corre pase lo que pase con la API.

    Estaba al final de `check_linkedin()`, después de tres `return` tempranos,
    así que un 403 por scopes —o la API caída, o un PERSON_URN que no coincide—
    se comía el aviso de vencimiento incluso corriendo el script a mano. La edad
    se calcula contra una fecha local: no necesita que LinkedIn conteste.
    """
    issued = os.environ.get("LINKEDIN_TOKEN_ISSUED", "").strip()
    if not issued:
        print(f"{WARN} Sin LINKEDIN_TOKEN_ISSUED: no se puede avisar del vencimiento.")
        return
    try:
        age = (date.today() - date.fromisoformat(issued)).days
    except ValueError:
        print(f"{WARN} LINKEDIN_TOKEN_ISSUED no es una fecha ISO válida.")
        return
    marker = WARN if age > 50 else OK
    print(f"{marker} Token emitido hace {age} días (expira a los ~60).")
```

- [ ] **Step 4: Llamarla temprano y borrar el bloque viejo**

En `check_linkedin()`, después del bloque del placeholder y **antes** del
`try` de `requests.get`, insertar la llamada:

```python
    token = os.environ.get("LINKEDIN_ACCESS_TOKEN", "").strip()
    if not token or _is_placeholder(token):
        return False

    _avisar_vencimiento()

    try:
        response = requests.get(
```

Y borrar el bloque viejo del final de la función, que hoy es:

```python
    issued = os.environ.get("LINKEDIN_TOKEN_ISSUED", "").strip()
    if issued:
        try:
            age = (date.today() - date.fromisoformat(issued)).days
            marker = WARN if age > 50 else OK
            print(f"{marker} Token emitido hace {age} días (expira a los ~60).")
        except ValueError:
            print(f"{WARN} LINKEDIN_TOKEN_ISSUED no es una fecha ISO válida.")
    else:
        print(f"{WARN} Sin LINKEDIN_TOKEN_ISSUED: no se puede avisar del vencimiento.")

    return not missing
```

que queda solo como:

```python
    return not missing
```

- [ ] **Step 5: Agregar los dos marcadores para el workflow**

Mismo patrón que `AV_RATE_LIMIT`: el workflow grepea la salida para elegir el
texto de la alerta, así que un corte de red no grita "token muerto".

En la rama de la excepción de red:

```python
    except requests.RequestException as e:
        print(f"{FAIL} No se pudo contactar la API de LinkedIn: {e}")
        print("LINKEDIN_UNREACHABLE")
        return False
```

En la rama del 401:

```python
    if response.status_code == 401:
        print(f"{FAIL} 401: el token es inválido o expiró (duran ~60 días).")
        print("LINKEDIN_TOKEN_DEAD")
        return False
```

**El texto de la excepción es seguro acá:** el token de LinkedIn viaja en un
header, no en la query string, así que un `RequestException` no lo incluye. Es
distinto de FRED, donde la `api_key` va en la URL y por eso hubo que redactarla
(`fa96abb`).

- [ ] **Step 6: Correr los tests para verificar que pasan**

```sh
./.venv/Scripts/python.exe -m pytest tests/unit/test_check_publishers.py -v
```

Esperado: PASS, todos. Sin `.env` local da un `skipped`, que es el viejo
`test_runs_against_the_real_repo_files` y es correcto.

- [ ] **Step 7: Lint, tipos y commit**

```sh
./.venv/Scripts/python.exe -m ruff check scripts/ tests/
./.venv/Scripts/python.exe -m ruff format --check scripts/ tests/
./.venv/Scripts/python.exe -m mypy scripts/
git add scripts/check_publishers.py tests/unit/test_check_publishers.py
git commit -m "fix(scripts): el aviso de vencimiento sobrevive a un 403

El bloque de edad estaba despues de tres return tempranos, asi que un 403 por
scopes se lo comia incluso corriendo el script a mano. La edad sale de una
fecha local y no necesita que LinkedIn conteste.

Ademas, dos marcadores (LINKEDIN_UNREACHABLE, LINKEDIN_TOKEN_DEAD) para que el
nightly distinga un corte de red de un token muerto, como ya hace con
AV_RATE_LIMIT.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 3: el paso de edad en el nightly

**Files:**
- Modify: `.github/workflows/contract-tests.yml`

- [ ] **Step 1: Insertar el paso justo después del `checkout`**

Va **primero**, antes del paso de comprobar secrets. Ese paso sale con 1 si
falta una key de FRED o AV, y con él saldrían salteados todos los de abajo: el
aviso del token no puede depender de que las credenciales de otras cuatro APIs
estén cargadas.

Insertar después de `- uses: actions/checkout@v7` y antes de
`- name: Comprobar secrets requeridos`:

```yaml
      # El aviso de vencimiento del token de LinkedIn. Va primero a proposito:
      # el paso de secrets de abajo sale con 1 si falta una key de FRED o AV, y
      # esta alarma no puede depender de eso. Usa `python3` del runner y no
      # `setup-python` ni el paquete instalado —el script es stdlib puro— para
      # que tampoco dependa de que el `pip install` funcione.
      - name: Avisar si el token de LinkedIn esta por vencer
        env:
          PUBLISH_LINKEDIN: ${{ vars.PUBLISH_LINKEDIN }}
          LINKEDIN_TOKEN_ISSUED: ${{ vars.LINKEDIN_TOKEN_ISSUED }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: |
          # `set -e` es lo que hace que un crash del script salga en rojo en
          # vez de dar mensaje vacio y pasar por silencio.
          set -euo pipefail

          mensaje=$(python3 scripts/linkedin_token_alert.py)

          if [ -z "${mensaje}" ]; then
            echo "Token de LinkedIn: hoy no toca avisar."
            exit 0
          fi

          echo "${mensaje}"
          echo "> ⚠️ ${mensaje}" >> "${GITHUB_STEP_SUMMARY}"

          if [ -z "${TELEGRAM_BOT_TOKEN}" ] || [ -z "${TELEGRAM_CHAT_ID}" ]; then
            echo "::warning title=Aviso no enviado::Sin credenciales de Telegram; el aviso del token queda solo en el resumen del run."
            exit 0
          fi

          # `curl -s` a secas devuelve 0 aunque la API conteste 404, asi que
          # una alerta que nunca llego se ve igual que una entregada.
          status=$(curl -sS -o /dev/null -w '%{http_code}' \
            -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d chat_id="${TELEGRAM_CHAT_ID}" \
            -d text="${mensaje}")

          if [ "${status}" != "200" ]; then
            echo "::error title=Aviso de LinkedIn no entregado::sendMessage devolvio HTTP ${status}."
            echo "> ❌ El aviso del token de LinkedIn no se entrego (HTTP ${status})." >> "${GITHUB_STEP_SUMMARY}"
            exit 1
          fi

          echo "Aviso del token de LinkedIn entregado."
```

- [ ] **Step 2: Verificar que el YAML parsea**

```sh
./.venv/Scripts/python.exe -c "import yaml,pathlib; yaml.safe_load(pathlib.Path('.github/workflows/contract-tests.yml').read_text(encoding='utf-8')); print('YAML OK')"
```

Esperado: `YAML OK`.

- [ ] **Step 3: Probar el script a mano en las dos direcciones**

```sh
PUBLISH_LINKEDIN=true LINKEDIN_TOKEN_ISSUED=2020-01-01 ./.venv/Scripts/python.exe scripts/linkedin_token_alert.py
```

Esperado: una línea que empieza con `[LinkedIn] El token esta vencido`.

```sh
PUBLISH_LINKEDIN=false LINKEDIN_TOKEN_ISSUED=2020-01-01 ./.venv/Scripts/python.exe scripts/linkedin_token_alert.py
```

Esperado: **salida vacía**.

- [ ] **Step 4: Commit**

```sh
git add .github/workflows/contract-tests.yml
git commit -m "feat(ci): el aviso del token de LinkedIn sale por Telegram

Va primero en el job: el paso de secrets sale con 1 si falta una key de FRED o
AV y se llevaria puesta esta alarma. Usa python3 del runner y stdlib pura para
no depender del pip install tampoco.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 4: el chequeo real de la credencial en el nightly

**Files:**
- Modify: `.github/workflows/contract-tests.yml`

- [ ] **Step 1: Insertar el paso después de `Run contract tests`**

Va después de `- name: Run contract tests` y antes de
`- name: Registrar el fallo en el resumen del run`:

```yaml
      # El chequeo real de la credencial de LinkedIn, no solo su fecha. Corre
      # en todas las corridas y no solo tras una reconexion: una vez que los
      # secrets estan en CI, hacerlo siempre no necesita detectar el hueco y
      # ademas caza un token *revocado* dentro del dia. `PUBLISH_X=false` hace
      # que el script se saltee X entero, asi que no hacen falta sus cuatro
      # credenciales. `always()` para que un contract test en rojo no lo saltee.
      - name: Verificar la credencial de LinkedIn
        if: always()
        env:
          PUBLISH_X: "false"
          PUBLISH_LINKEDIN: ${{ vars.PUBLISH_LINKEDIN }}
          LINKEDIN_ACCESS_TOKEN: ${{ secrets.LINKEDIN_ACCESS_TOKEN }}
          LINKEDIN_PERSON_URN: ${{ secrets.LINKEDIN_PERSON_URN }}
          LINKEDIN_TOKEN_ISSUED: ${{ vars.LINKEDIN_TOKEN_ISSUED }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: |
          set -o pipefail

          if python scripts/check_publishers.py | tee linkedin-check.txt; then
            echo "Credencial de LinkedIn verificada."
            exit 0
          fi

          # Un corte de red y un token muerto ponen los dos el chequeo en rojo,
          # pero solo uno pide ir al portal. Mismo criterio que AV_RATE_LIMIT.
          if grep -q "LINKEDIN_UNREACHABLE" linkedin-check.txt 2>/dev/null; then
            texto="⚠️ No se pudo contactar la API de LinkedIn, asi que la credencial quedo sin verificar. NO es necesariamente el token: basta con relanzar."
          elif grep -q "LINKEDIN_TOKEN_DEAD" linkedin-check.txt 2>/dev/null; then
            texto="🔴 El token de LinkedIn no sirve (401): esta vencido o revocado. Reemitirlo desde el token generator del portal y actualizar LINKEDIN_TOKEN_ISSUED en el .env y en las variables del repo."
          else
            texto="⚠️ La verificacion de la credencial de LinkedIn fallo por otro motivo. Revisar el run."
          fi

          run_url="${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
          texto="${texto} ${run_url}"

          echo "> ⚠️ ${texto}" >> "${GITHUB_STEP_SUMMARY}"

          if [ -z "${TELEGRAM_BOT_TOKEN}" ] || [ -z "${TELEGRAM_CHAT_ID}" ]; then
            echo "::warning title=Alerta no enviada::Sin credenciales de Telegram; el aviso queda solo en el resumen del run."
            exit 1
          fi

          status=$(curl -sS -o /dev/null -w '%{http_code}' \
            -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d chat_id="${TELEGRAM_CHAT_ID}" \
            -d text="${texto}")

          if [ "${status}" != "200" ]; then
            echo "::error title=Alerta de LinkedIn no entregada::sendMessage devolvio HTTP ${status}."
            echo "> ❌ La alerta no se entrego (HTTP ${status})." >> "${GITHUB_STEP_SUMMARY}"
          fi

          exit 1
```

**Nota de deuda:** con esto hay tres copias del bloque de `curl` + chequeo de
status code en este fichero. Es lo que el spec pidió (copiar el patrón que ya
está), pero si aparece una cuarta conviene extraerlo a
`scripts/telegram_notify.sh`: el punto de ese bloque es que un aviso no se
pierda en silencio, y tres copias que pueden derivar juegan en contra.

- [ ] **Step 2: Verificar que el YAML parsea**

```sh
./.venv/Scripts/python.exe -c "import yaml,pathlib; yaml.safe_load(pathlib.Path('.github/workflows/contract-tests.yml').read_text(encoding='utf-8')); print('YAML OK')"
```

Esperado: `YAML OK`.

- [ ] **Step 3: Probar el silenciado local del script**

```sh
PUBLISH_X=false PUBLISH_LINKEDIN=false ./.venv/Scripts/python.exe scripts/check_publishers.py; echo "exit=$?"
```

Esperado: imprime las dos redes como apagadas y `exit=0`. Verifica que la
bandera silencia también al chequeo de credencial, no solo al de edad.

- [ ] **Step 4: Commit**

```sh
git add .github/workflows/contract-tests.yml
git commit -m "feat(ci): el nightly autentica la credencial de LinkedIn

No solo mira la fecha: pega contra /v2/userinfo, asi que un token revocado se
caza dentro del dia y no solo cuando vence. PUBLISH_X=false hace que el script
se saltee X, o sea que no hacen falta sus credenciales en CI.

El texto distingue un corte de red de un 401, como ya se hace con
AV_RATE_LIMIT: solo uno de los dos pide ir al portal.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 5: el nightly no puede quedar apagado en silencio

**Files:**
- Modify: `.github/workflows/ci.yml`

**Por qué:** GitHub deshabilita los workflows programados de un repo público
tras 60 días sin actividad, y **re-habilitarlos es manual** (UI,
`gh workflow enable`, o REST API). Ninguna actividad posterior los reactiva. O
sea que se vuelve al repo, se commitea, se asume que la alarma está encendida, y
está apagada. La coincidencia es fea: los 60 días de inactividad son la misma
ventana que los 60 días del token. Va en `ci.yml` y no en el nightly porque un
workflow deshabilitado no puede reactivarse a sí mismo.

- [ ] **Step 1: Agregar el job al final de `ci.yml`**

```yaml
  nightly-vivo:
    name: El nightly no puede quedar apagado
    runs-on: ubuntu-latest
    # Solo en push: en un PR desde un fork el token es de solo lectura y el
    # paso fallaria por permisos, no por el estado del workflow.
    if: github.event_name == 'push'
    permissions:
      actions: write

    steps:
      # GitHub apaga los workflows programados de un repo publico tras 60 dias
      # sin actividad, y re-habilitarlos es manual: ninguna actividad posterior
      # los reactiva. Sin esto se vuelve al repo, se commitea, y la alarma del
      # token de LinkedIn sigue apagada sin que nadie se entere — y los 60 dias
      # de inactividad son justo la ventana del token.
      - name: Reactivar contract-tests.yml si GitHub lo apago
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          set -euo pipefail

          ruta="repos/${{ github.repository }}/actions/workflows/contract-tests.yml"
          estado=$(gh api "${ruta}" --jq .state)
          echo "Estado de contract-tests.yml: ${estado}"

          if [ "${estado}" != "disabled_inactivity" ]; then
            exit 0
          fi

          gh workflow enable contract-tests.yml
          echo "::warning title=Nightly reactivado::GitHub habia apagado contract-tests.yml por inactividad. Se reactivo, pero estuvo ciego un tiempo indeterminado."
          {
            echo "## ⚠️ El nightly estaba apagado"
            echo
            echo "GitHub deshabilita los workflows programados tras 60 dias sin"
            echo "actividad en el repo. \`contract-tests.yml\` estaba en"
            echo "\`disabled_inactivity\` y se reactivo automaticamente."
            echo
            echo "**Mientras estuvo apagado no corrio ninguna verificacion de"
            echo "contratos ni del token de LinkedIn.** Conviene correr"
            echo "\`python scripts/check_publishers.py\` a mano."
          } >> "${GITHUB_STEP_SUMMARY}"
```

- [ ] **Step 2: Verificar que el YAML parsea**

```sh
./.venv/Scripts/python.exe -c "import yaml,pathlib; d=yaml.safe_load(pathlib.Path('.github/workflows/ci.yml').read_text(encoding='utf-8')); print(sorted(d['jobs']))"
```

Esperado: `['lint-and-type-check', 'nightly-vivo', 'unit-tests']`.

- [ ] **Step 3: Probar la consulta a mano**

```sh
gh api "repos/SimonChiabo/MacroPipeline/actions/workflows/contract-tests.yml" --jq .state
```

Esperado: `active`. Si devuelve otra cosa, anotarlo — significa que el problema
ya estaba pasando.

Si el `gh api` falla por permisos, revisar en Settings → Actions → General que
"Workflow permissions" no esté en read-only; el bloque `permissions:` del job
solo puede reducir, no ampliar, lo que el repo permite.

- [ ] **Step 4: Commit**

```sh
git add .github/workflows/ci.yml
git commit -m "feat(ci): el nightly no puede quedar apagado en silencio

GitHub deshabilita los workflows programados tras 60 dias sin actividad y
re-habilitarlos es manual: ninguna actividad posterior los reactiva. Sin esto
se vuelve al repo, se commitea, y la alarma del token sigue apagada.

Los 60 dias de inactividad son la misma ventana que los 60 dias del token, o
sea que el escenario en que no se toca el repo es el escenario en que la
alarma se apaga.

Va en ci.yml porque un workflow deshabilitado no puede reactivarse solo.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 6: cargar las variables y los secrets

**Files:** ninguno (configuración del repo)

- [ ] **Step 1: Cargar las dos variables**

Son una fecha y un booleano: van como **variables**, no como secrets, para que
se lean del log al diagnosticar.

```sh
gh variable set LINKEDIN_TOKEN_ISSUED --body "2026-08-21" -R SimonChiabo/MacroPipeline
gh variable set PUBLISH_LINKEDIN --body "true" -R SimonChiabo/MacroPipeline
```

- [ ] **Step 2: Cargar los dos secrets**

**Pipear el valor por stdin.** `gh secret set NOMBRE` a secas pide el valor por
prompt interactivo y se cuelga en un contexto no interactivo.

```sh
grep '^LINKEDIN_ACCESS_TOKEN=' .env | cut -d= -f2- | gh secret set LINKEDIN_ACCESS_TOKEN -R SimonChiabo/MacroPipeline
grep '^LINKEDIN_PERSON_URN=' .env | cut -d= -f2- | gh secret set LINKEDIN_PERSON_URN -R SimonChiabo/MacroPipeline
```

- [ ] **Step 3: Verificar que existen**

```sh
gh variable list -R SimonChiabo/MacroPipeline
gh secret list -R SimonChiabo/MacroPipeline
```

Esperado: las dos variables y, entre los secrets, `LINKEDIN_ACCESS_TOKEN` y
`LINKEDIN_PERSON_URN` sumados a los siete que ya había.

**`gh secret list` prueba que el secret existe, no que el valor sirva.** Lo
único que lo prueba es la run — que es el Step 2 de la Task 8.

---

## Task 7: documentación

**Files:**
- Modify: `.env.example:29`
- Modify: `README.md`

- [ ] **Step 1: Ampliar la nota de `.env.example`**

Reemplazar la línea 29 de `.env.example`:

```
# LINKEDIN_TOKEN_ISSUED=2026-05-15
```

por:

```
# Fecha de emision del access token de LinkedIn, en ISO. El token dura ~60
# dias y se reemite a mano desde el token generator del portal.
# IMPORTANTE: al rotar hay que actualizarla en DOS sitios, el .env y la
# variable del repo (`gh variable set LINKEDIN_TOKEN_ISSUED --body ...`).
# El nightly avisa por Telegram los dias 50, 55, 58 y despues todos los dias,
# y lee la variable del repo, no este fichero.
# LINKEDIN_TOKEN_ISSUED=2026-05-15
```

**No romper el chequeo de deriva:** `LINKEDIN_TOKEN_ISSUED` cuenta como
documentada por `_commented_names()`, que busca la declaración comentada. La
línea `# LINKEDIN_TOKEN_ISSUED=2026-05-15` tiene que seguir existiendo tal cual;
los comentarios nuevos van **encima**, no en su lugar.

- [ ] **Step 2: Verificar que el chequeo de deriva sigue verde**

```sh
./.venv/Scripts/python.exe -m pytest tests/unit/test_check_publishers.py -v
```

Esperado: PASS. En particular
`test_the_example_keeps_the_two_decided_declarations`, que corre en CI.

- [ ] **Step 3: Agregar la sección al README**

Después del bloque que menciona `python scripts/check_publishers.py`
(`README.md:100`), agregar:

```markdown
### El token de LinkedIn vence cada ~60 días

Se reemite a mano desde el token generator del portal: con este montaje
(`w_member_social`) no hay refresh programático, así que rotar es coste externo
y lo único que el repo puede hacer es avisar a tiempo.

El nightly avisa por Telegram los días **50, 55, 58 y después todos los días**,
y además autentica la credencial contra `/v2/userinfo` en cada corrida, así que
un token **revocado** también se caza.

**Al rotar hay que actualizar la fecha en dos sitios:**

```sh
# 1. El .env local
LINKEDIN_TOKEN_ISSUED=2026-10-20

# 2. La variable del repo, que es la que lee el nightly
gh variable set LINKEDIN_TOKEN_ISSUED --body "2026-10-20"
```

Si no querés rotarlo, `PUBLISH_LINKEDIN=false` apaga la red y silencia el aviso
—en el `.env` y en `gh variable set PUBLISH_LINKEDIN --body "false"`—. Al
volver a encenderlo, la fecha vieja hace que el primer nightly avise solo.
```

- [ ] **Step 4: Commit**

```sh
git add .env.example README.md
git commit -m "docs: el token de LinkedIn se rota en dos sitios

La fecha vive en el .env y en una variable del repo, y el nightly lee la
segunda. Rotar sin actualizar la variable deja la alerta sonando, que es
fail-loud y aceptable; lo que no puede pasar es el silencio.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 8: verificación de punta a punta

**Files:** ninguno

- [ ] **Step 1: Correr la suite entera y los tres gates**

```sh
./.venv/Scripts/python.exe -m pytest tests/unit/ tests/integration/ -v
./.venv/Scripts/python.exe -m ruff check src/ tests/ scripts/
./.venv/Scripts/python.exe -m ruff format --check src/ tests/ scripts/
./.venv/Scripts/python.exe -m mypy src/ scripts/
```

Esperado: todo verde. El conteo de tests tiene que haber subido desde 205.

- [ ] **Step 2: Lanzar el nightly a mano**

Es cron + `workflow_dispatch`, así que sin esto los pasos nuevos no corren hasta
el día siguiente. Es el mismo motivo por el que se lanzó a mano en `6a764bc`.

```sh
git push
gh workflow run contract-tests.yml
sleep 90 && gh run list --workflow=contract-tests.yml --limit 1
```

Esperado: la run en verde, con el paso "Avisar si el token de LinkedIn esta por
vencer" diciendo `Token de LinkedIn: hoy no toca avisar` (el token tiene ~10
días al 2026-08-31) y "Verificar la credencial de LinkedIn" diciendo
`Credencial de LinkedIn verificada`.

- [ ] **Step 3: Falsificar el aviso a mano**

Un paso que nunca se vio disparar no está verificado. Poner la variable a una
fecha vieja, lanzar, confirmar que **llega el mensaje a Telegram**, y volver
atrás:

```sh
gh variable set LINKEDIN_TOKEN_ISSUED --body "2020-01-01"
gh workflow run contract-tests.yml
# esperar, confirmar el mensaje en el chat, y despues:
gh variable set LINKEDIN_TOKEN_ISSUED --body "2026-08-21"
```

Esperado: llega un mensaje que empieza con `[LinkedIn] El token esta vencido`.
**Si no llega, el trabajo no está hecho**, por más verde que esté la run: es
exactamente el modo de fallo que este plan existe para cerrar.

- [ ] **Step 4: Confirmar el CI sobre el HEAD exacto**

```sh
gh run list --workflow=ci.yml --limit 1
```

Esperado: verde, sobre el SHA del último commit, con el job `nightly-vivo`
reportando `Estado de contract-tests.yml: active`.

- [ ] **Step 5: Actualizar la memoria del backlog**

Marcar el punto 7 como cerrado en
`~/.claude/projects/.../memory/macropipeline-pending-work.md`, con el rango de
commits, el número de run de CI y el conteo de tests. Anotar lo que no se
dedujo del código:

- El aviso viejo no tenía canal: era un `print` de un script manual.
- Re-habilitar un workflow apagado por inactividad es **manual**; ninguna
  actividad posterior lo reactiva.
- `vars.PUBLISH_LINKEDIN` es un espejo a mano del `.env` y la deriva es
  asimétrica: espejo `false` con `.env` `true` es **fail-silent** y no lo cierra
  ninguna pieza de este trabajo.
- Quedan tres copias del bloque de `curl` + status code en
  `contract-tests.yml`; a la cuarta, extraer a un script.

---

## Cobertura del spec

| Sección del spec | Task |
|---|---|
| a. Dos variables de repo | 6 |
| b. Dos secrets nuevos | 6 |
| c. Paso de edad escalonado | 1, 3 |
| c. Orden de las guardas | 1 (test + mutación) |
| d. Chequeo real de credencial | 2 (marcadores), 4 (paso) |
| e. Guarda contra el disable | 5 |
| f. Bug del 403 | 2 |
| g. Documentación | 7 |
| Tests y mutación | 1, 2, 8 |
