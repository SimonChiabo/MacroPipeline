"""Chequeo de deriva entre `.env` y `.env.example`.

El 2026-08-24 aparecio `TELEGRAM_ALLOWED_USER_ID`: declarada en
`.env.example` y marcada como CRITICO desde el principio, implementada en
`TelegramBot.__init__`, y ausente del `.env` real probablemente desde mayo.
Sin ella, `wait_for_approval` acepta el boton de cualquiera en el chat y el
HITL de ADR-004 —lo unico entre el pipeline y publicar— queda apagado. Lo
unico que lo decia era un `logger.warning` al arrancar.

`scripts/check_publishers.py` no es un paquete, asi que se carga por ruta.
"""

import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def check_publishers():
    """El script, cargado por ruta y sin dejar el `.env` real en el entorno.

    Importarlo ejecuta `load_dotenv(ROOT / '.env')` a nivel de modulo. Sin
    restaurar `os.environ` eso filtra las credenciales reales al resto de la
    sesion de pytest: rompio `test_wait_for_approval_success`, que pasaba
    porque `TELEGRAM_ALLOWED_USER_ID` no estaba puesta.
    """
    previo = os.environ.copy()
    try:
        spec = importlib.util.spec_from_file_location(
            "check_publishers", ROOT / "scripts" / "check_publishers.py"
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
    check_publishers, tmp_path
):
    """El caso real: la variable existe en el ejemplo y no en el `.env`."""
    ejemplo, real = _escribir(
        tmp_path,
        "# --- HITL ---\n"
        "TELEGRAM_BOT_TOKEN=your_token\n"
        "TELEGRAM_ALLOWED_USER_ID=your_id\n",
        "TELEGRAM_BOT_TOKEN=8703868612:AAH_real\n",
    )

    hallazgos = check_publishers.compare_env_files(ejemplo, real)

    assert ("TELEGRAM_ALLOWED_USER_ID", "ausente") in hallazgos
    assert not any(n == "TELEGRAM_BOT_TOKEN" for n, _ in hallazgos)


def test_flags_a_key_left_with_the_example_placeholder(check_publishers, tmp_path):
    """Copiar el ejemplo y no rellenarlo es tan malo como no tener la clave."""
    ejemplo, real = _escribir(
        tmp_path,
        "FRED_API_KEY=your_fred_api_key\n",
        "FRED_API_KEY=your_fred_api_key\n",
    )

    hallazgos = check_publishers.compare_env_files(ejemplo, real)

    assert ("FRED_API_KEY", "placeholder") in hallazgos


def test_flags_a_key_the_example_does_not_document(check_publishers, tmp_path):
    """La deriva inversa: el `.env` tiene algo que el ejemplo no documenta.

    Es como se genera el problema para el siguiente que clone el repo: copia
    `.env.example` y le falta una variable que nadie escribio ahi.
    """
    ejemplo, real = _escribir(
        tmp_path,
        "FRED_API_KEY=your_fred_api_key\n",
        "FRED_API_KEY=real\nSTATE_DB_PATH=/tmp/x.db\n",
    )

    hallazgos = check_publishers.compare_env_files(ejemplo, real)

    assert ("STATE_DB_PATH", "sin documentar") in hallazgos


def test_no_findings_when_both_files_agree(check_publishers, tmp_path):
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

    assert check_publishers.compare_env_files(ejemplo, real) == []


def test_a_commented_declaration_in_the_example_counts_as_documented(
    check_publishers, tmp_path
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

    assert check_publishers.compare_env_files(ejemplo, real) == []


def test_a_commented_declaration_is_not_required_in_env(check_publishers, tmp_path):
    """Y comentada tampoco se exige: es opcional, ese es el punto."""
    ejemplo, real = _escribir(
        tmp_path,
        "# LINKEDIN_TOKEN_ISSUED=2026-05-15\nFRED_API_KEY=your_fred_api_key\n",
        "FRED_API_KEY=real\n",
    )

    assert check_publishers.compare_env_files(ejemplo, real) == []


def test_runs_against_the_real_repo_files(check_publishers):
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

    hallazgos = check_publishers.compare_env_files(ROOT / ".env.example", real)

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


def test_the_example_keeps_the_two_decided_declarations(check_publishers):
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

    declaradas = check_publishers._parse_env_file(ejemplo)
    comentadas = check_publishers._commented_names(ejemplo)

    assert "STATE_DB_PATH" in comentadas
    assert "STATE_DB_PATH" not in declaradas
    assert declaradas.get("ALLOW_MOCK_DATA") == "false"


def test_report_prints_every_finding_with_its_reason(
    check_publishers, tmp_path, capsys
):
    ejemplo, real = _escribir(
        tmp_path,
        "FALTANTE=your_valor\nPUESTA=your_valor\n",
        "PUESTA=your_valor\nEXTRA=algo\n",
    )

    check_publishers.report_env_drift(ejemplo, real)
    salida = capsys.readouterr().out

    assert "FALTANTE" in salida and "ausente" in salida
    assert "PUESTA" in salida and "placeholder" in salida
    assert "EXTRA" in salida and "sin documentar" in salida


def test_report_says_so_when_there_is_no_drift(check_publishers, tmp_path, capsys):
    ejemplo, real = _escribir(tmp_path, "A=your_a\n", "A=cargada\n")

    check_publishers.report_env_drift(ejemplo, real)
    salida = capsys.readouterr().out

    assert "sin deriva" in salida.lower()


def test_report_does_not_decide_the_exit_code(check_publishers, tmp_path):
    """La deriva se informa, no bloquea.

    El codigo de salida del script significa 'las credenciales de publicacion
    sirven'. Una opcional sin poner en el `.env` no es eso, y un chequeo que
    pone el script en rojo por una decision pendiente termina desactivado.
    """
    ejemplo, real = _escribir(tmp_path, "A=your_a\n", "B=x\n")

    assert check_publishers.report_env_drift(ejemplo, real) is None


def test_a_disabled_network_cannot_turn_the_script_red(
    check_publishers, monkeypatch, capsys
):
    """El codigo de salida significa "las credenciales de publicacion sirven".

    Una red apagada no tiene credenciales que sirvan ni que dejen de servir: no
    participa. Un gate que se pone rojo por una decision tomada a proposito
    termina desactivado, que es el mismo motivo por el que la deriva de `.env`
    informa en vez de bloquear.
    """
    monkeypatch.setenv("PUBLISH_X", "false")
    monkeypatch.setenv("PUBLISH_LINKEDIN", "false")
    monkeypatch.setattr(check_publishers, "report_env_drift", lambda *a, **k: None)

    assert check_publishers.main() == 0

    salida = capsys.readouterr().out
    assert "apagada" in salida.lower()


def test_a_disabled_network_is_not_even_checked(check_publishers, monkeypatch):
    """No se contacta la API de una red que no se va a usar.

    Se mockea `check_x` y no `requests.get`: `check_x` autentica con
    `OAuth1Session.get`, asi que un test que vigile `requests.get` pasaria
    igual con el bug puesto.
    """
    llamadas = []
    monkeypatch.setenv("PUBLISH_X", "false")
    monkeypatch.setattr(check_publishers, "report_env_drift", lambda *a, **k: None)
    monkeypatch.setattr(
        check_publishers, "check_x", lambda: llamadas.append("x") or True
    )
    monkeypatch.setattr(check_publishers, "check_linkedin", lambda: True)

    check_publishers.main()

    assert llamadas == []
