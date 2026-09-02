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
import json
import os
from pathlib import Path

import pytest

from macro_pipeline.llm.client import MODEL

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

    El codigo de salida del script significa 'algun componente encendido tiene
    credenciales que no sirven'. Una opcional sin poner en el `.env` no es eso,
    y un chequeo que pone el script en rojo por una decision pendiente termina
    desactivado.
    """
    ejemplo, real = _escribir(tmp_path, "A=your_a\n", "B=x\n")

    assert check_credentials.report_env_drift(ejemplo, real) is None


def test_a_disabled_network_cannot_turn_the_script_red(
    check_credentials, monkeypatch, capsys
):
    """El codigo de salida significa "algun componente encendido no sirve".

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
    assert "X:              apagado" in salida
    assert "LinkedIn:       apagado" in salida


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
    assert "FRED:           apagado" in capsys.readouterr().out


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


def test_un_corte_de_red_en_av_si_pone_rojo(check_credentials, monkeypatch, capsys):
    """La regla, enfrentada a su excepcion en el mismo fichero.

    El rate limit es lo unico que se perdona. Un corte de red deja la key sin
    verificar igual, pero no es una condicion que el chequeo se fabrique solo,
    y un verde que no verifico nada es peor que un rojo.
    """
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "una-key-cualquiera")

    def _revienta(*a, **k):
        raise check_credentials.requests.RequestException("sin ruta al host")

    monkeypatch.setattr(check_credentials.requests, "get", _revienta)

    assert check_credentials.check_av() == check_credentials.NO_LISTO
    assert "No se pudo contactar" in capsys.readouterr().out


def test_el_rate_limit_de_av_no_cambia_el_codigo_de_salida(
    check_credentials, monkeypatch
):
    """El veredicto tiene que llegar entero hasta el codigo de salida.

    Es la mitad que el test de arriba no cubre: `check_av` puede devolver
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


def test_un_corte_de_red_en_anthropic_pone_rojo(check_credentials, monkeypatch, capsys):
    """La regla general: no haber podido verificar pone rojo.

    El rate limit de AV es la unica excepcion, y es porque el chequeo se
    fabrica esa condicion solo. Un corte de red no es eso.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "una-key-cualquiera")

    def _revienta(*a, **k):
        raise check_credentials.requests.RequestException("sin ruta al host")

    monkeypatch.setattr(check_credentials.requests, "get", _revienta)

    assert check_credentials.check_anthropic() == check_credentials.NO_LISTO
    assert "No se pudo contactar" in capsys.readouterr().out


def test_anthropic_no_avisa_por_el_snapshot_fechado_del_mismo_modelo(
    check_credentials, monkeypatch, capsys
):
    """Lo que devuelve la API de verdad, y por que la igualdad no alcanza.

    Verificado en vivo el 2026-09-01: `/v1/models` lista
    `claude-haiku-4-5-20251001`, el snapshot fechado, y NO el alias
    `claude-haiku-4-5`, que es el que usa el pipeline y funciona. Mirando la
    igualdad a secas, el chequeo aviso de un retiro inexistente la primera vez
    que se corrio contra la API real. Es el mismo modo de fallo que el listado
    paginado: un AVISO falso entrena a ignorar el verdadero.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "una-key-cualquiera")
    monkeypatch.setattr(
        check_credentials.requests,
        "get",
        lambda *a, **k: _respuesta_de_modelos([f"{MODEL}-20251001"]),
    )

    assert check_credentials.check_anthropic() == check_credentials.LISTO
    assert "[AVISO]" not in capsys.readouterr().out


def test_anthropic_solo_acepta_una_fecha_como_sufijo(
    check_credentials, monkeypatch, capsys
):
    """La otra mitad: aflojar el match no puede volverlo mudo.

    Se acepta exactamente un sufijo de ocho digitos —la forma de un snapshot
    fechado— y nada mas. Un `startswith` a secas daria por presente cualquier
    id que empiece igual, y entonces el AVISO que este chequeo existe para dar
    no saldria nunca.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "una-key-cualquiera")
    monkeypatch.setattr(
        check_credentials.requests,
        "get",
        lambda *a, **k: _respuesta_de_modelos([f"{MODEL}-turbo"]),
    )

    assert check_credentials.check_anthropic() == check_credentials.LISTO
    assert "[AVISO]" in capsys.readouterr().out


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
        # Las keys se guardan aca y no en el script: que la sonda escriba donde
        # dice es asunto del test, y una global de modulo puesta para poder
        # mirarla desde afuera es codigo de produccion que no sirve a nadie.
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


def test_la_sonda_de_r2_nunca_toca_el_fichero_de_estado(check_credentials, monkeypatch):
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


def test_el_mensaje_de_error_lee_la_clave_de_telegram(check_credentials):
    """Telegram explica el fallo en `description`, como FRED en `error_message`.

    Sin esta rama, un chat_id equivocado se reporta como "HTTP 400" a secas y
    el script deja de hacer justo lo que existe para hacer: decirle a un humano
    que tiene mal configurado.
    """

    class Respuesta:
        status_code = 400
        text = (
            '{"ok":false,"error_code":400,"description":"Bad Request: chat not found"}'
        )

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


