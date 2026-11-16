"""Previsão local do próprio jogador, com reconciliação (client-side prediction).

O problema: o servidor é a autoridade, e a posição oficial só volta no ESTADO,
meio RTT depois de o COMANDO chegar lá. Se o cliente desenhasse só o ESTADO, a
bolinha responderia às teclas com um RTT inteiro de atraso (mais de 100 ms com
--atraso 50 dos dois lados), e o jogo pareceria "pesado".

A solução, a mesma dos jogos comerciais:

1. Ao enviar cada COMANDO, o cliente já aplica o movimento na própria cópia
   da posição, com as mesmas regras do servidor (docs/PROTOCOLO.md, seção 10),
   e guarda o COMANDO numa lista de pendentes.
2. Quando chega um ESTADO, o campo `ultimo_comando` diz até qual COMANDO o
   servidor já aplicou. O cliente descarta os pendentes até esse seq, parte da
   posição oficial e reaplica os que ainda estão em trânsito.
3. Se a previsão errou (COMANDO perdido, colisão com parede em outro ponto,
   taxa diferente), a diferença não é aplicada de uma vez: vira um "erro
   visual" que some em ~100 ms, para a bolinha não dar trancos.

Nada aqui conhece pygame ou socket: só números, para poder ser testado.
"""

from __future__ import annotations

import math
from collections import deque
from typing import Any

Posicao = tuple[float, float]

# Correção abaixo disto (pixels) não conta como erro de previsão: o servidor
# arredonda x e y para 1 casa decimal no ESTADO.
TOLERANCIA_CORRECAO = 0.5


class Previsor:
    def __init__(
        self,
        parametros: dict[str, Any],
        intervalo_comando: float,
        suavizacao: float = 0.1,
        limite_teletransporte: float | None = None,
        max_pendentes: int = 100,
    ) -> None:
        """`parametros` é o ENTRAR_OK; `intervalo_comando`, o tempo entre COMANDOs (s).

        `suavizacao` é a constante de tempo (s) com que o erro visual some.
        Correções maiores que `limite_teletransporte` (padrão: 4 raios) são
        aplicadas na hora: depois de uma pausa longa, deslizar seria pior.
        """
        self.largura = parametros["largura"]
        self.altura = parametros["altura"]
        self.raio = parametros["raio_jogador"]
        self.velocidade = parametros["velocidade"]
        # Cada COMANDO vale um intervalo de movimento. Com a taxa de COMANDO
        # igual à do servidor (20/s), é exatamente um tick; se forem diferentes,
        # o deslocamento por segundo continua o mesmo.
        self.intervalo = intervalo_comando
        self.suavizacao = suavizacao
        self.limite_teletransporte = (
            4 * self.raio if limite_teletransporte is None else limite_teletransporte)
        self._pendentes: deque[tuple[int, int, int]] = deque(maxlen=max_pendentes)
        self._anterior: Posicao | None = None   # previsão antes do último COMANDO
        self._atual: Posicao | None = None      # previsão depois do último COMANDO
        self._enviado_em = 0.0
        self._erro: Posicao = (0.0, 0.0)
        self._erro_em = 0.0
        # Números para o painel: quantas vezes e quanto a previsão errou.
        self.correcoes = 0
        self.ultima_correcao = 0.0
        self.maior_correcao = 0.0

    @property
    def pronto(self) -> bool:
        return self._atual is not None

    @property
    def pendentes(self) -> int:
        """COMANDOs enviados que o servidor ainda não aplicou (≈ RTT / 50 ms)."""
        return len(self._pendentes)

    @property
    def prevista(self) -> Posicao | None:
        """Onde o servidor vai pôr o jogador depois de aplicar todos os pendentes."""
        return self._atual

    # --- Entradas -----------------------------------------------------------

    def ao_enviar_comando(self, seq: int, dx: int, dy: int, agora: float) -> None:
        if self._atual is None:
            return  # ainda sem posição oficial: nada a prever
        self._pendentes.append((seq, dx, dy))
        self._anterior = self._atual
        self._atual = self.mover(self._atual, dx, dy)
        self._enviado_em = agora

    def ao_receber_estado(self, x: float, y: float, ultimo_comando: int, agora: float = 0.0) -> None:
        """Reconciliação: posição oficial + COMANDOs que o servidor ainda não aplicou."""
        while self._pendentes and self._pendentes[0][0] <= ultimo_comando:
            self._pendentes.popleft()
        nova: Posicao = (x, y)
        for _, dx, dy in self._pendentes:
            nova = self.mover(nova, dx, dy)
        if self._atual is None or self._anterior is None:
            self._anterior = self._atual = nova
            self._erro_em = agora
            return
        ex, ey = self._atual[0] - nova[0], self._atual[1] - nova[1]
        distancia = math.hypot(ex, ey)
        self.ultima_correcao = distancia
        if distancia > TOLERANCIA_CORRECAO:
            self.correcoes += 1
            self.maior_correcao = max(self.maior_correcao, distancia)
        # A trajetória inteira muda junto, para a interpolação entre COMANDOs
        # seguir valendo; o que estava sendo mostrado vira erro visual.
        self._anterior = (self._anterior[0] - ex, self._anterior[1] - ey)
        self._atual = nova
        self._decair_erro(agora)
        if distancia > self.limite_teletransporte:
            self._erro = (0.0, 0.0)
        else:
            self._erro = (self._erro[0] + ex, self._erro[1] + ey)

    # --- Saída --------------------------------------------------------------

    def posicao(self, agora: float) -> Posicao | None:
        """Posição para desenhar agora.

        O COMANDO enviado em t move o jogador durante [t, t + intervalo] no
        servidor, então o desenho caminha de `anterior` até `atual` nesse
        intervalo: fica suave a 60 FPS sem atrasar a resposta às teclas.
        """
        if self._atual is None or self._anterior is None:
            return None
        fracao = min(1.0, max(0.0, (agora - self._enviado_em) / self.intervalo))
        x = self._anterior[0] + (self._atual[0] - self._anterior[0]) * fracao
        y = self._anterior[1] + (self._atual[1] - self._anterior[1]) * fracao
        self._decair_erro(agora)
        x, y = x + self._erro[0], y + self._erro[1]
        return (_limitar(x, self.raio, self.largura - self.raio),
                _limitar(y, self.raio, self.altura - self.raio))

    def mover(self, posicao: Posicao, dx: int, dy: int) -> Posicao:
        """Um passo, com as regras do servidor (servidor/jogo.py, Partida._mover)."""
        passo = self.velocidade * self.intervalo
        if dx and dy:
            passo /= math.sqrt(2)
        r = self.raio
        return (_limitar(posicao[0] + dx * passo, r, self.largura - r),
                _limitar(posicao[1] + dy * passo, r, self.altura - r))

    def _decair_erro(self, agora: float) -> None:
        decorrido = max(0.0, agora - self._erro_em)
        self._erro_em = agora
        if self._erro == (0.0, 0.0):
            return
        fator = math.exp(-decorrido / self.suavizacao) if self.suavizacao > 0 else 0.0
        ex, ey = self._erro[0] * fator, self._erro[1] * fator
        self._erro = (0.0, 0.0) if math.hypot(ex, ey) < 0.05 else (ex, ey)


def _limitar(valor: float, minimo: float, maximo: float) -> float:
    return max(minimo, min(maximo, valor))
