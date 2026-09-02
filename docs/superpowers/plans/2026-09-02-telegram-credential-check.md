# Chequeo de credenciales de Telegram — plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** que `scripts/check_credentials.py` verifique Telegram —el último de
los ocho componentes con credenciales sin verificación en ninguna parte— con
una sonda de solo lectura que caza el token revocado, el webhook que rompe el
long polling y el `TELEGRAM_ALLOWED_USER_ID` equivocado.

**Architecture:** un séptimo `check_telegram()` en el script, gobernado por
`USE_TELEGRAM` como los otros seis. Construye un `TelegramBot()` para heredar
`base_url` y la regla exacta de `allowed_user_id`, y hace tres GET
(`getMe`, `getWebhookInfo`, `getChat`) con `requests` desde el script, a través
de un helper `_telegram_get` que comparte el manejo de errores. El cableado en
`main()` y el blindaje del nightly van juntos en la última task de código.

**Tech Stack:** Python 3.12, `requests`, `pytest` + `monkeypatch`, GitHub
Actions.

**Spec:** `docs/superpowers/specs/2026-09-02-telegram-credential-check-design.md`

---

## Ficheros

- **Modificar:** `scripts/check_credentials.py` — el chequeo nuevo, el helper,
  `TELEGRAM_VARS`, la extensión de `_mensaje_de_error`, la fila en `main()` y
  los cuatro sitios que dicen «seis».
- **Modificar:** `tests/unit/test_check_credentials.py` — ocho tests nuevos, el
  helper de respuestas falsas, y el rename de `_apagar_los_cuatro`.
- **Modificar:** `.github/workflows/contract-tests.yml` — `USE_TELEGRAM: "false"`
  en el paso `Verificar la credencial de LinkedIn` y su comentario.

Nada en `src/`: `TelegramBot` se usa tal como está. No se le agregan métodos.

## Orden y por qué

Las tasks 2 a 6 construyen `check_telegram()` sin cablearlo en `main()`. Hasta
la task 7 el script sigue chequeando seis componentes, así que cada commit deja
el repo verde y nadie corre el chequeo nuevo por accidente. La task 7 hace las
tres cosas que **tienen que ser atómicas**: la fila en `main()`, el blindaje de
la suite (`_apagar_los_cinco`) y el blindaje del nightly.

---

### Task 1: `_mensaje_de_error` aprende a leer `description`

Telegram pone el motivo en `description` (`{"ok": false, "error_code": 400,
"description": "Bad Request: chat not found"}`), igual que FRED lo pone en
`error_message`. Sin esto, un `getChat` a un chat inexistente se reporta como
«HTTP 400» a secas.

**Files:**
- Modify: `scripts/check_credentials.py:196-208` (`_mensaje_de_error`)
- Test: `tests/unit/test_check_credentials.py` (al final del fichero)

- [ ] **Step 1: Write the failing test**

Al final de `tests/unit/test_check_credentials.py`:

```python
def test_el_mensaje_de_error_lee_la_clave_de_telegram(check_credentials):
    """Telegram explica el fallo en `description`, como FRED en `error_message`.

    Sin esta rama, un chat_id equivocado se reporta como "HTTP 400" a secas y
    el script deja de hacer justo lo que existe para hacer: decirle a un humano
    que tiene mal configurado.
    """

    class Respuesta:
        status_code = 400
        text = '{"ok":false,"error_code":400,"description":"Bad Request: chat not found"}'

        @staticmethod
        def json():
            return {
                "ok": False,
                "error_code": 400,
                "description": "Bad Request: chat not found",
            }

    assert check_credentials._mensaje_de_error(Respuesta()) == (
        "Bad Request: chat not found"
    )
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_check_credentials.py::test_el_mensaje_de_error_lee_la_clave_de_telegram -v
```

Esperado: FAIL. La función devuelve el `text` crudo (el JSON entero), no la
`description`, así que la igualdad no se cumple.

- [ ] **Step 3: Write minimal implementation**

En `scripts/check_credentials.py`, reemplazar el cuerpo de `_mensaje_de_error`:

```python
def _mensaje_de_error(response: requests.Response) -> str:
    """El texto que la API da para explicarse, o el cuerpo crudo.

    FRED pone el motivo real en `error_message` y devuelve 400, no 401: sin
    esto el diagnostico seria "HTTP 400" a secas. Telegram hace lo mismo en
    `description`, asi que las dos claves se miran acá y no se duplica la
    funcion.
    """
    try:
        cuerpo = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(cuerpo, dict):
        for clave in ("error_message", "description"):
            if clave in cuerpo:
                return str(cuerpo[clave])
    return response.text[:200]
```

