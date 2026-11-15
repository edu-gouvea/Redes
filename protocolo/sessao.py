"""Lado cliente do protocolo, sem interface gráfica: a SessaoCliente.

Cuida de toda a conversa com o servidor: entrar na partida, mandar COMANDO na
taxa certa, aceitar só o ESTADO mais recente e juntar os EVENTOs. Não bloqueia:
o cliente chama processar() uma vez por frame e lê os atributos.

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
    sessao.sair()

Os ACKs e retransmissões (etapa d) e o PING (etapa e) entram aqui dentro, sem
mudar esse uso.
"""

from __future__ import annotations

import logging
import time
from enum import StrEnum
from typing import Any, Callable

import config
from protocolo.mensagens import Mensagem, MensagemInvalida, Tipo, codificar, decodificar
from protocolo.sequencia import ContadorSequencia, FiltroMaisRecente
from protocolo.transporte import Endereco, Transporte

log = logging.getLogger("arena.sessao")


class Situacao(StrEnum):
    ENTRANDO = "entrando"          # mandou ENTRAR, esperando ENTRAR_OK
    EM_JOGO = "em_jogo"
    RECUSADO = "recusado"          # recebeu ENTRAR_NEGADO (ver motivo_recusa)
    SEM_RESPOSTA = "sem_resposta"  # o servidor não respondeu ou parou de mandar ESTADO
    ENCERRADA = "encerrada"        # sair() foi chamado


class SessaoCliente:
    def __init__(
        self,
        transporte: Transporte,
        servidor: Endereco,
        nome: str,
        taxa_comandos: int = config.TAXA_COMANDOS,
        relogio: Callable[[], float] = time.monotonic,
    ) -> None:
        self.transporte = transporte
        self.servidor = servidor
        self.nome = _ajustar_nome(nome)
        self.situacao = Situacao.ENTRANDO
        self.meu_id: int | None = None
        self.parametros: dict[str, Any] | None = None   # dados do ENTRAR_OK (arena, raios...)
        self.estado: dict[str, Any] | None = None       # dados do último ESTADO aceito
        self.motivo_recusa: str | None = None
        self.estados_recebidos = 0
        self.descartadas_invalidas = 0
        self._relogio = relogio
        self._intervalo_comandos = 1.0 / taxa_comandos
        self._seq = ContadorSequencia()  # espaço de sequência cliente -> servidor
        self._estados = FiltroMaisRecente()
        self._eventos: list[dict[str, Any]] = []
        self._dx = 0
        self._dy = 0
        self._tentativas_entrar = 0
        self._proximo_entrar = relogio()
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
        if self.situacao is Situacao.ENTRANDO:
            self._tentar_entrar(agora)
        elif self.situacao is Situacao.EM_JOGO:
            self._enviar_comando_se_for_hora(agora)
            self._verificar_servidor(agora)

    def consumir_eventos(self) -> list[dict[str, Any]]:
        eventos, self._eventos = self._eventos, []
        return eventos

    def meu_jogador(self) -> dict[str, Any] | None:
        if self.estado is None:
            return None
        return next((j for j in self.estado["jogadores"] if j["id"] == self.meu_id), None)

    def sair(self) -> None:
        if self.situacao in (Situacao.ENTRANDO, Situacao.EM_JOGO):
            # Até a etapa d o SAIR vai uma vez só; se ele se perder, o servidor
            # remove o jogador pelo timeout de inatividade.
            self._enviar(Tipo.SAIR, {})
        self.situacao = Situacao.ENCERRADA

    @property
    def estados_fora_de_ordem(self) -> int:
        return self._estados.descartadas

    # --- Envio --------------------------------------------------------------

    def _tentar_entrar(self, agora: float) -> None:
        if agora < self._proximo_entrar:
            return
        if self._tentativas_entrar >= config.MAX_TENTATIVAS:
            log.warning("%s: servidor %s:%d não respondeu a %d ENTRAR",
                        self.nome, *self.servidor, self._tentativas_entrar)
            self.situacao = Situacao.SEM_RESPOSTA
            return
        # "Pare e espere" simplificado: sem resposta, pede de novo. O servidor
        # trata ENTRAR repetido sem criar outro jogador (idempotente).
        self._enviar(Tipo.ENTRAR, {"nome": self.nome})
        self._tentativas_entrar += 1
        self._proximo_entrar = agora + config.INTERVALO_REENVIO_ENTRAR

    def _enviar_comando_se_for_hora(self, agora: float) -> None:
        if agora < self._proximo_comando:
            return
        # O COMANDO leva o estado das teclas e vai mesmo sem mudança: se um se
        # perder, o próximo corrige; e o envio contínuo é o sinal de vida.
        self._enviar(Tipo.COMANDO, {"dx": self._dx, "dy": self._dy})
        self._proximo_comando += self._intervalo_comandos
        if self._proximo_comando <= agora:
            self._proximo_comando = agora + self._intervalo_comandos

    def _enviar(self, tipo: Tipo, dados: dict[str, Any]) -> None:
        mensagem = Mensagem(tipo, self._seq.proximo(), dados)
        datagrama = codificar(mensagem)
        log.debug("-> %s seq=%d %dB", tipo, mensagem.seq, len(datagrama))
        self.transporte.enviar(datagrama, self.servidor)

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
            log.warning("descartado do servidor (%d bytes): %s", len(dados), erro)
            return
        log.debug("<- %s seq=%d %dB", mensagem.tipo, mensagem.seq, len(dados))
        if mensagem.tipo is Tipo.ENTRAR_OK:
            self._ao_entrar_ok(mensagem, agora)
        elif mensagem.tipo is Tipo.ENTRAR_NEGADO:
            self._ao_entrar_negado(mensagem)
        elif mensagem.tipo is Tipo.ESTADO:
            self._ao_estado(mensagem, agora)
        elif mensagem.tipo is Tipo.EVENTO and self.situacao is Situacao.EM_JOGO:
            self._eventos.append(mensagem.dados)

    def _ao_entrar_ok(self, mensagem: Mensagem, agora: float) -> None:
        if self.situacao is not Situacao.ENTRANDO:
            return  # ENTRAR_OK repetido (respondeu a um ENTRAR reenviado)
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
