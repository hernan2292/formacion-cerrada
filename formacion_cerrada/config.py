"""
Configuracion de despliegue.

Todo lo que cambia entre una aeronave y otra, y entre el banco y el campo, vive
aca. El nucleo de deteccion no lee este archivo: lo leen los adaptadores.

Se carga de un YAML o de variables de entorno. En vuelo conviene el archivo,
porque queda registrado junto con el vuelo y se puede auditar despues.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# MODOS DE OPERACION
#
# El unico modo seguro por defecto es OBSERVACION, y conviene entender por que
# antes de cambiarlo.
#
#   OBSERVACION  El sistema mide, detecta, registra y avisa. NO le escribe nada
#                a la controladora de vuelo. La aeronave vuela exactamente como
#                volaria sin esto instalado.
#
#   ASISTIDO     El sistema puede pedirle a la controladora que cambie de
#                fuente de navegacion, pero solo con un operador que lo
#                autorice desde la estacion de tierra en el momento.
#
#   AUTONOMO     El sistema actua solo sobre la navegacion.
#
# Este codigo NO fue validado contra una aeronave real. Pasar a ASISTIDO o
# AUTONOMO sin completar la lista de verificacion de MONTAJE.md significa dejar
# que software no probado gobierne la navegacion de algo que vuela.
# ---------------------------------------------------------------------------
OBSERVACION = "observacion"
ASISTIDO = "asistido"
AUTONOMO = "autonomo"

MODOS_VALIDOS = (OBSERVACION, ASISTIDO, AUTONOMO)


@dataclass
class ConfigAeronave:
    # --- identidad dentro de la formacion
    indice: int = 0
    total_nodos: int = 5
    identificador: str = "D-01"

    # --- modo de operacion
    modo: str = OBSERVACION

    # --- enlace con la controladora de vuelo
    # En una Raspberry Pi conectada al puerto TELEM2 de una Pixhawk, el
    # dispositivo suele ser /dev/serial0 o /dev/ttyAMA0.
    mavlink_dispositivo: str = "/dev/serial0"
    mavlink_baudios: int = 921600

    # --- radio de distancias entre pares
    uwb_dispositivo: str = "/dev/ttyUSB0"
    uwb_baudios: int = 115200

    # --- malla del enjambre
    malla_grupo: str = "239.7.7.7"  # multidifusion local
    malla_puerto: int = 47700
    malla_interfaz: str = "0.0.0.0"
    # Ruta de la clave precompartida de la formacion. Sin esto, cualquiera que
    # alcance la red puede hacerse pasar por un integrante.
    clave_malla: str = "/etc/formacion-cerrada/clave"

    # --- ritmo
    hercios: float = 10.0

    # --- registro
    directorio_registro: str = "/var/log/formacion-cerrada"
    # Guardar cada ciclo permite reconstruir despues que vio el sistema. En una
    # prueba piloto es lo que convierte un vuelo en evidencia.
    registrar_crudos: bool = True

    def validar(self) -> list[str]:
        """Devuelve la lista de problemas. Vacia significa configuracion sana."""
        problemas: list[str] = []
        if self.modo not in MODOS_VALIDOS:
            problemas.append(f"modo desconocido: {self.modo!r}")
        if not 0 <= self.indice < self.total_nodos:
            problemas.append(f"indice {self.indice} fuera de rango para {self.total_nodos} nodos")
        if self.total_nodos < 4:
            problemas.append(
                f"{self.total_nodos} nodos: la prueba de rigidez necesita al menos 4, "
                "y 5 para tolerar a uno que mienta"
            )
        if not os.path.exists(self.clave_malla):
            problemas.append(f"no existe la clave de malla en {self.clave_malla}")
        elif os.name == "posix":
            modo_archivo = os.stat(self.clave_malla).st_mode & 0o777
            if modo_archivo & 0o077:
                problemas.append(
                    f"la clave {self.clave_malla} es legible por otros usuarios "
                    f"({modo_archivo:o}); deberia ser 600"
                )
        if self.modo != OBSERVACION:
            problemas.append(
                f"modo {self.modo}: este software no fue validado contra una aeronave "
                "real. Revisar la lista de verificacion de MONTAJE.md antes de volar."
            )
        return problemas


def desde_yaml(ruta: str) -> ConfigAeronave:
    """Carga la configuracion de un archivo. Requiere PyYAML."""
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("falta PyYAML: pip install pyyaml") from exc

    with open(ruta, "r", encoding="utf-8") as f:
        datos = yaml.safe_load(f) or {}

    conocidos = {c.name for c in ConfigAeronave.__dataclass_fields__.values()}
    desconocidos = set(datos) - conocidos
    if desconocidos:
        raise ValueError(f"claves desconocidas en {ruta}: {sorted(desconocidos)}")
    return ConfigAeronave(**datos)
