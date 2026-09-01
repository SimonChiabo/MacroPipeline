# Chequeo de credenciales para los seis componentes — plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** que `scripts/check_credentials.py` autentique de verdad los seis componentes con credenciales —X, LinkedIn, FRED, Alpha Vantage, Anthropic y R2— en vez de solo las dos redes, con una sonda de escritura para R2 porque su permiso de escritura no se puede confirmar leyendo.

**Architecture:** el script pasa de dos chequeos cableados a una tabla de seis, construida **dentro** de `main()` (para que `monkeypatch.setattr` sobre el módulo siga funcionando) y gateada por los `USE_*`/`PUBLISH_*` que `components.py` ya expone. Cada chequeo devuelve uno de tres veredictos —`listo`, `NO listo`, `sin verificar`— porque el rate limit de Alpha Vantage no es ninguno de los dos primeros. Los tres chequeos HTTP usan `requests` a pelo (el script no puede arrastrar pandas); R2 usa `R2Client` para ejercitar el mismo camino que el pipeline.

**Tech Stack:** Python 3.12, `requests`, `boto3` vía `R2Client`, pytest 9.0.3, ruff 0.15.12 (`line-length = 88`), mypy 2.1.0 (`--strict`, corre sobre `scripts/`).

**Spec:** `docs/superpowers/specs/2026-09-01-check-credentials-all-components-design.md`

**Todos los commits** de este plan terminan con los dos trailers del repo:

```
Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QBTyHnzK9HfeRi3LwmHFck
```

---

## Estructura de ficheros

| Fichero | Responsabilidad | Acción |
|---|---|---|
| `scripts/check_credentials.py` | Los seis chequeos, el gate por switch, el código de salida | Renombrado desde `check_publishers.py` + extendido |
| `src/macro_pipeline/storage/r2_client.py` | Único código de producción que cambia: gana `delete_object` | Modificado |
| `tests/unit/test_check_credentials.py` | Todos los tests del script | Renombrado desde `test_check_publishers.py` + extendido |
| `tests/unit/test_r2_client.py` | Tests de `delete_object` | Modificado (o creado si no existe) |
| `.github/workflows/contract-tests.yml` | Blindaje del paso que ya corre el script | Modificado |
| `README.md`, `src/macro_pipeline/orchestration/main.py`, `src/macro_pipeline/components.py`, `scripts/linkedin_token_alert.py`, `.github/workflows/ci.yml` | Referencias al nombre viejo y textos que quedan mentirosos | Modificados |

**Lo que NO se toca:** los `revision_*.md` de la raíz son informes fechados de lo que se vio ese día, no documentación viva.

---

## Task 1: El rename, sin ningún cambio de comportamiento

Va primero y solo: mezclado con lógica nueva, un rename de 60 ocurrencias hace ilegible el diff de todo lo demás.

**Files:**
- Rename: `scripts/check_publishers.py` → `scripts/check_credentials.py`
- Rename: `tests/unit/test_check_publishers.py` → `tests/unit/test_check_credentials.py`
- Modify: `README.md:100`, `.github/workflows/ci.yml:128`, `.github/workflows/contract-tests.yml:179`, `src/macro_pipeline/orchestration/main.py:552`, `src/macro_pipeline/components.py:5,62`, `scripts/linkedin_token_alert.py:5`

- [x] **Step 1: Mover los dos ficheros conservando la historia**

```bash
git mv scripts/check_publishers.py scripts/check_credentials.py
git mv tests/unit/test_check_publishers.py tests/unit/test_check_credentials.py
```

- [x] **Step 2: Reemplazar el nombre en el fichero de tests**

Un `sed` global sobre este fichero es correcto y no necesita cuidado especial: la cadena `check_publishers` aparece como nombre de fixture, como nombre de módulo cargado por ruta y dentro de la ruta `scripts/check_publishers.py`, y las tres se convierten bien con el mismo reemplazo.

```bash
sed -i 's/check_publishers/check_credentials/g' tests/unit/test_check_credentials.py
```

- [x] **Step 3: Reemplazar las referencias en el resto del repo**

```bash
sed -i 's|scripts/check_publishers\.py|scripts/check_credentials.py|g' \
  README.md \
  .github/workflows/ci.yml \
  .github/workflows/contract-tests.yml \
  src/macro_pipeline/orchestration/main.py \
  src/macro_pipeline/components.py \
  scripts/linkedin_token_alert.py \
  scripts/check_credentials.py
```

- [x] **Step 4: Verificar que no queda ninguna referencia viva**

```bash
grep -rn "check_publishers" --include=*.py --include=*.yml --include=*.md . | grep -v "revision_" | grep -v "docs/superpowers"
```

Expected: sin salida. Los hits en `revision_*.md` y en los planes/specs viejos son historia fechada y **se quedan como están**.

- [x] **Step 5: Correr la suite entera**

Run: `pytest tests/unit tests/integration -q`
Expected: PASS, el mismo número de tests que antes del rename (318 al momento de escribir esto). Un rename no cambia ninguna cuenta.

- [x] **Step 6: Lint y tipos**

Run: `ruff check src/ tests/ scripts/ && ruff format --check src/ tests/ scripts/ && mypy src/ scripts/`
Expected: todo limpio.

- [x] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor: check_publishers.py pasa a llamarse check_credentials.py

Su codigo de salida esta por dejar de hablar solo de publicacion. Un
nombre que miente es peor deuda que el churn del rename.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QBTyHnzK9HfeRi3LwmHFck"
```

---

## Task 2: `R2Client.delete_object`

El único código de producción que este trabajo agrega. Va en el cliente y no en el script para que la sonda pase por el mismo manejo de las dos ramas de botocore que el resto.

**Files:**
- Modify: `src/macro_pipeline/storage/r2_client.py` (después de `download_object`)
- Test: `tests/unit/test_r2_client.py`

- [x] **Step 1: Escribir los dos tests que fallan**

Si `tests/unit/test_r2_client.py` ya existe, estos tests se agregan al final y se reutilizan los dobles que ya haya en el fichero; si no existe, se crea con este contenido completo.

```python
"""Tests de `R2Client.delete_object`.

El pipeline nunca borra: `state_sync.py` solo sube y baja. Este metodo existe
para el chequeo de credenciales, que necesita limpiar su objeto de prueba, y
por eso su fallo es un aviso y no un fallo del chequeo.
"""

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from macro_pipeline.storage.r2_client import R2Client, R2ClientError


