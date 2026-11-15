"""Confiabilidade seletiva sobre um canal não confiável (UDP), para um par de pontas.

Só os tipos de TIPOS_CONFIAVEIS ganham ACK, temporizador e retransmissão; os
outros passam direto. A classe não conhece socket nem endereço: recebe uma
função de envio e é acionada por ao_receber(mensagem, agora) e
verificar_timeouts(agora). Por isso dá para testá-la sem rede e usar a mesma
classe no servidor (uma por cliente) e no cliente (uma para o servidor).

Cap. 3, princípios da transferência confiável de dados:
- rdt3.0: o remetente liga um temporizador por envio e retransmite se o ACK
  não chegar a tempo;
- rdt2.1/rdt2.2: números de sequência permitem ao destinatário reconhecer uma
  retransmissão (duplicata) e confirmá-la sem processá-la de novo;
- repetição seletiva: cada mensagem tem seu próprio temporizador e seu próprio
  ACK, e várias podem estar pendentes ao mesmo tempo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

import config
from protocolo.mensagens import Mensagem, Tipo
from protocolo.sequencia import ContadorSequencia

log = logging.getLogger("arena.confiavel")


class EstimadorRTT:
    """Timeout adaptativo do TCP (Cap. 3, estimativa do RTT e timeout do TCP).

        EstimatedRTT    = (1 − α)·EstimatedRTT + α·SampleRTT
        DevRTT          = (1 − β)·DevRTT       + β·|SampleRTT − EstimatedRTT|
        TimeoutInterval = EstimatedRTT + 4·DevRTT

    Primeira amostra e ordem das contas como na RFC 6298: DevRTT começa em
    SampleRTT/2 e é atualizado com o EstimatedRTT anterior. Valores em segundos.
    """

    def __init__(
        self,
        alfa: float = config.ALFA_RTT,
        beta: float = config.BETA_RTT,
        inicial: float = config.TIMEOUT_INICIAL,
        minimo: float = config.TIMEOUT_MINIMO,
        maximo: float = config.TIMEOUT_MAXIMO,
    ) -> None:
        self.alfa = alfa
        self.beta = beta
        self.minimo = minimo
        self.maximo = maximo
        self.estimated_rtt: float | None = None
        self.dev_rtt: float | None = None
        self.timeout = inicial  # TimeoutInterval

    def amostrar(self, sample_rtt: float) -> None:
        if self.estimated_rtt is None or self.dev_rtt is None:
            self.estimated_rtt = sample_rtt
            self.dev_rtt = sample_rtt / 2
        else:
            # Média móvel exponencial: amostras recentes pesam mais, mas uma
            # amostra isolada (um pacote preso numa fila) não domina a estimativa.
            self.dev_rtt = (1 - self.beta) * self.dev_rtt + self.beta * abs(sample_rtt - self.estimated_rtt)
            self.estimated_rtt = (1 - self.alfa) * self.estimated_rtt + self.alfa * sample_rtt
        # Folga de 4 desvios: com RTT estável o timeout fica justo; com RTT
        # instável ele se afasta, para não retransmitir à toa.
        self.timeout = _limitar(self.estimated_rtt + 4 * self.dev_rtt, self.minimo, self.maximo)


@dataclass
class Pendente:
    """Mensagem confiável enviada e ainda não confirmada."""

    mensagem: Mensagem
    enviada_em: float      # instante da primeira transmissão (para a amostra de RTT)
    timeout: float         # intervalo atual desta mensagem (dobra a cada retransmissão)
    prazo: float           # quando o temporizador estoura
    transmissoes: int = 1


@dataclass(frozen=True)
class Contadores:
    """Números do canal para os experimentos e o painel de métricas."""

    enviadas: int                  # mensagens confiáveis novas (sem contar retransmissões)
    retransmitidas: int
    duplicadas_recebidas: int
    desistencias: int
    rtt_estimado_ms: float | None  # EstimatedRTT; None antes da primeira amostra
    dev_rtt_ms: float | None
    timeout_ms: float              # TimeoutInterval atual


class JanelaDuplicatas:
    """Lembra os `seq` confiáveis já recebidos, dentro de uma janela dos mais recentes."""

    def __init__(self, tamanho: int = config.JANELA_DUPLICATAS) -> None:
        self.tamanho = tamanho
        self._maior = 0
        self._vistos: set[int] = set()

    def registrar(self, seq: int) -> bool:
        """True se `seq` é novo; False se é duplicata (ou velho demais para saber)."""
        if seq <= self._maior - self.tamanho:
            # Mais velho que a janela: por segurança, tratado como duplicata.
            # Uma retransmissão nunca demora tanto (são no máximo MAX_TENTATIVAS).
            return False
        if seq in self._vistos:
            return False
        self._vistos.add(seq)
        if seq > self._maior:
            self._maior = seq
        if len(self._vistos) > self.tamanho:
            limite = self._maior - self.tamanho
            self._vistos = {s for s in self._vistos if s > limite}
        return True


class CanalConfiavel:
    """Numeração e confiabilidade seletiva das mensagens trocadas com UMA outra ponta.

    Todas as mensagens enviadas a essa ponta passam por enviar(), para que o
    `seq` seja um só espaço por par (confiáveis e não confiáveis juntos).
    """

    def __init__(
        self,
        enviar: Callable[[Mensagem], None],
        ao_desistir: Callable[[Mensagem], None] | None = None,
        max_tentativas: int = config.MAX_TENTATIVAS,
        estimador: EstimadorRTT | None = None,
        janela: JanelaDuplicatas | None = None,
        rotulo: str = "",
    ) -> None:
        self._transmitir = enviar
        self._ao_desistir = ao_desistir
        self.max_tentativas = max_tentativas
        self.estimador = estimador if estimador is not None else EstimadorRTT()
        self._janela = janela if janela is not None else JanelaDuplicatas()
        self.rotulo = rotulo
        self._seq = ContadorSequencia()
        self._pendentes: dict[int, Pendente] = {}
        self._enviadas = 0
        self._retransmitidas = 0
        self._duplicadas = 0
        self._desistencias = 0

    # --- Remetente ----------------------------------------------------------

    def enviar(self, tipo: Tipo, dados: dict[str, Any], agora: float) -> Mensagem:
        """Numera e envia. Se o tipo é confiável, guarda como pendente até o ACK."""
        mensagem = Mensagem(tipo, self._seq.proximo(), dados)
        self._transmitir(mensagem)
        if mensagem.confiavel:
            timeout = self.estimador.timeout
            self._pendentes[mensagem.seq] = Pendente(mensagem, agora, timeout, agora + timeout)
            self._enviadas += 1
        return mensagem

    def verificar_timeouts(self, agora: float) -> None:
        """Retransmite as pendentes cujo temporizador estourou; desiste das que esgotaram as tentativas."""
        for seq, pendente in list(self._pendentes.items()):
            if agora < pendente.prazo:
                continue
            if pendente.transmissoes >= self.max_tentativas:
                self._desistir(seq, pendente)
            else:
                self._retransmitir(pendente, agora)

    def _retransmitir(self, pendente: Pendente, agora: float) -> None:
        # rdt3.0: estourou o temporizador sem ACK, então reenvia. É a MESMA
        # mensagem (mesmo seq, mesmo ts), para o destinatário poder reconhecê-la.
        self._transmitir(pendente.mensagem)
        pendente.transmissoes += 1
        self._retransmitidas += 1
        # Cap. 3, TCP: a cada expiração o intervalo dobra (backoff exponencial).
        # Se a rede está congestionada, insistir no mesmo ritmo só piora.
        pendente.timeout = min(pendente.timeout * 2, self.estimador.maximo)
        pendente.prazo = agora + pendente.timeout
        log.info("%sretransmite %s seq=%d (transmissão %d de %d; próximo timeout %.0f ms)",
                 self._prefixo(), pendente.mensagem.tipo, pendente.mensagem.seq,
                 pendente.transmissoes, self.max_tentativas, pendente.timeout * 1000)

    def _desistir(self, seq: int, pendente: Pendente) -> None:
        del self._pendentes[seq]
        self._desistencias += 1
        log.warning("%sdesiste de %s seq=%d após %d transmissões sem ACK",
                    self._prefixo(), pendente.mensagem.tipo, seq, pendente.transmissoes)
        if self._ao_desistir is not None:
            self._ao_desistir(pendente.mensagem)

    def _ao_receber_ack(self, seq: int, agora: float) -> None:
        pendente = self._pendentes.pop(seq, None)
        if pendente is None:
            return  # ACK repetido ou atrasado de algo já confirmado (ou já desistido)
        if pendente.transmissoes == 1:
            # Algoritmo de Karn: só mede o RTT de mensagem transmitida uma vez.
            # Com retransmissão, não dá para saber a qual transmissão o ACK
            # responde, e qualquer escolha contaminaria a estimativa.
            self.estimador.amostrar(agora - pendente.enviada_em)

    # --- Destinatário -------------------------------------------------------

    def ao_receber(self, mensagem: Mensagem, agora: float) -> bool:
        """Trata a parte de confiabilidade de uma mensagem que chegou da outra ponta.

        Devolve True se a aplicação deve processar a mensagem; False se ela
        era um ACK (assunto só deste canal) ou uma duplicata.
        """
        if mensagem.tipo is Tipo.ACK:
            # Um ACK nunca é confirmado: senão o ACK do ACK também precisaria de ACK.
            self._ao_receber_ack(mensagem.dados["ack"], agora)
            return False
        if not mensagem.confiavel:
            return True
        # Confirma SEMPRE, mesmo a duplicata: se ela chegou, é porque o ACK
        # anterior se perdeu, e o remetente só para de retransmitir com um novo ACK.
        self.enviar(Tipo.ACK, {"ack": mensagem.seq}, agora)
        if not self._janela.registrar(mensagem.seq):
            self._duplicadas += 1
            log.info("%sduplicata de %s seq=%d: confirmada de novo e não processada",
                     self._prefixo(), mensagem.tipo, mensagem.seq)
            return False
        return True

    # --- Consulta -----------------------------------------------------------

    def aguardando(self, seq: int) -> bool:
        return seq in self._pendentes

    @property
    def pendentes(self) -> int:
        return len(self._pendentes)

    def proximo_prazo(self) -> float | None:
        """Quando o próximo temporizador estoura (para o laço não dormir além disso)."""
        return min((p.prazo for p in self._pendentes.values()), default=None)

    def contadores(self) -> Contadores:
        est = self.estimador
        return Contadores(
            enviadas=self._enviadas,
            retransmitidas=self._retransmitidas,
            duplicadas_recebidas=self._duplicadas,
            desistencias=self._desistencias,
            rtt_estimado_ms=None if est.estimated_rtt is None else est.estimated_rtt * 1000,
            dev_rtt_ms=None if est.dev_rtt is None else est.dev_rtt * 1000,
            timeout_ms=est.timeout * 1000,
        )

    def _prefixo(self) -> str:
        return f"[{self.rotulo}] " if self.rotulo else ""


def _limitar(valor: float, minimo: float, maximo: float) -> float:
    return max(minimo, min(maximo, valor))
