"""Servidor autoritativo: laço com taxa fixa, tabela de clientes e despacho por tipo.

O servidor fala com a rede só através de um `Transporte` (bytes + endereço) e
com o jogo só através de `Partida`. Ele é a ponte entre os dois: traduz
mensagens do protocolo em ações no jogo, e o estado do jogo em mensagens.

Tudo roda numa única thread. O laço espera datagramas só até a hora do
próximo tick (o transporte usa `selectors`), processa o que chegou e, quando
dá a hora, avança a simulação e envia o ESTADO. Sem threads, não há locks nem
condições de corrida.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

import config
from protocolo.mensagens import Mensagem, MensagemInvalida, Tipo, codificar, decodificar
from protocolo.transporte import Endereco, Transporte
from servidor.clientes import Cliente, TabelaClientes
from servidor.jogo import Partida

log = logging.getLogger("arena.servidor")

Tratador = Callable[[Mensagem, Endereco, "Cliente | None", float], None]


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
        self._tratadores: dict[Tipo, Tratador] = {
            Tipo.ENTRAR: self._ao_entrar,
            Tipo.SAIR: self._ao_sair,
            Tipo.COMANDO: self._ao_comandar,
        }

    # --- Laço principal -----------------------------------------------------

    def executar(self) -> None:
        """Roda até parar(). Taxa fixa: um tick a cada `intervalo` segundos."""
        self._rodando = True
        proximo_tick = time.monotonic()
        while self._rodando:
            espera = proximo_tick - time.monotonic()
            for dados, origem in self.transporte.receber(timeout=espera):
                self.ao_receber_datagrama(dados, origem, time.monotonic())
            agora = time.monotonic()
            if agora >= proximo_tick:
                self.tick(agora)
                proximo_tick = _agendar(proximo_tick, self.intervalo, agora)

    def parar(self) -> None:
        self._rodando = False

    def tick(self, agora: float) -> None:
        """Um passo do servidor: simula, avisa os pontos, envia o ESTADO e limpa inativos."""
        for ponto in self.partida.avancar(self.intervalo):
            self._avisar_todos({"evento": "ponto", "jogador": ponto.jogador_id, "pontos": ponto.pontos})
        self._enviar_estados()
        self._remover_inativos(agora)

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
        cliente = self.clientes.buscar(origem)
        if cliente is not None:
            cliente.visto_em = agora
        tratador = self._tratadores.get(mensagem.tipo)
        if tratador is None:
            log.warning("tipo %s não é tratado pelo servidor (de %s:%d)", mensagem.tipo, *origem)
            return
        tratador(mensagem, origem, cliente, agora)

    def _ao_entrar(self, mensagem: Mensagem, origem: Endereco, cliente: Cliente | None, agora: float) -> None:
        if cliente is not None and cliente.em_jogo:
            # ENTRAR repetido (o ENTRAR_OK anterior se perdeu): responde de novo,
            # com o mesmo id. Idempotente: repetir o pedido não cria outro jogador.
            self._enviar_entrar_ok(cliente)
            return
        if cliente is None:
            cliente = self.clientes.adicionar(origem, agora)
        if len(self.partida.jogadores) >= self.max_jogadores:
            log.info("%s:%d recusado: partida cheia", *origem)
            self._enviar(cliente, Tipo.ENTRAR_NEGADO, {"motivo": "partida cheia"})
            return
        jogador = self.partida.adicionar_jogador(mensagem.dados["nome"])
        cliente.jogador_id = jogador.id
        log.info("jogador %d (%s) entrou de %s:%d", jogador.id, jogador.nome, *origem)
        self._enviar_entrar_ok(cliente)
        self._avisar_todos({"evento": "entrou", "jogador": jogador.id, "nome": jogador.nome}, exceto=cliente)

    def _ao_sair(self, mensagem: Mensagem, origem: Endereco, cliente: Cliente | None, agora: float) -> None:
        if cliente is not None:
            self._remover(cliente, motivo="pediu")

    def _ao_comandar(self, mensagem: Mensagem, origem: Endereco, cliente: Cliente | None, agora: float) -> None:
        if cliente is None or not cliente.em_jogo:
            log.debug("COMANDO de %s:%d, que não está na partida: descartado", *origem)
            return
        if not cliente.comandos.aceitar(mensagem.seq):
            # Cap. 3: o UDP não garante ordem. Este COMANDO é mais velho que um
            # já aplicado; usá-lo faria o jogador "voltar no tempo".
            self.comandos_fora_de_ordem += 1
            log.debug("COMANDO seq=%d de %s:%d fora de ordem (último: %d): descartado",
                      mensagem.seq, *origem, cliente.ultimo_comando)
            return
        self.partida.definir_direcao(cliente.jogador_id, mensagem.dados["dx"], mensagem.dados["dy"])

    # --- Envio --------------------------------------------------------------

    def _enviar_entrar_ok(self, cliente: Cliente) -> None:
        partida = self.partida
        self._enviar(cliente, Tipo.ENTRAR_OK, {
            "id": cliente.jogador_id,
            "largura": partida.largura,
            "altura": partida.altura,
            "raio_jogador": partida.raio_jogador,
            "raio_moeda": partida.raio_moeda,
            "velocidade": partida.velocidade,
            "taxa": self.taxa,
        })

    def _enviar_estados(self) -> None:
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
            })

    def _avisar_todos(self, evento: dict[str, Any], exceto: Cliente | None = None) -> None:
        for cliente in self.clientes.em_jogo():
            if cliente is not exceto:
                self._enviar(cliente, Tipo.EVENTO, evento)

    def _enviar(self, cliente: Cliente, tipo: Tipo, dados: dict[str, Any]) -> None:
        mensagem = Mensagem(tipo, cliente.seq_envio.proximo(), dados)
        try:
            datagrama = codificar(mensagem)
        except MensagemInvalida as erro:
            log.error("mensagem %s para %s:%d não pôde ser codificada: %s", tipo, *cliente.endereco, erro)
            return
        log.debug("-> %s seq=%d %dB para %s:%d", tipo, mensagem.seq, len(datagrama), *cliente.endereco)
        self.transporte.enviar(datagrama, cliente.endereco)

    # --- Saída de clientes --------------------------------------------------

    def _remover_inativos(self, agora: float) -> None:
        for cliente in self.clientes.inativos(agora, self.timeout_inatividade):
            self._remover(cliente, motivo="inatividade")

    def _remover(self, cliente: Cliente, motivo: str) -> None:
        self.clientes.remover(cliente.endereco)
        if not cliente.em_jogo:
            return
        self.partida.remover_jogador(cliente.jogador_id)
        log.info("jogador %d saiu (%s)", cliente.jogador_id, motivo)
        self._avisar_todos({"evento": "saiu", "jogador": cliente.jogador_id, "motivo": motivo})


def _agendar(proximo_tick: float, intervalo: float, agora: float) -> float:
    """Próximo tick sem deriva: soma o intervalo ao horário *previsto*, não ao atual.

    Se o processo ficou parado (ex.: suspenso) e perdeu ticks, não tenta
    recuperar todos de uma vez: retoma a partir de agora.
    """
    proximo_tick += intervalo
    if proximo_tick <= agora:
        proximo_tick = agora + intervalo
    return proximo_tick
