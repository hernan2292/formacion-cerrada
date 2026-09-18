"""
Lazo completo: simulador -> malla -> cinco nodos -> estado del enjambre.

Este modulo no sabe nada de la interfaz. Produce un diccionario con lo que el
enjambre concluyo en cada ciclo, y es el servidor el que lo traduce a la forma
que espera el tablero. Esa separacion es a proposito: el nucleo de deteccion
tiene que ser el mismo en el banco y en la aeronave, y lo unico que cambia
entre los dos mundos es el adaptador de entrada y salida.
"""

from __future__ import annotations

import numpy as np

from ..malla.bus import Malla, Paquete
from ..navegacion.maquina_estados import postura_de_formacion
from ..nodo import NodoDeteccion
from ..simulador.ataques import SIN_ATAQUE, Ataque
from ..simulador.cinematica import (
    ETIQUETAS,
    IDENTIFICADORES,
    INDICATIVOS,
    Enjambre,
)
from ..simulador.sensores import BancoSensores


class SimulacionEnjambre:
    def __init__(
        self,
        perdida_malla: float = 0.0,
        semilla: int = 20260917,
        defensa: bool = True,
    ) -> None:
        self.semilla = semilla
        self.perdida_malla = perdida_malla
        # Interruptor del antes y despues. Con la defensa apagada la deteccion
        # SIGUE CORRIENDO y sigue informando -- lo que se desconecta es la
        # consecuencia: cada dron navega con la posicion satelital cruda, crea
        # lo que el atacante le diga, y se va a donde lo lleven.
        self.defensa = defensa
        self.reiniciar()

    # Segundos de vuelo limpio que se corren INTERNAMENTE al reiniciar, antes
    # de devolver el control. Los filtros de las dos capas tienen constantes de
    # tiempo de 2 y 3 segundos, asi que recien asientan despues de unas tres
    # constantes; inyectar un ataque sobre filtros a medio converger da
    # resultados erraticos.
    #
    # Corriendolos aca, el reinicio devuelve un enjambre ya asentado y la demo
    # puede reiniciar e inyectar el ataque siguiente sin esperar. Son 150
    # iteraciones de algebra sobre matrices de 5x5: decimas de segundo.
    SEGUNDOS_ASENTAMIENTO = 15.0

    # Cuanto tiene que sostenerse el disparo de una capa antes de informarlo.
    SEGUNDOS_PARA_INFORMAR = 0.6

    # ------------------------------------------------------------------ mando
    def reiniciar(self) -> None:
        self.enjambre = Enjambre()
        self.n = self.enjambre.n
        self.sensores = BancoSensores(self.n, semilla=self.semilla)
        self.malla = Malla(self.n, perdida=self.perdida_malla, semilla=self.semilla + 1)
        self.ataque = Ataque()
        self.nodos = [NodoDeteccion(i, self.n) for i in range(self.n)]
        self.ciclo = 0
        self._creidas = None
        self._distancias_vistas = None
        self._navegacion_absoluta = None
        self._modos = ["satelital"] * self.n
        self._sostenido = {"c1": 0.0, "c2": 0.0, "c3": 0.0}
        for _ in range(int(self.SEGUNDOS_ASENTAMIENTO / 0.1)):
            self.paso(0.1)

    def inyectar(self, tipo: str) -> None:
        self.ataque.inyectar(tipo)

    # ------------------------------------------------------------------ ciclo
    def paso(self, dt: float) -> dict:
        self.ciclo += 1
        self._cambio_de_modo = getattr(self, '_cambio_de_modo', None)
        # El guiado usa lo que los drones creyeron en el ciclo anterior. Ese
        # retardo de un ciclo es fisico: la decision de navegacion de ahora se
        # toma con la estimacion de recien.
        self.enjambre.avanzar(
            dt,
            self._creidas,
            self._cambio_de_modo,
            self._distancias_vistas,
        )
        self.ataque.avanzar(dt)

        lecturas = self.sensores.medir(
            dt, self.enjambre.posiciones, self.enjambre.velocidades
        )

        # El ataque falsea lo que los receptores INFORMAN. Las aeronaves siguen
        # fisicamente donde estan: lo unico que cambia es lo que creen.
        gnss = self.ataque.aplicar_a_posicion(lecturas["gnss"])
        filas = self.ataque.aplicar_a_distancias(lecturas["filas_uwb"])

        # --- fase 1: cada nodo mide y evalua con lo que le llego por la malla
        emitidos = [
            Paquete(emisor=i, contenido={"fila": filas[i], "gnss": gnss[i]})
            for i in range(self.n)
        ]
        vistas = []
        for nodo in self.nodos:
            recibidos = self.malla.difundir(emitidos, nodo.indice)
            visibles = [p.emisor for p in recibidos]
            vistas.append(
                nodo.fase_local(
                    dt,
                    gnss,
                    filas,
                    visibles,
                    lecturas["vel_imu"][nodo.indice],
                    float(lecturas["baro"][nodo.indice]),
                )
            )

        # --- difusion de votos y de estados de anclas
        votos = [v.voto for v in vistas]
        capa2 = [v.capa2_propia for v in vistas]

        # --- fase 2: consenso, fusion, estados y solucion de navegacion
        for nodo, vista in zip(self.nodos, vistas):
            nodo.fase_consenso(
                dt, vista, votos, capa2, filas, gnss, lecturas["vel_imu"][nodo.indice]
            )

        # --- con que posicion navega cada dron el proximo ciclo
        if self.defensa:
            self._creidas = np.array(
                [
                    v.posicion_navegacion
                    if v.posicion_navegacion is not None
                    else gnss[i]
                    for i, v in enumerate(vistas)
                ]
            )
        else:
            # Sin defensa el dron le cree al satelite pase lo que pase. Es el
            # comportamiento de cualquier aeronave que no lleve esto a bordo.
            self._creidas = gnss.copy()

        # El integrador se reinicia solo cuando cambia la NATURALEZA de la
        # referencia, no en cada matiz de estado. Pasar de satelital a no
        # satelital es un salto real de la posicion creida y hay que soltar el
        # acumulado; alternar entre colectivo e inercial, en cambio, es un
        # cambio menor, y reiniciar en cada uno hace que el lazo nunca asiente.
        # Quien tiene una referencia absoluta creible y quien no.
        self._navegacion_absoluta = np.array(
            [v.modo_navegacion in ("satelital", "colectivo") for v in vistas]
        )

        modos = [v.modo_navegacion for v in vistas]
        self._cambio_de_modo = np.array(
            [
                (modos[i] == "satelital") != (self._modos[i] == "satelital")
                for i in range(self.n)
            ]
        )
        self._modos = modos

        self._distancias_vistas = np.minimum(filas, filas.T)
        np.fill_diagonal(self._distancias_vistas, 0.0)

        return self._resumir(vistas, gnss, filas)

    # --------------------------------------------------------------- resumen
    def _resumir(self, vistas, gnss: np.ndarray, filas: np.ndarray) -> dict:
        """
        Consolida la vista de los cinco nodos.

        Los residuos que se informan son la MEDIANA de lo que calculo cada
        nodo, no lo que dice uno solo. Es coherente con el resto del diseno:
        si un nodo miente sobre lo que calculo, su valor no arrastra el
        resumen.
        """
        residuos = np.median(np.array([v.voto.residuos for v in vistas]), axis=0)
        umbral = float(np.median([v.voto.umbral for v in vistas]))
        referencia = vistas[0]
        confianza = referencia.confianza

        estados = [v.estado for v in vistas]
        postura = postura_de_formacion(estados)

        # La expulsion la decide la capa 3 de cada nodo. Se toma por mayoria
        # entre las cinco vistas en vez de creerle a una sola: si un nodo
        # miente sobre a quien expulso, su voto no alcanza para cambiar el
        # resultado. Es el mismo criterio que rige todo lo demas.
        expulsados = (
            np.sum([v.capa3.expulsados for v in vistas if v.capa3 is not None], axis=0)
            > (self.n / 2.0)
        )

        nodos = []
        for i, vista in enumerate(vistas):
            recuperada = vista.posicion_recuperada
            error_recuperacion = None
            if recuperada is not None and vista.recuperacion_valida:
                error_recuperacion = float(
                    np.linalg.norm(recuperada - self.enjambre.posiciones[i])
                )
            nodos.append(
                {
                    "id": IDENTIFICADORES[i],
                    "indice": i,
                    "etiqueta": ETIQUETAS[i],
                    "indicativo": INDICATIVOS[i],
                    "real": self.enjambre.posiciones[i].tolist(),
                    "informada": gnss[i].tolist(),
                    "residuo": float(residuos[i]),
                    "confianza": float(confianza.por_dron[i]) if confianza else 1.0,
                    "estado": vista.estado,
                    "capa2_disparada": bool(vista.capa2_propia.disparada),
                    "discrepancia_inercial": float(
                        vista.capa2_propia.discrepancia_inercial
                    ),
                    "cota_inercial": float(vista.capa2_propia.cota_inercial),
                    "discrepancia_barometrica": float(
                        vista.capa2_propia.discrepancia_barometrica
                    ),
                    "expulsado": bool(expulsados[i]),
                    "posicion_recuperada": (
                        recuperada.tolist() if recuperada is not None else None
                    ),
                    "error_recuperacion": error_recuperacion,
                }
            )

        disparadas_c2 = sum(1 for v in vistas if v.capa2_propia.disparada)

        # Persistencia de los indicadores que se informan.
        #
        # Un disparo de un solo ciclo no es una deteccion: es ruido. Sin este
        # filtro, las luces de capa parpadean con cada fluctuacion y -- peor --
        # la afirmacion central del proyecto, que la capa 1 no ve la traslacion
        # coherente, se rompe por un unico transitorio en el instante en que el
        # ataque arranca y el lazo de control todavia no reacciono.
        #
        # Es el mismo criterio que ya rige el aislamiento y la expulsion; lo
        # unico que cambia es cuanto tiene que sostenerse.
        crudas = {
            "c1": bool(confianza.capa1_disparada) if confianza else False,
            "c2": bool(confianza.capa2_disparada_global) if confianza else False,
            "c3": bool(expulsados.any()),
        }
        for clave, activa in crudas.items():
            self._sostenido[clave] = self._sostenido[clave] + 0.1 if activa else 0.0
        firme = {k: self._sostenido[k] >= self.SEGUNDOS_PARA_INFORMAR for k in crudas}

        return {
            "ciclo": self.ciclo,
            "defensa": bool(self.defensa),
            "desvio_ruta": self.enjambre.desvio_de_ruta(),
            "t": round(self.enjambre.t, 2),
            "ataque": self.ataque.tipo,
            "postura": postura,
            "quorum": f"{self.n - int(expulsados.sum())}/{self.n}",
            "umbral": umbral,
            "residuo_global": float(np.max(residuos)),
            "rango_efectivo": int(referencia.voto.rango_efectivo),
            "traslacion": float(np.linalg.norm(referencia.voto.traslacion)),
            "motivo": confianza.motivo if confianza else "",
            "confianza_formacion": float(confianza.formacion) if confianza else 1.0,
            "capas": {
                "c1": {
                    "disparada": firme["c1"],
                    "metrica": float(np.max(residuos)),
                    "ciega": bool(firme["c2"] and not firme["c1"]),
                },
                "c2": {
                    "disparada": firme["c2"],
                    "nodos_disparados": disparadas_c2,
                    "metrica": float(
                        max(v.capa2_propia.metrica for v in vistas)
                    ),
                },
                "c3": {
                    "disparada": firme["c3"],
                    "quorum": int(self.n - expulsados.sum()),
                    "total": self.n,
                },
            },
            "nodos": nodos,
        }
