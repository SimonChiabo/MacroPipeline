"""Los switches por componente: parseo estricto y construccion tri-estado.

Ocho variables —`USE_FMP`, `USE_AV`, `USE_FRED`, `USE_ANTHROPIC`, `USE_R2`,
`USE_TELEGRAM`, `PUBLISH_X` y `PUBLISH_LINKEDIN`— deciden si cada componente
con credenciales se construye, no solo las dos redes de publicacion. Un
parseo laxo tendria que elegir en silencio entre dos lecturas de
`PUBLISH_LINKEDIN=no` —apagada si se compara contra "true", encendida si se
compara contra "false"— y las dos son malas: una pausa que no pausa, o una
pausa que nadie pidio.

`component_enabled` levanta ante un valor invalido, que es lo que
`check_publishers.py` quiere. `read_switch` en cambio devuelve el motivo en
vez de levantar, para que el constructor del orquestador pueda seguir. Y
`build_component` distingue las tres combinaciones que le siguen: listo,
apagado a proposito, y encendido pero roto.
"""

import pytest

from macro_pipeline.components import (
    PUBLISH_LINKEDIN_VAR,
    PUBLISH_X_VAR,
    USE_FRED_VAR,
    USE_TELEGRAM_VAR,
    build_component,
    component_enabled,
    read_switch,
)


def test_the_default_is_enabled(monkeypatch):
    """Sin la variable, se publica: es el comportamiento de siempre."""
    monkeypatch.delenv(PUBLISH_X_VAR, raising=False)
    assert component_enabled(PUBLISH_X_VAR) is True


def test_an_empty_value_is_enabled(monkeypatch):
    """`PUBLISH_X=` es una variable sin decidir, no una red apagada."""
    monkeypatch.setenv(PUBLISH_X_VAR, "")
    assert component_enabled(PUBLISH_X_VAR) is True


@pytest.mark.parametrize("value", ["false", "FALSE", "False", "  false  "])
def test_false_in_any_casing_disables(monkeypatch, value):
    monkeypatch.setenv(PUBLISH_LINKEDIN_VAR, value)
    assert component_enabled(PUBLISH_LINKEDIN_VAR) is False


@pytest.mark.parametrize("value", ["true", "TRUE", "True", "  true  "])
def test_true_in_any_casing_enables(monkeypatch, value):
    monkeypatch.setenv(PUBLISH_LINKEDIN_VAR, value)
    assert component_enabled(PUBLISH_LINKEDIN_VAR) is True


@pytest.mark.parametrize("value", ["no", "0", "off", "yes", "sí"])
def test_anything_else_raises_instead_of_guessing(monkeypatch, value):
    """El valor invalido mata la run en el constructor, y eso es el punto.

    Las dos alternativas son silenciosas: tratarlo como apagada deja de
    publicar sin que nadie lo pida, tratarlo como encendida publica en una red
    que se quiso pausar. Morir con un mensaje claro en la primera run despues
    del typo es lo unico que se ve.
    """
    monkeypatch.setenv(PUBLISH_X_VAR, value)
    with pytest.raises(ValueError) as exc:
        component_enabled(PUBLISH_X_VAR)
    assert PUBLISH_X_VAR in str(exc.value)
    assert value in str(exc.value)


def test_the_error_shows_what_the_operator_actually_typed(monkeypatch):
    """`!r` esta para que se vea el espacio de mas; normalizar antes lo anula.

    El typo mas dificil de ver a ojo en un `.env` es un espacio o un CRLF
    pegado al final de la linea, y era justo el que el mensaje escondia.
    """
    monkeypatch.setenv(PUBLISH_X_VAR, "  Yes  ")
    with pytest.raises(ValueError) as exc:
        component_enabled(PUBLISH_X_VAR)
    assert "'  Yes  '" in str(exc.value)


class _Cliente:
    pass


def test_a_healthy_client_comes_back_with_no_error():
    cliente, error = build_component("x", _Cliente, enabled=True)
    assert isinstance(cliente, _Cliente)
    assert error is None


def test_a_disabled_component_is_not_constructed_at_all():
    """Apagada no es "se construye y no se usa": no se construye.

    Es lo que hace que un token de LinkedIn vencido con la bandera en false
    de una run verde y silenciosa en vez de una degradada con alerta.
    """
    llamadas = []

    def factory():
        llamadas.append(1)
        return _Cliente()

    cliente, error = build_component("linkedin", factory, enabled=False)

    assert cliente is None
    assert error is None, "un componente apagado no es un fallo y no debe alertar"
    assert llamadas == [], "no se debe ni intentar construir el cliente"


def test_a_broken_client_comes_back_with_the_reason():
    """Rota: sin cliente, pero con el motivo, que es lo que va en la alerta."""

    def factory():
        raise ValueError("Faltan credenciales de X API.")

    cliente, error = build_component("x", factory, enabled=True)

    assert cliente is None
    assert error == "Faltan credenciales de X API."


def test_only_valueerror_is_swallowed():
    """Un fallo que no sea de credenciales no se disfraza de red rota."""

    def factory():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        build_component("x", factory, enabled=True)


def test_read_switch_reports_an_invalid_value_instead_of_raising(monkeypatch):
    """El orquestador no puede dejar que esto levante: seria el bug (d) otra vez.

    `component_enabled` sigue levantando porque `check_publishers.py` lo quiere
    asi. `read_switch` es la version que devuelve el motivo para que el
    constructor pueda seguir y el punto de decision lo reporte.
    """
    monkeypatch.setenv(USE_FRED_VAR, "maybe")

    encendido, motivo = read_switch(USE_FRED_VAR)

    assert encendido is False
    assert motivo is not None
    assert "maybe" in motivo


def test_read_switch_has_no_motive_when_the_value_is_valid(monkeypatch):
    monkeypatch.setenv(USE_FRED_VAR, "false")
    assert read_switch(USE_FRED_VAR) == (False, None)


def test_an_invalid_switch_reads_as_off_which_is_why_the_motive_matters(monkeypatch):
    """La trampa que este par de valores esconde.

    Un valor invalido devuelve `False`, asi que el componente no se construye y
    queda **indistinguible de un apagado deliberado**. Lo unico que los separa
    es el motivo. Por eso el punto de decision mira `switch_errors` antes que
    cualquier rama de apagado.
    """
    monkeypatch.setenv(USE_TELEGRAM_VAR, "maybe")
    encendido, motivo = read_switch(USE_TELEGRAM_VAR)
    assert encendido is False
    assert motivo is not None