- [ ] **Step 4: Run the tests**

```
pytest tests/unit/test_check_credentials.py -v
```

Esperado: PASS, incluido el test nuevo y los que ya cubrían `error_message` de
FRED.

- [ ] **Step 5: Commit**

```bash
git add scripts/check_credentials.py tests/unit/test_check_credentials.py
git commit -m "feat(scripts): _mensaje_de_error lee tambien la clave de Telegram"
```

---

### Task 2: `check_telegram` — presencia de variables y `getMe`

**Files:**
- Modify: `scripts/check_credentials.py` (imports, `TELEGRAM_VARS`, `_telegram_get`, `check_telegram`)
- Test: `tests/unit/test_check_credentials.py`

- [ ] **Step 1: Write the failing tests**

Al final del fichero de tests, primero el helper de respuestas falsas y después
los dos tests:

```python
def _telegram_responde(check_credentials, monkeypatch, respuestas):
    """Falsea `requests.get` despachando por el ultimo segmento de la URL.

    Los tres GET del chequeo van al mismo host y solo se distinguen por el
    metodo, asi que un fake que devuelva siempre lo mismo haria pasar tests que
    no prueban nada del orden. `respuestas` es {metodo: (status, cuerpo)}, y
    una llamada a un metodo que el test no declaro es un fallo del test.
    """

    class Respuesta:
        def __init__(self, status_code, cuerpo):
            self.status_code = status_code
            self._cuerpo = cuerpo
            self.text = str(cuerpo)

        def json(self):
            return self._cuerpo

    def _get(url, *a, **k):
        metodo = url.rsplit("/", 1)[-1]
        assert metodo in respuestas, f"llamada inesperada a {metodo}"
        status, cuerpo = respuestas[metodo]
        return Respuesta(status, cuerpo)

    monkeypatch.setattr(check_credentials.requests, "get", _get)


def _credenciales_de_telegram(monkeypatch, allowed="4242"):
    """Las tres variables, con valores de juguete.

    Explicitas en cada test y no heredadas del `.env` real: la fixture es de
    modulo y deja las credenciales de verdad en el entorno, asi que un test que
    no las pise pasaria o fallaria segun la maquina.
    """
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:token-de-juguete")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "4242")
    if allowed is None:
        monkeypatch.delenv("TELEGRAM_ALLOWED_USER_ID", raising=False)
    else:
        monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", allowed)


def test_telegram_sin_token_no_sale_a_la_red(check_credentials, monkeypatch, capsys):
    """La presencia se mira antes de contactar a nadie, como en los otros seis."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "4242")

    def _no_deberia_llamar(*a, **k):
        raise AssertionError("no tenia que salir a la red sin token")

    monkeypatch.setattr(check_credentials.requests, "get", _no_deberia_llamar)

    assert check_credentials.check_telegram() == check_credentials.NO_LISTO
    assert "TELEGRAM_BOT_TOKEN" in capsys.readouterr().out


def test_un_token_de_telegram_revocado_falla(check_credentials, monkeypatch, capsys):
    """Un token regenerado en BotFather deja al viejo autenticando con 401.

    Es el caso que apaga el HITL de ADR-004 entero: sin token no hay aprobacion
    y tampoco hay canal para avisar de que no hay canal.
    """
    _credenciales_de_telegram(monkeypatch)
    _telegram_responde(
        check_credentials,
        monkeypatch,
        {"getMe": (401, {"ok": False, "description": "Unauthorized"})},
    )

    assert check_credentials.check_telegram() == check_credentials.NO_LISTO
    salida = capsys.readouterr().out
    assert "401" in salida
    assert "BotFather" in salida
```

- [ ] **Step 2: Run the tests to verify they fail**

```
pytest tests/unit/test_check_credentials.py -k telegram -v
```

Esperado: FAIL con `AttributeError: module 'check_credentials' has no attribute
'check_telegram'`.

- [ ] **Step 3: Write the implementation**

En `scripts/check_credentials.py`:

1. Agregar el import del cliente junto al de R2 (línea ~41):

```python
from macro_pipeline.storage.r2_client import R2Client, R2ClientError
from macro_pipeline.telegram.bot import TelegramBot
```

2. Agregar las variables junto a `R2_VARS` (línea ~65):

```python
# Las dos que exige `TelegramBot.__init__`. `TELEGRAM_ALLOWED_USER_ID` no
# entra: su ausencia avisa, no falla, porque el cierre se publica igual.
TELEGRAM_VARS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
```

