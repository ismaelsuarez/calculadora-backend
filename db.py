"""
Persistencia OPCIONAL del historial de operaciones.

La palabra importante es OPCIONAL, y conviene entender por que antes de leer
una linea de codigo.

Si la variable de entorno DATABASE_URL no esta definida, este modulo entero se
queda dormido y la API funciona exactamente como funcionaba antes: calcula,
responde, y no guarda nada. Nadie se rompe. Eso permite tres cosas:

  1. Que puedas desarrollar en tu maquina sin levantar un Postgres.
  2. Que el deploy que hiciste antes de agregar la base siga andando igual.
  3. Que si la base se cae un domingo, la calculadora SIGA CALCULANDO.

El punto 3 no es un detalle didactico, es una decision de arquitectura:
guardar el historial es una funcionalidad SECUNDARIA. Calcular es lo que la
aplicacion hace. Una funcionalidad secundaria nunca puede tumbar a la
principal. Se degrada, no explota.

Como se conecta en produccion (Easypanel):

    DATABASE_URL=postgres://usuario:clave@calculadora_db:5432/calculadora
                                          ^^^^^^^^^^^^^^
                            el nombre INTERNO del servicio, no un dominio

Ese nombre solo existe adentro de la red del proyecto. Si lo escribis en el
navegador no resuelve, y esta perfecto que no resuelva: el puerto 5432 nunca
se publico hacia afuera. El puerto mas seguro es el que no existe.
"""

import logging
import os
from typing import Any

logger = logging.getLogger("calculadora.db")

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# El pool de conexiones. Arranca en None y solo se llena si iniciar() tuvo
# exito. Todo el resto del modulo consulta esta variable para saber si hay
# persistencia disponible.
_pool: Any = None


