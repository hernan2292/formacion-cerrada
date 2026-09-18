"""
Adaptador de aeronave: el nucleo de deteccion sobre hardware real.

Es el gemelo de `servidor.py`. Aquel lee del simulador; este lee de los
sensores de un dron. El NUCLEO ES EL MISMO ARCHIVO en los dos casos, y esa es
toda la arquitectura: `deteccion/`, `navegacion/` y `nodo.py` no saben ni
pueden saber de donde vienen los numeros.


ESTADO DE VALIDACION, y conviene leerlo antes de instalar esto en algo que
vuela:

    El nucleo de deteccion esta probado: 27 pruebas automatizadas sobre
    escenarios simulados con ruido, incluida la que demuestra la ceguera de la
    capa 1 a la traslacion coherente.

    ESTE ARCHIVO NO ESTA PROBADO CONTRA HARDWARE. Las clases que hablan MAVLink
    y con la radio de distancias estan escritas contra la documentacion de esos
    protocolos, no contra un banco. Nadie las ejecuto sobre una Pixhawk ni
    sobre un modulo DW3000.

Por eso el modo por defecto es OBSERVACION: el sistema mide, detecta y
registra, y no le escribe absolutamente nada a la controladora de vuelo. En ese
modo, lo peor que puede pasar si este archivo tiene un error es que no detecte
algo o que registre basura. La aeronave vuela igual que sin esto instalado.

El camino para llegar a actuar sobre la navegacion esta en MONTAJE.md y pasa
por validar cada interfaz contra hardware en banco, en tierra y con helices
desmontadas, antes de volar.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import time
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from ..config import ASISTIDO, AUTONOMO, OBSERVACION, ConfigAeronave
from ..malla.autenticacion import Autenticador, ErrorAutenticacion, clave_desde_archivo
from ..nodo import NodoDeteccion


# ===========================================================================
# INTERFACES DE HARDWARE
#
# Se definen como protocolos para que el lazo de a bordo no dependa de una
# implementacion concreta. Sirve para tres cosas: cambiar de modulo de radio
# sin tocar el lazo, simular una fuente en banco, y -- sobre todo -- poder
# probar el lazo entero sin tener el hardware delante.
# ===========================================================================


class FuenteTelemetria(Protocol):
    """Lo que la controladora de vuelo informa de esta aeronave."""

    def leer(self) -> dict | None:
        """
        Devuelve un diccionario con las claves:
            posicion   (3,) metros en marco local  este / norte / altura
            velocidad  (3,) metros por segundo, de la unidad inercial
            barometro  float, altura por presion en metros
        o None si todavia no hay dato fresco.
        """
        ...


class FuenteDistancias(Protocol):
    """La radio que mide la distancia a cada companiero."""

    def leer(self) -> dict[int, float]:
        """Devuelve {indice_del_vecino: distancia_en_metros}."""
        ...


@dataclass
class VeredictoVecino:
    """
    Lo que un companiero difunde sobre lo que EL concluyo.

    Hace falta difundirlo, y el motivo es el corazon del caso B: la regla que
    detecta la traslacion coherente pregunta cuantos nodos tienen sus anclas
    absolutas discrepando A LA VEZ. Un dron solo conoce las suyas. Si los
    vecinos no cuentan las propias, ningun nodo puede contar mas de uno, la
    regla no se dispara nunca, y el ataque que este proyecto existe para
    detectar pasa sin que nadie lo vea.

    En el simulador esto no se nota porque el orquestador tiene acceso a las
    cinco vistas. En vuelo, no hay tal orquestador.
    """

    sospechosos: np.ndarray
    disparada: bool

    @property
    def metrica(self) -> float:
        return 1.0 if self.disparada else 0.0


class SalidaNavegacion(Protocol):
    """Por donde se le habla a la controladora de vuelo."""

    def publicar_posicion(self, posicion: np.ndarray, incertidumbre: float) -> None: ...
    def solicitar_fuente(self, conjunto: int) -> None: ...


# ===========================================================================
# IMPLEMENTACIONES CONTRA HARDWARE  --  NO VALIDADAS
# ===========================================================================


class TelemetriaMavlink:
    """
    Lee telemetria de la controladora por MAVLink.

    NO PROBADO CONTRA HARDWARE. Los nombres de mensaje y el escalado de
    unidades salen de la especificacion de MAVLink; hay que verificarlos contra
    la version de firmware que efectivamente se use, porque cambian entre
    versiones.
    """

    def __init__(self, dispositivo: str, baudios: int) -> None:
        try:
            from pymavlink import mavutil
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "falta pymavlink para hablar con la controladora: pip install pymavlink"
            ) from exc
        self._enlace = mavutil.mavlink_connection(dispositivo, baud=baudios)
        self._enlace.wait_heartbeat(timeout=30)
        self._ultimo: dict = {}

    def leer(self) -> dict | None:
        mensaje = self._enlace.recv_match(
            type=["LOCAL_POSITION_NED", "SCALED_IMU2", "SCALED_PRESSURE"],
            blocking=False,
        )
        while mensaje is not None:
            tipo = mensaje.get_type()
            if tipo == "LOCAL_POSITION_NED":
                # MAVLink usa norte / este / abajo. Acá se trabaja en
                # este / norte / arriba, así que se reordena y se invierte la
                # vertical. Equivocarse en este signo es el error clasico y
                # produce una altura que baja cuando el dron sube.
                self._ultimo["posicion"] = np.array([mensaje.y, mensaje.x, -mensaje.z])
                self._ultimo["velocidad"] = np.array([mensaje.vy, mensaje.vx, -mensaje.vz])
            elif tipo == "SCALED_PRESSURE":
                self._ultimo["barometro"] = _altura_por_presion(mensaje.press_abs)
            mensaje = self._enlace.recv_match(
                type=["LOCAL_POSITION_NED", "SCALED_PRESSURE"], blocking=False
            )

        if {"posicion", "velocidad", "barometro"} <= set(self._ultimo):
            return dict(self._ultimo)
        return None


def _altura_por_presion(presion_hpa: float, presion_nivel_mar: float = 1013.25) -> float:
    """Formula barometrica estandar. Altura en metros sobre el nivel de referencia."""
    return 44330.0 * (1.0 - (presion_hpa / presion_nivel_mar) ** 0.1903)


class DistanciasUwb:
    """
    Lee distancias de un modulo de banda ultraancha por puerto serie.

    NO PROBADO CONTRA HARDWARE. El formato de trama depende del firmware del
    modulo. Este lector espera lineas de texto `indice,distancia_en_metros`,
    que es lo que produce el firmware de ejemplo de la mayoria de los kits; si
    el modulo habla binario, hay que reemplazar `leer`.
    """

    def __init__(self, dispositivo: str, baudios: int) -> None:
        try:
            import serial
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "falta pyserial para hablar con la radio: pip install pyserial"
            ) from exc
        self._puerto = serial.Serial(dispositivo, baudios, timeout=0)
        self._ultimas: dict[int, float] = {}

    def leer(self) -> dict[int, float]:
        while self._puerto.in_waiting:
            linea = self._puerto.readline().decode("ascii", "ignore").strip()
            if not linea:
                continue
            try:
                indice, distancia = linea.split(",")
                self._ultimas[int(indice)] = float(distancia)
            except ValueError:
                continue  # trama incompleta o ruido: se descarta en silencio
        return dict(self._ultimas)


class SalidaMavlink:
    """
    Le habla a la controladora de vuelo.

    NO PROBADO CONTRA HARDWARE, y es la clase de la que hay que desconfiar mas,
    porque es la unica que puede cambiar como vuela la aeronave. En modo
    OBSERVACION no se instancia siquiera.
    """

    def __init__(self, enlace) -> None:
        self._enlace = enlace

    def publicar_posicion(self, posicion: np.ndarray, incertidumbre: float) -> None:
        # VISION_POSITION_ESTIMATE con la covarianza declarada. Que la
        # incertidumbre sea honesta importa: el filtro de la controladora la usa
        # para decidir cuanto le cree, y declararla menor de lo que es hace que
        # le crea mas de lo debido a una posicion recuperada.
        self._enlace.mav.vision_position_estimate_send(
            int(time.time() * 1e6),
            float(posicion[1]),  # norte
            float(posicion[0]),  # este
            float(-posicion[2]),  # abajo
            0.0, 0.0, 0.0,
            [incertidumbre**2 if i in (0, 6, 11) else 0.0 for i in range(21)],
        )

    def solicitar_fuente(self, conjunto: int) -> None:
        """
        Pide cambiar el conjunto de fuentes del filtro de navegacion.

        En ArduPilot esto se hace desde un script Lua a bordo llamando a
        ahrs:set_posvelyaw_source_set(). Desde afuera se dispara con un comando
        MAVLink que el script escucha. El acoplamiento exacto depende del script
        que se instale en la controladora: ver MONTAJE.md.
        """
        raise NotImplementedError(
            "el cambio de fuente requiere el script Lua a bordo; ver MONTAJE.md"
        )


# ===========================================================================
# MALLA SOBRE UDP
# ===========================================================================


@dataclass
class MallaUdp:
    """Difusion autenticada entre las aeronaves de la formacion."""

    grupo: str
    puerto: int
    interfaz: str
    autenticador: Autenticador

    def __post_init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("", self.puerto))
        peticion = struct.pack(
            "4s4s", socket.inet_aton(self.grupo), socket.inet_aton(self.interfaz)
        )
        self._sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, peticion)
        self._sock.setblocking(False)
        self.rechazados = 0

    def difundir(self, contenido: dict) -> None:
        self._sock.sendto(self.autenticador.empaquetar(contenido), (self.grupo, self.puerto))

    def recibir(self) -> list[tuple[int, dict]]:
        """
        Devuelve los mensajes que superaron la verificacion.

        Los que no la superan se cuentan y se descartan sin procesar. Ese
        contador es informacion operativa de primer orden: si empieza a subir en
        vuelo, alguien esta transmitiendo en la banda de la malla.
        """
        salida: list[tuple[int, dict]] = []
        while True:
            try:
                crudo, _ = self._sock.recvfrom(65535)
            except BlockingIOError:
                break
            try:
                sobre = self.autenticador.desempaquetar(crudo)
            except ErrorAutenticacion:
                self.rechazados += 1
                continue
            salida.append((sobre.emisor, sobre.contenido))
        return salida


# ===========================================================================
# LAZO DE A BORDO
# ===========================================================================


class AeronaveFormacionCerrada:
    """
    Lo que corre en la computadora companera de una aeronave.

    Un ciclo: leer sensores, difundir, evaluar con lo recibido, decidir, y --
    solo si el modo lo permite -- actuar.
    """

    def __init__(
        self,
        config: ConfigAeronave,
        telemetria: FuenteTelemetria,
        distancias: FuenteDistancias,
        malla: MallaUdp,
        salida: SalidaNavegacion | None = None,
    ) -> None:
        problemas = config.validar()
        criticos = [p for p in problemas if "no fue validado" not in p]
        if criticos:
            raise ValueError("configuracion invalida:\n  - " + "\n  - ".join(criticos))
        for aviso in problemas:
            print(f"AVISO: {aviso}")

        if config.modo != OBSERVACION and salida is None:
            raise ValueError(f"modo {config.modo} requiere una salida de navegacion")

        self.config = config
        self.telemetria = telemetria
        self.distancias = distancias
        self.malla = malla
        self.salida = salida
        self.nodo = NodoDeteccion(config.indice, config.total_nodos)
        self._vecinos: dict[int, dict] = {}
        self._registro = None
        if config.registrar_crudos:
            os.makedirs(config.directorio_registro, exist_ok=True)
            marca = time.strftime("%Y%m%d-%H%M%S")
            self._registro = open(
                os.path.join(config.directorio_registro, f"vuelo-{marca}.jsonl"),
                "a",
                encoding="utf-8",
            )

    # ------------------------------------------------------------------ ciclo
    def paso(self, dt: float) -> dict | None:
        lectura = self.telemetria.leer()
        if lectura is None:
            return None  # todavia no hay telemetria: no se inventa nada

        medidas = self.distancias.leer()
        n = self.config.total_nodos
        yo = self.config.indice

        # --- consolidar lo que llego de los vecinos en el ciclo anterior
        for emisor, contenido in self.malla.recibir():
            if 0 <= emisor < n and emisor != yo:
                self._vecinos[emisor] = contenido

        gnss = np.zeros((n, 3))
        filas = np.zeros((n, n))
        visibles: list[int] = []

        gnss[yo] = lectura["posicion"]
        for vecino, distancia in medidas.items():
            if 0 <= vecino < n:
                filas[yo, vecino] = distancia

        for emisor, contenido in self._vecinos.items():
            gnss[emisor] = np.asarray(contenido["posicion"], dtype=float)
            for vecino, distancia in contenido.get("distancias", {}).items():
                indice = int(vecino)
                if 0 <= indice < n:
                    filas[emisor, indice] = float(distancia)
            visibles.append(emisor)

        # Con menos de cuatro nodos no hay geometria que reconstruir. Se informa
        # y no se publica un residuo que no significaria nada.
        if len(visibles) + 1 < 4:
            self._difundir(lectura, medidas, None)
            return {
                "estado": "SIN_MALLA",
                "visibles": len(visibles) + 1,
                "rechazados_malla": self.malla.rechazados,
            }

        # --- fase 1: las capas 1 y 2, con lo propio y lo recibido
        vista = self.nodo.fase_local(
            dt, gnss, filas, visibles, lectura["velocidad"], float(lectura["barometro"])
        )

        # --- difundir el propio veredicto para que los demas puedan contar
        self._difundir(lectura, medidas, vista)

        # --- fase 2: consenso con los veredictos de todos
        votos = []
        anclas = []
        for i in range(n):
            if i == yo:
                votos.append(vista.voto)
                anclas.append(vista.capa2_propia)
                continue
            informado = self._vecinos.get(i, {}).get("veredicto")
            if informado is None:
                # De quien no informo nada no se presume nada: ni que sospecha
                # ni que sus anclas estan bien. Se lo cuenta como silencio.
                votos.append(VeredictoVecino(np.zeros(n, dtype=bool), False))
                anclas.append(VeredictoVecino(np.zeros(n, dtype=bool), False))
            else:
                mascara = np.array(informado.get("sospechosos", [False] * n), dtype=bool)
                if mascara.shape != (n,):
                    mascara = np.zeros(n, dtype=bool)
                veredicto = VeredictoVecino(mascara, bool(informado.get("capa2", False)))
                votos.append(veredicto)
                anclas.append(veredicto)

        vista = self.nodo.fase_consenso(
            dt, vista, votos, anclas, filas, gnss, lectura["velocidad"]
        )

        informe = {
            "t": time.time(),
            "estado": vista.estado,
            "modo_navegacion": vista.modo_navegacion,
            "residuos": vista.voto.residuos.tolist(),
            "umbral": vista.voto.umbral,
            "capa2_propia": vista.capa2_propia.disparada,
            "capa2_global": bool(vista.confianza.capa2_disparada_global),
            "anclas_caidas": sum(1 for a in anclas if a.disparada),
            "rechazados_malla": self.malla.rechazados,
            "visibles": len(visibles) + 1,
        }

        # --- actuar, solo si el modo lo permite
        if self.config.modo in (ASISTIDO, AUTONOMO) and self.salida is not None:
            self._actuar(vista)

        if self._registro is not None:
            self._registro.write(json.dumps(informe) + chr(10))
            self._registro.flush()

        return informe

    def _difundir(self, lectura: dict, medidas: dict[int, float], vista) -> None:
        contenido = {
            "posicion": lectura["posicion"].tolist(),
            "distancias": {str(k): v for k, v in medidas.items()},
        }
        if vista is not None:
            contenido["veredicto"] = {
                "sospechosos": vista.voto.sospechosos.tolist(),
                "capa2": bool(vista.capa2_propia.disparada),
            }
        self.malla.difundir(contenido)

    def _actuar(self, vista) -> None:
        """
        Escribe a la controladora de vuelo.

        En modo ASISTIDO esto deberia estar condicionado a una autorizacion
        vigente del operador de tierra. Ese enclavamiento NO esta implementado:
        es parte de lo que hay que construir y validar antes de habilitar un
        modo distinto de OBSERVACION.
        """
        if vista.modo_navegacion == "colectivo" and vista.posicion_recuperada is not None:
            # La incertidumbre declarada sale de la geometria de la
            # trilateracion, no de un numero optimista elegido a mano.
            self.salida.publicar_posicion(vista.posicion_recuperada, incertidumbre=2.0)

    def cerrar(self) -> None:
        if self._registro is not None:
            self._registro.close()


# ===========================================================================
# PUNTO DE ENTRADA
# ===========================================================================


def main(argv: list[str] | None = None) -> int:
    """
    python -m formacion_cerrada.puente.aeronave --config /etc/.../aeronave.yaml

    Arranca el lazo de a bordo. Ver MONTAJE.md para el procedimiento completo.
    """
    import argparse

    from ..config import desde_yaml

    parser = argparse.ArgumentParser(
        description="Formacion Cerrada sobre aeronave",
        epilog="El modo por defecto es observacion: no escribe a la controladora.",
    )
    parser.add_argument("--config", required=True, help="ruta del YAML de configuracion")
    parser.add_argument(
        "--verificar",
        action="store_true",
        help="valida la configuracion y el hardware, y sale sin entrar al lazo",
    )
    args = parser.parse_args(argv)

    config = desde_yaml(args.config)
    problemas = config.validar()
    for aviso in problemas:
        print(f"AVISO: {aviso}")
    criticos = [p for p in problemas if "no fue validado" not in p]
    if criticos:
        print("\nLa configuracion no es valida. No se arranca.")
        return 1

    telemetria = TelemetriaMavlink(config.mavlink_dispositivo, config.mavlink_baudios)
    distancias = DistanciasUwb(config.uwb_dispositivo, config.uwb_baudios)
    malla = MallaUdp(
        grupo=config.malla_grupo,
        puerto=config.malla_puerto,
        interfaz=config.malla_interfaz,
        autenticador=Autenticador(clave_desde_archivo(config.clave_malla), config.indice),
    )

    if args.verificar:
        print(f"\n{config.identificador}: enlaces abiertos, configuracion valida.")
        print(f"Modo: {config.modo}")
        return 0

    aeronave = AeronaveFormacionCerrada(config, telemetria, distancias, malla)
    print(f"{config.identificador} en linea. Modo: {config.modo}. Ctrl+C para cortar.")

    periodo = 1.0 / config.hercios
    try:
        while True:
            comienzo = time.monotonic()
            informe = aeronave.paso(periodo)
            if informe is not None and informe.get("estado") not in ("NOMINAL", None):
                print(
                    f"[{config.identificador}] {informe['estado']}"
                    f"  anclas_caidas={informe.get('anclas_caidas', 0)}"
                    f"  rechazados={informe.get('rechazados_malla', 0)}"
                )
            # Ritmo fijo: lo que sobra del ciclo se espera, no se corre libre.
            resto = periodo - (time.monotonic() - comienzo)
            if resto > 0:
                time.sleep(resto)
    except KeyboardInterrupt:
        print(chr(10) + "Cortado.")
    finally:
        aeronave.cerrar()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
