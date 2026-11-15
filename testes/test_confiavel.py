"""Confiabilidade seletiva sem rede: perda de dado, perda de ACK, duplicatas e desistência.

Dois CanalConfiavel ("servidor" e "cliente") ligados por listas: o teste
decide o que é entregue e o que se perde no caminho.
"""

import pytest

import config
from protocolo.confiavel import CanalConfiavel, EstimadorRTT, JanelaDuplicatas
from protocolo.mensagens import Mensagem, Tipo

PONTO = {"evento": "ponto", "jogador": 1, "pontos": 1}
ESTADO = {"tick": 1, "ultimo_comando": 0, "jogadores": [], "moedas": []}


class Ponta:
    """Um canal mais a caixa de saída onde caem as mensagens que ele transmite."""

    def __init__(self):
        self.saida: list[Mensagem] = []
        self.desistiu: list[Mensagem] = []
        self.canal = CanalConfiavel(self.saida.append, ao_desistir=self.desistiu.append)

    def tirar(self) -> list[Mensagem]:
        mensagens, self.saida[:] = list(self.saida), []
        return mensagens


@pytest.fixture
def servidor():
    return Ponta()


@pytest.fixture
def cliente():
    return Ponta()


def entregar(de: Ponta, para: Ponta, agora: float) -> list[Mensagem]:
    """Leva tudo o que `de` transmitiu até `para`; devolve o que a aplicação de `para` deve processar."""
    return [m for m in de.tirar() if para.canal.ao_receber(m, agora)]


def perder(de: Ponta) -> list[Mensagem]:
    return de.tirar()


# --- Caminho feliz ----------------------------------------------------------

def test_mensagem_confiavel_e_confirmada_e_gera_amostra_de_rtt(servidor, cliente):
    enviada = servidor.canal.enviar(Tipo.EVENTO, PONTO, agora=0.0)
    assert servidor.canal.pendentes == 1
    assert entregar(servidor, cliente, agora=0.04) == [enviada]
    [ack] = cliente.saida
    assert ack.tipo is Tipo.ACK and ack.dados == {"ack": enviada.seq}
    entregar(cliente, servidor, agora=0.08)
    assert servidor.canal.pendentes == 0
    assert servidor.canal.estimador.estimated_rtt == pytest.approx(0.08)


def test_mensagem_nao_confiavel_nao_fica_pendente_nem_gera_ack(servidor, cliente):
    servidor.canal.enviar(Tipo.ESTADO, ESTADO, agora=0.0)
    assert servidor.canal.pendentes == 0
    assert len(entregar(servidor, cliente, agora=0.01)) == 1
    assert cliente.saida == []


def test_ack_nunca_e_confirmado(servidor, cliente):
    servidor.canal.enviar(Tipo.EVENTO, PONTO, agora=0.0)
    entregar(servidor, cliente, agora=0.01)
    assert entregar(cliente, servidor, agora=0.02) == []  # o ACK não sobe para a aplicação
    assert servidor.saida == []                           # e não gera ACK de volta


def test_seq_e_um_espaco_so_para_confiaveis_e_nao_confiaveis(servidor):
    tipos = [Tipo.ESTADO, Tipo.EVENTO, Tipo.ESTADO, Tipo.ACK]
    dados = [ESTADO, PONTO, ESTADO, {"ack": 9}]
    seqs = [servidor.canal.enviar(t, d, agora=0.0).seq for t, d in zip(tipos, dados)]
    assert seqs == [1, 2, 3, 4]


# --- Perda do dado: timeout e retransmissão (rdt3.0) ------------------------

def test_dado_perdido_e_retransmitido_com_o_mesmo_seq_e_ts(servidor, cliente):
    original = servidor.canal.enviar(Tipo.EVENTO, PONTO, agora=0.0)
    perder(servidor)
    servidor.canal.verificar_timeouts(agora=config.TIMEOUT_INICIAL - 0.01)
    assert servidor.saida == []  # ainda não deu o tempo
    servidor.canal.verificar_timeouts(agora=config.TIMEOUT_INICIAL)
    [retransmitida] = servidor.saida
    assert retransmitida == original  # é o mesmo pacote, não um novo
    assert entregar(servidor, cliente, agora=1.05) == [original]
    entregar(cliente, servidor, agora=1.1)
    assert servidor.canal.pendentes == 0
    assert servidor.canal.contadores().retransmitidas == 1


def test_retransmitida_nao_gera_amostra_de_rtt_algoritmo_de_karn(servidor, cliente):
    servidor.canal.enviar(Tipo.EVENTO, PONTO, agora=0.0)
    perder(servidor)
    servidor.canal.verificar_timeouts(agora=1.0)
    entregar(servidor, cliente, agora=1.05)
    entregar(cliente, servidor, agora=1.1)
    # Medido desde a 1ª transmissão daria 1,1 s; desde a 2ª, 0,1 s. Nenhum dos dois é confiável.
    assert servidor.canal.estimador.estimated_rtt is None
    assert servidor.canal.estimador.timeout == config.TIMEOUT_INICIAL