3. Agregar el helper y el chequeo **después de `check_r2()`** y antes de
   `main()`:

```python
def _telegram_get(
    bot: TelegramBot, metodo: str, params: dict[str, str] | None = None
) -> dict[str, object] | None:
    """El `result` de un metodo de la API, o `None` dejando el motivo impreso.

    Los tres GET comparten la forma de la respuesta y la del error, asi que
    comparten el manejo. El 401 se nombra aparte porque es el unico que no se
    arregla en el `.env`: hay que volver a BotFather.
    """
    try:
        response = requests.get(f"{bot.base_url}/{metodo}", params=params, timeout=15)
    except requests.RequestException as e:
        print(f"{FAIL} No se pudo contactar la API de Telegram: {e}")
        return None

    if response.status_code == 401:
        print(f"{FAIL} 401 en {metodo}: el TELEGRAM_BOT_TOKEN no autentica.")
        print("       Un token revocado o regenerado en BotFather da esto: el")
        print("       valor viejo deja de servir apenas se emite el nuevo.")
        return None
    if response.status_code != 200:
        print(f"{FAIL} {metodo}: HTTP {response.status_code}: ", end="")
        print(_mensaje_de_error(response))
        return None

    try:
        cuerpo = response.json()
    except ValueError:
        print(f"{FAIL} {metodo}: Telegram no devolvió JSON: {response.text[:200]}")
        return None

    resultado = cuerpo.get("result")
    if not isinstance(resultado, dict):
        print(f"{FAIL} {metodo}: la respuesta no trae un `result` utilizable.")
        return None
    return resultado


def check_telegram() -> str:
    """Tres GET de solo lectura: el token, el modo de uso y el chat.

    Solo lectura y no un mensaje de prueba porque acá sí alcanza. R2 tuvo que
    escribir porque la API de S3 no ofrece forma de confirmar el permiso sin
    ejercerlo; Telegram sí la ofrece, y mandar un mensaje en cada corrida
    rompería la promesa de pasividad del script por nada.
    """
    if _check_present(TELEGRAM_VARS):
        return NO_LISTO

    try:
        bot = TelegramBot()
    except ValueError as e:
        print(f"{FAIL} {e}")
        return NO_LISTO

    # getMe va primero por el mismo motivo por el que el put va antes que el
    # get en R2: sin él, un 400 de getChat no distingue "chat mal configurado"
    # de "token revocado", y el diagnostico sale al reves.
    identidad = _telegram_get(bot, "getMe")
    if identidad is None:
        return NO_LISTO
    print(f"{OK} El token autentica como @{identidad.get('username')}.")

    return LISTO
```

- [ ] **Step 4: Run the tests**

```
pytest tests/unit/test_check_credentials.py -k telegram -v
```

Esperado: PASS los dos.

- [ ] **Step 5: Commit**

```bash
git add scripts/check_credentials.py tests/unit/test_check_credentials.py
git commit -m "feat(scripts): el chequeo de Telegram autentica el token con getMe"
```

---

### Task 3: el webhook que rompe el long polling

**Files:**
- Modify: `scripts/check_credentials.py` (`check_telegram`)
- Test: `tests/unit/test_check_credentials.py`

- [ ] **Step 1: Write the failing test**

```python
def test_un_webhook_registrado_falla_aunque_el_token_sirva(
    check_credentials, monkeypatch, capsys
):
    """La credencial es valida y el HITL esta muerto igual.

    `wait_for_approval` hace long polling con getUpdates, y Telegram le
    contesta 409 mientras exista un webhook: el borrador sale, el boton no
    llega nunca y la run se cuelga hasta el timeout. Es el mismo modo de fallo
    que `x-access-level: read` — la credencial autentica, el modo de uso esta
    roto—, y es la razon por la que este chequeo no se queda en getMe.
    """
    _credenciales_de_telegram(monkeypatch)
    _telegram_responde(
        check_credentials,
        monkeypatch,
        {
            "getMe": (200, {"ok": True, "result": {"username": "MacroPipelineBot"}}),
            "getWebhookInfo": (
                200,
                {"ok": True, "result": {"url": "https://ejemplo.test/hook"}},
            ),
        },
    )

    assert check_credentials.check_telegram() == check_credentials.NO_LISTO
    salida = capsys.readouterr().out
    assert "409" in salida
    assert "deleteWebhook" in salida
```

- [ ] **Step 2: Run it to verify it fails**

```
pytest tests/unit/test_check_credentials.py::test_un_webhook_registrado_falla_aunque_el_token_sirva -v
```

