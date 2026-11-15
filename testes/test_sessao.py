"""SessaoCliente com transporte falso e relógio controlado (protocolo/sessao.py)."""

import pytest
from falsos import Relogio, TransporteFalso

import config
from protocolo.mensagens import Mensagem, Tipo
from protocolo.sessao import SessaoCliente, Situacao

SERVIDOR = ("10.0.0.10", 5005)
OK = {"id": 3, "largura": 800, "altura": 600, "raio_jogador": 15, "raio_moeda": 8, "velocidade": 200, "taxa": 20}


@pytest.fixture
def relogio():
    return Relogio()


@pytest.fixture
def transporte():
    return TransporteFalso(("10.0.0.1", 40001))


@pytest.fixture
def sessao(transporte, relogio):
    return SessaoCliente(transporte, SERVIDOR, "ana", relogio=relogio)


def estado(tick, jogadores=None):
    return {"tick": tick, "ultimo_comando": 0, "moedas": [[1.0, 2.0]],
            "jogadores": jogadores or [{"id": 3, "nome": "ana", "x": 10.0, "y": 20.0, "pontos": 0}]}


def chega(transporte, tipo, dados, seq, origem=SERVIDOR):
    transporte.entregar(Mensagem(tipo, seq, dados), origem)


def em_jogo(sessao, transporte):
    sessao.processar()
    chega(transporte, Tipo.ENTRAR_OK, OK, seq=1)
    sessao.processar()
    transporte.limpar()


def enviados(transporte, tipo):
    return [m for m, destino in transporte.enviados if m.tipo == tipo and destino == SERVIDOR]


# --- Entrada ----------------------------------------------------------------

def test_primeiro_processar_envia_entrar(sessao, transporte):
    sessao.processar()
    [entrar] = enviados(transporte, Tipo.ENTRAR)
    assert entrar.dados == {"nome": "ana"}
    assert entrar.seq == 1


def test_entrar_sem_ack_e_retransmitido_pelo_canal_e_depois_desiste(sessao, transporte, relogio):
    instantes = []
    for _ in range(1400):  # 14 s em passos de 10 ms
        antes = len(enviados(transporte, Tipo.ENTRAR))
        sessao.processar()
        if len(enviados(transporte, Tipo.ENTRAR)) > antes:
            instantes.append(round(relogio.agora - 1000.0, 2))
        relogio.avancar(0.01)
    entradas = enviados(transporte, Tipo.ENTRAR)
    assert len(entradas) == config.MAX_TENTATIVAS
    assert {m.seq for m in entradas} == {1}  # sempre a mesma mensagem
    # timeout de 1 s dobrando até o teto de 3 s (resolução de 10 ms do laço do teste)
    assert instantes == pytest.approx([0.0, 1.0, 3.0, 6.0, 9.0], abs=0.05)
    assert sessao.situacao is Situacao.SEM_RESPOSTA


def test_entrar_confirmado_nao_e_mais_retransmitido(sessao, transporte, relogio):
    sessao.processar()
    chega(transporte, Tipo.ACK, {"ack": 1}, seq=1)
    relogio.avancar(0.05)
    sessao.processar()
    relogio.avancar(5.0)
    sessao.processar()
    assert len(enviados(transporte, Tipo.ENTRAR)) == 1
    assert sessao.situacao is Situacao.ENTRANDO  # confirmado, esperando o ENTRAR_OK
    assert sessao.canal.contadores().rtt_estimado_ms == pytest.approx(50)


def test_entrar_ok_e_confirmado_com_ack(sessao, transporte):
    sessao.processar()
    chega(transporte, Tipo.ENTRAR_OK, OK, seq=2)
    sessao.processar()
    [ack] = enviados(transporte, Tipo.ACK)
    assert ack.dados == {"ack": 2}


def test_entrar_ok_retransmitido_e_confirmado_de_novo_sem_reprocessar(sessao, transporte):
    sessao.processar()
    chega(transporte, Tipo.ENTRAR_OK, OK, seq=2)
    sessao.processar()
    chega(transporte, Tipo.ENTRAR_OK, dict(OK, id=99), seq=2)  # mesma mensagem: o ACK se perdeu
    sessao.processar()
    assert sessao.meu_id == 3
    assert len(enviados(transporte, Tipo.ACK)) == 2
    assert sessao.canal.contadores().duplicadas_recebidas == 1


def test_entrar_ok_coloca_em_jogo(sessao, transporte):
    sessao.processar()
    chega(transporte, Tipo.ENTRAR_OK, OK, seq=1)
    sessao.processar()
    assert sessao.situacao is Situacao.EM_JOGO
    assert sessao.meu_id == 3
    assert sessao.parametros["largura"] == 800


def test_entrar_negado(sessao, transporte):
    sessao.processar()
    chega(transporte, Tipo.ENTRAR_NEGADO, {"motivo": "partida cheia"}, seq=1)
    sessao.processar()
    assert sessao.situacao is Situacao.RECUSADO
    assert sessao.motivo_recusa == "partida cheia"


def test_estado_antes_do_entrar_ok_e_ignorado(sessao, transporte):
    sessao.processar()
    chega(transporte, Tipo.ESTADO, estado(1), seq=5)
    sessao.processar()
    assert sessao.estado is None


