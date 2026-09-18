"""
Adaptador de entrada y salida: el nucleo de deteccion hacia el tablero.

Este es el unico modulo que sabe que existe una interfaz web. El nucleo no
cambia entre el banco y la aeronave; lo que cambia es este archivo, y en campo
se reemplaza por el que habla MAVLink con la controladora de vuelo.

    python -m formacion_cerrada.puente.servidor

Protocolo, deliberadamente minimo:
    servidor -> cliente   el estado completo del enjambre, 10 veces por segundo
    cliente  -> servidor  {"accion": "ataque", "tipo": "coherent"}
                          {"accion": "reiniciar"}
                          {"accion": "perdida", "valor": 0.3}
                          {"accion": "defensa", "activa": false}
"""

from __future__ import annotations

import asyncio
import json

import websockets

from ..simulador.ataques import DESCRIPCIONES, SIN_ATAQUE
from .simulacion import SimulacionEnjambre

PUERTO = 8765
HERCIOS = 10.0

simulacion = SimulacionEnjambre()
clientes: set = set()


async def _lazo() -> None:
    """Corre el enjambre a ritmo fijo y difunde el estado."""
    dt = 1.0 / HERCIOS
    while True:
        estado = simulacion.paso(dt)
        if clientes:
            mensaje = json.dumps(estado)
            await asyncio.gather(
                *(c.send(mensaje) for c in list(clientes)), return_exceptions=True
            )
        await asyncio.sleep(dt)


async def _atender(websocket) -> None:
    clientes.add(websocket)
    try:
        await websocket.send(
            json.dumps({"tipo_mensaje": "bienvenida", "ataques": DESCRIPCIONES})
        )
        async for crudo in websocket:
            try:
                orden = json.loads(crudo)
            except json.JSONDecodeError:
                continue
            accion = orden.get("accion")
            if accion == "ataque":
                tipo = orden.get("tipo", SIN_ATAQUE)
                if tipo in DESCRIPCIONES or tipo == SIN_ATAQUE:
                    simulacion.inyectar(tipo)
            elif accion == "reiniciar":
                simulacion.reiniciar()
            elif accion == "defensa":
                # El antes y despues. Se reinicia para que la comparacion
                # arranque del mismo estado: si se conmuta a mitad de un ataque,
                # lo que se ve es una transicion, no una comparacion.
                simulacion.defensa = bool(orden.get("activa", True))
                simulacion.reiniciar()
            elif accion == "perdida":
                simulacion.perdida_malla = float(orden.get("valor", 0.0))
                simulacion.malla.perdida = simulacion.perdida_malla
    except websockets.ConnectionClosed:
        pass
    finally:
        clientes.discard(websocket)


async def principal() -> None:
    print(f"Nucleo de deteccion escuchando en ws://localhost:{PUERTO}")
    print(f"Enjambre de {simulacion.n} nodos a {HERCIOS:.0f} Hz. Ctrl+C para cortar.")
    async with websockets.serve(_atender, "0.0.0.0", PUERTO):
        await _lazo()


def main() -> None:
    """Punto de entrada del comando `formacion-cerrada-banco`."""
    try:
        asyncio.run(principal())
    except KeyboardInterrupt:
        print(chr(10) + "Cortado.")


if __name__ == "__main__":
    main()
