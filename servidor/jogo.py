"""Lógica do jogo, sem rede: jogadores, moedas, movimento, colisão e placar.

Este módulo não sabe que existe rede, socket, JSON ou endereço. O servidor o
alimenta com direções e lê o resultado. É o que permite trocar UDP por TCP
sem tocar na regra do jogo, e testá-la sem abrir socket nenhum.

As regras daqui estão descritas em docs/PROTOCOLO.md ("Regras da simulação"),
porque o cliente precisa reproduzi-las na previsão local.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

import config

TENTATIVAS_POSICAO_LIVRE = 20


@dataclass
class Jogador:
    id: int
    nome: str
    x: float
    y: float
    pontos: int = 0
    dx: int = 0
    dy: int = 0


@dataclass
class Moeda:
    x: float
    y: float


@dataclass(frozen=True)
class Ponto:
    """Um jogador pegou uma moeda neste tick."""

    jogador_id: int
    pontos: int


class Partida:
    def __init__(
        self,
        largura: int = config.LARGURA_ARENA,
        altura: int = config.ALTURA_ARENA,
        raio_jogador: int = config.RAIO_JOGADOR,
        raio_moeda: int = config.RAIO_MOEDA,
        velocidade: int = config.VELOCIDADE_JOGADOR,
        numero_moedas: int = config.NUMERO_MOEDAS,
        semente: int | None = config.SEMENTE_ALEATORIA,
    ) -> None:
        self.largura = largura
        self.altura = altura
        self.raio_jogador = raio_jogador
        self.raio_moeda = raio_moeda
        self.velocidade = velocidade
        self._aleatorio = random.Random(semente)
        self._proximo_id = 1
        self.tick = 0
        self.jogadores: dict[int, Jogador] = {}
        self.moedas = [self._nova_moeda() for _ in range(numero_moedas)]

    # --- Jogadores ----------------------------------------------------------

    def adicionar_jogador(self, nome: str) -> Jogador:
        x, y = self._posicao_livre(self.raio_jogador)
        jogador = Jogador(id=self._proximo_id, nome=nome, x=x, y=y)
        self._proximo_id += 1
        self.jogadores[jogador.id] = jogador
        return jogador

    def remover_jogador(self, jogador_id: int) -> None:
        self.jogadores.pop(jogador_id, None)

    def definir_direcao(self, jogador_id: int, dx: int, dy: int) -> None:
        jogador = self.jogadores[jogador_id]
        jogador.dx, jogador.dy = dx, dy

    def ranking(self) -> list[Jogador]:
        return sorted(self.jogadores.values(), key=lambda j: (-j.pontos, j.id))

    # --- Simulação ----------------------------------------------------------

    def avancar(self, dt: float) -> list[Ponto]:
        """Um passo de simulação: move todos e resolve colisões com moedas."""
        self.tick += 1
        for jogador in self.jogadores.values():
            self._mover(jogador, dt)
        return self._coletar_moedas()

    def _mover(self, jogador: Jogador, dt: float) -> None:
        passo = self.velocidade * dt
        if jogador.dx and jogador.dy:
            passo /= math.sqrt(2)  # na diagonal, mesma velocidade que em linha reta
        r = self.raio_jogador
        jogador.x = _limitar(jogador.x + jogador.dx * passo, r, self.largura - r)
        jogador.y = _limitar(jogador.y + jogador.dy * passo, r, self.altura - r)

    def _coletar_moedas(self) -> list[Ponto]:
        pontos: list[Ponto] = []
        # Em ordem de id: se dois jogadores tocam a mesma moeda no mesmo tick, o
        # servidor (a única autoridade) decide de forma determinística.
        for jogador in sorted(self.jogadores.values(), key=lambda j: j.id):
            for indice, moeda in enumerate(self.moedas):
                if self._tocou(jogador, moeda):
                    jogador.pontos += 1
                    pontos.append(Ponto(jogador.id, jogador.pontos))
                    self.moedas[indice] = self._nova_moeda()
        return pontos

    def _tocou(self, jogador: Jogador, moeda: Moeda) -> bool:
        distancia = math.hypot(jogador.x - moeda.x, jogador.y - moeda.y)
        return distancia <= self.raio_jogador + self.raio_moeda

    # --- Posições aleatórias ------------------------------------------------

    def _nova_moeda(self) -> Moeda:
        return Moeda(*self._posicao_livre(self.raio_moeda))

    def _posicao_livre(self, raio: int) -> tuple[float, float]:
        """Sorteia uma posição longe dos jogadores (para não nascer já pegando moeda)."""
        x, y = self._sortear(raio)
        for _ in range(TENTATIVAS_POSICAO_LIVRE):
            if all(math.hypot(j.x - x, j.y - y) > self.raio_jogador + raio for j in self.jogadores.values()):
                break
            x, y = self._sortear(raio)
        return x, y

    def _sortear(self, raio: int) -> tuple[float, float]:
        x = self._aleatorio.uniform(raio, self.largura - raio)
        y = self._aleatorio.uniform(raio, self.altura - raio)
        return round(x, 1), round(y, 1)


def _limitar(valor: float, minimo: float, maximo: float) -> float:
    return max(minimo, min(maximo, valor))
