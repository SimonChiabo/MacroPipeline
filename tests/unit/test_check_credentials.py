"""Chequeo de deriva entre `.env` y `.env.example`.

El 2026-08-24 aparecio `TELEGRAM_ALLOWED_USER_ID`: declarada en
`.env.example` y marcada como CRITICO desde el principio, implementada en
`TelegramBot.__init__`, y ausente del `.env` real probablemente desde mayo.
Sin ella, `wait_for_approval` acepta el boton de cualquiera en el chat y el
HITL de ADR-004 —lo unico entre el pipeline y publicar— queda apagado. Lo
unico que lo decia era un `logger.warning` al arrancar.

`scripts/check_credentials.py` no es un paquete, asi que se carga por ruta.
"""

import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def check_credentials():
    """El script, cargado por ruta y sin dejar el `.env` real en el entorno.

    Importarlo ejecuta `load_dotenv(ROOT / '.env')` a nivel de modulo. Sin
    restaurar `os.environ` eso filtra las credenciales reales al resto de la
    sesion de pytest: rompio `test_wait_for_approval_success`, que pasaba
    porque `TELEGRAM_ALLOWED_USER_ID` no estaba puesta.
    """
    previo = os.environ.copy()
    try:
        spec = importlib.util.spec_from_file_location(
            "check_credentials", ROOT / "scripts" / "check_credentials.py"
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        os.environ.clear()
        os.environ.update(previo)


def _escribir(tmp_path: Path, ejemplo: str, real: str) -> tuple[Path, Path]:
    p_ejemplo = tmp_path / ".env.example"
    p_real = tmp_path / ".env"
    p_ejemplo.write_text(ejemplo, encoding="utf-8")
    p_real.write_text(real, encoding="utf-8")
    return p_ejemplo, p_real


def test_flags_a_key_declared_in_example_but_missing_from_env(
    check_credentials, tmp_path
):
    """El caso real: la variable existe en el ejemplo y no en el `.env`."""
    ejemplo, real = _escribir(
        tmp_path,
        "# --- HITL ---\n"
        "TELEGRAM_BOT_TOKEN=your_token\n"
        "TELEGRAM_ALLOWED_USER_ID=your_id\n",
        "TELEGRAM_BOT_TOKEN=8703868612:AAH_real\n",
    )

    hallazgos = check_credentials.compare_env_files(ejemplo, real)

    assert ("TELEGRAM_ALLOWED_USER_ID", "ausente") in hallazgos
    assert not any(n == "TELEGRAM_BOT_TOKEN" for n, _ in hallazgos)


def test_flags_a_key_left_with_the_example_placeholder(check_credentials, tmp_path):
    """Copiar el ejemplo y no rellenarlo es tan malo como no tener la clave."""
    ejemplo, real = _escribir(
        tmp_path,
        "FRED_API_KEY=your_fred_api_key\n",
        "FRED_API_KEY=your_fred_api_key\n",
    )

    hallazgos = check_credentials.compare_env_files(ejemplo, real)

    assert ("FRED_API_KEY", "placeholder") in hallazgos


def test_flags_a_key_the_example_does_not_document(check_credentials, tmp_path):
    """La deriva inversa: el `.env` tiene algo que el ejemplo no documenta.

    Es como se genera el problema para el siguiente que clone el repo: copia
    `.env.example` y le falta una variable que nadie escribio ahi.
    """
    ejemplo, real = _escribir(
        tmp_path,
        "FRED_API_KEY=your_fred_api_key\n",
        "FRED_API_KEY=real\nSTATE_DB_PATH=/tmp/x.db\n",
    )

    hallazgos = check_credentials.compare_env_files(ejemplo, real)

    assert ("STATE_DB_PATH", "sin documentar") in hallazgos


def test_no_findings_when_both_files_agree(check_credentials, tmp_path):
    """Comentarios, lineas en blanco y valores con `=` dentro no son hallazgos."""
    ejemplo, real = _escribir(
        tmp_path,
        "# --- Seccion ---\n"
        "FRED_API_KEY=your_fred_api_key\n\n"
        "OTEL_HEADERS=Authorization=Bearer x\n",
        "# otro comentario\n"
        "FRED_API_KEY=abc123\n\n"
        "OTEL_HEADERS=Authorization=Bearer real\n",
    )

    assert check_credentials.compare_env_files(ejemplo, real) == []


def test_a_commented_declaration_in_the_example_counts_as_documented(
    check_credentials, tmp_path
):
    """`.env.example` documenta las opcionales comentandolas con un ejemplo.

    Asi esta `LINKEDIN_TOKEN_ISSUED`, que el propio script lee para avisar del
    vencimiento del token. Tratarla como 'sin documentar' seria ruido, y un
    chequeo ruidoso se ignora.
    """
    ejemplo, real = _escribir(
        tmp_path,
        "# Anotar fecha de emision aqui:\n# LINKEDIN_TOKEN_ISSUED=2026-05-15\n",
        "LINKEDIN_TOKEN_ISSUED=2026-08-21\n",
    )

    assert check_credentials.compare_env_files(ejemplo, real) == []


def test_a_commented_declaration_is_not_required_in_env(check_credentials, tmp_path):
    """Y comentada tampoco se exige: es opcional, ese es el punto."""
    ejemplo, real = _escribir(
        tmp_path,
        "# LINKEDIN_TOKEN_ISSUED=2026-05-15\nFRED_API_KEY=your_fred_api_key\n",
        "FRED_API_KEY=real\n",
    )

    assert check_credentials.compare_env_files(ejemplo, real) == []


def test_runs_against_the_real_repo_files(check_credentials):
    """Humo sobre los ficheros de verdad: se parsean y devuelven hallazgos.

    A proposito NO se exige cero deriva. Convertir esto en un gate de deriva
    cero es la misma trampa que ya se evito en el codigo de salida del
    script: la primera variable que se agregue al `.env` antes de
    documentarla rompe los tests locales, y asi es como alguien termina
    desactivando el chequeo.

    Lo que si se fija son las decisiones ya tomadas, una por variable.
    `STATE_DB_PATH` y `ALLOW_MOCK_DATA` se decidieron el 2026-08-24
    (ver `docs/superpowers/specs/`): la primera queda declarada comentada en
    el ejemplo, porque su default —`~/.macropipeline/state.db`— ya es
    absoluto y ajeno al CWD (ADR-007 pide que la variable sea configurable,
    que es cosa distinta: para eso alcanza con que exista); la
    segunda va explicita en el `.env`, porque decide si se puede publicar con
    datos sinteticos y eso no deberia depender de un default del codigo.
    """
    real = ROOT / ".env"
    if not real.exists():
        pytest.skip("sin .env local: nada que comparar")

    hallazgos = check_credentials.compare_env_files(ROOT / ".env.example", real)

    assert isinstance(hallazgos, list)
    motivos_validos = ("ausente", "placeholder", "sin documentar")
    assert all(motivo in motivos_validos for _, motivo in hallazgos)
    # La que motivo todo esto ya esta cargada y no puede volver a faltar.
    assert ("TELEGRAM_ALLOWED_USER_ID", "ausente") not in hallazgos
    # Decididas el 2026-08-24. Se mira solo el nombre y no el motivo: da
    # igual si reaparecen como 'ausente', 'placeholder' o 'sin documentar',
    # las tres significan que la decision se deshizo.
    nombres = [name for name, _ in hallazgos]
    assert "STATE_DB_PATH" not in nombres
    assert "ALLOW_MOCK_DATA" not in nombres


def test_the_example_keeps_the_two_decided_declarations(check_credentials):
    """El `.env.example` sigue declarando cada variable como se decidio.

    Existe por donde NO corre el test de arriba: se salta entero cuando no
    hay `.env`, y en CI no hay `.env`. O sea que la mitad de la decision que
    vive en un fichero commiteado —como esta declarada cada variable en el
    ejemplo— no la miraba nadie fuera de la maquina de Simon.

    Aca no se toca el `.env`: solo el ejemplo, que si esta en git. Esto no
    reabre lo de exigir cero deriva; fija dos declaraciones concretas, no una
    politica.

    Las dos mitades importan, y por motivos distintos. Descomentar
    `STATE_DB_PATH` la vuelve exigible y el chequeo empieza a pedir una ruta
    de maquina que nadie quiso poner. Comentar `ALLOW_MOCK_DATA` es peor y
    mas silencioso: deja de exigirse en el `.env`, y la bandera que bloquea
    publicar con datos sinteticos vuelve a depender del default del codigo
    sin que nada lo diga.
    """
    ejemplo = ROOT / ".env.example"

    declaradas = check_credentials._parse_env_file(ejemplo)
    comentadas = check_credentials._commented_names(ejemplo)

    assert "STATE_DB_PATH" in comentadas
    assert "STATE_DB_PATH" not in declaradas
    assert declaradas.get("ALLOW_MOCK_DATA") == "false"


def test_the_example_declares_both_publish_flags(check_credentials):
    """Explícitas y no heredadas del default, igual que `ALLOW_MOCK_DATA`.

    Deciden si se publica. Una bandera que decide eso no debería depender de un
    default del código: hay que poder leer el `.env` y saber qué va a pasar.
    """
    ejemplo = check_credentials._parse_env_file(ROOT / ".env.example")

    assert ejemplo.get("PUBLISH_X") == "true"
    assert ejemplo.get("PUBLISH_LINKEDIN") == "true"


def test_the_example_declares_the_six_component_switches_commented(check_credentials):
    """Comentados y no puestos, a diferencia de las dos banderas de publicación.

    Ausente significa encendido, así que no hay nada que copiar al `.env`:
    declararlos sin comentar obligaría a tenerlos y la deriva avisaría de una
    ausencia que es correcta. Entran en `documentadas` vía `_commented_names`,
    igual que `STATE_DB_PATH`.
    """
    ejemplo = ROOT / ".env.example"
    comentadas = check_credentials._commented_names(ejemplo)
    declaradas = check_credentials._parse_env_file(ejemplo)

    for var in (
        "USE_FMP",
        "USE_AV",
        "USE_FRED",
        "USE_ANTHROPIC",
        "USE_R2",
        "USE_TELEGRAM",
    ):
        assert var in comentadas, var
        assert var not in declaradas, var


def test_report_prints_every_finding_with_its_reason(
    check_credentials, tmp_path, capsys
):
    ejemplo, real = _escribir(
        tmp_path,
        "FALTANTE=your_valor\nPUESTA=your_valor\n",
        "PUESTA=your_valor\nEXTRA=algo\n",
    )

    check_credentials.report_env_drift(ejemplo, real)
    salida = capsys.readouterr().out

    assert "FALTANTE" in salida and "ausente" in salida
    assert "PUESTA" in salida and "placeholder" in salida
    assert "EXTRA" in salida and "sin documentar" in salida


def test_report_says_so_when_there_is_no_drift(check_credentials, tmp_path, capsys):
    ejemplo, real = _escribir(tmp_path, "A=your_a\n", "A=cargada\n")

    check_credentials.report_env_drift(ejemplo, real)
    salida = capsys.readouterr().out

    assert "sin deriva" in salida.lower()


def test_report_does_not_decide_the_exit_code(check_credentials, tmp_path):
    """La deriva se informa, no bloquea.

    El codigo de salida del script significa 'las credenciales de publicacion
    sirven'. Una opcional sin poner en el `.env` no es eso, y un chequeo que
    pone el script en rojo por una decision pendiente termina desactivado.
    """
    ejemplo, real = _escribir(tmp_path, "A=your_a\n", "B=x\n")

    assert check_credentials.report_env_drift(ejemplo, real) is None


def test_a_disabled_network_cannot_turn_the_script_red(
    check_credentials, monkeypatch, capsys
):
    """El codigo de salida significa "las credenciales de publicacion sirven".

    Una red apagada no tiene credenciales que sirvan ni que dejen de servir: no
    participa. Un gate que se pone rojo por una decision tomada a proposito
    termina desactivado, que es el mismo motivo por el que la deriva de `.env`
    informa en vez de bloquear.
    """
    monkeypatch.setenv("PUBLISH_X", "false")
    monkeypatch.setenv("PUBLISH_LINKEDIN", "false")
    _apagar_los_cuatro(monkeypatch)
    monkeypatch.setattr(check_credentials, "report_env_drift", lambda *a, **k: None)

    assert check_credentials.main() == 0

    salida = capsys.readouterr().out
    assert "X:        apagado" in salida
    assert "LinkedIn: apagado" in salida


def test_a_disabled_network_is_not_even_checked(check_credentials, monkeypatch):
    """No se contacta la API de una red que no se va a usar.

    Se mockea `check_x` y no `requests.get`: `check_x` autentica con
    `OAuth1Session.get`, asi que un test que vigile `requests.get` pasaria
    igual con el bug puesto.
    """
    llamadas = []
    monkeypatch.setenv("PUBLISH_X", "false")
    _apagar_los_cuatro(monkeypatch)
    monkeypatch.setattr(check_credentials, "report_env_drift", lambda *a, **k: None)
    monkeypatch.setattr(
        check_credentials, "check_x", lambda: llamadas.append("x") or True
    )
    monkeypatch.setattr(check_credentials, "check_linkedin", lambda: True)

    check_credentials.main()

    assert llamadas == []


def test_a_malformed_flag_gets_a_diagnostic_and_not_a_traceback(
    check_credentials, monkeypatch, capsys
):
    """El unico sitio donde el valor malo no debe salir por traceback.

    El orquestador se muere loudly a proposito; este script existe para
    decirle a un humano que tiene mal configurado, y todos sus otros caminos
    de fallo imprimen una linea legible.
    """
    monkeypatch.setenv("PUBLISH_X", "yes")
    _apagar_los_cuatro(monkeypatch)
    monkeypatch.setattr(check_credentials, "report_env_drift", lambda *a, **k: None)

    assert check_credentials.main() == 1

    salida = capsys.readouterr().out
    assert "PUBLISH_X" in salida
    assert "yes" in salida


def test_el_aviso_de_vencimiento_no_depende_de_que_linkedin_conteste(
    check_credentials, monkeypatch, capsys
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
        check_credentials.requests, "get", lambda *a, **k: Respuesta403()
    )

    check_credentials.check_linkedin()

    salida = capsys.readouterr().out
    assert "Token emitido hace" in salida


def test_un_corte_de_red_deja_el_marcador_que_el_nightly_grepea(
    check_credentials, monkeypatch, capsys
):
    """`LINKEDIN_UNREACHABLE` es un contrato con el workflow, no un print.

    El nightly lo grepea para distinguir un corte de red de un token muerto:
    solo uno de los dos pide ir al portal. Si alguien lo borra, la alerta cae
    a la rama generica sin que nada se ponga en rojo.
    """
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "un-token-cualquiera")
    monkeypatch.setenv("LINKEDIN_PERSON_URN", "urn:li:person:abc")
    # El fixture es `scope="module"` y solo restaura `os.environ` al final, asi
    # que sin esto el `.env` del desarrollador se cuela y el test depende de la
    # maquina. Es el mismo agujero que el `load_dotenv` de contract/conftest.
    monkeypatch.delenv("LINKEDIN_TOKEN_ISSUED", raising=False)

    def _revienta(*a, **k):
        raise check_credentials.requests.RequestException("sin ruta al host")

    monkeypatch.setattr(check_credentials.requests, "get", _revienta)

    assert check_credentials.check_linkedin() is False
    assert "LINKEDIN_UNREACHABLE" in capsys.readouterr().out


def test_un_401_deja_el_marcador_que_el_nightly_grepea(
    check_credentials, monkeypatch, capsys
):
    """`LINKEDIN_TOKEN_DEAD` es la otra mitad del mismo contrato."""
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "un-token-cualquiera")
    monkeypatch.setenv("LINKEDIN_PERSON_URN", "urn:li:person:abc")
    monkeypatch.delenv("LINKEDIN_TOKEN_ISSUED", raising=False)

    class Respuesta401:
        status_code = 401
        text = ""

    monkeypatch.setattr(
        check_credentials.requests, "get", lambda *a, **k: Respuesta401()
    )

    assert check_credentials.check_linkedin() is False
    assert "LINKEDIN_TOKEN_DEAD" in capsys.readouterr().out


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
    un `USE_FRED` mal escrito dejaria a X ya contactada.
    """
    llamadas = []
    monkeypatch.setenv("USE_FRED", "puede ser")
    monkeypatch.setattr(check_credentials, "report_env_drift", lambda *a, **k: None)
    monkeypatch.setattr(
        check_credentials, "check_x", lambda: llamadas.append("x") or True
    )

    assert check_credentials.main() == 1

    salida = capsys.readouterr().out
    assert "USE_FRED" in salida
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
