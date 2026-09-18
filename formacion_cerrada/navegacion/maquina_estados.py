"""
Maquina de estados de navegacion.

Detectar es la mitad del problema. La otra mitad es que la aeronave HAGA algo,
y esa parte es una decision de doctrina antes que una decision tecnica: define
en que momento se deja de creer al satelite, con que se navega mientras tanto,
y cuando se vuelve a confiar.

    NOMINAL     navegacion satelital normal
    SOSPECHA    los numeros de alguien no cierran; se registra, no se actua
    AISLADO     este dron dejo de creerle a su propia fuente satelital
    COLECTIVO   posicion recuperada por trilateracion desde los pares confiables
    REPLIEGUE   ninguna fuente absoluta es confiable en TODO el enjambre;
                se navega por rumbo inercial

El estado REPLIEGUE es el que ningun trabajo publicado sobre validacion
cooperativa define, y es el unico que responde la pregunta incomoda: que hace
la aeronave cuando ya no puede confiar ni en el satelite ni en sus companeros.
"""

from __future__ import annotations

from dataclasses import dataclass, field

ESTADOS = ("NOMINAL", "SOSPECHA", "AISLADO", "COLECTIVO", "REPLIEGUE")


@dataclass
class ParametrosEstados:
    # Cuanto tiene que sostenerse la sospecha antes de aislar la fuente.
    # No es un numero cosmetico: sin esta espera, una rafaga de ruido de
    # medicion aisla el satelite de un dron sano.
    segundos_para_aislar: float = 2.0
    # Cuanto tiene que sostenerse la confianza recuperada antes de volver a
    # creerle al satelite. Deliberadamente largo y asimetrico respecto del
    # anterior: desconfiar es barato, volver a confiar tiene que costar.
    segundos_para_recuperar: float = 30.0
    # Pares confiables minimos para poder recuperar posicion por trilateracion.
    pares_minimos: int = 3


@dataclass
class MaquinaEstados:
    """Una instancia por dron. Corre a bordo, sin coordinador."""

    parametros: ParametrosEstados = field(default_factory=ParametrosEstados)
    estado: str = "NOMINAL"
    _acumulado_sospecha: float = 0.0
    _acumulado_confianza: float = 0.0

    def actualizar(
        self,
        dt: float,
        sospechoso: bool,
        anclas_caidas_global: bool,
        pares_confiables: int,
    ) -> str:
        """
        sospechoso            : la capa 1 o la 3 marcaron a ESTE dron.
        anclas_caidas_global  : la capa 2 discrepa en todo el enjambre (caso B).
        pares_confiables      : cuantos companeros siguen siendo creibles.
        """
        # El caso B manda sobre cualquier otra consideracion: si la referencia
        # comun se movio debajo de todos, recalcular la posicion desde los
        # companeros solo reproduce el mismo error coherente. No hay a quien
        # preguntarle.
        if anclas_caidas_global:
            self.estado = "REPLIEGUE"
            self._acumulado_sospecha = 0.0
            self._acumulado_confianza = 0.0
            return self.estado

        if sospechoso:
            self._acumulado_sospecha += dt
            self._acumulado_confianza = 0.0
        else:
            self._acumulado_confianza += dt
            self._acumulado_sospecha = 0.0

        if self.estado in ("NOMINAL", "SOSPECHA"):
            if sospechoso:
                if self._acumulado_sospecha >= self.parametros.segundos_para_aislar:
                    self.estado = "AISLADO"
                else:
                    self.estado = "SOSPECHA"
            else:
                self.estado = "NOMINAL"

        elif self.estado == "REPLIEGUE":
            # De REPLIEGUE se sale UNICAMENTE por recuperacion completa, nunca
            # pasando a COLECTIVO. Es la transicion que hay que entender: se
            # llego aca porque las anclas absolutas se cayeron en todo el
            # enjambre, y preguntarles la posicion a los companeros en esa
            # situacion solo reproduce el mismo error coherente en todos. Si el
            # ancla deja de marcar -- por ejemplo porque el filtro inercial
            # termino absorbiendo un arrastre lento como si fuera deriva propia
            # del sensor -- eso NO es evidencia de que el ataque termino, y
            # volver a confiar en los pares seria justamente lo que el atacante
            # necesita.
            if (
                not sospechoso
                and self._acumulado_confianza
                >= self.parametros.segundos_para_recuperar
            ):
                self.estado = "NOMINAL"

        elif self.estado in ("AISLADO", "COLECTIVO"):
            if (
                not sospechoso
                and self._acumulado_confianza
                >= self.parametros.segundos_para_recuperar
            ):
                self.estado = "NOMINAL"
            elif pares_confiables >= self.parametros.pares_minimos:
                # Hay a quien preguntarle: se recupera posicion en vez de
                # abortar la mision.
                self.estado = "COLECTIVO"
            else:
                self.estado = "AISLADO"

        return self.estado


def postura_de_formacion(estados: list[str]) -> str:
    """
    Resume el estado del enjambre para la interfaz de mando.

    Se toma el estado mas grave presente: la formacion no esta mejor que su
    peor nodo.
    """
    for grave in ("REPLIEGUE", "COLECTIVO", "AISLADO", "SOSPECHA"):
        if grave in estados:
            return grave
    return "NOMINAL"
