"""Lado cliente do protocolo, sem interface gráfica: a SessaoCliente.

Cuida de toda a conversa com o servidor: entrar na partida, mandar COMANDO na
taxa certa, aceitar só o ESTADO mais recente, juntar os EVENTOs e, pelo
CanalConfiavel, confirmar o que é confiável e retransmitir o que não foi
confirmado. Não bloqueia: o cliente chama processar() uma vez por frame e lê
os atributos.

Uso típico (cliente pygame):

    from protocolo.sessao import SessaoCliente, Situacao
    from protocolo.transporte import TransporteUDP, resolver

    sessao = SessaoCliente(TransporteUDP.cliente(), resolver("192.168.0.10", 5005), "ana")
    while rodando:
        sessao.definir_direcao(dx, dy)        # o que as teclas dizem agora
        sessao.processar()                    # rede: recebe e envia, sem esperar
        for evento in sessao.consumir_eventos():
            ...                               # {"evento": "ponto", "jogador": 3, "pontos": 5}
        if sessao.situacao is Situacao.EM_JOGO and sessao.estado:
            desenhar(sessao.estado)           # dados do último ESTADO aceito
    sessao.sair()                             # espera até 1 s pelo ACK do SAIR

A sessão também manda um PING por segundo e guarda o último RTT medido em
`ultimo_rtt_ms`. Cada pacote enviado ou recebido passa por `sessao.registro`
(protocolo/registro.py); para o log de pacotes na tela, basta um observador:

    sessao.registro.observadores.append(lambda pacote: linhas.append(pacote.como_texto()))
"""

from __future__ import annotations

import logging
import time
from enum import StrEnum
from typing import Any, Callable

import config
from protocolo.confiavel import CanalConfiavel
from protocolo.mensagens import Mensagem, MensagemInvalida, Tipo, agora_ms, codificar, decodificar
from protocolo.registro import RegistroPacotes
from protocolo.sequencia import FiltroMaisRecente
from protocolo.transporte import Endereco, Transporte

log = logging.getLogger("arena.sessao")


class Situacao(StrEnum):
    ENTRANDO = "entrando"          # mandou ENTRAR, esperando ENTRAR_OK (ou ENTRAR_NEGADO)
    EM_JOGO = "em_jogo"
    RECUSADO = "recusado"          # recebeu ENTRAR_NEGADO (ver motivo_recusa)
    SEM_RESPOSTA = "sem_resposta"  # ENTRAR sem ACK após todas as tentativas, ou servidor calado
    ENCERRADA = "encerrada"        # sair() foi chamado


