"""Servidor com transporte falso: entrada, ESTADO, COMANDO, descarte e inatividade."""

import math

import pytest
from falsos import TransporteFalso

from protocolo.mensagens import Mensagem, Tipo, codificar
from servidor.jogo import Moeda, Partida
from servidor.servidor import Servidor

ANA = ("10.0.0.1", 40001)
BIA = ("10.0.0.1", 40002)   # mesmo IP da ana, outra porta: outro cliente
CAIO = ("10.0.0.2", 40001)  # mesma porta da ana, outro IP: outro cliente


@pytest.fixture
def transporte():
    return TransporteFalso()


@pytest.fixture
def servidor(transporte):
    return Servidor(transporte, Partida(semente=1), max_jogadores=3, timeout_inatividade=5.0)


def mandar(servidor, origem, tipo, dados=None, seq=1, agora=0.0):
    servidor.ao_receber_datagrama(codificar(Mensagem(tipo, seq, dados or {})), origem, agora)


def entrar(servidor, origem, nome="ana", seq=1, agora=0.0):
    mandar(servidor, origem, Tipo.ENTRAR, {"nome": nome}, seq, agora)


def comandar(servidor, origem, dx, dy, seq, agora=0.0):
    mandar(servidor, origem, Tipo.COMANDO, {"dx": dx, "dy": dy}, seq, agora)


def id_de(transporte, origem):
    return transporte.para(origem, Tipo.ENTRAR_OK)[-1].dados["id"]


# --- Entrada ----------------------------------------------------------------

def test_entrar_cria_jogador_e_responde_entrar_ok_com_parametros(servidor, transporte):
    entrar(servidor, ANA)
    [ok] = transporte.para(ANA, Tipo.ENTRAR_OK)
    assert ok.dados == {"id": 1, "largura": 800, "altura": 600, "raio_jogador": 15,
                        "raio_moeda": 8, "velocidade": 200, "taxa": 20}
    assert set(servidor.partida.jogadores) == {1}


def test_entrar_repetido_e_idempotente(servidor, transporte):
    entrar(servidor, ANA, seq=1)
    entrar(servidor, ANA, seq=2)  # o ENTRAR_OK anterior se perdeu e o cliente pediu de novo
    oks = transporte.para(ANA, Tipo.ENTRAR_OK)
    assert [ok.dados["id"] for ok in oks] == [1, 1]
    assert len(servidor.partida.jogadores) == 1


def test_clientes_sao_separados_por_ip_e_porta(servidor, transporte):
    for origem, nome in [(ANA, "ana"), (BIA, "bia"), (CAIO, "caio")]:
        entrar(servidor, origem, nome)
    assert {id_de(transporte, o) for o in (ANA, BIA, CAIO)} == {1, 2, 3}


def test_partida_cheia_responde_entrar_negado(servidor, transporte):
    for porta in (1, 2, 3):
        entrar(servidor, ("10.0.0.9", porta))
    entrar(servidor, ANA)
    [negado] = transporte.para(ANA, Tipo.ENTRAR_NEGADO)
    assert negado.dados == {"motivo": "partida cheia"}
    assert len(servidor.partida.jogadores) == 3


def test_recusado_entra_quando_abre_vaga(servidor, transporte):
    for porta in (1, 2, 3):
        entrar(servidor, ("10.0.0.9", porta))
    entrar(servidor, ANA, seq=1)
    mandar(servidor, ("10.0.0.9", 1), Tipo.SAIR, seq=2)
    entrar(servidor, ANA, seq=2)
    assert transporte.para(ANA, Tipo.ENTRAR_OK)


def test_quem_ja_esta_e_avisado_da_entrada_mas_quem_entrou_nao(servidor, transporte):
    entrar(servidor, ANA, "ana")
    entrar(servidor, BIA, "bia")
    [aviso] = transporte.para(ANA, Tipo.EVENTO)
    assert aviso.dados == {"evento": "entrou", "jogador": 2, "nome": "bia"}
    assert transporte.para(BIA, Tipo.EVENTO) == []


# --- ESTADO e números de sequência ------------------------------------------

def test_tick_envia_estado_para_cada_jogador(servidor, transporte):
    entrar(servidor, ANA, "ana")
    entrar(servidor, BIA, "bia")
    servidor.tick(agora=0.05)
    for origem in (ANA, BIA):
        [estado] = transporte.para(origem, Tipo.ESTADO)
        assert estado.dados["tick"] == 1
        assert {j["nome"] for j in estado.dados["jogadores"]} == {"ana", "bia"}
        assert len(estado.dados["moedas"]) == 5


