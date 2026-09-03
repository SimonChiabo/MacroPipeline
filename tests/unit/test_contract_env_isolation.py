"""El `.env` de los contract tests no puede filtrarse al resto de la suite.

`tests/contract/conftest.py` cargaba el `.env` real en tiempo de importacion, y
pytest importa esa conftest aunque el marcador `contract` deseleccione todos sus
tests. El resultado era que `pytest` a secas fallaba en la maquina del autor y
pasaba en CI y en un clon limpio: con `USE_ANTHROPIC=false` en el `.env` local,
`test_the_llm_layer_is_still_built_when_the_key_is_there` se caia porque el
orquestador no construia la capa LLM.

El arreglo no es restaurar `os.environ` despues —eso es lo que ya hace la
fixture de `test_check_credentials.py`, y llega tarde cuando la carga ocurre en
el import—. Es no tocar `os.environ` en ningun momento: las credenciales se
leen a un diccionario y viajan por fixture a quien las pide.

Estos tests viven en `tests/unit/` a proposito. Puestos en `tests/contract/` no
correrian nunca por defecto, que es justo el agujero que dejo pasar el bug.
"""

import importlib.util
import os
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONFTEST = ROOT / "tests" / "contract" / "conftest.py"

# Nombre que no existe en ningun entorno real: si aparece en `os.environ`
# despues de leer un `.env`, la unica explicacion es que alguien lo puso ahi.
CENTINELA = "MACROPIPELINE_CENTINELA_DE_FUGA"


def _cargar_conftest() -> ModuleType:
    """La conftest de contract como modulo suelto, cargada por ruta."""
    spec = importlib.util.spec_from_file_location("contract_conftest", CONFTEST)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def conftest_contract():
    """El modulo, y `os.environ` restaurado pase lo que pase.

    La restauracion es la red de seguridad del propio test, no lo que se esta
    probando: si el modulo vuelve a ensuciar el entorno, el `assert` falla
    primero y esta fixture evita que ademas contamine los tests siguientes.
    """
    previo = os.environ.copy()
    try:
        yield _cargar_conftest()
    finally:
        os.environ.clear()
        os.environ.update(previo)


def test_importing_the_contract_conftest_does_not_touch_the_environment(
    conftest_contract, monkeypatch, tmp_path
):
    """Importar la conftest no puede meter nada en `os.environ`.

    El `.env` de mentira lleva el centinela, asi que el test es determinista en
    cualquier maquina: no depende de que exista un `.env` real ni de que tenga
    una variable concreta. En un clon limpio dice lo mismo que en la del autor.
    """
    env_falso = tmp_path / ".env"
    env_falso.write_text(f"{CENTINELA}=se-filtro\n", encoding="utf-8")
    monkeypatch.setattr(conftest_contract, "ENV_FILE", env_falso)

    modulo = _cargar_conftest()

    assert CENTINELA not in os.environ
    assert modulo is not None


def test_the_credentials_are_read_without_exporting_them(
    conftest_contract, monkeypatch, tmp_path
):
    """La fixture entrega el valor del `.env` y no lo publica en el entorno.

    Las dos mitades importan. Sin la primera, "no ensuciar el entorno" se
    cumpliria no leyendo nada y los contract tests se saltarian siempre en
    local; sin la segunda volvemos al bug.
    """
    env_falso = tmp_path / ".env"
    env_falso.write_text(f"{CENTINELA}=valor-real\n", encoding="utf-8")
    monkeypatch.setattr(conftest_contract, "ENV_FILE", env_falso)

    credenciales = conftest_contract.leer_credenciales()

    assert credenciales[CENTINELA] == "valor-real"
    assert CENTINELA not in os.environ


def test_a_missing_env_file_is_not_an_error(conftest_contract, monkeypatch, tmp_path):
    """El clon limpio no tiene `.env`, y eso no puede reventar la coleccion."""
    monkeypatch.setattr(conftest_contract, "ENV_FILE", tmp_path / "no-existe.env")

    assert conftest_contract.leer_credenciales() == {}


def test_the_real_environment_wins_over_the_env_file(
    conftest_contract, monkeypatch, tmp_path
):
    """En CI las credenciales llegan por entorno y tienen que ganarle al `.env`.

    Es la precedencia que ya tenia `load_dotenv` sin `override`, y perderla
    haria que un `.env` viejo olvidado en el runner tapara los GitHub Secrets.
    """
    env_falso = tmp_path / ".env"
    env_falso.write_text(f"{CENTINELA}=el-del-fichero\n", encoding="utf-8")
    monkeypatch.setattr(conftest_contract, "ENV_FILE", env_falso)
    monkeypatch.setenv(CENTINELA, "el-del-entorno")

    valor = conftest_contract.require_api_key(
        conftest_contract.leer_credenciales(), CENTINELA
    )

    assert valor == "el-del-entorno"


def test_a_missing_key_skips_naming_it(conftest_contract, monkeypatch):
    """El salteo sigue nombrando la credencial que falta."""
    monkeypatch.delenv(CENTINELA, raising=False)
    monkeypatch.delenv("CI", raising=False)

    with pytest.raises(BaseException) as exc:
        conftest_contract.require_api_key({}, CENTINELA)

    assert CENTINELA in str(exc.value)


def test_a_missing_key_fails_loudly_in_ci(conftest_contract, monkeypatch):
    """En CI faltar una credencial es un fallo, no un salteo (ADR-008).

    Sin esto, el nightly saldria verde sin haber verificado ningun contrato.
    """
    monkeypatch.delenv(CENTINELA, raising=False)
    monkeypatch.setenv("CI", "true")

    with pytest.raises(BaseException) as exc:
        conftest_contract.require_api_key({}, CENTINELA)

    assert "CI" in str(exc.value)
    assert CENTINELA in str(exc.value)
