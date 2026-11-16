"""Interpolação dos outros jogadores, com atraso de reprodução adaptativo.

O ESTADO chega 20 vezes por segundo, mas a tela desenha 60. Desenhar o último
ESTADO faria os outros jogadores andarem "aos pulos" de 10 px; e, com jitter,
os pulos ficam irregulares (dois ESTADOs juntos, depois um buraco).

É o mesmo problema do áudio em VoIP (Kurose, cap. 9, "atraso de reprodução
adaptativo"): os pacotes saem a intervalos fixos, chegam com atraso variável,
e o receptor precisa de um buffer para reproduzi-los no ritmo certo. Aqui:

- t_i = tick · intervalo é o instante em que o servidor gerou o ESTADO
  (no relógio do servidor, que não está sincronizado com o nosso);
- r_i é quando ele chegou, no nosso relógio;
- d estima a média de (r_i − t_i) e v, o desvio, com médias móveis
  exponenciais (u = 0,05), exatamente as fórmulas do livro;
- desenhamos o mundo como estava em  agora − d − intervalo − K·v.
  O d desconta a defasagem dos relógios e o atraso médio; o intervalo garante
  um ESTADO depois do instante desenhado, para interpolar entre dois; K·v
  absorve o jitter. Com jitter alto, o buffer cresce sozinho.

O defasamento entre os relógios cai fora da conta porque só a diferença
r_i − t_i é usada, como no cálculo do jitter da RFC 3550.

Nada aqui conhece pygame ou socket.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Iterable

Posicao = tuple[float, float]

# Se (r_i − t_i) pular mais que isto, o servidor reiniciou ou o relógio mudou:
# recomeça a estimativa em vez de levar segundos para convergir.
SALTO_MAXIMO = 1.0  # segundos


class Interpolador:
    def __init__(self, intervalo_tick: float, u: float = 0.05, k: float = 4.0,
                 historico: float = 1.0) -> None:
        self.intervalo = intervalo_tick
        self.u = u
        self.k = k
        self.historico = historico
        self._instantaneos: deque[tuple[float, dict[int, Posicao]]] = deque()
        self._d: float | None = None
        self._v = 0.0
        self._sem_dados = False
        # Para o painel: quantas vezes o buffer esvaziou (o instante a desenhar
        # passou do último ESTADO recebido, e os outros jogadores congelaram).
        self.esgotamentos = 0

    @property
    def atraso_reproducao(self) -> float:
        """Quanto (s) o desenho dos outros fica atrás do ESTADO médio que chega."""
        return self.intervalo + self.k * self._v

    @property
    def desvio(self) -> float:
        return self._v

    def adicionar(self, tick: int, jogadores: Iterable[dict[str, Any]], chegada: float) -> None:
        gerado = tick * self.intervalo
        amostra = chegada - gerado
        if self._d is None or abs(amostra - self._d) > SALTO_MAXIMO:
            self._d, self._v = amostra, 0.0
            self._instantaneos.clear()
        else:
            self._d = (1 - self.u) * self._d + self.u * amostra
            self._v = (1 - self.u) * self._v + self.u * abs(amostra - self._d)
        if self._instantaneos and gerado <= self._instantaneos[-1][0]:
            return  # a SessaoCliente já descarta ESTADO velho; isto é só defesa
        self._instantaneos.append((gerado, {j["id"]: (j["x"], j["y"]) for j in jogadores}))
        while len(self._instantaneos) > 2 and self._instantaneos[1][0] < gerado - self.historico:
            self._instantaneos.popleft()

    def posicoes(self, agora: float) -> dict[int, Posicao]:
        """Posição de cada jogador no instante de reprodução."""
        if not self._instantaneos or self._d is None:
            return {}
        alvo = agora - self._d - self.atraso_reproducao
        ultimo_t, ultimo = self._instantaneos[-1]
        if alvo >= ultimo_t:
            if not self._sem_dados and len(self._instantaneos) > 1:
                self.esgotamentos += 1
            self._sem_dados = True
            return dict(ultimo)
        self._sem_dados = False
        primeiro_t, primeiro = self._instantaneos[0]
        if alvo <= primeiro_t:
            return dict(primeiro)
        for (t0, antes), (t1, depois) in zip(self._instantaneos, list(self._instantaneos)[1:]):
            if t0 <= alvo <= t1:
                fracao = (alvo - t0) / (t1 - t0)
                resultado: dict[int, Posicao] = {}
                for jogador_id, (x1, y1) in depois.items():
                    x0, y0 = antes.get(jogador_id, (x1, y1))  # quem acabou de entrar
                    resultado[jogador_id] = (x0 + (x1 - x0) * fracao, y0 + (y1 - y0) * fracao)
                return resultado
        return dict(ultimo)