def test_nome_e_cortado_no_limite_do_protocolo(transporte, relogio):
    sessao = SessaoCliente(transporte, SERVIDOR, "  um nome bem comprido  ", relogio=relogio)
    assert sessao.nome == "um nome bem "[: config.TAMANHO_MAXIMO_NOME]
    sessao.processar()  # e o ENTRAR sai válido


@pytest.mark.parametrize("nome", ["", "   ", "a\nb"])
def test_nome_invalido_e_recusado_logo(transporte, nome):
    with pytest.raises(ValueError):
        SessaoCliente(transporte, SERVIDOR, nome)


# --- COMANDO ----------------------------------------------------------------

def test_comando_sai_na_taxa_fixa_mesmo_sem_mudanca(sessao, transporte, relogio):
    em_jogo(sessao, transporte)
    for _ in range(40):  # 1 s em passos de 25 ms, como um jogo a 40 fps
        relogio.avancar(0.025)
        sessao.processar()
    comandos = enviados(transporte, Tipo.COMANDO)
    assert len(comandos) == config.TAXA_COMANDOS
    assert all(c.dados == {"dx": 0, "dy": 0} for c in comandos)
    assert [c.seq for c in comandos] == sorted(c.seq for c in comandos)  # seq crescente


def test_comando_leva_a_direcao_atual(sessao, transporte, relogio):
    em_jogo(sessao, transporte)
    sessao.definir_direcao(True, -1)  # bool de tecla do pygame vira inteiro
    relogio.avancar(0.05)
    sessao.processar()
    assert enviados(transporte, Tipo.COMANDO)[-1].dados == {"dx": 1, "dy": -1}


def test_direcao_invalida_e_erro(sessao):
    with pytest.raises(ValueError):
        sessao.definir_direcao(2, 0)


def test_nao_envia_comando_antes_de_entrar(sessao, transporte, relogio):
    for _ in range(5):
        sessao.processar()
        relogio.avancar(0.05)
    assert enviados(transporte, Tipo.COMANDO) == []


# --- ESTADO e EVENTO --------------------------------------------------------

def test_estado_mais_recente_e_aceito_e_o_velho_descartado(sessao, transporte):
    em_jogo(sessao, transporte)
    chega(transporte, Tipo.ESTADO, estado(11), seq=11)
    chega(transporte, Tipo.ESTADO, estado(10), seq=10)  # atrasou na rede
    sessao.processar()
    assert sessao.estado["tick"] == 11
    assert sessao.estados_recebidos == 1
    assert sessao.estados_fora_de_ordem == 1


def test_meu_jogador(sessao, transporte):
    em_jogo(sessao, transporte)
    chega(transporte, Tipo.ESTADO, estado(1), seq=2)
    sessao.processar()
    assert sessao.meu_jogador()["nome"] == "ana"


def test_eventos_sao_entregues_uma_vez(sessao, transporte):
    em_jogo(sessao, transporte)
    chega(transporte, Tipo.EVENTO, {"evento": "ponto", "jogador": 3, "pontos": 1}, seq=2)
    sessao.processar()
    assert sessao.consumir_eventos() == [{"evento": "ponto", "jogador": 3, "pontos": 1}]
    assert sessao.consumir_eventos() == []


def test_evento_retransmitido_conta_uma_vez_so(sessao, transporte):
    em_jogo(sessao, transporte)
    ponto = Mensagem(Tipo.EVENTO, 7, {"evento": "ponto", "jogador": 3, "pontos": 1})
    transporte.entregar(ponto, SERVIDOR)
    transporte.entregar(ponto, SERVIDOR)  # retransmissão: o ACK da primeira se perdeu
    sessao.processar()
    assert len(sessao.consumir_eventos()) == 1
    assert [m.dados for m in enviados(transporte, Tipo.ACK)] == [{"ack": 7}, {"ack": 7}]


def test_datagrama_de_quem_nao_e_o_servidor_e_ignorado(sessao, transporte):
    em_jogo(sessao, transporte)
    chega(transporte, Tipo.ESTADO, estado(99), seq=99, origem=("6.6.6.6", 5005))
    sessao.processar()
    assert sessao.estado is None


def test_lixo_do_servidor_nao_derruba_a_sessao(sessao, transporte):
    em_jogo(sessao, transporte)
    transporte.entregar(b"\x00lixo", SERVIDOR)
    sessao.processar()
    assert sessao.descartadas_invalidas == 1
    assert sessao.situacao is Situacao.EM_JOGO


def test_servidor_calado_por_muito_tempo_vira_sem_resposta(sessao, transporte, relogio):
    em_jogo(sessao, transporte)
    relogio.avancar(config.TIMEOUT_INATIVIDADE + 0.1)
    sessao.processar()
    assert sessao.situacao is Situacao.SEM_RESPOSTA


# --- Saída ------------------------------------------------------------------

def test_sair_envia_sair_e_encerra(sessao, transporte):
    em_jogo(sessao, transporte)
    sessao.sair(prazo=0.05)  # o transporte falso nunca responde: espera o prazo e desiste
    assert len(enviados(transporte, Tipo.SAIR)) == 1
    assert sessao.situacao is Situacao.ENCERRADA
    sessao.processar()  # depois de encerrada, não manda mais nada
    assert enviados(transporte, Tipo.COMANDO) == []