def test_seq_do_servidor_e_independente_para_cada_cliente(servidor, transporte):
    entrar(servidor, ANA, "ana")
    entrar(servidor, BIA, "bia")
    for t in range(3):
        servidor.tick(agora=0.05 * (t + 1))
    para_ana = [(m.seq, m.tipo) for m in transporte.para(ANA)]
    para_bia = [(m.seq, m.tipo) for m in transporte.para(BIA)]
    # Cada cliente tem a sua numeração, sem lacunas: cada lacuna que o cliente
    # vir será um datagrama perdido. O ACK também consome um seq.
    assert para_ana == [(1, Tipo.ACK), (2, Tipo.ENTRAR_OK), (3, Tipo.EVENTO),
                        (4, Tipo.ESTADO), (5, Tipo.ESTADO), (6, Tipo.ESTADO)]
    assert para_bia == [(1, Tipo.ACK), (2, Tipo.ENTRAR_OK),
                        (3, Tipo.ESTADO), (4, Tipo.ESTADO), (5, Tipo.ESTADO)]


def test_estado_informa_o_ultimo_comando_aplicado(servidor, transporte):
    entrar(servidor, ANA)
    servidor.tick(agora=0.05)
    comandar(servidor, ANA, 1, 0, seq=7)
    servidor.tick(agora=0.10)
    primeiro, segundo = transporte.para(ANA, Tipo.ESTADO)
    assert primeiro.dados["ultimo_comando"] == 0
    assert segundo.dados["ultimo_comando"] == 7


def test_quem_nao_esta_na_partida_nao_recebe_estado(servidor, transporte):
    servidor.tick(agora=0.05)
    assert transporte.enviados == []


# --- COMANDO ----------------------------------------------------------------

def test_comando_move_o_jogador_no_proximo_tick(servidor, transporte):
    entrar(servidor, ANA)
    jogador = servidor.partida.jogadores[1]
    jogador.x, jogador.y = 400.0, 300.0
    comandar(servidor, ANA, 1, 0, seq=2)
    assert jogador.x == 400.0  # só o tick move: o servidor é quem simula
    servidor.tick(agora=0.05)
    assert jogador.x == pytest.approx(400.0 + 200 * 0.05)


def test_comando_fora_de_ordem_e_descartado(servidor, transporte):
    entrar(servidor, ANA)
    comandar(servidor, ANA, 1, 0, seq=10)
    comandar(servidor, ANA, -1, 0, seq=9)  # atrasado na rede: chegou depois do 10
    assert servidor.partida.jogadores[1].dx == 1
    assert servidor.comandos_fora_de_ordem == 1


def test_comando_de_quem_nao_entrou_e_ignorado(servidor, transporte):
    comandar(servidor, ANA, 1, 0, seq=1)
    assert servidor.partida.jogadores == {}
    assert transporte.enviados == []


# --- Mensagens inválidas ----------------------------------------------------

@pytest.mark.parametrize("lixo", [b"", b"\xff\xfe", b"{}", b"[]", b'{"v":9,"tipo":"ENTRAR"}', b"x" * 5000])
def test_datagrama_invalido_e_descartado_sem_derrubar_nem_responder(servidor, transporte, lixo):
    servidor.ao_receber_datagrama(lixo, ANA, agora=0.0)
    assert servidor.descartadas_invalidas == 1
    assert transporte.enviados == []
    entrar(servidor, ANA)  # e o servidor continua funcionando
    assert transporte.para(ANA, Tipo.ENTRAR_OK)


def test_tipo_que_so_o_servidor_envia_e_ignorado(servidor, transporte):
    entrar(servidor, ANA)
    transporte.limpar()
    mandar(servidor, ANA, Tipo.ESTADO, {"tick": 1, "ultimo_comando": 0, "jogadores": [], "moedas": []}, seq=2)
    assert transporte.enviados == []


# --- Saída ------------------------------------------------------------------

def test_sair_remove_e_avisa_os_outros(servidor, transporte):
    entrar(servidor, ANA, "ana")
    entrar(servidor, BIA, "bia")
    mandar(servidor, BIA, Tipo.SAIR, seq=2)
    assert set(servidor.partida.jogadores) == {1}
    assert transporte.para(ANA, Tipo.EVENTO)[-1].dados == {"evento": "saiu", "jogador": 2, "motivo": "pediu"}