def test_timeout_dobra_a_cada_retransmissao_ate_o_teto(servidor):
    servidor.canal.enviar(Tipo.EVENTO, PONTO, agora=0.0)
    servidor.tirar()
    # Intervalos de 1 s, 2 s, 3 s (teto) e 3 s: retransmissões em 1, 3, 6 e 9 s.
    for instante in [1.0, 3.0, 6.0, 9.0]:
        servidor.canal.verificar_timeouts(instante - 0.001)
        assert servidor.tirar() == []
        servidor.canal.verificar_timeouts(instante)
        assert len(servidor.tirar()) == 1
    servidor.canal.verificar_timeouts(11.999)
    assert servidor.desistiu == []
    servidor.canal.verificar_timeouts(12.0)  # a 5ª transmissão também ficou sem ACK
    assert len(servidor.desistiu) == 1


# --- Perda do ACK: duplicata reconhecida ------------------------------------

def test_ack_perdido_gera_duplicata_que_e_confirmada_mas_nao_processada(servidor, cliente):
    original = servidor.canal.enviar(Tipo.EVENTO, PONTO, agora=0.0)
    processadas = entregar(servidor, cliente, agora=0.05)  # chega e é processada
    perder(cliente)                                        # mas o ACK se perde
    servidor.canal.verificar_timeouts(agora=1.0)           # servidor retransmite
    processadas += entregar(servidor, cliente, agora=1.05)
    assert processadas == [original]                       # o ponto conta UMA vez
    [novo_ack] = cliente.saida
    assert novo_ack.dados == {"ack": original.seq}         # mas é confirmado de novo
    entregar(cliente, servidor, agora=1.1)
    assert servidor.canal.pendentes == 0
    assert cliente.canal.contadores().duplicadas_recebidas == 1


def test_duplicada_pela_rede_tambem_e_reconhecida(servidor, cliente):
    original = servidor.canal.enviar(Tipo.EVENTO, PONTO, agora=0.0)
    servidor.tirar()
    assert cliente.canal.ao_receber(original, agora=0.01)
    assert not cliente.canal.ao_receber(original, agora=0.02)  # cópia (ex.: netem duplicate)
    assert len(cliente.saida) == 2                             # dois ACKs


# --- Desistência ------------------------------------------------------------

def test_desiste_apos_max_tentativas_e_avisa(servidor):
    original = servidor.canal.enviar(Tipo.EVENTO, PONTO, agora=0.0)
    transmissoes = len(servidor.tirar())
    for t in range(1, 30):
        servidor.canal.verificar_timeouts(agora=float(t))
        transmissoes += len(servidor.tirar())
    assert transmissoes == config.MAX_TENTATIVAS
    assert servidor.desistiu == [original]
    assert servidor.canal.pendentes == 0
    assert servidor.canal.contadores().desistencias == 1


def test_ack_que_chega_depois_da_desistencia_e_ignorado(servidor):
    servidor.canal.enviar(Tipo.EVENTO, PONTO, agora=0.0)
    for t in range(1, 30):
        servidor.canal.verificar_timeouts(agora=float(t))
    servidor.canal.ao_receber(Mensagem(Tipo.ACK, 1, {"ack": 1}), agora=31.0)
    assert servidor.canal.estimador.estimated_rtt is None


# --- Várias pendentes (repetição seletiva) ----------------------------------

def test_varias_pendentes_com_acks_fora_de_ordem(servidor, cliente):
    a = servidor.canal.enviar(Tipo.EVENTO, PONTO, agora=0.0)
    b = servidor.canal.enviar(Tipo.EVENTO, PONTO, agora=0.0)
    assert servidor.canal.pendentes == 2
    servidor.canal.ao_receber(Mensagem(Tipo.ACK, 1, {"ack": b.seq}), agora=0.1)
    assert servidor.canal.aguardando(a.seq) and not servidor.canal.aguardando(b.seq)
    servidor.tirar()
    servidor.canal.verificar_timeouts(agora=1.0)
    assert [m.seq for m in servidor.saida] == [a.seq]  # só a que faltou é retransmitida


def test_proximo_prazo(servidor):
    assert servidor.canal.proximo_prazo() is None
    servidor.canal.enviar(Tipo.EVENTO, PONTO, agora=2.0)
    servidor.canal.enviar(Tipo.EVENTO, PONTO, agora=1.0)
    assert servidor.canal.proximo_prazo() == 1.0 + config.TIMEOUT_INICIAL


