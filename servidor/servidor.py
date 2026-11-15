"""Servidor autoritativo: laço com taxa fixa, tabela de clientes e despacho por tipo.

O servidor fala com a rede só através de um `Transporte` (bytes + endereço) e
com o jogo só através de `Partida`. Ele é a ponte entre os dois: traduz
mensagens do protocolo em ações no jogo, e o estado do jogo em mensagens.

Tudo roda numa única thread. Cada volta do laço (passo) espera datagramas só
até o próximo prazo (o tick ou uma retransmissão), processa o que chegou,
confere os temporizadores e, quando dá a hora, avança a simulação e envia o
ESTADO. Sem threads, não há locks nem condições de corrida.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import config
from protocolo.confiavel import CanalConfiavel
from protocolo.mensagens import Mensagem, MensagemInvalida, Tipo, codificar, decodificar
from protocolo.transporte import Endereco, Transporte
from servidor.clientes import Cliente, TabelaClientes
from servidor.jogo import Partida

log = logging.getLogger("arena.servidor")


class Servidor:
    def __init__(
        self,
        transporte: Transporte,
        partida: Partida | None = None,
        taxa: int = config.TAXA_ATUALIZACAO,
        max_jogadores: int = config.MAX_JOGADORES,
        timeout_inatividade: float = config.TIMEOUT_INATIVIDADE,
    ) -> None:
        self.transporte = transporte
        self.partida = partida if partida is not None else Partida()
        self.clientes = TabelaClientes()
        self.taxa = taxa
        self.intervalo = 1.0 / taxa
        self.max_jogadores = max_jogadores
        self.timeout_inatividade = timeout_inatividade
        self.descartadas_invalidas = 0
        self.comandos_fora_de_ordem = 0
        self._rodando = False
        self._proximo_tick = time.monotonic()
        self._tratadores = {
            Tipo.ENTRAR: self._ao_entrar,
            Tipo.SAIR: self._ao_sair,
            Tipo.COMANDO: self._ao_comandar,
        }

    # --- Laço principal -----------------------------------------------------

    def executar(self) -> None:
        """Roda até parar()."""
        self._rodando = True
        self._proximo_tick = time.monotonic()
        while self._rodando:
            self.passo()

    def parar(self) -> None:
        self._rodando = False

    def passo(self, espera_maxima: float | None = None) -> None:
        """Uma volta do laço: espera pacotes até o próximo prazo, trata, retransmite e, na hora, faz o tick."""
        espera = self._proximo_prazo() - time.monotonic()
        if espera_maxima is not None:
            espera = min(espera, espera_maxima)
        for dados, origem in self.transporte.receber(timeout=espera):
            self.ao_receber_datagrama(dados, origem, time.monotonic())
        agora = time.monotonic()
        self.verificar_timeouts(agora)
        if agora >= self._proximo_tick:
            self.tick(agora)
            self._proximo_tick = _agendar(self._proximo_tick, self.intervalo, agora)

    def _proximo_prazo(self) -> float:
        """O que vier antes: o próximo tick ou o temporizador de retransmissão mais próximo."""
        prazos = [c.canal.proximo_prazo() for c in self.clientes.todos()]
        return min([self._proximo_tick] + [p for p in prazos if p is not None])

    def tick(self, agora: float) -> None:
        """Um passo do servidor: simula, avisa os pontos, envia o ESTADO e limpa inativos."""
        for ponto in self.partida.avancar(self.intervalo):
            self._avisar_todos({"evento": "ponto", "jogador": ponto.jogador_id, "pontos": ponto.pontos}, agora)
        self._enviar_estados(agora)
        self._remover_inativos(agora)

    def verificar_timeouts(self, agora: float) -> None:
        for cliente in self.clientes.todos():
            cliente.canal.verificar_timeouts(agora)

    # --- Recepção -----------------------------------------------------------

    def ao_receber_datagrama(self, dados: bytes, origem: Endereco, agora: float) -> None:
        try:
            mensagem = decodificar(dados)
        except MensagemInvalida as erro:
            # Lixo, versão errada ou datagrama grande demais: descarta, registra
            # e não responde (responder a lixo pode criar laços ou ser usado
            # para refletir tráfego contra um endereço forjado).
            self.descartadas_invalidas += 1
            log.warning("descartado de %s:%d (%d bytes): %s | início: %r",
                        *origem, len(dados), erro, dados[:60])
            return
        log.debug("<- %s seq=%d %dB de %s:%d", mensagem.tipo, mensagem.seq, len(dados), *origem)
        cliente = self._cliente_para(mensagem, origem, agora)
        if cliente is None:
            return
        cliente.visto_em = agora
        # Camada de confiabilidade: manda ACK das confiáveis, consome os ACKs
        # recebidos e barra duplicatas. Só o que sobra chega à lógica do jogo.
        if not cliente.canal.ao_receber(mensagem, agora):
            return
        tratador = self._tratadores.get(mensagem.tipo)
        if tratador is None:
            log.warning("tipo %s não é tratado pelo servidor (de %s:%d)", mensagem.tipo, *origem)
            return
        tratador(mensagem, cliente, agora)

    def _cliente_para(self, mensagem: Mensagem, origem: Endereco, agora: float) -> Cliente | None:
        """Demultiplexação pela origem. Endereço novo só ganha entrada na tabela com ENTRAR."""
        cliente = self.clientes.buscar(origem)
        if cliente is not None:
            return cliente
        if mensagem.tipo is Tipo.ENTRAR:
            return self.clientes.adicionar(origem, agora, self._novo_canal(origem))
        if mensagem.tipo is Tipo.SAIR:
            # Retransmissão de um SAIR já processado (o ACK se perdeu): confirma
            # sem guardar estado, para o cliente parar de retransmitir.
            self._novo_canal(origem).ao_receber(mensagem, agora)
            return None
        log.debug("%s de %s:%d, que não está na partida: descartado", mensagem.tipo, *origem)
        return None

    def _ao_entrar(self, mensagem: Mensagem, cliente: Cliente, agora: float) -> None:
        if cliente.em_jogo:
            # Novo ENTRAR de quem já é jogador: responde de novo, com o mesmo id.
            # Idempotente: repetir o pedido não cria outro jogador. (A
            # retransmissão do MESMO ENTRAR nem chega aqui: o canal a reconhece
            # como duplicata.)
            self._enviar_entrar_ok(cliente, agora)
            return
        if len(self.partida.jogadores) >= self.max_jogadores:
            # O endereço fica na tabela, sem jogador, só para o ENTRAR_NEGADO
            # poder ser retransmitido; sai pelo timeout de inatividade.
            log.info("%s:%d recusado: partida cheia", *cliente.endereco)
            self._enviar(cliente, Tipo.ENTRAR_NEGADO, {"motivo": "partida cheia"}, agora)
            return
        jogador = self.partida.adicionar_jogador(mensagem.dados["nome"])
        cliente.jogador_id = jogador.id
        log.info("jogador %d (%s) entrou de %s:%d", jogador.id, jogador.nome, *cliente.endereco)
        self._enviar_entrar_ok(cliente, agora)
        self._avisar_todos({"evento": "entrou", "jogador": jogador.id, "nome": jogador.nome}, agora, exceto=cliente)

    def _ao_sair(self, mensagem: Mensagem, cliente: Cliente, agora: float) -> None:
        self._remover(cliente, "pediu", agora)

    def _ao_comandar(self, mensagem: Mensagem, cliente: Cliente, agora: float) -> None:
        if not cliente.em_jogo:
            return
        if not cliente.comandos.aceitar(mensagem.seq):
            # Cap. 3: o UDP não garante ordem. Este COMANDO é mais velho que um
            # já aplicado; usá-lo faria o jogador "voltar no tempo".
            self.comandos_fora_de_ordem += 1
            log.debug("COMANDO seq=%d de %s:%d fora de ordem (último: %d): descartado",
                      mensagem.seq, *cliente.endereco, cliente.ultimo_comando)
            return
        self.partida.definir_direcao(cliente.jogador_id, mensagem.dados["dx"], mensagem.dados["dy"])

    # --- Envio --------------------------------------------------------------

    def _enviar_entrar_ok(self, cliente: Cliente, agora: float) -> None:
        partida = self.partida
        self._enviar(cliente, Tipo.ENTRAR_OK, {
            "id": cliente.jogador_id,
            "largura": partida.largura,
            "altura": partida.altura,
            "raio_jogador": partida.raio_jogador,
            "raio_moeda": partida.raio_moeda,
            "velocidade": partida.velocidade,
            "taxa": self.taxa,
        }, agora)

    def _enviar_estados(self, agora: float) -> None:
        jogadores = [
            {"id": j.id, "nome": j.nome, "x": round(j.x, 1), "y": round(j.y, 1), "pontos": j.pontos}
            for j in self.partida.jogadores.values()
        ]
        moedas = [[m.x, m.y] for m in self.partida.moedas]
        # Um ESTADO por cliente: o seq e o ultimo_comando são de cada um.
        for cliente in self.clientes.em_jogo():
            self._enviar(cliente, Tipo.ESTADO, {
                "tick": self.partida.tick,
                "ultimo_comando": cliente.ultimo_comando,
                "jogadores": jogadores,
                "moedas": moedas,
            }, agora)

    def _avisar_todos(self, evento: dict[str, Any], agora: float, exceto: Cliente | None = None) -> None:
        for cliente in self.clientes.em_jogo():
            if cliente is not exceto:
                self._enviar(cliente, Tipo.EVENTO, evento, agora)

    def _enviar(self, cliente: Cliente, tipo: Tipo, dados: dict[str, Any], agora: float) -> None:
        # O canal numera; se o tipo for confiável, ele também guarda a
        # mensagem até o ACK e retransmite sozinho em verificar_timeouts().
        cliente.canal.enviar(tipo, dados, agora)

    def _novo_canal(self, endereco: Endereco) -> CanalConfiavel:
        return CanalConfiavel(
            enviar=lambda mensagem: self._transmitir(mensagem, endereco),
            ao_desistir=lambda mensagem: self._ao_desistir(mensagem, endereco),
            rotulo=f"servidor->{endereco[0]}:{endereco[1]}",
        )

    def _transmitir(self, mensagem: Mensagem, destino: Endereco) -> None:
        try:
            datagrama = codificar(mensagem)
        except MensagemInvalida as erro:
            log.error("mensagem %s para %s:%d não pôde ser codificada: %s", mensagem.tipo, *destino, erro)
            return
        log.debug("-> %s seq=%d %dB para %s:%d", mensagem.tipo, mensagem.seq, len(datagrama), *destino)
        self.transporte.enviar(datagrama, destino)

    def _ao_desistir(self, mensagem: Mensagem, destino: Endereco) -> None:
        # Desistir não quer dizer que o cliente não recebeu: pode ter sido só
        # o ACK que se perdeu todas as vezes. Se o cliente sumiu de verdade, o
        # timeout de inatividade o remove.
        log.warning("%s seq=%d para %s:%d nunca foi confirmado", mensagem.tipo, mensagem.seq, *destino)

    # --- Saída de clientes --------------------------------------------------

    def _remover_inativos(self, agora: float) -> None:
        for cliente in self.clientes.inativos(agora, self.timeout_inatividade):
            self._remover(cliente, "inatividade", agora)

    def _remover(self, cliente: Cliente, motivo: str, agora: float) -> None:
        self.clientes.remover(cliente.endereco)
        if not cliente.em_jogo:
            return
        self.partida.remover_jogador(cliente.jogador_id)
        log.info("jogador %d saiu (%s)", cliente.jogador_id, motivo)
        self._avisar_todos({"evento": "saiu", "jogador": cliente.jogador_id, "motivo": motivo}, agora)


def _agendar(proximo_tick: float, intervalo: float, agora: float) -> float:
    """Próximo tick sem deriva: soma o intervalo ao horário *previsto*, não ao atual.

    Se o processo ficou parado (ex.: suspenso) e perdeu ticks, não tenta
    recuperar todos de uma vez: retoma a partir de agora.
    """
    proximo_tick += intervalo
    if proximo_tick <= agora:
        proximo_tick = agora + intervalo
    return proximo_tick