class SessaoCliente:
    def __init__(
        self,
        transporte: Transporte,
        servidor: Endereco,
        nome: str,
        taxa_comandos: int = config.TAXA_COMANDOS,
        relogio: Callable[[], float] = time.monotonic,
        registro: RegistroPacotes | None = None,
        intervalo_ping: float = config.INTERVALO_PING,
        confiabilidade: bool | None = None,
    ) -> None:
        self.transporte = transporte
        # None = automático, como no servidor: os dois lados decidem igual.
        self.confiabilidade = (not transporte.garante_entrega) if confiabilidade is None else confiabilidade
        self.servidor = servidor
        self.registro = registro if registro is not None else RegistroPacotes()
        self._local = transporte.endereco_local
        self.nome = _ajustar_nome(nome)
        self.situacao = Situacao.ENTRANDO
        self.meu_id: int | None = None
        self.parametros: dict[str, Any] | None = None   # dados do ENTRAR_OK (arena, raios...)
        self.estado: dict[str, Any] | None = None       # dados do último ESTADO aceito
        self.motivo_recusa: str | None = None
        self.estados_recebidos = 0
        self.descartadas_invalidas = 0
        self.ultimo_rtt_ms: int | None = None  # do último PONG (resolução de 1 ms)
        self.pings_enviados = 0
        self.pongs_recebidos = 0
        self._relogio = relogio
        self._intervalo_comandos = 1.0 / taxa_comandos
        # Espaço de sequência cliente -> servidor, ACKs, retransmissões e RTT.
        self.canal = CanalConfiavel(self._transmitir, ao_desistir=self._ao_desistir,
                                    rotulo=f"{self.nome}->servidor", ativo=self.confiabilidade)
        self._estados = FiltroMaisRecente()
        self._eventos: list[dict[str, Any]] = []
        self._dx = 0
        self._dy = 0
        self._entrada_pedida = False
        self._intervalo_ping = intervalo_ping
        self._proximo_ping = 0.0
        self._proximo_comando = 0.0
        self._ultimo_estado_em = 0.0

    # --- Interface para o jogo ----------------------------------------------

    def definir_direcao(self, dx: int, dy: int) -> None:
        """Direção que as teclas indicam agora; vai no próximo COMANDO."""
        if dx not in (-1, 0, 1) or dy not in (-1, 0, 1):
            raise ValueError(f"direção inválida: ({dx}, {dy})")
        # int(): aceita bool (estado de tecla do pygame) sem mandar `true` no JSON.
        self._dx, self._dy = int(dx), int(dy)

    def processar(self, timeout: float = 0.0) -> None:
        """Recebe o que chegou e envia o que estiver na hora. Chamar a cada frame."""
        for dados, origem in self.transporte.receber(timeout):
            self._ao_receber(dados, origem, self._relogio())
        agora = self._relogio()
        self.canal.verificar_timeouts(agora)
        if self.situacao is Situacao.ENTRANDO and not self._entrada_pedida:
            # Uma vez só: se o ENTRAR se perder, quem retransmite é o canal.
            self.canal.enviar(Tipo.ENTRAR, {"nome": self.nome}, agora)
            self._entrada_pedida = True
        elif self.situacao is Situacao.EM_JOGO:
            self._enviar_comando_se_for_hora(agora)
            self._enviar_ping_se_for_hora(agora)
            self._verificar_servidor(agora)

    def consumir_eventos(self) -> list[dict[str, Any]]:
        eventos, self._eventos = self._eventos, []
        return eventos

    def meu_jogador(self) -> dict[str, Any] | None:
        if self.estado is None:
            return None
        return next((j for j in self.estado["jogadores"] if j["id"] == self.meu_id), None)

    def sair(self, prazo: float = 1.0) -> None:
        """Envia SAIR (confiável) e espera até `prazo` segundos pelo ACK.

        Bloqueia por no máximo `prazo`, para não travar o fechamento da janela
        se o servidor estiver fora do ar. Sem ACK, o servidor remove o jogador
        pelo timeout de inatividade de qualquer forma.
        """
        anterior, self.situacao = self.situacao, Situacao.ENCERRADA
        if anterior not in (Situacao.ENTRANDO, Situacao.EM_JOGO):
            return
        saida = self.canal.enviar(Tipo.SAIR, {}, self._relogio())
        # Relógio real de propósito: aqui o programa espera de verdade.
        limite = time.monotonic() + prazo
        while self.canal.aguardando(saida.seq) and time.monotonic() < limite:
            self.processar(timeout=0.01)

    @property
    def estados_fora_de_ordem(self) -> int:
        return self._estados.descartadas

    # --- Envio --------------------------------------------------------------

    def _enviar_comando_se_for_hora(self, agora: float) -> None:
        if agora < self._proximo_comando:
            return
        # O COMANDO leva o estado das teclas e vai mesmo sem mudança: se um se
        # perder, o próximo corrige; e o envio contínuo é o sinal de vida.
        self.canal.enviar(Tipo.COMANDO, {"dx": self._dx, "dy": self._dy}, agora)
        self._proximo_comando += self._intervalo_comandos
        if self._proximo_comando <= agora:
            self._proximo_comando = agora + self._intervalo_comandos

    def _enviar_ping_se_for_hora(self, agora: float) -> None:
        if agora < self._proximo_ping:
            return
        # O PING não é confiável: um PING perdido é só uma amostra a menos.
        self.canal.enviar(Tipo.PING, {}, agora)
        self.pings_enviados += 1
        self._proximo_ping = agora + self._intervalo_ping

    def _transmitir(self, mensagem: Mensagem) -> None:
        datagrama = codificar(mensagem)
        self.registro.enviado(mensagem, len(datagrama), self._local, self.servidor)
        self.transporte.enviar(datagrama, self.servidor)

    def _ao_desistir(self, mensagem: Mensagem) -> None:
        if mensagem.tipo is Tipo.ENTRAR and self.situacao is Situacao.ENTRANDO:
            log.warning("%s: servidor %s:%d não confirmou o ENTRAR após %d tentativas",
                        self.nome, *self.servidor, self.canal.max_tentativas)
            self.situacao = Situacao.SEM_RESPOSTA

    # --- Recepção -----------------------------------------------------------

    def _ao_receber(self, dados: bytes, origem: Endereco, agora: float) -> None:
        if origem != self.servidor:
            # Só o servidor fala com o cliente. Ignorar o resto impede que outro
            # programa na rede injete um ESTADO falso.
            log.debug("datagrama de %s:%d, que não é o servidor: ignorado", *origem)
            return
        try:
            mensagem = decodificar(dados)
        except MensagemInvalida as erro:
            self.descartadas_invalidas += 1
            self.registro.recebido(None, len(dados), self._local, origem)
            log.warning("descartado do servidor (%d bytes): %s", len(dados), erro)
            return
        self.registro.recebido(mensagem, len(dados), self._local, origem)
        # ACK das confiáveis, consumo dos ACKs recebidos e descarte de duplicatas.
        if not self.canal.ao_receber(mensagem, agora):
            return
        if mensagem.tipo is Tipo.ENTRAR_OK:
            self._ao_entrar_ok(mensagem, agora)
        elif mensagem.tipo is Tipo.ENTRAR_NEGADO:
            self._ao_entrar_negado(mensagem)
        elif mensagem.tipo is Tipo.ESTADO:
            self._ao_estado(mensagem, agora)
        elif mensagem.tipo is Tipo.EVENTO and self.situacao is Situacao.EM_JOGO:
            self._eventos.append(mensagem.dados)
        elif mensagem.tipo is Tipo.PONG:
            self._ao_pong(mensagem)

    def _ao_entrar_ok(self, mensagem: Mensagem, agora: float) -> None:
        if self.situacao is not Situacao.ENTRANDO:
            return  # ENTRAR_OK de um ENTRAR repetido pela aplicação
        self.meu_id = mensagem.dados["id"]
        self.parametros = mensagem.dados
        self.situacao = Situacao.EM_JOGO
        self._proximo_comando = agora
        self._ultimo_estado_em = agora
        log.info("%s entrou na partida como jogador %d", self.nome, self.meu_id)

    def _ao_entrar_negado(self, mensagem: Mensagem) -> None:
        if self.situacao is Situacao.ENTRANDO:
            self.situacao = Situacao.RECUSADO
            self.motivo_recusa = mensagem.dados["motivo"]

    def _ao_estado(self, mensagem: Mensagem, agora: float) -> None:
        # Antes do ENTRAR_OK não sabemos nosso id: o ESTADO ainda não serve.
        if self.situacao is not Situacao.EM_JOGO:
            return
        # Cap. 3: o UDP pode entregar fora de ordem; ESTADO velho é descartado.
        if not self._estados.aceitar(mensagem.seq):
            return
        self.estado = mensagem.dados
        self.estados_recebidos += 1
        self._ultimo_estado_em = agora

    def _ao_pong(self, mensagem: Mensagem) -> None:
        # RTT = agora − ts do PING, ambos lidos do relógio DESTE cliente: a
        # defasagem entre os relógios das duas máquinas não entra na conta.
        self.ultimo_rtt_ms = agora_ms() - mensagem.dados["ts_eco"]
        self.pongs_recebidos += 1

    def _verificar_servidor(self, agora: float) -> None:
        if agora - self._ultimo_estado_em > config.TIMEOUT_INATIVIDADE:
            log.warning("%s: nenhum ESTADO há %.1f s; servidor fora do ar?",
                        self.nome, agora - self._ultimo_estado_em)
            self.situacao = Situacao.SEM_RESPOSTA


def _ajustar_nome(nome: str) -> str:
    """Corta o nome no limite do protocolo; recusa nome vazio ou com caracteres de controle."""
    ajustado = nome.strip()[: config.TAMANHO_MAXIMO_NOME]
    if not ajustado or not ajustado.isprintable():
        raise ValueError(f"nome inválido: {nome!r}")
    return ajustado