def test_contadores(servidor, cliente):
    servidor.canal.enviar(Tipo.EVENTO, PONTO, agora=0.0)
    servidor.canal.enviar(Tipo.ESTADO, ESTADO, agora=0.0)
    entregar(servidor, cliente, agora=0.1)
    entregar(cliente, servidor, agora=0.2)
    c = servidor.canal.contadores()
    assert (c.enviadas, c.retransmitidas, c.duplicadas_recebidas, c.desistencias) == (1, 0, 0, 0)
    assert c.rtt_estimado_ms == pytest.approx(200)
    assert c.dev_rtt_ms == pytest.approx(100)
    assert c.timeout_ms == pytest.approx(600)


# --- Janela de duplicatas ---------------------------------------------------

def test_janela_reconhece_repetido_e_aceita_novo():
    janela = JanelaDuplicatas(tamanho=4)
    assert janela.registrar(5)
    assert not janela.registrar(5)
    assert janela.registrar(3)  # mais velho, mas dentro da janela e nunca visto


def test_janela_trata_seq_velho_demais_como_duplicata():
    janela = JanelaDuplicatas(tamanho=4)
    janela.registrar(10)
    assert not janela.registrar(6)  # 6 <= 10 - 4: fora da janela
    assert janela.registrar(7)


def test_janela_nao_cresce_sem_limite():
    janela = JanelaDuplicatas(tamanho=8)
    for seq in range(1, 1000):
        janela.registrar(seq)
    assert len(janela._vistos) <= 9


# --- Estimador de RTT (fórmulas do TCP) -------------------------------------

def test_estimador_antes_da_primeira_amostra():
    estimador = EstimadorRTT()
    assert estimador.estimated_rtt is None
    assert estimador.timeout == config.TIMEOUT_INICIAL == 1.0


def test_primeira_amostra():
    estimador = EstimadorRTT(minimo=0.0)
    estimador.amostrar(0.100)
    assert estimador.estimated_rtt == pytest.approx(0.100)
    assert estimador.dev_rtt == pytest.approx(0.050)
    assert estimador.timeout == pytest.approx(0.100 + 4 * 0.050)


def test_segunda_amostra_com_alfa_e_beta_do_livro():
    estimador = EstimadorRTT(alfa=0.125, beta=0.25, minimo=0.0)
    estimador.amostrar(0.100)
    estimador.amostrar(0.200)
    # DevRTT  = 0,75·0,050 + 0,25·|0,200 − 0,100| = 0,0625  (com o EstimatedRTT anterior)
    # EstRTT  = 0,875·0,100 + 0,125·0,200        = 0,1125
    # Timeout = 0,1125 + 4·0,0625                = 0,3625
    assert estimador.dev_rtt == pytest.approx(0.0625)
    assert estimador.estimated_rtt == pytest.approx(0.1125)
    assert estimador.timeout == pytest.approx(0.3625)


def test_rtt_estavel_faz_o_timeout_convergir_para_o_rtt():
    estimador = EstimadorRTT(minimo=0.0)
    for _ in range(200):
        estimador.amostrar(0.080)
    assert estimador.estimated_rtt == pytest.approx(0.080)
    assert estimador.timeout == pytest.approx(0.080, abs=0.001)  # DevRTT -> 0


def test_um_pico_isolado_pesa_pouco():
    estimador = EstimadorRTT(minimo=0.0)
    for _ in range(50):
        estimador.amostrar(0.050)
    estimador.amostrar(0.500)  # um pacote preso numa fila
    assert estimador.estimated_rtt == pytest.approx(0.050 + 0.125 * 0.450)
    assert estimador.timeout > 0.2  # mas o desvio sobe e o timeout ganha folga


def test_timeout_respeita_minimo_e_maximo():
    estimador = EstimadorRTT(minimo=0.1, maximo=3.0)
    estimador.amostrar(0.0004)  # localhost
    assert estimador.timeout == 0.1
    estimador = EstimadorRTT(minimo=0.1, maximo=3.0)
    estimador.amostrar(5.0)
    assert estimador.timeout == 3.0


# --- Canal inativo (sobre TCP) ----------------------------------------------

def test_canal_inativo_so_numera():
    saida = []
    canal = CanalConfiavel(saida.append, ativo=False)
    enviada = canal.enviar(Tipo.EVENTO, PONTO, agora=0.0)
    assert canal.pendentes == 0                  # nada a retransmitir: o TCP cuida
    canal.verificar_timeouts(agora=100.0)
    assert saida == [enviada]
    assert canal.contadores().enviadas == 1


def test_canal_inativo_nao_confirma_nem_filtra():
    saida = []
    canal = CanalConfiavel(saida.append, ativo=False)
    evento = Mensagem(Tipo.EVENTO, 5, PONTO)
    assert canal.ao_receber(evento, agora=0.0)
    assert canal.ao_receber(evento, agora=0.1)   # sobre TCP não existe duplicata
    assert saida == []                           # e nenhum ACK