# La tabla se crea sola al arrancar si no existe.
#
# Para un proyecto de esta escala esto alcanza y sobra. Cuando el esquema
# empiece a cambiar (agregar una columna, renombrar otra) esto se queda corto
# y entra en juego una herramienta de migraciones — Alembic, en el mundo de
# Python. No la usamos aca porque el esquema es una sola tabla que no va a
# cambiar, y meter una herramienta de migraciones para esto seria enseñar
# ceremonia en vez de concepto.
CREAR_TABLA = """
CREATE TABLE IF NOT EXISTS historial (
    id         BIGSERIAL PRIMARY KEY,
    a          DOUBLE PRECISION NOT NULL,
    b          DOUBLE PRECISION NOT NULL,
    operacion  TEXT NOT NULL,
    simbolo    TEXT NOT NULL,
    resultado  DOUBLE PRECISION NOT NULL,
    expresion  TEXT NOT NULL,
    creado_en  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def hay_persistencia() -> bool:
    """
    ¿Se puede guardar y leer historial ahora mismo?

    La usan el healthcheck y el endpoint de historial. Es una sola linea, pero
    tener el estado detras de una funcion (y no leyendo la variable global
    desde media aplicacion) permite cambiar como se decide sin tocar a nadie.
    """
    return _pool is not None


def iniciar() -> None:
    """
    Abre el pool de conexiones y crea la tabla. Se llama UNA vez, al arrancar.

    Fijate que el import de psycopg esta ACA ADENTRO y no arriba de todo. Eso
    es a proposito: si no hay DATABASE_URL, ni siquiera intentamos importar la
    libreria. Asi, alguien que clona el repo para tocar el frontend y no
    reinstalo las dependencias, igual puede levantar la API.

    ¿Por que un POOL y no abrir una conexion por pedido? Porque establecer una
    conexion a Postgres cuesta bastante mas que la consulta que vas a hacer:
    hay handshake TCP, autenticacion y arranque de un proceso del lado del
    servidor. Un pool las abre una vez y las presta. Abrir y cerrar una
    conexion por request es de los errores de performance mas comunes y mas
    invisibles que existen: no falla, solo va lento.
    """
    global _pool

    if not DATABASE_URL:
        logger.info(
            "DATABASE_URL no esta definida. La API arranca SIN historial. "
            "Esto es valido: la calculadora funciona igual."
        )
        return

    try:
        from psycopg_pool import ConnectionPool

        pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=4, open=False)

        # wait=True hace que esto espere a que la base este realmente
        # disponible. Hace falta porque en un deploy los contenedores arrancan
        # todos juntos y la API suele estar lista antes que Postgres.
        pool.open(wait=True, timeout=15)

        with pool.connection() as conexion:
            conexion.execute(CREAR_TABLA)

        _pool = pool
        logger.info("Historial habilitado: conexion a la base establecida.")

    except Exception:
        # OJO CON ESTO: si la base no esta, la API NO se cae. Registra el
        # problema con todo el detalle y arranca sin historial.
        #
        # La alternativa —reventar al arrancar— dejaria la calculadora entera
        # fuera de servicio por no poder guardar una fila. Eso es exactamente
        # lo que no queremos.
        logger.exception(
            "No se pudo conectar a la base. La API arranca SIN historial. "
            "Revisá DATABASE_URL y que el servicio de base este levantado."
        )
        _pool = None


def cerrar() -> None:
    """Cierra el pool al apagar la aplicacion. Buena educacion con la base."""
    global _pool

    if _pool is not None:
        _pool.close()
        _pool = None


def guardar(
    *,
    a: float,
    b: float,
    operacion: str,
    simbolo: str,
    resultado: float,
    expresion: str,
) -> None:
    """
    Guarda una operacion. NUNCA lanza excepcion.

    Esto ultimo es el contrato mas importante de la funcion, y esta puesto a
    proposito. La llama el endpoint de calcular, DESPUES de haber calculado
    bien. Si guardar fallara y esa excepcion subiera, el usuario recibiria un
    error por una cuenta que en realidad salio perfecta.

    Los argumentos van con * adelante, o sea que son obligatoriamente por
    nombre. Con seis parametros del mismo tipo, permitir llamarla por posicion
    es una invitacion a mandar `b` donde va `a` y no enterarte nunca.
    """
    if _pool is None:
        return

    try:
        with _pool.connection() as conexion:
            conexion.execute(
                """
                INSERT INTO historial (a, b, operacion, simbolo, resultado, expresion)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (a, b, operacion, simbolo, resultado, expresion),
            )
    except Exception:
        # Se registra en el log del servidor, donde lo ve quien programa.
        # El usuario no se entera de nada, y hace bien: su cuenta salio bien.
        logger.exception("No se pudo guardar la operacion en el historial.")


def listar(limite: int) -> list[dict]:
    """
    Devuelve las ultimas operaciones, de la mas reciente a la mas vieja.

    Fijate en los %s de la consulta: NO son formato de Python. Son marcadores
    que psycopg reemplaza del lado del driver, escapando el valor.

    Nunca, jamas, bajo ninguna circunstancia armes una consulta SQL
    concatenando strings o con un f-string. Asi es como se hace una inyeccion
    SQL, que sigue siendo despues de veinticinco años una de las
    vulnerabilidades mas explotadas del mundo. El limite de esta funcion viene
    validado por FastAPI ademas, pero la regla vale igual: los valores van
    SIEMPRE por parametro.
    """
    if _pool is None:
        return []

    with _pool.connection() as conexion:
        filas = conexion.execute(
            """
            SELECT a, b, operacion, simbolo, resultado, expresion, creado_en
            FROM historial
            ORDER BY id DESC
            LIMIT %s
            """,
            (limite,),
        ).fetchall()

    return [
        {
            "a": fila[0],
            "b": fila[1],
            "operacion": fila[2],
            "simbolo": fila[3],
            "resultado": fila[4],
            "expresion": fila[5],
            "creado_en": fila[6],
        }
        for fila in filas
    ]