def test_cliente_em_silencio_e_removido_por_inatividade(servidor, transporte):
    entrar(servidor, ANA, "ana", agora=0.0)
    entrar(servidor, BIA, "bia", agora=0.0)
    comandar(servidor, ANA, 0, 0, seq=2, agora=4.0)  # ana continua dando sinal de vida
    servidor.tick(agora=5.5)                         # bia está calada há 5,5 s
    assert set(servidor.partida.jogadores) == {1}
    aviso = transporte.para(ANA, Tipo.EVENTO)[-1]
    assert aviso.dados == {"evento": "saiu", "jogador": 2, "motivo": "inatividade"}


def test_qualquer_mensagem_valida_conta_como_sinal_de_vida(servidor, transporte):
    entrar(servidor, ANA, agora=0.0)
    entrar(servidor, ANA, seq=2, agora=4.0)
    servidor.tick(agora=8.0)
    assert set(servidor.partida.jogadores) == {1}


# --- Pontos -----------------------------------------------------------------

def test_moeda_coletada_gera_evento_de_ponto_para_todos(servidor, transporte):
    entrar(servidor, ANA, "ana")
    entrar(servidor, BIA, "bia")
    ana = servidor.partida.jogadores[1]
    servidor.partida.moedas[0] = Moeda(ana.x, ana.y)
    servidor.tick(agora=0.05)
    for origem in (ANA, BIA):
        assert transporte.para(origem, Tipo.EVENTO)[-1].dados == {"evento": "ponto", "jogador": 1, "pontos": 1}
    [estado] = transporte.para(BIA, Tipo.ESTADO)
    placar = {j["id"]: j["pontos"] for j in estado.dados["jogadores"]}
    assert placar == {1: 1, 2: 0}


def test_estado_tem_posicoes_com_uma_casa_decimal(servidor, transporte):
    entrar(servidor, ANA)
    servidor.partida.jogadores[1].x = 100 + math.pi
    servidor.tick(agora=0.05)
    [estado] = transporte.para(ANA, Tipo.ESTADO)
    assert estado.dados["jogadores"][0]["x"] == 103.1


# --- Confiabilidade (etapa d) -----------------------------------------------

def confirmar(servidor, origem, mensagem, seq, agora):
    mandar(servidor, origem, Tipo.ACK, {"ack": mensagem.seq}, seq=seq, agora=agora)


def test_entrar_recebe_ack_antes_do_entrar_ok(servidor, transporte):
    entrar(servidor, ANA, seq=1)
    ack, ok = transporte.para(ANA)
    assert (ack.tipo, ack.dados) == (Tipo.ACK, {"ack": 1})
    assert ok.tipo is Tipo.ENTRAR_OK


def test_retransmissao_do_mesmo_entrar_so_gera_novo_ack(servidor, transporte):
    entrar(servidor, ANA, seq=1)
    entrar(servidor, ANA, seq=1)  # o ACK se perdeu e o cliente retransmitiu
    tipos = [m.tipo for m in transporte.para(ANA)]
    assert tipos == [Tipo.ACK, Tipo.ENTRAR_OK, Tipo.ACK]
    assert servidor.clientes.buscar(ANA).canal.contadores().duplicadas_recebidas == 1


def test_entrar_ok_sem_ack_e_retransmitido_e_para_quando_confirmado(servidor, transporte):
    entrar(servidor, ANA, agora=0.0)
    [ok] = transporte.para(ANA, Tipo.ENTRAR_OK)
    servidor.verificar_timeouts(agora=1.0)
    assert transporte.para(ANA, Tipo.ENTRAR_OK) == [ok, ok]  # mesma mensagem, mesmo seq
    confirmar(servidor, ANA, ok, seq=2, agora=1.1)
    servidor.verificar_timeouts(agora=10.0)
    assert len(transporte.para(ANA, Tipo.ENTRAR_OK)) == 2