def _telegram_responde(check_credentials, monkeypatch, respuestas):
    """Falsea `requests.get` despachando por el ultimo segmento de la URL.

    Los tres GET del chequeo van al mismo host y solo se distinguen por el
    metodo, asi que un fake que devuelva siempre lo mismo haria pasar tests que
    no prueban nada del orden. `respuestas` es {metodo: (status, cuerpo)}, y
    una llamada a un metodo que el test no declaro es un fallo del test.

    Devuelve la lista `llamadas` de `(metodo, params)`, en orden: sin esto,
    borrar el `params` de una llamada (p.ej. el `chat_id` de `getChat`) deja
    la suite en verde igual, porque el fake solo mira el ultimo segmento de la
    URL y tira los kwargs. Contra la API real ese `params` faltante pide el
    chat de nadie.
    """
    llamadas: list[tuple[str, dict[str, str] | None]] = []

    class Respuesta:
        def __init__(self, status_code, cuerpo):
            self.status_code = status_code
            self._cuerpo = cuerpo
            self.text = json.dumps(cuerpo)

        def json(self):
            return self._cuerpo

    def _get(url, *a, **k):
        metodo = url.rsplit("/", 1)[-1]
        assert metodo in respuestas, f"llamada inesperada a {metodo}"
        llamadas.append((metodo, k.get("params")))
        status, cuerpo = respuestas[metodo]
        return Respuesta(status, cuerpo)

    monkeypatch.setattr(check_credentials.requests, "get", _get)
    return llamadas


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
    """La presencia se mira antes de contactar a nadie, como en los otros seis.

    El assert es sobre "sin definir" y no sobre "TELEGRAM_BOT_TOKEN" a secas:
    ese segundo texto tambien lo imprime el `except ValueError` que envuelve
    `TelegramBot()` cuando el constructor revienta por falta de credenciales,
    asi que un test que solo mirara eso seguiria en verde con la guarda de
    `_check_present` borrada. "sin definir" solo lo puede imprimir esa guarda.
    """
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "4242")

    def _no_deberia_llamar(*a, **k):
        raise AssertionError("no tenia que salir a la red sin token")

    monkeypatch.setattr(check_credentials.requests, "get", _no_deberia_llamar)

    assert check_credentials.check_telegram() == check_credentials.NO_LISTO
    assert "sin definir" in capsys.readouterr().out


def test_un_token_de_telegram_con_el_placeholder_no_sale_a_la_red(
    check_credentials, monkeypatch, capsys
):
    """El placeholder de .env.example es un string truthy: el constructor no lo caza.

    `TelegramBot.__init__` solo revienta si el valor es falsy (ausente o
    vacio); "your_telegram_bot_token" pasa esa prueba igual que un token real,
    asi que sin la guarda de `_check_present` —que ademas mira `_is_placeholder`—
    este caso saldria a la red con una credencial que nunca sirvio.
    """
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "your_telegram_bot_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "4242")

    def _no_deberia_llamar(*a, **k):
        raise AssertionError("no tenia que salir a la red con el placeholder")

    monkeypatch.setattr(check_credentials.requests, "get", _no_deberia_llamar)

    assert check_credentials.check_telegram() == check_credentials.NO_LISTO
    assert "sigue con el placeholder" in capsys.readouterr().out


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
    llamadas = _telegram_responde(
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
    # Ancla que `getChat` se llama CON `chat_id`: sin este assert, borrar el
    # argumento deja la suite en verde igual (el fake tira los kwargs), y
    # contra la API real ese chequeo pediria el chat de nadie y quedaria en
    # rojo permanente sin que ningun test se quejara.
    assert ("getChat", {"chat_id": "4242"}) in llamadas


def test_un_corte_de_red_en_getchat_no_imprime_la_pista_del_400(
    check_credentials, monkeypatch, capsys
):
    """La pista del 400/403 es sobre el chat; un corte de red no llego a pedirlo.

    `_telegram_get` tiene cinco causas distintas para devolver `None`, y la
    pista de `getChat` es especifica de una sola: la rama generica de HTTP !=
    200 (el 400 del chat mal copiado, el 403 del bot expulsado). Imprimirla
    tambien en un corte de red pondria un diagnostico sobre un chat que nunca
    se llego a pedir, como el `[FALLA] No se pudo contactar...` seguido de un
    "Un 400 aca suele ser..." que no tiene nada que ver.
    """
    _credenciales_de_telegram(monkeypatch)

    def _get(url, *a, **k):
        metodo = url.rsplit("/", 1)[-1]
        if metodo == "getChat":
            raise check_credentials.requests.RequestException("conexion perdida")
        cuerpos = {
            "getMe": {"ok": True, "result": {"username": "MacroPipelineBot"}},
            "getWebhookInfo": {"ok": True, "result": {"url": ""}},
        }
        assert metodo in cuerpos, f"llamada inesperada a {metodo}"

        class Respuesta:
            status_code = 200

            @staticmethod
            def json():
                return cuerpos[metodo]

        return Respuesta()

    monkeypatch.setattr(check_credentials.requests, "get", _get)

    assert check_credentials.check_telegram() == check_credentials.NO_LISTO
    salida = capsys.readouterr().out
    assert "No se pudo contactar" in salida


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