Esperado: FAIL con `AssertionError: llamada inesperada a getWebhookInfo`… no:
el chequeo todavía no llama a `getWebhookInfo`, así que devuelve `LISTO` y
falla en `assert ... == NO_LISTO`.

- [ ] **Step 3: Write the implementation**

En `check_telegram()`, entre el bloque de `getMe` y el `return LISTO`:

```python
    webhook = _telegram_get(bot, "getWebhookInfo")
    if webhook is None:
        return NO_LISTO
    if webhook.get("url"):
        print(f"{FAIL} Hay un webhook registrado y eso mata el HITL:")
        print("       `wait_for_approval` hace long polling con getUpdates, y")
        print("       Telegram le contesta 409 mientras el webhook exista. El")
        print("       borrador sale, el boton no llega nunca y la run se cuelga")
        print("       hasta el timeout. Se borra con deleteWebhook.")
        return NO_LISTO
    print(f"{OK} Sin webhook: el long polling de getUpdates tiene via libre.")
```

- [ ] **Step 4: Run the tests**

```
pytest tests/unit/test_check_credentials.py -k telegram -v
```

Esperado: PASS los tres.

- [ ] **Step 5: Commit**

```bash
git add scripts/check_credentials.py tests/unit/test_check_credentials.py
git commit -m "feat(scripts): un webhook registrado pone el chequeo en rojo"
```

---

### Task 4: el chat inalcanzable

**Files:**
- Modify: `scripts/check_credentials.py` (`check_telegram`)
- Test: `tests/unit/test_check_credentials.py`

- [ ] **Step 1: Write the failing test**

```python
def test_un_chat_id_que_no_existe_falla_con_el_motivo_de_telegram(
    check_credentials, monkeypatch, capsys
):
    """400 y no 401: el token sirve, el destino no.

    Pasa con un TELEGRAM_CHAT_ID mal copiado y tambien cuando el operador nunca
    le hablo al bot, que es la trampa de estreno: el bot no puede iniciar una
    conversacion. La `description` es lo que distingue los dos casos, y por eso
    la task 1 existe.
    """
    _credenciales_de_telegram(monkeypatch)
    _telegram_responde(
        check_credentials,
        monkeypatch,
        {
            "getMe": (200, {"ok": True, "result": {"username": "MacroPipelineBot"}}),
            "getWebhookInfo": (200, {"ok": True, "result": {"url": ""}}),
            "getChat": (
                400,
                {
                    "ok": False,
                    "error_code": 400,
                    "description": "Bad Request: chat not found",
                },
            ),
        },
    )

    assert check_credentials.check_telegram() == check_credentials.NO_LISTO
    assert "chat not found" in capsys.readouterr().out
```

- [ ] **Step 2: Run it to verify it fails**

```
pytest tests/unit/test_check_credentials.py::test_un_chat_id_que_no_existe_falla_con_el_motivo_de_telegram -v
```

Esperado: FAIL. `check_telegram` todavía devuelve `LISTO` sin llamar a
`getChat`.

- [ ] **Step 3: Write the implementation**

En `check_telegram()`, antes del `return LISTO`:

```python
    chat = _telegram_get(bot, "getChat", {"chat_id": str(bot.chat_id)})
    if chat is None:
        print("       Un 400 acá suele ser el TELEGRAM_CHAT_ID mal copiado, o")
        print("       que el operador nunca inicio conversacion con el bot: un")
        print("       bot no puede escribir primero. Un 403 es el bot bloqueado")
        print("       o expulsado del grupo.")
        return NO_LISTO

    tipo = chat.get("type")
    print(f"{OK} El chat existe y el bot lo alcanza (type={tipo}).")
```

- [ ] **Step 4: Run the tests**

```
pytest tests/unit/test_check_credentials.py -k telegram -v
```

Esperado: PASS los cuatro.

- [ ] **Step 5: Commit**

```bash
git add scripts/check_credentials.py tests/unit/test_check_credentials.py
git commit -m "feat(scripts): el chequeo confirma que el chat existe y es alcanzable"
```

---

### Task 5: el dueño del chat privado

Los dos tests van juntos porque anclan las dos mitades del mismo `if`: sin el
camino feliz, invertir la comparación deja la suite verde.

