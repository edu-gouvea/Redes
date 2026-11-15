"""Servidor real e clientes reais, com sockets UDP de verdade em 127.0.0.1."""

import socket
import threading
import time

import pytest

from ferramentas import robo
from ferramentas.perda import TransporteComPerda
from protocolo.mensagens import Tipo, decodificar
from protocolo.sessao import SessaoCliente, Situacao
from protocolo.transporte import TransporteUDP
from servidor.jogo import Moeda, Partida
from servidor.servidor import Servidor


@pytest.fixture
def servidor_real():
    transporte = TransporteUDP(("127.0.0.1", 0))
    servidor = Servidor(transporte, Partida(semente=3))
    thread = threading.Thread(target=servidor.executar, daemon=True)
    thread.start()
    yield servidor, transporte.endereco_local
    servidor.parar()
    thread.join(timeout=2)
    transporte.fechar()


def rodar(sessoes, condicao, prazo=3.0):
    limite = time.monotonic() + prazo
    while time.monotonic() < limite:
        for sessao in sessoes:
            sessao.processar(timeout=0.005)
        if condicao():
            return True
    return False


def test_dois_clientes_na_mesma_partida(servidor_real):
    servidor, endereco = servidor_real
    ana = SessaoCliente(TransporteUDP.cliente(), endereco, "ana")
    bia = SessaoCliente(TransporteUDP.cliente(), endereco, "bia")
    try:
        def os_dois_se_veem():
            return all(s.estado is not None and len(s.estado["jogadores"]) == 2 for s in (ana, bia))

        assert rodar([ana, bia], os_dois_se_veem)
        assert {ana.meu_id, bia.meu_id} == {1, 2}
        assert ana.estado["moedas"] == bia.estado["moedas"]  # uma única verdade: a do servidor

        # A ana anda para o centro; a bia, pelo ESTADO do servidor, vê a ana andar.
        x_inicial = ana.meu_jogador()["x"]
        ana.definir_direcao(1 if x_inicial < 400 else -1, 0)
        assert rodar([ana, bia], lambda: abs(_x_de(bia, ana.meu_id) - x_inicial) > 20)
    finally:
        for sessao in (ana, bia):
            sessao.sair()
            sessao.transporte.fechar()


def _x_de(sessao, jogador_id):
    return next(j["x"] for j in sessao.estado["jogadores"] if j["id"] == jogador_id)


def test_servidor_sobrevive_a_lixo_e_continua_atendendo(servidor_real):
    servidor, endereco = servidor_real
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as cru:
        for lixo in (b"", b"\xff" * 10, b"{}", b"x" * 3000):
            cru.sendto(lixo, endereco)
    ana = SessaoCliente(TransporteUDP.cliente(), endereco, "ana")
    try:
        assert rodar([ana], lambda: ana.situacao is Situacao.EM_JOGO)
    finally:
        ana.transporte.fechar()


def test_sair_remove_o_jogador_do_estado_dos_outros(servidor_real):
    servidor, endereco = servidor_real
    ana = SessaoCliente(TransporteUDP.cliente(), endereco, "ana")
    bia = SessaoCliente(TransporteUDP.cliente(), endereco, "bia")
    try:
        assert rodar([ana, bia], lambda: ana.estado and len(ana.estado["jogadores"]) == 2)
        bia.sair()
        assert rodar([ana], lambda: len(ana.estado["jogadores"]) == 1)
        eventos = ana.consumir_eventos()
        assert {"evento": "saiu", "jogador": bia.meu_id, "motivo": "pediu"} in eventos
    finally:
        ana.transporte.fechar()
        bia.transporte.fechar()


def test_estado_chega_na_taxa_configurada(servidor_real):
    _, endereco = servidor_real
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as cru:
        cru.settimeout(1)
        cru.sendto(b'{"v":1,"tipo":"ENTRAR","seq":1,"ts":0,"dados":{"nome":"cru"}}', endereco)
        chegadas = []
        while len(chegadas) < 11:
            mensagem = decodificar(cru.recvfrom(65535)[0])
            if mensagem.tipo is Tipo.ESTADO:
                chegadas.append(time.monotonic())
    intervalo_medio = (chegadas[-1] - chegadas[0]) / 10
    assert intervalo_medio == pytest.approx(0.05, abs=0.01)  # 20 ESTADOs por segundo


def test_robos_entram_e_saem(servidor_real, capsys):
    _, endereco = servidor_real
    codigo = robo.main(["--servidor", f"127.0.0.1:{endereco[1]}", "--quantidade", "2",
                        "--modo", "cacador", "--duracao", "2.5", "--semente", "1"])
    saida = capsys.readouterr().out
    assert codigo == 0
    assert "robo-1 | entrou como jogador" in saida
    assert "robo-2 | entrou como jogador" in saida
    assert "placar" in saida


def test_robo_sem_servidor_desiste():
    vazio = TransporteUDP(("127.0.0.1", 0))
    porta = vazio.endereco_local[1]
    vazio.fechar()
    assert robo.main(["--servidor", f"127.0.0.1:{porta}", "--duracao", "1.5"]) == 1


def test_partida_funciona_com_perda_e_atraso_variavel():
    """20% de perda e 10 ± 10 ms de atraso em cada sentido, na porta do servidor.

    Servidor e clientes giram na mesma thread (servidor.passo), então o teste
    pode mexer na partida sem condição de corrida.
    """
    rede = TransporteComPerda(TransporteUDP(("127.0.0.1", 0)), perda=0.2, atraso=0.01, variacao=0.01, semente=5)
    servidor = Servidor(rede, Partida(semente=3))
    ana = SessaoCliente(TransporteUDP.cliente(), rede.endereco_local, "ana")
    bia = SessaoCliente(TransporteUDP.cliente(), rede.endereco_local, "bia")
    eventos = {"ana": [], "bia": []}

    def girar(condicao, prazo):
        limite = time.monotonic() + prazo
        while time.monotonic() < limite:
            servidor.passo(espera_maxima=0.002)
            for nome, sessao in (("ana", ana), ("bia", bia)):
                sessao.processar()
                eventos[nome] += sessao.consumir_eventos()
            if condicao():
                return True
        return False

    def pontos_vistos(nome):
        return [e for e in eventos[nome] if e["evento"] == "ponto"]

    try:
        assert girar(lambda: ana.situacao is bia.situacao is Situacao.EM_JOGO, prazo=20)
        jogadora = servidor.partida.jogadores[ana.meu_id]
        servidor.partida.moedas[0] = Moeda(jogadora.x, jogadora.y)
        assert girar(lambda: pontos_vistos("ana") and pontos_vistos("bia"), prazo=20)
        girar(lambda: False, prazo=1.0)  # tempo para retransmissões e duplicatas atrasadas
        esperado = [{"evento": "ponto", "jogador": ana.meu_id, "pontos": 1}]
        assert pontos_vistos("ana") == esperado  # chegou, e uma vez só, apesar das perdas
        assert pontos_vistos("bia") == esperado
        assert rede.perdidos_envio + rede.perdidos_recepcao > 0
    finally:
        for sessao in (ana, bia):
            sessao.transporte.fechar()
        rede.fechar()
