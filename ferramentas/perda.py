"""Simulador de rede ruim: um Transporte que envolve outro e perde e atrasa pacotes.

Serve para testar a confiabilidade localmente sem `tc netem` (que só existe no
Linux) nem `clumsy` (só no Windows). Tem a mesma interface `Transporte`, então
servidor e SessaoCliente não percebem a diferença:

    transporte = TransporteComPerda(TransporteUDP.cliente(), perda=0.2, atraso=0.05, variacao=0.03)

Por padrão age nos dois sentidos (o que este lado envia e o que recebe),
como um enlace ruim. O atraso de cada pacote é sorteado em
[atraso − variacao, atraso + variacao]; como cada pacote sorteia o seu, um
pode ultrapassar o outro e a ordem de chegada muda, como na rede de verdade
quando as filas variam (Cap. 1, atraso de fila e perda de pacote).

Use só com UDP: o TCP nunca perde mensagem para a aplicação, então descartar
uma mensagem acima dele simularia algo que não existe (use netem para isso).
"""

from __future__ import annotations

import heapq
import itertools
import random
import time
from typing import Callable

from protocolo.transporte import Endereco, Pacote, Transporte

SENTIDOS = ("ambos", "envio", "recepcao")


class TransporteComPerda(Transporte):
    def __init__(
        self,
        interno: Transporte,
        perda: float = 0.0,
        atraso: float = 0.0,
        variacao: float = 0.0,
        sentido: str = "ambos",
        semente: int | None = None,
        relogio: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 0.0 <= perda <= 1.0:
            raise ValueError("perda é uma fração entre 0 e 1")
        if sentido not in SENTIDOS:
            raise ValueError(f"sentido deve ser um de {SENTIDOS}")
        self.interno = interno
        self.perda = perda
        self.atraso = atraso
        self.variacao = variacao
        self.sentido = sentido
        self._aleatorio = random.Random(semente)
        self._relogio = relogio
        self._ordem = itertools.count()  # desempate no heap: mesma hora, ordem de chegada
        self._saida: list[tuple[float, int, bytes, Endereco]] = []
        self._entrada: list[tuple[float, int, bytes, Endereco]] = []
        self.perdidos_envio = 0
        self.perdidos_recepcao = 0

    @property
    def endereco_local(self) -> Endereco:
        return self.interno.endereco_local

    def enviar(self, dados: bytes, destino: Endereco) -> None:
        if self.sentido in ("ambos", "envio"):
            if self._sorteia_perda():
                self.perdidos_envio += 1
                return
            heapq.heappush(self._saida, (self._relogio() + self._sorteia_atraso(), next(self._ordem), dados, destino))
            self._despachar_saida(self._relogio())
        else:
            self.interno.enviar(dados, destino)

    def receber(self, timeout: float) -> list[Pacote]:
        limite = self._relogio() + max(0.0, timeout)
        espera = 0.0  # a primeira leitura nunca espera: só recolhe o que já chegou
        while True:
            for dados, origem in self.interno.receber(espera):
                self._chegou(dados, origem)
            agora = self._relogio()
            self._despachar_saida(agora)
            prontos = self._retirar_entrada(agora)
            if prontos or agora >= limite:
                return prontos
            espera = min(limite, self._proximo_evento()) - agora

    def fechar(self) -> None:
        self.interno.fechar()

    # --- Internos -----------------------------------------------------------

    def _chegou(self, dados: bytes, origem: Endereco) -> None:
        if self.sentido not in ("ambos", "recepcao"):
            heapq.heappush(self._entrada, (self._relogio(), next(self._ordem), dados, origem))
            return
        if self._sorteia_perda():
            self.perdidos_recepcao += 1
            return
        heapq.heappush(self._entrada, (self._relogio() + self._sorteia_atraso(), next(self._ordem), dados, origem))

    def _despachar_saida(self, agora: float) -> None:
        while self._saida and self._saida[0][0] <= agora:
            _, _, dados, destino = heapq.heappop(self._saida)
            self.interno.enviar(dados, destino)

    def _retirar_entrada(self, agora: float) -> list[Pacote]:
        prontos: list[Pacote] = []
        while self._entrada and self._entrada[0][0] <= agora:
            _, _, dados, origem = heapq.heappop(self._entrada)
            prontos.append((dados, origem))
        return prontos

    def _proximo_evento(self) -> float:
        cabecas = [fila[0][0] for fila in (self._saida, self._entrada) if fila]
        return min(cabecas, default=float("inf"))

    def _sorteia_perda(self) -> bool:
        return self._aleatorio.random() < self.perda

    def _sorteia_atraso(self) -> float:
        return max(0.0, self._aleatorio.uniform(self.atraso - self.variacao, self.atraso + self.variacao))


# --- Linha de comando (servidor e robô) -------------------------------------

def adicionar_argumentos(parser) -> None:
    grupo = parser.add_argument_group("rede simulada (ferramentas/perda.py), em cada sentido")
    grupo.add_argument("--perda", type=float, default=0.0, metavar="PCT", help="%% de pacotes descartados")
    grupo.add_argument("--atraso", type=float, default=0.0, metavar="MS", help="atraso médio em ms")
    grupo.add_argument("--variacao", type=float, default=0.0, metavar="MS",
                       help="variação do atraso em ms (jitter; também reordena)")


def envolver_se_pedido(transporte: Transporte, args) -> Transporte:
    """Devolve o transporte com perda/atraso se alguma opção foi dada; senão, o próprio."""
    if not (args.perda or args.atraso or args.variacao):
        return transporte
    return TransporteComPerda(transporte, perda=args.perda / 100, atraso=args.atraso / 1000,
                              variacao=args.variacao / 1000)