**Files:**
- Modify: `scripts/check_credentials.py` (`check_telegram`)
- Test: `tests/unit/test_check_credentials.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_el_camino_feliz_de_telegram_en_un_chat_privado(
    check_credentials, monkeypatch, capsys
):
    """Token, sin webhook, chat alcanzable y el operador correcto."""
    _credenciales_de_telegram(monkeypatch, allowed="4242")
    _telegram_responde(
        check_credentials,
        monkeypatch,
        {
            "getMe": (200, {"ok": True, "result": {"username": "MacroPipelineBot"}}),
            "getWebhookInfo": (200, {"ok": True, "result": {"url": ""}}),
            "getChat": (200, {"ok": True, "result": {"id": 4242, "type": "private"}}),
        },
    )

    assert check_credentials.check_telegram() == check_credentials.LISTO
    assert "coincide" in capsys.readouterr().out


def test_un_allowed_user_id_de_otro_no_deja_aprobar_a_nadie(
    check_credentials, monkeypatch, capsys
):
    """El HITL muerto mas silencioso de los tres.

    En un chat privado el id del chat ES el del operador. Si no coinciden,
    `wait_for_approval` descarta el callback sin decir nada, la run se cuelga
    hasta el timeout y no publica. El texto tiene que nombrar las DOS variables:
    con el aviso generico, quien lo lee no sabe cual de las dos corregir.
    """
    _credenciales_de_telegram(monkeypatch, allowed="9999")
    _telegram_responde(
        check_credentials,
        monkeypatch,
        {
            "getMe": (200, {"ok": True, "result": {"username": "MacroPipelineBot"}}),
            "getWebhookInfo": (200, {"ok": True, "result": {"url": ""}}),
            "getChat": (200, {"ok": True, "result": {"id": 4242, "type": "private"}}),
        },
    )

    assert check_credentials.check_telegram() == check_credentials.NO_LISTO
    salida = capsys.readouterr().out
    assert "TELEGRAM_ALLOWED_USER_ID" in salida
    assert "TELEGRAM_CHAT_ID" in salida
```

- [ ] **Step 2: Run them to verify they fail**

```
pytest tests/unit/test_check_credentials.py -k "camino_feliz_de_telegram or allowed_user_id_de_otro" -v
```

Esperado: FAIL los dos. El primero por el `assert "coincide"`, el segundo
porque `check_telegram` devuelve `LISTO`.

- [ ] **Step 3: Write the implementation**

En `check_telegram()`, después del `print` del `type` y antes del `return
LISTO`:

```python
    if tipo == "private" and bot.allowed_user_id is not None:
        if bot.allowed_user_id != chat.get("id"):
            print(f"{FAIL} TELEGRAM_ALLOWED_USER_ID no es el dueño de este chat.")
            print("       En un chat privado el id del chat ES el del operador,")
            print("       asi que `wait_for_approval` va a descartar el boton en")
            print("       silencio: la run se cuelga hasta el timeout y no")
            print("       publica. Una de las dos variables esta mal:")
            print(f"       TELEGRAM_CHAT_ID={bot.chat_id}")
            print(f"       TELEGRAM_ALLOWED_USER_ID={bot.allowed_user_id}")
            return NO_LISTO
        print(f"{OK} TELEGRAM_ALLOWED_USER_ID coincide con el dueño del chat.")
```

- [ ] **Step 4: Run the tests**

```
pytest tests/unit/test_check_credentials.py -k telegram -v
```

Esperado: PASS los seis.

- [ ] **Step 5: Commit**

```bash
git add scripts/check_credentials.py tests/unit/test_check_credentials.py
git commit -m "feat(scripts): el id del operador se compara con el dueño del chat"
```

---

### Task 6: los dos casos que NO son fallo

Son los que impiden que el chequeo se ponga rojo por configuraciones legítimas,
que es como un chequeo se termina desactivando.

