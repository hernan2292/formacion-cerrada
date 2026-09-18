"""
Autenticacion de los mensajes de la malla.

Cierra el hueco mas grave que tenia el diseno. La capa 3 tolera que un
INTEGRANTE del enjambre mienta: eso esta resuelto por la asimetria de pares y
la votacion por mediana. Lo que no tolera es que un tercero desde afuera
inyecte mensajes con formato valido, porque puede fabricar los votos que quiera
y arrastrar la mediana a donde le convenga.

Y no es un adversario hipotetico: alguien capaz de falsificar la senal
satelital es exactamente alguien capaz de transmitir en la banda de la malla.

Dos mecanismos, los dos necesarios:

  FIRMA      HMAC-SHA256 con una clave precompartida por la formacion. Sin la
             clave no se puede fabricar un mensaje que el resto acepte. No hace
             falta infraestructura de clave publica para una salida de cinco
             aeronaves: la clave se carga en tierra antes del vuelo.

  CONTADOR   Un numero de secuencia que solo crece. Sin esto, un atacante que
             no puede FABRICAR mensajes puede igual grabar los de hace treinta
             segundos y reenviarlos, y un mensaje viejo es perfectamente valido
             criptograficamente. El contador convierte el reenvio en algo que
             el receptor descarta sin mirar.

La comparacion de las firmas usa hmac.compare_digest y no el operador de
igualdad, para no filtrar informacion por el tiempo que tarda en fallar.
"""

from __future__ import annotations

import hmac
import json
import os
import struct
import time
from dataclasses import dataclass, field
from hashlib import sha256

# Bytes de la clave precompartida de la formacion.
LARGO_CLAVE = 32
# Ventana de contadores que se acepta por encima del ultimo visto. Sirve para
# tolerar reordenamiento y perdida sin abrir la puerta al reenvio.
VENTANA_CONTADOR = 256
# Antiguedad maxima de un mensaje, en segundos. Segunda barrera contra reenvio
# para el caso de un nodo que se reinicia y pierde su tabla de contadores.
ANTIGUEDAD_MAXIMA_S = 5.0


class ErrorAutenticacion(Exception):
    """El mensaje no supero la verificacion. Nunca se procesa su contenido."""


def generar_clave() -> bytes:
    """Clave nueva para una formacion. Se genera en tierra, no en vuelo."""
    return os.urandom(LARGO_CLAVE)


def clave_desde_archivo(ruta: str) -> bytes:
    """
    Carga la clave precompartida.

    El archivo tiene que tener permisos restrictivos: si cualquiera puede
    leerlo, cualquiera puede firmar mensajes como si fuera un integrante de la
    formacion, y toda la autenticacion no sirve de nada.
    """
    with open(ruta, "rb") as f:
        clave = f.read().strip()
    if len(clave) < LARGO_CLAVE:
        raise ErrorAutenticacion(
            f"clave demasiado corta: {len(clave)} bytes, se esperaban {LARGO_CLAVE}"
        )
    return clave


@dataclass
class Sobre:
    """Un mensaje de la malla, ya autenticado."""

    emisor: int
    contador: int
    instante: float
    contenido: dict


@dataclass
class Autenticador:
    """
    Firma y verifica mensajes de la malla.

    Una instancia por dron. Lleva su propio contador de emision y la tabla de
    ultimos contadores vistos de cada vecino.
    """

    clave: bytes
    indice_propio: int
    _contador: int = 0
    _ultimo_visto: dict[int, int] = field(default_factory=dict)

    # ------------------------------------------------------------------ firma
    def empaquetar(self, contenido: dict) -> bytes:
        """Arma un mensaje firmado listo para transmitir."""
        self._contador += 1
        cuerpo = json.dumps(
            {
                "emisor": self.indice_propio,
                "contador": self._contador,
                "instante": time.time(),
                "contenido": contenido,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        firma = hmac.new(self.clave, cuerpo, sha256).digest()
        return struct.pack("!H", len(firma)) + firma + cuerpo

    # ------------------------------------------------------------- verificacion
    def desempaquetar(self, crudo: bytes) -> Sobre:
        """
        Verifica y devuelve el mensaje. Levanta ErrorAutenticacion si algo no
        cierra, y en ese caso el contenido NO se toca.
        """
        if len(crudo) < 2:
            raise ErrorAutenticacion("mensaje truncado")

        (largo,) = struct.unpack("!H", crudo[:2])
        firma = crudo[2 : 2 + largo]
        cuerpo = crudo[2 + largo :]

        esperada = hmac.new(self.clave, cuerpo, sha256).digest()
        # compare_digest y no ==: la comparacion tiene que tardar lo mismo
        # cierre o no cierre.
        if not hmac.compare_digest(firma, esperada):
            raise ErrorAutenticacion("firma invalida")

        try:
            datos = json.loads(cuerpo.decode("utf-8"))
            emisor = int(datos["emisor"])
            contador = int(datos["contador"])
            instante = float(datos["instante"])
            contenido = datos["contenido"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ErrorAutenticacion(f"cuerpo mal formado: {exc}") from exc

        # --- reenvio por contador
        ultimo = self._ultimo_visto.get(emisor)
        if ultimo is not None:
            if contador <= ultimo:
                raise ErrorAutenticacion(
                    f"contador repetido o viejo del nodo {emisor}: {contador} <= {ultimo}"
                )
            if contador > ultimo + VENTANA_CONTADOR:
                raise ErrorAutenticacion(
                    f"salto de contador fuera de ventana del nodo {emisor}"
                )

        # --- reenvio por antiguedad, para el nodo que acaba de reiniciarse y
        #     todavia no tiene tabla de contadores
        if abs(time.time() - instante) > ANTIGUEDAD_MAXIMA_S:
            raise ErrorAutenticacion(f"mensaje fuera de ventana temporal del nodo {emisor}")

        self._ultimo_visto[emisor] = contador
        return Sobre(
            emisor=emisor, contador=contador, instante=instante, contenido=contenido
        )