class _S3Falso:
    """Doble del cliente de boto3: registra llamadas y levanta si se le pide."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.borrados: list[tuple[str, str]] = []

    def delete_object(self, Bucket: str, Key: str) -> None:  # noqa: N803
        if self.error:
            raise self.error
        self.borrados.append((Bucket, Key))


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setenv("R2_ACCOUNT_ID", "una-cuenta")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "una-key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "un-secreto")
    monkeypatch.setenv("R2_BUCKET_NAME", "un-bucket")
    return R2Client()


def test_delete_object_borra_la_key_pedida(cliente):
    s3 = _S3Falso()
    cliente.s3 = s3

    cliente.delete_object("healthcheck/probe.txt")

    assert s3.borrados == [("un-bucket", "healthcheck/probe.txt")]


def test_delete_object_atrapa_las_dos_ramas_de_botocore(cliente):
    """`ClientError` y `BotoCoreError` son hermanas, no madre e hija.

    Un `except ClientError` a secas deja escapar `EndpointConnectionError`, que
    es el fallo mas probable. Es la divergencia (b) de ADR-009.
    """
    for error in (
        ClientError({"Error": {"Code": "AccessDenied"}}, "DeleteObject"),
        EndpointConnectionError(endpoint_url="https://ejemplo"),
    ):
        cliente.s3 = _S3Falso(error=error)
        with pytest.raises(R2ClientError):
            cliente.delete_object("healthcheck/probe.txt")
```

- [x] **Step 2: Correr los tests para verificar que fallan**

Run: `pytest tests/unit/test_r2_client.py -q -k delete_object`
Expected: FAIL con `AttributeError: 'R2Client' object has no attribute 'delete_object'` en los dos tests.

- [x] **Step 3: Implementar el método**

En `src/macro_pipeline/storage/r2_client.py`, inmediatamente después de `download_object` y antes del comentario `# ── Imágenes ──`:

```python
    def delete_object(self, key: str) -> None:
        """Borra `key`. Lo usa el chequeo de credenciales, no el pipeline.

        Nada en el camino de producción borra: `state_sync.py` solo llama a
        `upload_object` y `download_object`. Por eso el chequeo trata un fallo
        de borrado como aviso y no como fallo — exigir permiso de `DeleteObject`
        pondría en rojo un token capaz de hacer todo lo que el pipeline
        necesita.

        Vive acá y no en el script para que la sonda no invente su propio
        manejo de errores: las dos ramas de botocore, igual que sus hermanas.
        """
        try:
            self.s3.delete_object(Bucket=self.bucket, Key=key)
        except (ClientError, BotoCoreError) as e:
            logger.error("r2_delete_failed", key=key, error=str(e))
            raise R2ClientError(f"Error borrando de R2: {e}") from e
```

- [x] **Step 4: Correr los tests para verificar que pasan**

Run: `pytest tests/unit/test_r2_client.py -q && mypy src/`
Expected: PASS y mypy limpio.

- [x] **Step 5: Commit**

```bash
git add src/macro_pipeline/storage/r2_client.py tests/unit/test_r2_client.py
git commit -m "feat(storage): R2Client aprende a borrar, para que el chequeo limpie

El pipeline sigue sin borrar nada. Este metodo existe solo para que la
sonda de credenciales no deje su objeto de prueba tirado, y por eso su
fallo va a ser un aviso y no un fallo.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QBTyHnzK9HfeRi3LwmHFck"
```

---

## Task 3: La tabla de seis, los tres veredictos y el chequeo de FRED

Esta task cambia la forma de `main()` y suma el primer componente nuevo. Los dos van juntos porque una tabla vacía no se puede testear y un chequeo sin tabla no se puede llamar.

**Files:**
- Modify: `scripts/check_credentials.py`
- Test: `tests/unit/test_check_credentials.py`

- [x] **Step 1: Escribir los tests que fallan**

Al final de `tests/unit/test_check_credentials.py`. El helper `_apagar_los_cuatro` es imprescindible y no cosmético: **los `USE_*` ausentes significan encendido**, así que sin él cualquier test que llame a `main()` sale a internet.

```python
def _apagar_los_cuatro(monkeypatch):
    """Los cuatro componentes nuevos, apagados.

    Sin esto, un test que llame a `main()` contacta FRED, Alpha Vantage,
    Anthropic y R2 de verdad: `component_enabled` trata la variable ausente
    como encendido a proposito, y la suite unitaria no sale a la red.
    """
    for var in ("USE_FRED", "USE_AV", "USE_ANTHROPIC", "USE_R2"):
        monkeypatch.setenv(var, "false")


def test_un_componente_apagado_no_se_chequea_ni_pone_rojo(
    check_credentials, monkeypatch, capsys
):
    """La misma regla que ya rige para las redes, extendida a los cuatro.

    Apagar es una decision, no un fallo (tercer eje de ADR-009): el componente
    no se contacta y no cuenta para el codigo de salida.
    """
    llamadas = []
    monkeypatch.setenv("PUBLISH_X", "false")
    monkeypatch.setenv("PUBLISH_LINKEDIN", "false")
    _apagar_los_cuatro(monkeypatch)
    monkeypatch.setattr(check_credentials, "report_env_drift", lambda *a, **k: None)
    monkeypatch.setattr(
        check_credentials, "check_fred", lambda: llamadas.append("fred") or "listo"
    )

    assert check_credentials.main() == 0

    assert llamadas == []
    assert "FRED:     apagado" in capsys.readouterr().out


def test_un_switch_ilegible_de_un_componente_nuevo_no_da_traceback(
    check_credentials, monkeypatch, capsys
):
    """Mismo trato que `PUBLISH_X=yes`, y antes de contactar a nadie.

    Los seis switches se leen enteros antes de correr ningun chequeo: con uno
    ilegible, ninguna API se contacta. Si se leyeran de a uno dentro del bucle,
    un `USE_R2` mal escrito dejaria a X ya contactada.
    """
    llamadas = []
    monkeypatch.setenv("USE_R2", "puede ser")
    monkeypatch.setattr(check_credentials, "report_env_drift", lambda *a, **k: None)
    monkeypatch.setattr(
        check_credentials, "check_x", lambda: llamadas.append("x") or True
    )

    assert check_credentials.main() == 1

    salida = capsys.readouterr().out
    assert "USE_R2" in salida
    assert "puede ser" in salida
    assert llamadas == []


def test_fred_autentica_con_un_200(check_credentials, monkeypatch, capsys):
    monkeypatch.setenv("FRED_API_KEY", "una-key-cualquiera")

    class Respuesta200:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"seriess": [{"id": "UNRATE"}]}

    monkeypatch.setattr(
        check_credentials.requests, "get", lambda *a, **k: Respuesta200()
    )

    assert check_credentials.check_fred() == check_credentials.LISTO
    assert "[ OK ]" in capsys.readouterr().out


def test_fred_con_key_invalida_muestra_el_mensaje_de_la_api(
    check_credentials, monkeypatch, capsys
):
    """FRED no contesta 401: contesta 400 con `error_message` en el cuerpo.

    Un chequeo que solo mirara `!= 200` diria "HTTP 400" y nada mas, que es
    justo lo que no ayuda a nadie a las once de la noche.
    """
    monkeypatch.setenv("FRED_API_KEY", "una-key-cualquiera")

    class Respuesta400:
        status_code = 400
        text = ""

        @staticmethod
        def json():
            return {
                "error_code": 400,
                "error_message": "Bad Request. The value for variable api_key "
                "is not registered.",
            }

    monkeypatch.setattr(
        check_credentials.requests, "get", lambda *a, **k: Respuesta400()
    )

    assert check_credentials.check_fred() == check_credentials.NO_LISTO
    assert "is not registered" in capsys.readouterr().out


def test_fred_sin_respuesta_pone_rojo(check_credentials, monkeypatch, capsys):
    """No haber podido verificar pone rojo, y el texto nombra el transporte.

    Un verde que no verifico nada es peor que un rojo (es el argumento del
    fixture de `test_av_contract.py`), y una alerta que dice "la key no sirve"
    por un corte de red manda a rotar una credencial sana.
    """
    monkeypatch.setenv("FRED_API_KEY", "una-key-cualquiera")

    def _revienta(*a, **k):
        raise check_credentials.requests.RequestException("sin ruta al host")

    monkeypatch.setattr(check_credentials.requests, "get", _revienta)

    assert check_credentials.check_fred() == check_credentials.NO_LISTO
    salida = capsys.readouterr().out
    assert "No se pudo contactar" in salida
    assert "key" not in salida.lower().split("no se pudo contactar")[0]
```

- [x] **Step 2: Correr los tests para verificar que fallan**

Run: `pytest tests/unit/test_check_credentials.py -q -k "apagado or ilegible or fred"`
Expected: FAIL. Los cinco con `AttributeError: module 'check_credentials' has no attribute 'check_fred'` (o `'LISTO'`, según cuál evalúe primero) — **ninguno** con un fallo de aserción, porque todavía no existe nada que aserta.

- [x] **Step 3: Los tres veredictos y las constantes**

En `scripts/check_credentials.py`, debajo de `OK, FAIL, WARN = ...`:

```python
# Tres veredictos y no dos: el rate limit de Alpha Vantage no es "listo" ni
# "NO listo". Decir "listo" de algo que no se pudo verificar es exactamente el
# verde que no verifico nada, y decir "NO listo" de una cuota agotada pone el
# script en rojo por usarlo.
LISTO, NO_LISTO, SIN_VERIFICAR = "listo", "NO listo", "sin verificar"

FRED_VARS = ("FRED_API_KEY",)
AV_VARS = ("ALPHA_VANTAGE_API_KEY",)
ANTHROPIC_VARS = ("ANTHROPIC_API_KEY",)
R2_VARS = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
```

Y en el `import` de `macro_pipeline.components`, sumar los cuatro switches:

```python
from macro_pipeline.components import (
    PUBLISH_LINKEDIN_VAR,
    PUBLISH_X_VAR,
    USE_ANTHROPIC_VAR,
    USE_AV_VAR,
    USE_FRED_VAR,
    USE_R2_VAR,
    component_enabled,
)
```

- [x] **Step 4: El encabezado y el chequeo de FRED**

Antes de `def main()`:

```python
def _encabezado(titulo: str) -> None:
    """La linea de seccion, del mismo ancho para los seis."""
    print(f"\n-- {titulo} " + "-" * max(3, 48 - len(titulo)))


def _mensaje_de_error(response: requests.Response) -> str:
    """El texto que la API da para explicarse, o el cuerpo crudo.

    FRED pone el motivo real en `error_message` y devuelve 400, no 401: sin
    esto el diagnostico seria "HTTP 400" a secas.
    """
    try:
        cuerpo = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(cuerpo, dict) and "error_message" in cuerpo:
        return str(cuerpo["error_message"])
    return response.text[:200]


def check_fred() -> str:
    """GET /fred/series: la llamada mas barata que igual autentica."""
    if _check_present(FRED_VARS):
        return NO_LISTO

    try:
        response = requests.get(
            "https://api.stlouisfed.org/fred/series",
            params={
                "series_id": "UNRATE",
                "file_type": "json",
                "api_key": os.environ["FRED_API_KEY"],
            },
            timeout=15,
        )
    except requests.RequestException as e:
        print(f"{FAIL} No se pudo contactar la API de FRED: {e}")
        print("       La key quedo sin verificar; no es necesariamente ella.")
        return NO_LISTO

    if response.status_code == 200:
        print(f"{OK} La key de FRED autentica.")
        return LISTO

    print(f"{FAIL} HTTP {response.status_code}: {_mensaje_de_error(response)}")
    return NO_LISTO
```

- [x] **Step 5: Reescribir `main()` como tabla**

Reemplazar el cuerpo de `main()` entero por esto. Tres detalles que **no** son estéticos:

1. La lista se construye **dentro** de `main()`. A nivel de módulo congelaría las referencias a las funciones y `monkeypatch.setattr(check_credentials, "check_x", ...)` dejaría de tener efecto, rompiendo los tests que ya existen.
2. Los seis switches se leen **antes** de correr ningún chequeo, que es lo que hace hoy con los dos. Leyéndolos dentro del bucle, un `USE_R2` ilegible dejaría a X ya contactada.
3. El ancho `:<9` del resumen está elegido para que `"X:        apagado"` y `"LinkedIn: apagado"` sigan saliendo con el espaciado exacto de siempre, y `"Alpha Vantage: listo"` no quede pegado.

```python
def main() -> int:
    print("Verificación de credenciales. No publica nada; para R2 escribe y")
    print("borra un objeto de prueba en tu propio bucket (ver el docstring).")
    report_env_drift(ROOT / ".env.example", ROOT / ".env")

    # Las funciones se nombran acá y no en una constante de módulo: así
    # `monkeypatch.setattr` sobre el módulo sigue funcionando en los tests.
    chequeos: list[tuple[str, str, Callable[[], str]]] = [
        ("X", PUBLISH_X_VAR, _veredicto(check_x)),
        ("LinkedIn", PUBLISH_LINKEDIN_VAR, _veredicto(check_linkedin)),
        ("FRED", USE_FRED_VAR, check_fred),
    ]

    # Los seis switches, antes de contactar a nadie. Uno ilegible no puede
    # dejar media verificación hecha.
    estados: list[tuple[str, str, Callable[[], str], bool]] = []
    for titulo, var, chequeo in chequeos:
        try:
            estados.append((titulo, var, chequeo, component_enabled(var)))
        except ValueError as e:
            # El único sitio donde el valor malo no sale por traceback: este
            # script existe para decirle a un humano qué tiene mal configurado.
            print(f"{FAIL} {e}")
            return 1

    resultados: list[tuple[str, str]] = []
    for titulo, var, chequeo, encendido in estados:
        _encabezado(titulo)
        if not encendido:
            print(f"{OK} Apagado por {var}=false: no se verifica.")
            resultados.append((titulo, "apagado"))
            continue
        resultados.append((titulo, chequeo()))

    print("\n-- Resultado --------------------------------------")
    for titulo, estado in resultados:
        print(f"{titulo + ':':<9} {estado}")

    if all(estado == "apagado" for _, estado in resultados):
        print("\nTodo apagado: no hay ninguna credencial que verificar.")
        return 0
    if any(estado == NO_LISTO for _, estado in resultados):
        return 1
    return 0
```

Y arriba, junto a `_encabezado`:

```python
def _veredicto(chequeo: Callable[[], bool]) -> Callable[[], str]:
    """Adapta los dos chequeos que ya devolvían `bool` a los tres veredictos.

    X y LinkedIn no tienen un caso "sin verificar": un corte de red ya sale
    como `False` y con su marcador. Envolverlos acá evita tocarlos y evita
    romper los tests que asertan `check_linkedin() is False`.
    """
    return lambda: LISTO if chequeo() else NO_LISTO
```

El import de `Callable` va arriba del todo:

```python
from collections.abc import Callable
```

- [x] **Step 6: Adaptar los tres tests existentes que llaman a `main()`**

No es churn gratuito: `main()` ahora corre seis chequeos y estos tests dejarían la suite saliendo a la red.

En `test_a_disabled_network_cannot_turn_the_script_red`, `test_a_disabled_network_is_not_even_checked` y `test_a_malformed_flag_gets_a_diagnostic_and_not_a_traceback`, agregar `_apagar_los_cuatro(monkeypatch)` justo después de los `monkeypatch.setenv` de las banderas.

Y en el primero, las dos aserciones del resumen cambian de género —el resumen ya no lista dos redes sino seis componentes—:

```python
    assert "X:        apagado" in salida
    assert "LinkedIn: apagado" in salida
```

- [x] **Step 7: Correr la suite y verificar que pasa**

Run: `pytest tests/unit/test_check_credentials.py -q`
Expected: PASS, todos. Si alguno tarda más de un segundo, algo está saliendo a la red: revisar que el test apague los cuatro.

- [x] **Step 8: Verificación por mutación del orden de lectura de switches**

Mover el `component_enabled` dentro del segundo bucle (leyendo el switch justo antes de correr cada chequeo) y correr:

Run: `pytest tests/unit/test_check_credentials.py -q -k ilegible`
Expected: FAIL en `test_un_switch_ilegible_de_un_componente_nuevo_no_da_traceback`, por `assert llamadas == []`. Deshacer la mutación.

- [x] **Step 9: Commit**

```bash
git add scripts/check_credentials.py tests/unit/test_check_credentials.py
git commit -m "feat(scripts): el chequeo pasa a tabla de seis y suma FRED

Tres veredictos y no dos: 'sin verificar' existe porque el rate limit de
AV no es ni listo ni roto, y decir listo de algo que no se verifico es el
verde que no verifica nada.

Los seis switches se leen antes de contactar a nadie: uno ilegible no
puede dejar media verificacion hecha.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QBTyHnzK9HfeRi3LwmHFck"
```

---

## Task 4: Alpha Vantage, y la única excepción a la regla del rojo

**Files:**
- Modify: `scripts/check_credentials.py`
- Test: `tests/unit/test_check_credentials.py`

- [x] **Step 1: Escribir los tests que fallan**

```python
def test_av_con_key_invalida_falla(check_credentials, monkeypatch, capsys):
    """Alpha Vantage contesta 200 hasta para los errores (`av_client.py:79`).

    El status code no decide nada: lo que decide es el cuerpo.
    """
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "una-key-cualquiera")

    class Respuesta:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"Error Message": "the parameter apikey is invalid"}

    monkeypatch.setattr(check_credentials.requests, "get", lambda *a, **k: Respuesta())

    assert check_credentials.check_av() == check_credentials.NO_LISTO
    assert "apikey is invalid" in capsys.readouterr().out


def test_av_en_rate_limit_avisa_pero_no_pone_rojo(
    check_credentials, monkeypatch, capsys
):
    """La unica excepcion a "no haber podido verificar pone rojo".

    No es un fallo ajeno: el chequeo se lo fabrica solo, porque consume una
    llamada de la cuota diaria cada vez que corre. Ponerlo rojo haria que
    correrlo dos veces seguidas lo pusiera rojo por su propia culpa, y un
    chequeo que se pone rojo por usarlo es un chequeo que se desactiva.
    """
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "una-key-cualquiera")

    class Respuesta:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {
                "Information": "Thank you for using Alpha Vantage! Our standard "
                "API rate limit is 25 requests per day."
            }

    monkeypatch.setattr(check_credentials.requests, "get", lambda *a, **k: Respuesta())

    assert check_credentials.check_av() == check_credentials.SIN_VERIFICAR
    assert "AV_RATE_LIMIT" in capsys.readouterr().out


def test_el_rate_limit_de_av_no_cambia_el_codigo_de_salida(
    check_credentials, monkeypatch
):
    """El veredicto tiene que llegar entero hasta el codigo de salida.

    Es la mitad del test que el de arriba no cubre: `check_av` puede devolver
    `SIN_VERIFICAR` y `main()` contarlo igual que un `NO_LISTO`.
    """
    monkeypatch.setenv("PUBLISH_X", "false")
    monkeypatch.setenv("PUBLISH_LINKEDIN", "false")
    monkeypatch.setenv("USE_FRED", "false")
    monkeypatch.setenv("USE_ANTHROPIC", "false")
    monkeypatch.setenv("USE_R2", "false")
    monkeypatch.setenv("USE_AV", "true")
    monkeypatch.setattr(check_credentials, "report_env_drift", lambda *a, **k: None)
    monkeypatch.setattr(
        check_credentials, "check_av", lambda: check_credentials.SIN_VERIFICAR
    )

    assert check_credentials.main() == 0
```

- [x] **Step 2: Correr los tests para verificar que fallan**

Run: `pytest tests/unit/test_check_credentials.py -q -k av`
Expected: FAIL. Los dos primeros con `AttributeError: module 'check_credentials' has no attribute 'check_av'`. **El tercero también falla con `AttributeError`**, y no con `assert 1 == 0`: el `monkeypatch.setattr` sobre un atributo inexistente levanta antes de llegar al `assert`.

- [x] **Step 3: Implementar `check_av`**

```python
# El marcador que ya conoce el repo. Mismo texto y mismo motivo que en
# `tests/contract/test_av_contract.py`: separa "no pudimos verificar" de "la
# credencial no sirve". No colisiona con el del nightly, que se grepea sobre
# `pytest-output.txt`.
AV_RATE_LIMIT_MARKER = "AV_RATE_LIMIT"


def check_av() -> str:
    """GLOBAL_QUOTE: una llamada, la mas barata, y el cuerpo manda."""
    if _check_present(AV_VARS):
        return NO_LISTO

    try:
        response = requests.get(
            "https://www.alphavantage.co/query",
            params={
                "function": "GLOBAL_QUOTE",
                "symbol": "SPY",
                "apikey": os.environ["ALPHA_VANTAGE_API_KEY"],
            },
            timeout=15,
        )
    except requests.RequestException as e:
        print(f"{FAIL} No se pudo contactar la API de Alpha Vantage: {e}")
        return NO_LISTO

    if response.status_code != 200:
        print(f"{FAIL} HTTP {response.status_code}: {response.text[:200]}")
        return NO_LISTO

    try:
        datos = response.json()
    except ValueError:
        print(f"{FAIL} Alpha Vantage no devolvió JSON: {response.text[:200]}")
        return NO_LISTO

    if "Error Message" in datos:
        print(f"{FAIL} {datos['Error Message']}")
        return NO_LISTO

    if "rate limit" in str(datos.get("Information", "")).lower():
        print(f"{WARN} {AV_RATE_LIMIT_MARKER}: se agotó la cuota diaria, así que")
        print("       la key quedó SIN VERIFICAR. No es un fallo de la")
        print("       credencial: este mismo chequeo consume una llamada de esa")
        print("       cuota cada vez que corre.")
        return SIN_VERIFICAR

    print(f"{OK} La key de Alpha Vantage autentica.")
    return LISTO
```

Y sumar la fila a la tabla de `main()`, después de FRED:

```python
        ("Alpha Vantage", USE_AV_VAR, check_av),
```

- [x] **Step 4: Correr los tests para verificar que pasan**

Run: `pytest tests/unit/test_check_credentials.py -q`
Expected: PASS, todos.

- [x] **Step 5: Verificación por mutación de la excepción**

Cambiar `return SIN_VERIFICAR` por `return NO_LISTO` en la rama del rate limit y correr:

Run: `pytest tests/unit/test_check_credentials.py -q`
Expected: FAIL en **exactamente dos** tests —`test_av_en_rate_limit_avisa_pero_no_pone_rojo` (compara el veredicto) y ninguno más—; `test_el_rate_limit_de_av_no_cambia_el_codigo_de_salida` mockea `check_av`, así que la mutación no lo toca. Si cae un tercero, hay un test acoplado de más. Deshacer la mutación.

- [x] **Step 6: Commit**

```bash
git add scripts/check_credentials.py tests/unit/test_check_credentials.py
git commit -m "feat(scripts): el chequeo suma Alpha Vantage, con su excepcion

El rate limit avisa y sigue en verde. Es la unica excepcion a que no
poder verificar ponga rojo, y esta es la razon: el chequeo se fabrica
solo esa condicion al consumir cuota, asi que ponerlo rojo lo desactiva.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QBTyHnzK9HfeRi3LwmHFck"
```

---

## Task 5: Anthropic, y el listado paginado

**Files:**
- Modify: `scripts/check_credentials.py`
- Test: `tests/unit/test_check_credentials.py`

- [x] **Step 1: Escribir los tests que fallan**

El modelo se lee de `llm.client` también en los tests, por el mismo motivo por el que el script no copia el string: un literal acá haría pasar el test el día que el pipeline cambie de modelo y el chequeo deje de mirar el correcto.

```python
from macro_pipeline.llm.client import MODEL


def _respuesta_de_modelos(ids, has_more=False):
    class Respuesta:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"data": [{"id": i} for i in ids], "has_more": has_more}

    return Respuesta()


def test_anthropic_con_key_invalida_falla(check_credentials, monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "una-key-cualquiera")

    class Respuesta401:
        status_code = 401
        text = ""

    monkeypatch.setattr(
        check_credentials.requests, "get", lambda *a, **k: Respuesta401()
    )

    assert check_credentials.check_anthropic() == check_credentials.NO_LISTO
    assert "401" in capsys.readouterr().out


def test_anthropic_avisa_si_el_modelo_del_pipeline_no_esta(
    check_credentials, monkeypatch, capsys
):
    """El retiro del modelo, cazado temprano y sin poner rojo.

    Este repo ya se comio uno (`claude-3-haiku-20240307`, retirado el
    2026-04-19). Que no este no impide publicar hoy: el pipeline caeria al
    titular de emergencia, que es degradacion y no caida.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "una-key-cualquiera")
    monkeypatch.setattr(
        check_credentials.requests,
        "get",
        lambda *a, **k: _respuesta_de_modelos(["un-modelo-que-no-es"]),
    )

    assert check_credentials.check_anthropic() == check_credentials.LISTO
    salida = capsys.readouterr().out
    assert "[AVISO]" in salida
    assert MODEL in salida