**Files:**
- Modify: `scripts/check_credentials.py` (`check_telegram`)
- Test: `tests/unit/test_check_credentials.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_en_un_grupo_no_se_compara_el_id_pero_se_dice(
    check_credentials, monkeypatch, capsys
):
    """En un grupo los dos ids son distintos por diseño (el del chat es negativo).

    Comparar ahi pondria rojo a una configuracion legitima. Y el aviso importa
    tanto como no fallar: un silencio se lee como "comparado y OK", asi que hay
    que decir que quien aprueba quedo sin verificar.
    """
    _credenciales_de_telegram(monkeypatch, allowed="4242")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100987654321")
    _telegram_responde(
        check_credentials,
        monkeypatch,
        {
            "getMe": (200, {"ok": True, "result": {"username": "MacroPipelineBot"}}),
            "getWebhookInfo": (200, {"ok": True, "result": {"url": ""}}),
            "getChat": (
                200,
                {"ok": True, "result": {"id": -100987654321, "type": "supergroup"}},
            ),
        },
    )

    assert check_credentials.check_telegram() == check_credentials.LISTO
    assert "sin verificar quien aprueba" in capsys.readouterr().out


def test_sin_allowed_user_id_avisa_pero_no_pone_rojo(
    check_credentials, monkeypatch, capsys
):
    """Publica igual: el HITL funciona, solo que sin restringir quien aprueba.

    El codigo de salida significa "esta credencial no sirve" y esta sirve. La
    ausencia ya la reporta `report_env_drift`, que esta escrito a proposito
    para no poner el script en rojo por una decision pendiente; fallar aca
    tendria las dos politicas a la vez.

    `isdigit()` trata igual a la ausente y a la ilegible ('@simon'), asi que
    este test cubre las dos.
    """
    _credenciales_de_telegram(monkeypatch, allowed=None)
    _telegram_responde(
        check_credentials,
        monkeypatch,
        {
            "getMe": (200, {"ok": True, "result": {"username": "MacroPipelineBot"}}),
            "getWebhookInfo": (200, {"ok": True, "result": {"url": ""}}),
            "getChat": (200, {"ok": True, "result": {"id": 4242, "type": "private"}}),
        },
    )

    assert check_credentials.check_telegram() == check_credentials.LISTO
    assert "cualquiera en el chat puede aprobar" in capsys.readouterr().out
```

- [ ] **Step 2: Run them to verify they fail**

```
pytest tests/unit/test_check_credentials.py -k "en_un_grupo or sin_allowed_user_id" -v
```

Esperado: FAIL los dos por los `assert` sobre el texto — hoy no se imprime
ninguno de los dos avisos.

- [ ] **Step 3: Write the implementation**

Reemplazar el bloque `if tipo == "private" and ...` de la task 5 por la cadena
completa. **Ojo:** el bloque de abajo incluye el `return LISTO` final que ya
existe desde la task 2 — se reemplaza también, no se duplica.

```python
    if tipo != "private":
        print(f"{WARN} No es un chat privado, asi que el id del chat y el del")
        print("       operador son distintos por diseño y no se pueden comparar:")
        print("       queda sin verificar quien aprueba.")
    elif bot.allowed_user_id is None:
        print(f"{WARN} Sin TELEGRAM_ALLOWED_USER_ID legible (ausente o no")
        print("       numerica): cualquiera en el chat puede aprobar una")
        print("       publicacion, que es lo que el HITL de ADR-004 existe para")
        print("       impedir. Publica igual, por eso avisa y no falla.")
    elif bot.allowed_user_id != chat.get("id"):
        print(f"{FAIL} TELEGRAM_ALLOWED_USER_ID no es el dueño de este chat.")
        print("       En un chat privado el id del chat ES el del operador,")
        print("       asi que `wait_for_approval` va a descartar el boton en")
        print("       silencio: la run se cuelga hasta el timeout y no")
        print("       publica. Una de las dos variables esta mal:")
        print(f"       TELEGRAM_CHAT_ID={bot.chat_id}")
        print(f"       TELEGRAM_ALLOWED_USER_ID={bot.allowed_user_id}")
        return NO_LISTO
    else:
        print(f"{OK} TELEGRAM_ALLOWED_USER_ID coincide con el dueño del chat.")

    return LISTO
```

- [ ] **Step 4: Run the tests**

```
pytest tests/unit/test_check_credentials.py -k telegram -v
```

Esperado: PASS los ocho.

- [ ] **Step 5: Commit**

```bash
git add scripts/check_credentials.py tests/unit/test_check_credentials.py
git commit -m "feat(scripts): el grupo y el id ausente avisan en vez de fallar"
```

---

### Task 7: cablear en `main()`, con los dos blindajes en el mismo commit

**Esta task es atómica a propósito.** En cuanto Telegram entra en la tabla de
`main()`, dos cosas que hoy están verdes se rompen o se vuelven mentirosas:

- Los seis tests que llaman a `main()` empiezan a **contactar la API real de
  Telegram** (`component_enabled` trata la variable ausente como encendida).
- El paso `Verificar la credencial de LinkedIn` del nightly **sí tiene**
  `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` —los usa para su propia alerta—,
  así que no fallaría por falta de credenciales: correría de verdad contra el
  bot y el chat privado del operador todas las noches, y el día que el token
  muera la alerta culparía a LinkedIn (y no se podría entregar, porque viaja
  por ese mismo token).

**Files:**
- Modify: `scripts/check_credentials.py:1`, `:184`, `:216`, `:598` (los «seis»), imports y `main()`
- Modify: `tests/unit/test_check_credentials.py:424-432` (`_apagar_los_cuatro`) y `:658`
- Modify: `.github/workflows/contract-tests.yml:171-181`