def test_evento_de_ponto_e_retransmitido_ate_o_ack(servidor, transporte):
    entrar(servidor, ANA, agora=0.0)
    confirmar(servidor, ANA, transporte.para(ANA, Tipo.ENTRAR_OK)[0], seq=2, agora=0.01)
    ana = servidor.partida.jogadores[1]
    servidor.partida.moedas[0] = Moeda(ana.x, ana.y)
    servidor.tick(agora=0.05)
    [ponto] = transporte.para(ANA, Tipo.EVENTO)
    # O ACK do ENTRAR_OK veio em 10 ms: o timeout adaptativo já aprendeu o RTT
    # (10 ms + 4 · 5 ms = 30 ms) e ficou no mínimo configurado, 100 ms.
    assert servidor.clientes.buscar(ANA).canal.estimador.timeout == pytest.approx(0.1)
    servidor.verificar_timeouts(agora=0.05 + 0.09)
    assert len(transporte.para(ANA, Tipo.EVENTO)) == 1
    servidor.verificar_timeouts(agora=0.05 + 0.1)
    assert transporte.para(ANA, Tipo.EVENTO) == [ponto, ponto]


def test_prazo_de_retransmissao_encurta_a_espera_do_laco(servidor, transporte):
    import time

    agora = time.monotonic()
    servidor._proximo_tick = agora + 10.0
    entrar(servidor, ANA, agora=agora)  # ENTRAR_OK pendente: timeout de 1 s
    assert servidor._proximo_prazo() == pytest.approx(agora + 1.0)


def test_sair_recebe_ack_e_retransmissao_do_sair_tambem(servidor, transporte):
    entrar(servidor, ANA, seq=1)
    mandar(servidor, ANA, Tipo.SAIR, seq=5)
    mandar(servidor, ANA, Tipo.SAIR, seq=5)  # o ACK se perdeu; ana já não está na tabela
    acks = [m.dados["ack"] for m in transporte.para(ANA, Tipo.ACK)]
    assert acks == [1, 5, 5]
    assert servidor.partida.jogadores == {}


def test_entrar_negado_e_confiavel(servidor, transporte):
    for porta in (1, 2, 3):
        entrar(servidor, ("10.0.0.9", porta))
    entrar(servidor, ANA, agora=0.0)
    servidor.verificar_timeouts(agora=1.0)
    assert len(transporte.para(ANA, Tipo.ENTRAR_NEGADO)) == 2


def test_ack_de_cliente_desconhecido_e_ignorado(servidor, transporte):
    mandar(servidor, ANA, Tipo.ACK, {"ack": 1}, seq=1)
    assert transporte.enviados == []
    assert len(servidor.clientes) == 0


# --- PING/PONG e contadores (etapa e) ---------------------------------------

def test_ping_e_respondido_na_hora_com_o_ts_do_ping(servidor, transporte):
    entrar(servidor, ANA)
    transporte.limpar()
    ping = Mensagem(Tipo.PING, 9, {}, ts=1795564805000)
    servidor.ao_receber_datagrama(codificar(ping), ANA, agora=0.01)
    # Sem tick nenhum no meio: o PONG sai no tratamento do próprio datagrama.
    [pong] = transporte.para(ANA, Tipo.PONG)
    assert pong.dados == {"ts_eco": 1795564805000}


def test_ping_de_quem_nao_entrou_e_respondido_sem_criar_cliente(servidor, transporte):
    mandar(servidor, ANA, Tipo.PING, seq=1)
    assert transporte.para(ANA, Tipo.PONG)
    assert len(servidor.clientes) == 0


def test_ping_conta_como_sinal_de_vida(servidor, transporte):
    entrar(servidor, ANA, agora=0.0)
    mandar(servidor, ANA, Tipo.PING, seq=2, agora=4.0)
    servidor.tick(agora=8.0)
    assert set(servidor.partida.jogadores) == {1}


def test_contadores_do_servidor(servidor, transporte):
    entrar(servidor, ANA, agora=0.0)
    entrar(servidor, BIA, "bia", agora=0.0)
    servidor.ao_receber_datagrama(b"lixo", CAIO, agora=0.0)
    mandar(servidor, ANA, Tipo.PING, seq=2)
    servidor.verificar_timeouts(agora=1.0)       # ENTRAR_OKs e o EVENTO "entrou" sem ACK
    mandar(servidor, BIA, Tipo.SAIR, seq=3)      # a bia sai: os números dela não podem sumir
    c = servidor.contadores()
    assert c["jogadores"] == 1
    assert c["pacotes_recebidos"] == 5 and c["invalidos_descartados"] == 1
    assert c["pings_respondidos"] == 1
    assert c["confiaveis_enviadas"] == 4         # ENTRAR_OK x2, EVENTO entrou e EVENTO saiu
    assert c["confiaveis_retransmitidas"] == 3   # os três primeiros, em 1 s
    assert c["bytes_enviados"] > 0
    assert "retransmitidas 3" in servidor.resumo()