def test_anthropic_pagina_el_listado(check_credentials, monkeypatch, capsys):
    """El listado es paginado y el modelo puede caer en la segunda pagina.

    Un chequeo que mirara solo la primera respuesta avisaria de un retiro
    inexistente. Un AVISO falso entrena a ignorar el verdadero, que es el modo
    de fallo que este trabajo existe para evitar.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "una-key-cualquiera")
    paginas = [
        _respuesta_de_modelos(["otro-modelo"], has_more=True),
        _respuesta_de_modelos([MODEL]),
    ]
    monkeypatch.setattr(
        check_credentials.requests, "get", lambda *a, **k: paginas.pop(0)
    )

    assert check_credentials.check_anthropic() == check_credentials.LISTO
    assert "[AVISO]" not in capsys.readouterr().out
```

- [x] **Step 2: Correr los tests para verificar que fallan**

Run: `pytest tests/unit/test_check_credentials.py -q -k anthropic`
Expected: FAIL, los tres con `AttributeError: module 'check_credentials' has no attribute 'check_anthropic'`. `MODEL` se importa directo de `llm.client` en el fichero de tests, así que ese import **no** falla: el único que falta es la función.

- [x] **Step 3: Implementar `check_anthropic`**

El modelo se importa de `llm/client.py` y **no se copia el string**: copiarlo haría que el chequeo mintiera el día que el pipeline cambie de modelo, que es exactamente el día en que uno quiere que no mienta.

Pero el import va **dentro de la función**, no arriba. Medido en este repo: `from macro_pipeline.llm.client import MODEL` arrastra el SDK de Anthropic entero —**1642 módulos y 1.9 s**, contra 474 y 0.5 s del script tal como está hoy—. Es el import más caro de todo el script, y adentro solo se paga cuando Anthropic está encendido: el paso del nightly, que lo apaga, no lo paga nunca. `check_x` y `check_linkedin` ya usan sus dependencias así de local, y ruff no se queja de un import diferido con motivo.

Las constantes de módulo y el cuerpo entero:

```python
# Tope de páginas. El listado es chico, pero un `has_more` siempre en `true`
# —un bug de la API, un proxy raro— dejaría el chequeo colgado para siempre.
_MAX_PAGINAS = 10


def check_anthropic() -> str:
    """GET /v1/models: autentica sin gastar tokens, por eso este y no un mensaje."""
    # Adentro a propósito: importar esto arrastra el SDK de Anthropic entero
    # (1642 módulos, ~1.9 s medidos) y es el import más caro del script. Acá
    # solo lo paga quien tiene Anthropic encendido; el paso del nightly, que lo
    # apaga, no lo paga nunca. Se importa la constante y no se copia el string:
    # una copia mentiría el día que el pipeline cambie de modelo.
    from macro_pipeline.llm.client import MODEL

    if _check_present(ANTHROPIC_VARS):
        return NO_LISTO

    headers = {
        "x-api-key": os.environ["ANTHROPIC_API_KEY"],
        "anthropic-version": "2023-06-01",
    }
    params: dict[str, str | int] = {"limit": 1000}
    ids: list[str] = []

    for _ in range(_MAX_PAGINAS):
        try:
            response = requests.get(
                "https://api.anthropic.com/v1/models",
                headers=headers,
                params=params,
                timeout=15,
            )
        except requests.RequestException as e:
            print(f"{FAIL} No se pudo contactar la API de Anthropic: {e}")
            return NO_LISTO

        if response.status_code == 401:
            print(f"{FAIL} 401: la ANTHROPIC_API_KEY no autentica.")
            return NO_LISTO
        if response.status_code != 200:
            print(f"{FAIL} HTTP {response.status_code}: {response.text[:200]}")
            return NO_LISTO

        cuerpo = response.json()
        ids.extend(str(m.get("id")) for m in cuerpo.get("data", []))
        if not cuerpo.get("has_more"):
            break
        params = {"limit": 1000, "after_id": ids[-1]}

    print(f"{OK} La key de Anthropic autentica ({len(ids)} modelos visibles).")

    if MODEL not in ids:
        print(f"{WARN} {MODEL} no aparece en el listado: puede estar retirado o")
        print("       no habilitado para esta key. No impide publicar hoy —el")
        print("       pipeline caería al titular de emergencia— pero es la")
        print("       señal temprana, y este repo ya se comió un retiro.")

    return LISTO
```

Y la fila en la tabla de `main()`, después de Alpha Vantage:

```python
        ("Anthropic", USE_ANTHROPIC_VAR, check_anthropic),
```

- [x] **Step 4: Correr los tests para verificar que pasan**

Run: `pytest tests/unit/test_check_credentials.py -q && mypy scripts/`
Expected: PASS y mypy limpio.

- [x] **Step 5: Commit**

```bash
git add scripts/check_credentials.py tests/unit/test_check_credentials.py
git commit -m "feat(scripts): el chequeo suma Anthropic, paginando el listado

/v1/models autentica sin gastar tokens. Pagina a proposito: con una sola
pagina, el dia que claude-haiku-4-5 caiga en la segunda el chequeo
avisaria de un retiro inexistente, y un aviso falso entrena a ignorar el
verdadero.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QBTyHnzK9HfeRi3LwmHFck"
```

---

## Task 6: R2, la sonda de escritura

El chequeo que motiva todo el trabajo, y el único con efectos.

**Files:**
- Modify: `scripts/check_credentials.py`
- Test: `tests/unit/test_check_credentials.py`

- [x] **Step 1: Escribir los tests que fallan**

```python
class _R2Falso:
    """Doble de `R2Client`: registra el orden de las llamadas.

    El orden es lo que se testea, asi que el doble lo graba: un doble que solo
    contara llamadas no podria distinguir put->get de get->put.
    """

    def __init__(self, error_en=None, error=None):
        self.error_en = error_en
        self.error = error or Exception("fallo")
        self.llamadas: list[str] = []
        self.keys: list[str] = []
        self.objetos: dict[str, bytes] = {}

    def _quizas_reventar(self, operacion: str, key: str) -> None:
        self.llamadas.append(operacion)
        # Las keys se guardan acá y no en el script: que la sonda escriba donde
        # dice es asunto del test, y una global de módulo puesta para poder
        # mirarla desde afuera es código de producción que no sirve a nadie.
        self.keys.append(key)
        if self.error_en == operacion:
            raise self.error

    def upload_object(self, key: str, body: bytes, content_type: str) -> None:
        self._quizas_reventar("put", key)
        self.objetos[key] = body

    def download_object(self, key: str) -> bytes | None:
        self._quizas_reventar("get", key)
        return self.objetos.get(key)

    def delete_object(self, key: str) -> None:
        self._quizas_reventar("delete", key)
        self.objetos.pop(key, None)


def _con_r2(check_credentials, monkeypatch, doble):
    monkeypatch.setenv("R2_ACCOUNT_ID", "una-cuenta")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "una-key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "un-secreto")
    monkeypatch.setattr(check_credentials, "R2Client", lambda: doble)
    return doble


def test_r2_escribe_lee_y_limpia(check_credentials, monkeypatch, capsys):
    doble = _con_r2(check_credentials, monkeypatch, _R2Falso())

    assert check_credentials.check_r2() == check_credentials.LISTO
    assert doble.llamadas == ["put", "get", "delete"]
    assert doble.objetos == {}


def test_la_sonda_de_r2_escribe_antes_de_leer(check_credentials, monkeypatch):
    """El orden no es estetico: `download_object` traduce NoSuchBucket a None.

    Esa traduccion es correcta para el sincronizado de estado —un bucket sin
    crear es indistinguible de un objeto que aun no existe— y engañosa aca.
    Arrancando por la lectura, un bucket inexistente se leeria como "primera
    corrida" y el chequeo pasaria en verde con el bucket sin crear.
    """
    doble = _con_r2(check_credentials, monkeypatch, _R2Falso())

    check_credentials.check_r2()

    assert doble.llamadas[0] == "put"


def test_la_sonda_de_r2_nunca_toca_el_fichero_de_estado(
    check_credentials, monkeypatch
):
    """`state/state.db` es el fichero cuya perdida republica un cierre."""
    doble = _con_r2(check_credentials, monkeypatch, _R2Falso())

    check_credentials.check_r2()

    assert doble.keys, "la sonda no toco R2 en absoluto"
    assert all(k.startswith("healthcheck/") for k in doble.keys)


def test_r2_con_token_de_solo_lectura_nombra_el_caso_de_x(
    check_credentials, monkeypatch, capsys
):
    """Es el diagnostico que justifica toda la sonda.

    Un token de solo lectura autentica perfecto y falla al escribir, igual que
    el `x-access-level: read` de X. La API de S3 no tiene cabecera equivalente:
    no hay forma de saberlo sin poner un objeto.
    """
    from macro_pipeline.storage.r2_client import R2ClientError

    doble = _con_r2(
        check_credentials,
        monkeypatch,
        _R2Falso(error_en="put", error=R2ClientError("... AccessDenied ...")),
    )

    assert check_credentials.check_r2() == check_credentials.NO_LISTO
    salida = capsys.readouterr().out
    assert "SOLO LECTURA" in salida
    assert doble.llamadas == ["put"]


def test_un_borrado_fallido_avisa_pero_no_pone_rojo(
    check_credentials, monkeypatch, capsys
):
    """El pipeline nunca borra: `state_sync.py` solo sube y baja.

    Exigir permiso de DeleteObject pondria en rojo un token capaz de hacer
    todo lo que el pipeline necesita.
    """
    from macro_pipeline.storage.r2_client import R2ClientError

    _con_r2(
        check_credentials,
        monkeypatch,
        _R2Falso(error_en="delete", error=R2ClientError("sin permiso")),
    )

    assert check_credentials.check_r2() == check_credentials.LISTO
    assert "[AVISO]" in capsys.readouterr().out


def test_r2_falla_si_lo_escrito_no_se_puede_releer(
    check_credentials, monkeypatch, capsys
):
    """Un put que dice haber funcionado y un get que no lo ve es un fallo."""

    class _R2Amnesico(_R2Falso):
        def download_object(self, key):
            self._quizas_reventar("get", key)
            return None

    _con_r2(check_credentials, monkeypatch, _R2Amnesico())

    assert check_credentials.check_r2() == check_credentials.NO_LISTO
    assert "no se pudo releer" in capsys.readouterr().out
```

- [x] **Step 2: Correr los tests para verificar que fallan**

Run: `pytest tests/unit/test_check_credentials.py -q -k r2`
Expected: FAIL. Los seis con `AttributeError: module 'check_credentials' has no attribute 'R2Client'` — el `monkeypatch.setattr` sobre el nombre inexistente levanta antes que cualquier `assert`.

- [x] **Step 3: Implementar `check_r2`**

Imports arriba: `R2Client` entra sin arrastrar pandas —`storage/` no tiene `__init__.py` y `r2_client.py` solo importa boto3 y structlog—.

```python
import uuid
from datetime import UTC, date, datetime

from macro_pipeline.storage.r2_client import R2Client, R2ClientError
```

(`date` ya está importado; la línea queda así al sumarle `UTC` y `datetime`.)

```python
# Prefijo propio, lejos de `state/`. La sonda no puede acercarse al fichero
# cuya perdida republica un cierre.
HEALTHCHECK_PREFIX = "healthcheck/"


def check_r2() -> str:
    """Sonda de escritura: es lo unico que no se puede obtener leyendo.

    Los tokens de R2 se emiten *Object Read only* u *Object Read & Write*, y la
    API de S3 no tiene equivalente a la cabecera `x-access-level` que salva a X:
    no hay dry-run ni forma de confirmar `PutObject` sin poner un objeto.
    """
    if _check_present(R2_VARS):
        return NO_LISTO

    try:
        cliente = R2Client()
    except ValueError as e:
        print(f"{FAIL} {e}")
        return NO_LISTO

    key = f"{HEALTHCHECK_PREFIX}check-credentials-{uuid.uuid4()}.txt"
    cuerpo = f"macropipeline healthcheck {datetime.now(UTC).isoformat()}".encode()

    # El put va PRIMERO a proposito. `download_object` traduce `NoSuchBucket` a
    # ausencia (None) —correcto para el sincronizado de estado, engañoso aca—,
    # asi que arrancando por la lectura un bucket inexistente se leeria como
    # "primera corrida" y esto pasaria en verde.
    try:
        cliente.upload_object(key, cuerpo, "text/plain")
    except R2ClientError as e:
        print(f"{FAIL} No se pudo escribir en R2: {e}")
        if "AccessDenied" in str(e):
            print("       Es el mismo caso que el token de X: este es de SOLO")
            print("       LECTURA. Hay que reemitirlo con permiso 'Object Read")
            print("       & Write'; cambiarlo en el panel no alcanza para un")
            print("       token ya emitido.")
        return NO_LISTO
    print(f"{OK} PutObject: el token puede escribir.")

    try:
        leido = cliente.download_object(key)
    except R2ClientError as e:
        print(f"{FAIL} Se escribió pero no se pudo releer: {e}")
        return NO_LISTO
    if leido != cuerpo:
        print(f"{FAIL} Se escribió pero no se pudo releer igual: lo que volvió")
        print("       no coincide con lo que se subió.")
        return NO_LISTO
    print(f"{OK} GetObject: lo escrito se lee igual.")

    try:
        cliente.delete_object(key)
    except R2ClientError as e:
        print(f"{WARN} No se pudo borrar el objeto de prueba: {e}")
        print(f"{WARN} Queda huérfano en {key}. No impide publicar: el pipeline")
        print("       nunca borra, así que el permiso de Delete no le hace falta.")
        return LISTO
    print(f"{OK} DeleteObject: el objeto de prueba se limpió.")

    return LISTO
```

Y la última fila de la tabla en `main()`:

```python
        ("R2", USE_R2_VAR, check_r2),
```

- [x] **Step 4: Correr los tests para verificar que pasan**

Run: `pytest tests/unit/test_check_credentials.py -q && mypy scripts/`
Expected: PASS y mypy limpio.

- [x] **Step 5: Verificación por mutación del orden**

Mover el bloque del `download_object` delante del `upload_object` y correr:

Run: `pytest tests/unit/test_check_credentials.py -q -k r2`
Expected: FAIL en `test_la_sonda_de_r2_escribe_antes_de_leer` **y solo ahí** entre los tests de orden (los de camino feliz también caen porque la lista de llamadas cambia; lo que se comprueba es que el test del orden no pase con la mutación puesta). Deshacer.

- [x] **Step 6: Commit**

```bash
git add scripts/check_credentials.py tests/unit/test_check_credentials.py
git commit -m "feat(scripts): la sonda de escritura de R2, que es el motivo de todo

R2 es el unico de los ocho componentes sin contract test, sin secret en
CI y sin fixture, y el unico cuyo permiso no se puede confirmar leyendo.
Put, get y delete bajo healthcheck/: el put va primero porque
download_object traduce NoSuchBucket a ausencia y un bucket sin crear
pasaria en verde.

Un borrado fallido avisa y sigue: el pipeline nunca borra.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QBTyHnzK9HfeRi3LwmHFck"
```

---

## Task 7: Blindar el paso del nightly

**Sin esto, el primer nightly después del merge alerta todas las noches culpando a LinkedIn de que falta la key de FRED.** Va en este plan y no "después".

**Files:**
- Modify: `.github/workflows/contract-tests.yml` (paso "Verificar la credencial de LinkedIn", bloque `env:`)
- Test: `tests/unit/test_check_credentials.py`

- [x] **Step 1: Escribir el test que falla**

```python
def test_el_nightly_apaga_los_cuatro_componentes_no_publicadores(check_credentials):
    """El blindaje del paso de LinkedIn, fijado por un test.

    Ese paso corre el script con solo los secrets de LinkedIn. Sin apagar los
    cuatro, cae en la rama generica y manda "la verificacion de la credencial
    de LinkedIn fallo por otro motivo" todas las noches, culpando a LinkedIn de
    que falta la key de FRED. El comentario de ese mismo paso ya nombra ese
    modo de fallo: una alerta que señala al componente equivocado es peor que
    ninguna.

    Se lee el YAML como texto y no con un parser: lo que hay que fijar es que
    las cuatro lineas esten en ESE paso, y el bloque se identifica por su
    nombre.
    """
    workflow = (ROOT / ".github" / "workflows" / "contract-tests.yml").read_text(
        encoding="utf-8"
    )
    inicio = workflow.index("name: Verificar la credencial de LinkedIn")
    paso = workflow[inicio : workflow.index("\n      - name:", inicio + 1)]

    for var in ("USE_FRED", "USE_AV", "USE_ANTHROPIC", "USE_R2"):
        assert f'{var}: "false"' in paso, (
            f"{var} no esta apagada en el paso de LinkedIn: ese paso corre el "
            f"chequeo con solo los secrets de LinkedIn y va a alertar todas "
            f"las noches culpando a la red equivocada."
        )
```

- [x] **Step 2: Correr el test para verificar que falla**

Run: `pytest tests/unit/test_check_credentials.py -q -k nightly`
Expected: FAIL con el mensaje `USE_FRED no esta apagada en el paso de LinkedIn: ...`.

- [x] **Step 3: Blindar el paso**

En `.github/workflows/contract-tests.yml`, en el `env:` del paso `Verificar la credencial de LinkedIn`, debajo de `PUBLISH_X: "false"`:

```yaml
          # Mismo movimiento que el `PUBLISH_X` de arriba y por el mismo
          # motivo: este paso verifica LinkedIn y su alerta habla de LinkedIn.
          # Desde que el script chequea los seis componentes, dejarlos
          # encendidos aca lo pondria rojo por credenciales que este job no
          # tiene, y la alerta culparia a LinkedIn de que falta la key de FRED.
          USE_FRED: "false"
          USE_AV: "false"
          USE_ANTHROPIC: "false"
          USE_R2: "false"
```

Y actualizar el comentario que está arriba del paso para que mencione el blindaje.

- [x] **Step 4: Correr el test para verificar que pasa**

Run: `pytest tests/unit/test_check_credentials.py -q -k nightly`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add .github/workflows/contract-tests.yml tests/unit/test_check_credentials.py
git commit -m "ci: el paso de LinkedIn apaga los cuatro componentes nuevos

El script ahora chequea seis cosas y ese job solo tiene los secrets de
LinkedIn. Sin esto alerta todas las noches culpando a LinkedIn de que
falta la key de FRED, que es el modo de fallo que el comentario de ese
paso dice haber evitado. Lo fija un test que lee el YAML.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QBTyHnzK9HfeRi3LwmHFck"
```

---

## Task 8: La documentación que quedó mintiendo

Dos textos afirman cosas que dejan de ser verdad con este cambio. Uno de ellos se lo manda el pipeline al operador por Telegram.

**Files:**
- Modify: `scripts/check_credentials.py` (docstring del módulo)
- Modify: `src/macro_pipeline/orchestration/main.py:572-577`
- Modify: `README.md:99-100`

- [x] **Step 1: El docstring del módulo**

Reemplazar el docstring de `scripts/check_credentials.py` por:

```python
"""Verifica que las credenciales de los seis componentes sirvan de verdad.

