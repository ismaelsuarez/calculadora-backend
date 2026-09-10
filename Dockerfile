# Imagen del BACKEND.
#
# Un Dockerfile es una receta: describe una maquina desde cero, paso a paso.
# Easypanel lee este archivo, arma la imagen en el servidor y la corre.
# La ventaja es que la receta es la misma en tu maquina y en produccion, asi
# que "en mi maquina andaba" deja de ser una excusa valida.

FROM python:3.12-slim

WORKDIR /app

# Copiamos SOLO requirements.txt primero, y recien despues el codigo.
#
# ¿Por que en dos pasos si podriamos copiar todo junto? Por el cache de capas.
# Docker guarda el resultado de cada linea y lo reutiliza si nada cambio.
# Si copiaras todo junto, cualquier cambio de una coma en main.py invalidaria
# la capa y volveria a bajar TODAS las dependencias de internet.
# Asi, mientras no toques requirements.txt, el pip install sale del cache.
#
# Fijate que NO copiamos requirements-dev.txt. pytest y httpx solo hacen falta
# para correr los tests, y los tests no se corren en el servidor: se corren en
# la integracion continua, antes de llegar hasta aca. Todo lo que metas en la
# imagen es peso y superficie de ataque.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Los archivos de la aplicacion, uno por uno y a proposito.
#
# Podria ser `COPY . .` y seria mas corto, pero entonces cualquier archivo
# suelto de la carpeta —un .env con credenciales, un dump de la base, notas
# personales— se iria adentro de la imagen sin que nadie se entere. Y una vez
# que entro a una capa, borrarlo en una linea posterior NO lo elimina: la capa
# anterior sigue ahi y es recuperable.
#
# Nombrar lo que entra es mas trabajo y es la opcion correcta.
COPY main.py .
COPY db.py .

# Documenta que la app escucha en el 8000. No abre nada por si solo: es una
# anotacion para quien lea la receta y para Easypanel.
EXPOSE 8000

# OJO ACA, que es el error numero uno al meter una API en un contenedor:
# --host 0.0.0.0, NO 127.0.0.1.
#
# Dentro de un contenedor, 127.0.0.1 significa "solo yo, este contenedor".
# Si uvicorn escucha ahi, nadie de afuera puede entrar: ni Easypanel, ni el
# proxy, ni vos. El servicio arranca, el log dice "Uvicorn running", todo
# parece perfecto... y no responde nunca. 0.0.0.0 significa "escucho en todas
# las interfaces de red que tenga".
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
