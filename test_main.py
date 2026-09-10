"""
Tests de la API de la calculadora.

Fijate un detalle importante: estos tests NO levantan un servidor de verdad.
TestClient de FastAPI llama a la aplicacion directamente en memoria, asi que
corren rapido y no dependen de que haya un puerto libre.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import db
import main
from main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Camino feliz: las cuatro operaciones
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "operacion, a, b, esperado",
    [
        ("suma", 2, 3, 5),
        ("suma", -4, 1.5, -2.5),
        ("resta", 10, 4, 6),
        ("resta", 4, 10, -6),
        ("multiplicacion", 6, 7, 42),
        ("multiplicacion", 3, 0, 0),
        ("division", 10, 4, 2.5),
        ("division", -9, 3, -3),
    ],
)
def test_calcula_correctamente(operacion, a, b, esperado):
    respuesta = client.post("/api/calcular", json={"a": a, "b": b, "operacion": operacion})

    assert respuesta.status_code == 200
    assert respuesta.json()["resultado"] == esperado


def test_la_respuesta_incluye_la_expresion_legible():
    respuesta = client.post("/api/calcular", json={"a": 8, "b": 2, "operacion": "division"})

    cuerpo = respuesta.json()
    assert cuerpo["expresion"] == "8.0 / 2.0 = 4.0"
    assert cuerpo["simbolo"] == "/"


# ---------------------------------------------------------------------------
# Casos borde: aca es donde se separa el codigo serio del codigo de juguete
# ---------------------------------------------------------------------------

def test_division_por_cero_devuelve_400_y_no_revienta():
    respuesta = client.post("/api/calcular", json={"a": 5, "b": 0, "operacion": "division"})

    assert respuesta.status_code == 400
    assert "cero" in respuesta.json()["detail"].lower()


def test_operacion_desconocida_devuelve_422():
    # 422 lo genera Pydantic solo, porque el campo esta tipado como Literal.
    respuesta = client.post("/api/calcular", json={"a": 1, "b": 2, "operacion": "potencia"})

    assert respuesta.status_code == 422


def test_valor_no_numerico_devuelve_422():
    respuesta = client.post("/api/calcular", json={"a": "hola", "b": 2, "operacion": "suma"})

    assert respuesta.status_code == 422


# ---------------------------------------------------------------------------
# Limites de los numeros de la computadora
# ---------------------------------------------------------------------------
# Un float de 64 bits llega hasta ~1.8e308. Pasado ese punto el resultado es
# "infinito", y ACA esta el problema: infinito NO EXISTE en JSON. El estandar
# no lo contempla. Si dejas que llegue al serializador, la API revienta con un
# 500 — o sea, culpa al servidor de un dato que mando el cliente.

@pytest.mark.parametrize(
    "a, b, operacion",
    [
        (1e308, 10, "multiplicacion"),      # overflow hacia +infinito
        (-1e308, 10, "multiplicacion"),     # overflow hacia -infinito
        (1, 5e-324, "division"),            # dividir por algo diminuto tambien desborda
    ],
)
def test_resultado_fuera_de_rango_devuelve_400_no_500(a, b, operacion):
    respuesta = client.post("/api/calcular", json={"a": a, "b": b, "operacion": operacion})

    assert respuesta.status_code == 400
    assert "rango" in respuesta.json()["detail"].lower()


@pytest.mark.parametrize(
    "literal_a",
    [
        "1e400",     # numero JSON tan grande que al parsearlo ya es infinito
        '"inf"',     # Pydantic en modo lax acepta strings numericas...
        '"nan"',     # ...y float("nan") es "valido" en Python
        '"-inf"',
    ],
)
def test_operando_no_finito_devuelve_422(literal_a):
    """
    Si el dato de ENTRADA ya es infinito o NaN, es un problema de validacion
    (422), no de calculo. Se rechaza antes de hacer la cuenta.

    Fijate que mandamos el cuerpo como TEXTO CRUDO con content=, no con json=.
    ¿Por que? Porque json= usa json.dumps de Python, que se NIEGA a serializar
    infinito. Asimetria curiosa del modulo json:
        json.dumps(float("inf"))  -> ValueError
        json.loads("1e400")       -> inf, sin una queja
    O sea: Python no lo escribe, pero lo lee feliz. Y un cliente cualquiera
    (curl, otro lenguaje) SI puede mandar ese texto. Por eso lo probamos asi.
    """
    cuerpo = f'{{"a": {literal_a}, "b": 1, "operacion": "suma"}}'

    respuesta = client.post(
        "/api/calcular",
        content=cuerpo,
        headers={"Content-Type": "application/json"},
    )

    assert respuesta.status_code == 422


def test_una_cuenta_grande_pero_valida_sigue_funcionando():
    # Que no nos pasemos de celosos: 1e308 es enorme pero es finito.
    respuesta = client.post("/api/calcular", json={"a": 1e308, "b": 1, "operacion": "suma"})

    assert respuesta.status_code == 200
    assert respuesta.json()["resultado"] == 1e308


def test_falta_un_campo_devuelve_422():
    respuesta = client.post("/api/calcular", json={"a": 1, "operacion": "suma"})

    assert respuesta.status_code == 422


# ---------------------------------------------------------------------------
# CORS — ahora que el front vive en otro servidor, esto es de verdad
# ---------------------------------------------------------------------------
# Antes de mandar un POST con Content-Type: application/json a otro origen,
# el navegador manda solo un OPTIONS preguntando "¿me dejas?". Eso se llama
# PREFLIGHT. Si la API no contesta bien ese OPTIONS, tu POST nunca sale.
# El usuario ve un error de CORS en la consola y jura que la API esta caida.

def test_preflight_desde_el_front_permitido():
    respuesta = client.options(
        "/api/calcular",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert respuesta.status_code == 200
    assert respuesta.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_preflight_desde_127_0_0_1_tambien_permitido():
    # localhost y 127.0.0.1 son ORIGENES DISTINTOS para el navegador, aunque
    # sean la misma maquina. Si solo permitis uno, el otro falla.
    respuesta = client.options(
        "/api/calcular",
        headers={
            "Origin": "http://127.0.0.1:3000",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert respuesta.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"


def test_un_origen_desconocido_no_recibe_permiso():
    respuesta = client.options(
        "/api/calcular",
        headers={
            "Origin": "http://sitio-malicioso.com",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert "access-control-allow-origin" not in respuesta.headers


def test_los_errores_400_llevan_headers_de_cors():
    # ESTE TEST ES IMPORTANTE. Si una respuesta de error no lleva el header de
    # CORS, el navegador la tira a la basura antes de que el front la vea, y el
    # usuario recibe "no se pudo contactar a la API" cuando en realidad la API
    # contesto perfecto. Es el bug mas confuso de debuggear que existe.
    respuesta = client.post(
        "/api/calcular",
        json={"a": 5, "b": 0, "operacion": "division"},
        headers={"Origin": "http://localhost:3000"},
    )

    assert respuesta.status_code == 400
    assert respuesta.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_los_errores_422_llevan_headers_de_cors():
    respuesta = client.post(
        "/api/calcular",
        json={"a": "hola", "b": 1, "operacion": "suma"},
        headers={"Origin": "http://localhost:3000"},
    )

    assert respuesta.status_code == 422
    assert respuesta.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_un_error_inesperado_devuelve_json_con_cors_y_no_texto_plano(monkeypatch):
    # Simulamos un bug nuestro: hacemos que la suma explote.
    # Sin la red de seguridad, Starlette contesta 500 en text/plain y SIN
    # headers de CORS — el front no ve nada y miente sobre la causa.
    def bomba(a, b):
        raise RuntimeError("bug inventado a proposito para este test")

    monkeypatch.setitem(main.OPERACIONES, "suma", ("+", bomba))

    respuesta = client.post(
        "/api/calcular",
        json={"a": 1, "b": 2, "operacion": "suma"},
        headers={"Origin": "http://localhost:3000"},
    )

    assert respuesta.status_code == 500
    assert respuesta.headers["content-type"].startswith("application/json")
    assert respuesta.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "detail" in respuesta.json()


def test_la_api_ya_no_sirve_el_front():
    # El backend ahora es SOLO una API. El HTML lo sirve otro servidor.
    respuesta = client.get("/")

    assert respuesta.status_code == 404


def test_healthcheck():
    respuesta = client.get("/api/salud")

    assert respuesta.status_code == 200
    assert respuesta.json()["estado"] == "ok"


# ---------------------------------------------------------------------------
# Historial — la persistencia es OPCIONAL
# ---------------------------------------------------------------------------
# Estos tests corren sin ninguna base de datos levantada, y eso no es una
# limitacion: es exactamente lo que queremos verificar. La API tiene que
# funcionar entera con la base caida o inexistente.
#
# Fijate que ni siquiera hace falta un Postgres en la integracion continua.
# Esa comodidad es consecuencia directa de haber diseñado la persistencia como
# opcional, no de una casualidad.


class PoolRoto:
    """Un pool que revienta apenas alguien le pide una conexion."""

    def connection(self):
        raise RuntimeError("la base se cayo (simulado a proposito)")


def test_sin_base_de_datos_el_healthcheck_lo_declara():
    respuesta = client.get("/api/salud")

    assert respuesta.status_code == 200
    assert respuesta.json()["persistencia"] is False


def test_sin_base_de_datos_el_historial_da_503_y_no_lista_vacia():
    # La diferencia importa. Una lista vacia significa "todavia no calculaste
    # nada". Un 503 significa "esta funcionalidad no esta disponible en este
    # servidor". Son dos situaciones distintas y el cliente tiene que poder
    # distinguirlas.
    respuesta = client.get("/api/historial")

    assert respuesta.status_code == 503
    assert "detail" in respuesta.json()


def test_la_calculadora_sigue_calculando_con_la_base_caida(monkeypatch):
    # ESTE es el test que justifica todo el diseño de db.py.
    #
    # La base explota en cada intento de guardar, y la cuenta tiene que salir
    # igual, con un 200 y el resultado correcto. Guardar el historial es una
    # funcionalidad SECUNDARIA: no puede tumbar a la principal.
    monkeypatch.setattr(db, "_pool", PoolRoto())

    respuesta = client.post("/api/calcular", json={"a": 6, "b": 7, "operacion": "multiplicacion"})

    assert respuesta.status_code == 200
    assert respuesta.json()["resultado"] == 42


def test_guardar_nunca_lanza_aunque_la_base_falle(monkeypatch):
    # El contrato de db.guardar() es "no lanza nunca". Lo verificamos directo,
    # sin pasar por la API: si esta funcion algun dia deja de tragarse los
    # errores, este test lo caza antes que el usuario.
    monkeypatch.setattr(db, "_pool", PoolRoto())

    db.guardar(
        a=1, b=2, operacion="suma", simbolo="+", resultado=3.0, expresion="1.0 + 2.0 = 3.0"
    )


def test_con_base_el_historial_devuelve_las_operaciones(monkeypatch):
    # Simulamos que hay persistencia sin levantar ningun Postgres: reemplazamos
    # las dos funciones que main.py le pide al modulo db.
    #
    # Esto se puede hacer limpio porque main.py nunca habla con la base
    # directamente: le habla a db. Esa frontera es lo que hace testeable al
    # endpoint.
    fila = {
        "a": 10.0,
        "b": 4.0,
        "operacion": "division",
        "simbolo": "/",
        "resultado": 2.5,
        "expresion": "10.0 / 4.0 = 2.5",
        "creado_en": datetime(2026, 7, 27, 12, 0, 0, tzinfo=timezone.utc),
    }

    monkeypatch.setattr(db, "hay_persistencia", lambda: True)
    monkeypatch.setattr(db, "listar", lambda limite: [fila])

    respuesta = client.get("/api/historial")

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert len(cuerpo) == 1
    assert cuerpo[0]["expresion"] == "10.0 / 4.0 = 2.5"


def test_el_historial_respeta_el_limite_pedido(monkeypatch):
    # Segundo caso, con otro valor, para que no alcance con devolver una
    # constante. Verificamos que el limite llega efectivamente hasta db.listar.
    recibidos = {}

    def listar_espia(limite):
        recibidos["limite"] = limite
        return []

    monkeypatch.setattr(db, "hay_persistencia", lambda: True)
    monkeypatch.setattr(db, "listar", listar_espia)

    client.get("/api/historial?limite=25")

    assert recibidos["limite"] == 25


def test_si_la_base_se_cae_en_caliente_el_historial_da_503_y_no_500(monkeypatch):
    # El caso que no cubre ninguno de los tests de arriba, y que en produccion
    # es el MAS probable de todos.
    #
    # La API arranco con la base andando, asi que el pool existe y
    # hay_persistencia() dice True. Despues, en algun momento, la base se cae.
    # Ahora db.listar() lanza — y si esa excepcion subiera, el cliente recibe
    # un 500: "algo se rompio y no se que".
    #
    # Y eso es mentira. La API esta perfecta, entiende el pedido, y no puede
    # cumplirlo porque una dependencia no esta. Eso es un 503, exactamente
    # igual que cuando no hay base configurada. La causa cambia; lo que el
    # cliente necesita saber, no.
    def listar_que_revienta(limite):
        raise RuntimeError("la base se cayo despues de arrancar (simulado)")

    monkeypatch.setattr(db, "hay_persistencia", lambda: True)
    monkeypatch.setattr(db, "listar", listar_que_revienta)

    respuesta = client.get("/api/historial")

    assert respuesta.status_code == 503
    assert "detail" in respuesta.json()


@pytest.mark.parametrize("limite", [0, -5, 101, 999999999])
def test_el_historial_rechaza_limites_fuera_de_rango(limite, monkeypatch):
    # Sin tope, alguien pide limite=999999999 y se lleva puesta la memoria del
    # proceso. Es la misma regla de siempre: nunca confies en el cliente.
    #
    # Y fijate que la validacion ocurre ANTES de tocar la base: el 422 sale sin
    # que db.listar llegue a ejecutarse nunca.
    monkeypatch.setattr(db, "hay_persistencia", lambda: True)

    respuesta = client.get(f"/api/historial?limite={limite}")

    assert respuesta.status_code == 422