El pipeline solo comprueba que las variables *existan*: una key presente pero
rotada o revocada es indistinguible de una buena hasta que la corrida pega
contra la API. Este script hace la pregunta que importa —¿esta credencial
sirve?— contra endpoints baratos. Un componente apagado con su `USE_*` o
`PUBLISH_*` en `false` no se verifica y no afecta el código de salida.

    python scripts/check_credentials.py

**No publica nada, pero ya no es del todo pasivo.** Para R2 escribe y borra un
objeto de prueba bajo `healthcheck/` en tu propio bucket: los tokens de R2 se
emiten de solo lectura o de lectura y escritura, y la API de S3 no tiene nada
como la cabecera `x-access-level` de X. Confirmar que el token puede guardar el
estado exige guardarlo una vez. Nada de esto toca `state/state.db`.

Sale con código 1 si algo encendido no está listo. No imprime credenciales.
"""
```

- [x] **Step 2: El texto que el pipeline manda por Telegram**

`src/macro_pipeline/orchestration/main.py` dice hoy que el script solo verifica X y LinkedIn "y para los demás sólo comprueba que la variable esté puesta: una key presente pero rotada no la detecta nadie todavía". Deja de ser cierto. Reemplazar ese bloque por:

```python
            self.telegram.send_alert(
                "⚠️ El cierre semanal arranca con componentes encendidos y sin "
                f"credenciales:\n\n{lineas}\n\n"
                "Se publica igual si lo aprobás.\n\n"
                "`python scripts/check_credentials.py` verifica las seis "
                "credenciales de verdad contra cada API, así que dice cuál "
                "está rotada y no sólo cuál falta."
            )
```

- [x] **Step 3: El README**

Reemplazar las dos líneas de `README.md:99-100`:

```markdown
# Verificar que las credenciales sirven de verdad (no publica; para R2
# escribe y borra un objeto de prueba en tu bucket)
python scripts/check_credentials.py
```

- [x] **Step 4: Verificar que no queda ninguna afirmación vieja**

```bash
grep -rn "no la detecta nadie todavía\|credenciales de publicacion sirven\|credenciales de publicación" --include=*.py --include=*.md . | grep -v revision_ | grep -v docs/superpowers
```

Expected: sin salida.

- [x] **Step 5: Correr la suite entera**

Run: `pytest tests/unit tests/integration -q`
Expected: PASS. Si algún test de `test_orchestration*` asertaba sobre el texto viejo de la alerta, actualizarlo: el texto cambió a propósito.

- [x] **Step 6: Commit**

```bash
git add -A
git commit -m "docs: los dos textos que quedaron mintiendo con el chequeo nuevo

Uno de ellos se lo manda el pipeline al operador por Telegram y decia
que una key rotada no la detecta nadie. Ya la detecta. Y el script deja
de ser pasivo: para R2 escribe y borra un objeto de prueba.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QBTyHnzK9HfeRi3LwmHFck"
```

---

## Task 9: Verificación en vivo contra las APIs reales

El spec pide dos confirmaciones que **no se pueden deducir de la doc**, y el repo ya tiene el precedente: el comentario de `_CODIGOS_DE_AUSENCIA` en `r2_client.py` dice "verificado en vivo el 2026-08-31 contra el bucket real" porque eso es lo que lo hace confiable.

Esta task necesita el `.env` real con credenciales cargadas. **Si no está disponible, no se marca como hecha ni se inventan los resultados**: se anota qué quedó sin verificar.

- [x] **Step 1: Correr el chequeo completo a mano**

Run: `python scripts/check_credentials.py`
Expected: los seis bloques, y un código de salida coherente con lo que tenga cargado el `.env`.

```bash
python scripts/check_credentials.py; echo "codigo de salida: $?"
```

- [x] **Step 2: Confirmar la forma del error de FRED**

Con una key inválida a propósito:

```bash
FRED_API_KEY=una-key-que-no-existe python -c "
import requests, json
r = requests.get('https://api.stlouisfed.org/fred/series',
                 params={'series_id':'UNRATE','file_type':'json',
                         'api_key':'una-key-que-no-existe'}, timeout=15)
print(r.status_code); print(r.text[:300])
"
```

Anotar en el comentario de `check_fred` el status code y el nombre del campo **realmente observados**. Si no son 400 y `error_message`, ajustar `_mensaje_de_error` y su test.

- [x] **Step 3: Confirmar la paginación de Anthropic**

```bash
python -c "
import os, requests
r = requests.get('https://api.anthropic.com/v1/models',
                 headers={'x-api-key': os.environ['ANTHROPIC_API_KEY'],
                          'anthropic-version': '2023-06-01'},
                 params={'limit': 1000}, timeout=15)
d = r.json()
print(r.status_code, len(d.get('data', [])), 'has_more=', d.get('has_more'))
print([m['id'] for m in d.get('data', [])])
"
```

Confirmar que `has_more` es `False` con `limit=1000` y que `claude-haiku-4-5` está en la lista. Si el parámetro de continuación no se llama `after_id`, corregir `check_anthropic` y el test de paginación.

- [x] **Step 4: Confirmar que la sonda de R2 dejó el bucket limpio**

```bash
python -c "
from macro_pipeline.storage.r2_client import R2Client
c = R2Client()
print(c.s3.list_objects_v2(Bucket=c.bucket, Prefix='healthcheck/').get('KeyCount'))
"
```

Expected: `0`. Si hay huérfanos, el borrado falló y el `[AVISO]` del Step 1 lo dijo: investigar el permiso del token antes de seguir.

- [x] **Step 5: Dejar constancia en el código**

Sumar al comentario de `check_r2` la línea de verificación en vivo con la fecha, con el mismo formato que `_CODIGOS_DE_AUSENCIA`:

```python
    # **Verificado en vivo el <fecha> contra el bucket real**: put, get y
    # delete bajo `healthcheck/` funcionan con el token actual, y el prefijo
    # quedó vacío después.
```

- [x] **Step 6: Commit**

```bash
git add -A
git commit -m "test: la verificacion en vivo de FRED, Anthropic y R2

Los codigos y los nombres de campo salen de la API real y no de la doc,
igual que _CODIGOS_DE_AUSENCIA. La sonda dejo el prefijo healthcheck/
vacio.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QBTyHnzK9HfeRi3LwmHFck"
```

---

## Task 10: Cierre

- [x] **Step 1: Los tres gates de CI, en orden**

```bash
ruff check src/ tests/ scripts/
ruff format --check src/ tests/ scripts/
mypy src/ scripts/
pytest tests/unit/ tests/integration/ -q
```

Expected: los cuatro limpios. La cuenta de tests sube respecto de los 318 de partida en unos 20.

- [x] **Step 2: Comprobar que la suite unitaria no sale a la red**

```bash
pytest tests/unit -q --timeout=10
```

Expected: PASS. Un timeout acá significa que algún test llegó a un chequeo real: le falta apagar el componente.

- [x] **Step 3: Marcar el plan como ejecutado y commitear**

Actualizar los checkboxes de este fichero y:

```bash
git add docs/superpowers/plans/2026-09-01-check-credentials-all-components.md
git commit -m "docs(plan): las diez tasks hechas

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QBTyHnzK9HfeRi3LwmHFck"
```

---

## Lo que este plan deja abierto a propósito

Está en el spec y se repite acá para que quien ejecute no lo tome por olvido:

- **En CI no se verifica nada de esto.** El paso del nightly apaga los cuatro. R2 sigue sin cobertura automática, que es justo lo que lo hacía el peor de los ocho. Candidato al siguiente spec.
- **Telegram queda sin chequeo**, y es el que apaga el HITL de ADR-004 si su token muere.
- **`R2Client.delete_object` existe y producción no lo usa.** Acotado por el prefijo y por no llamarse desde ningún camino real, pero el método está ahí.
- **Un objeto huérfano por cada borrado fallido**, con key aleatoria, así que se acumulan.

---

## Lo que la ejecución cambió respecto del plan

Cinco correcciones, todas por evidencia y no por gusto:

1. **Task 7 se adelantó**, de después de la 8 a inmediatamente después de la 3.
   El plan la dejaba para el final y eso rompía el nightly durante cuatro
   tasks: reproducido en un directorio limpio sin `.env`, el script salía con
   código 1 por una `FRED_API_KEY` que ese job nunca tuvo, y el paso habría
   mandado la alerta culpando a LinkedIn. El mismo modo de fallo que su propio
   comentario dice haber evitado.

2. **El listado de Anthropic devuelve el snapshot fechado, no el alias.**
   `/v1/models` trae `claude-haiku-4-5-20251001`; el pipeline usa
   `claude-haiku-4-5`. Con la igualdad a secas que pedía el plan, la primera
   corrida real avisó de un retiro inexistente — el aviso falso que este
   trabajo existe para no dar. Se agregó `_esta_listado`, que acepta el alias o
   el alias con sufijo de ocho dígitos y nada más. Verificado además en vivo:
   `after_id` es el parámetro correcto y `limit=1000` es el máximo (1001 da
   400).

3. **El ancho del resumen pasó de `:<9` a `:<15`.** `Alpha Vantage:` mide 14 y
   dejaba la columna dentada. Las tres aserciones que fijaban el espaciado
   viejo se recalcularon ejecutando el código, no contando espacios.

4. **`Task 2` reutilizó el doble que ya existía.** `tests/unit/test_r2_client.py`
   ya tenía `_FakeS3`; el plan traía uno paralelo. Se extendió el existente con
   `delete_error`/`delete_calls`.

5. **El test del switch ilegible usa `USE_FRED` y no `USE_R2`.** R2 no entraba a
   la tabla hasta la Task 6, así que como estaba escrito habría fallado por la
   razón equivocada.

Verificaciones por mutación, todas reproducidas: quitar `BotoCoreError` de
`delete_object` mata un test; el rate limit de AV en `NO_LISTO` mata exactamente
uno; invertir put/get mata el test del orden y deja vivos los dos que deben
sobrevivir. La sonda de R2 se corrió 200 veces contra un espía: 200 claves
distintas, ninguna fuera de `healthcheck/`, ninguna cerca de `state/`. El bucket
real quedó sin huérfanos.