- [ ] **Step 1: Write the failing test**

```python
def test_telegram_apagado_no_se_chequea_ni_pone_rojo(
    check_credentials, monkeypatch, capsys
):
    """La septima fila de la tabla, y su gate.

    Confirma dos cosas de una: que Telegram aparece en el resumen (o sea que
    esta cableado en `main()`) y que `USE_TELEGRAM=false` lo saltea sin
    contactar a nadie, como los otros seis.
    """
    monkeypatch.setenv("PUBLISH_X", "false")
    monkeypatch.setenv("PUBLISH_LINKEDIN", "false")
    _apagar_los_cinco(monkeypatch)
    monkeypatch.setattr(check_credentials, "report_env_drift", lambda *a, **k: None)

    def _no_deberia_llamar(*a, **k):
        raise AssertionError("un componente apagado no se contacta")

    monkeypatch.setattr(check_credentials.requests, "get", _no_deberia_llamar)

    assert check_credentials.main() == 0
    assert "Telegram:       apagado" in capsys.readouterr().out
```

- [ ] **Step 2: Run it to verify it fails**

```
pytest tests/unit/test_check_credentials.py::test_telegram_apagado_no_se_chequea_ni_pone_rojo -v
```

Esperado: FAIL con `NameError: name '_apagar_los_cinco' is not defined`.

- [ ] **Step 3: Renombrar el helper de la suite y arreglar el test que no lo usa**

En `tests/unit/test_check_credentials.py`, reemplazar `_apagar_los_cuatro`:

```python
def _apagar_los_cinco(monkeypatch):
    """Los cinco componentes que no son las dos redes, apagados.

    Sin esto, un test que llame a `main()` contacta FRED, Alpha Vantage,
    Anthropic, R2 y Telegram de verdad: `component_enabled` trata la variable
    ausente como encendido a proposito, y la suite unitaria no sale a la red.
    """
    for var in ("USE_FRED", "USE_AV", "USE_ANTHROPIC", "USE_R2", "USE_TELEGRAM"):
        monkeypatch.setenv(var, "false")
```

Actualizar las cinco llamadas (`sed` sirve: `_apagar_los_cuatro` →
`_apagar_los_cinco`), y en
`test_el_rate_limit_de_av_no_cambia_el_codigo_de_salida` —que setea las
variables a mano y **no** usa el helper— agregar la línea que falta junto a las
otras:

```python
    monkeypatch.setenv("USE_R2", "false")
    monkeypatch.setenv("USE_TELEGRAM", "false")
```

- [ ] **Step 4: Cablear la fila en `main()`**

En `scripts/check_credentials.py`, agregar `USE_TELEGRAM_VAR` al import de
`macro_pipeline.components` (respetando el orden alfabético que ya tiene) y la
séptima fila al final de la lista `chequeos`:

```python
        ("R2", USE_R2_VAR, check_r2),
        ("Telegram", USE_TELEGRAM_VAR, check_telegram),
    ]
```

- [ ] **Step 5: Blindar el nightly, en este mismo commit**

En `.github/workflows/contract-tests.yml`, en el bloque `env:` del paso
`Verificar la credencial de LinkedIn`, junto a los otros cuatro:

```yaml
          USE_FRED: "false"
          USE_AV: "false"
          USE_ANTHROPIC: "false"
          USE_R2: "false"
          # Telegram se apaga por un motivo distinto al de los otros cuatro:
          # este paso SI tiene TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID, porque
          # los usa para mandar su propia alerta. Sin esta linea el chequeo
          # correria de verdad, todas las noches, contra el bot y el chat
          # privado del operador: el color de un paso que habla de LinkedIn
          # pasaria a depender de una credencial de Telegram, y el dia que ese
          # token muera la alerta culparia a LinkedIn — sin poder entregarse,
          # porque viaja por ese mismo token.
          USE_TELEGRAM: "false"
```

Y actualizar el comentario de arriba del paso (línea ~175), que dice «los seis
componentes»:

```yaml
          # Desde que el script chequea los siete componentes, dejarlos
```

- [ ] **Step 6: Actualizar los cuatro «seis» del script**

- `scripts/check_credentials.py:1` — `"""Verifica que las credenciales de los siete componentes sirvan de verdad.`
- `:184` — `"""La linea de seccion, del mismo ancho para los siete."""`
- `:216` — `El encabezado de seccion lo imprime `main()` para los siete componentes:`
- `:598` — `# salia dentada. Este es el titulo mas largo de los siete mas un espacio.`

El ancho `:<15` **no** cambia: `Telegram:` mide 9 y el más largo sigue siendo
`Alpha Vantage:` con 14.

- [ ] **Step 7: Run the full suite**

```
pytest tests/unit tests/integration -q
```

Esperado: PASS todo, 352 tests (342 + los 10 nuevos: uno en la task 1, dos en
la 2, uno en la 3, uno en la 4, dos en la 5, dos en la 6 y uno acá). Ningún
test sale a la red.

- [ ] **Step 8: Lint y tipos — los tres gates que corre CI, y sólo ésos**

```
ruff check src/ tests/ scripts/
ruff format --check src/ tests/ scripts/
mypy src/ scripts/
```

Esperado: sin errores. `mypy` corre con `strict = true`, así que la anotación
de `_telegram_get` tiene que ser `dict[str, object] | None`: un `dict` pelado
es error de `disallow_any_generics`.

**No usar `pre-commit run --all-files` como gate acá.** Su hook de `mypy` no
lleva filtro de ficheros, así que también tipa `tests/`, y el fichero de tests
tenía **80 errores de `no-untyped-def`/`no-untyped-call` ya en `8831487`** —
todo el fichero está escrito sin anotar, a propósito—. `ci.yml:36` corre
`mypy src/ scripts/` y nada más. Verificado el 2026-09-02; era un error de este
plan, no del código.

- [ ] **Step 9: Commit**

```bash
git add scripts/check_credentials.py tests/unit/test_check_credentials.py .github/workflows/contract-tests.yml
git commit -m "feat(scripts): Telegram entra a la tabla, con los dos blindajes"
```

---

### Task 8: verificación en vivo contra la API real

Los ocho tests corren contra respuestas falsas: prueban la lógica, no el
contrato. Es exactamente lo que dejó invisible un modelo retirado durante meses
en este repo, y lo que ayer destapó que `/v1/models` devuelve el snapshot
fechado y que FRED contesta 400 en vez de 401.

**Files:** ninguno todavía; los hallazgos se anotan en el siguiente paso.

- [ ] **Step 1: Correr el script entero con las credenciales reales**

```
python scripts/check_credentials.py
```

Esperado: los siete componentes en el resumen, `Telegram: listo`, y el bloque
de Telegram con `@` del bot, «Sin webhook», el `type=private` y la línea de
que el id coincide.

- [ ] **Step 2: Confirmar las tres formas de respuesta**

Verificar contra la salida real que:
- `getMe` devuelve `result.username` (y no `result.user.username`).
- `getWebhookInfo` devuelve `result.url` como **string vacío** cuando no hay
  webhook, no ausente ni `null`. Si viene ausente, `webhook.get("url")` sigue
  siendo falsy y el chequeo es correcto igual — anotarlo.
- `getChat` devuelve `result.id` como **int** y `result.type` como `"private"`.
  Si `id` viniera como string, la comparación con `allowed_user_id` (int)
  sería siempre distinta y el chequeo fallaría en verde para todos.

- [ ] **Step 3: Provocar un fallo real, el más barato**

Con el token intacto, correr una vez con un chat_id inventado:

```
TELEGRAM_CHAT_ID=1 python scripts/check_credentials.py
```

Esperado: `Telegram: NO listo`, código de salida 1, y la `description` de
Telegram en la salida («chat not found» o similar) — que es la task 1
demostrada de punta a punta.

- [ ] **Step 4: Anotar lo que la corrida real haya corregido**

Si algo de la forma de las respuestas no era como el plan asumía, corregir el
código **y** el spec en el mismo commit, con el hallazgo escrito. Si todo
coincidió, agregar una línea al docstring de `check_telegram` con la fecha de
la verificación, como hizo `check_r2`:

```python
    # **Verificado en vivo el 2026-09-02 contra el bot real**: getMe,
    # getWebhookInfo y getChat contestan la forma que este chequeo asume.
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "docs(scripts): la sonda de Telegram, verificada contra la API real"
```

---

## Verificación final

- [ ] `pytest tests/unit tests/integration -q` en verde.
- [ ] `pre-commit run --all-files` sin errores.
- [ ] `python scripts/check_credentials.py` muestra siete componentes.
- [ ] Push y **esperar el CI sobre el HEAD exacto**: verde en local no es verde
      en Actions. `gh run list --limit 3`.
- [ ] Mirar el nightly siguiente (07:00 UTC): el paso de LinkedIn tiene que
      seguir en verde y **no** mencionar Telegram en su salida.
