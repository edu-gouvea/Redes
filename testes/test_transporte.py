"""TransporteUDP com sockets reais em 127.0.0.1 (protocolo/transporte.py)."""

import time

import pytest

from protocolo.transporte import TransporteUDP, resolver


@pytest.fixture
def par():
    servidor = TransporteUDP(("127.0.0.1", 0))
    cliente = TransporteUDP(("127.0.0.1", 0))
    yield servidor, cliente
    servidor.fechar()
    cliente.fechar()


def receber_ate(transporte, quantidade, prazo=1.0):
    pacotes = []
    limite = time.monotonic() + prazo
    while len(pacotes) < quantidade and time.monotonic() < limite:
        pacotes += transporte.receber(timeout=0.05)
    return pacotes


def test_porta_zero_vira_porta_efemera(par):
    servidor, cliente = par
    assert servidor.endereco_local[1] > 0
    assert servidor.endereco_local[1] != cliente.endereco_local[1]


def test_datagrama_chega_com_o_endereco_de_origem(par):
    servidor, cliente = par
    cliente.enviar(b"oi", servidor.endereco_local)
    [(dados, origem)] = receber_ate(servidor, 1)
    assert dados == b"oi"
    assert origem == cliente.endereco_local  # é assim que o servidor sabe quem mandou


def test_cada_envio_e_um_datagrama_separado(par):
    servidor, cliente = par
    for texto in (b"um", b"dois", b"tres"):
        cliente.enviar(texto, servidor.endereco_local)
    recebidos = [dados for dados, _ in receber_ate(servidor, 3)]
    # UDP preserva as fronteiras: nada de "umdoistres" grudado (em localhost a ordem se mantém).
    assert recebidos == [b"um", b"dois", b"tres"]


def test_receber_sem_nada_respeita_o_timeout(par):
    servidor, _ = par
    inicio = time.monotonic()
    assert servidor.receber(timeout=0.1) == []
    assert 0.08 <= time.monotonic() - inicio < 0.5


def test_receber_com_timeout_zero_nao_bloqueia(par):
    servidor, _ = par
    inicio = time.monotonic()
    assert servidor.receber(timeout=0) == []
    assert time.monotonic() - inicio < 0.05


def test_datagrama_grande_chega_inteiro_para_o_protocolo_poder_recusar(par):
    servidor, cliente = par
    grande = b"x" * 5000
    cliente.enviar(grande, servidor.endereco_local)
    [(dados, _)] = receber_ate(servidor, 1)
    assert dados == grande  # não foi truncado em silêncio no buffer do recvfrom


def test_enviar_para_porta_sem_ninguem_nao_levanta_erro(par):
    _, cliente = par
    vazio = TransporteUDP(("127.0.0.1", 0))
    destino = vazio.endereco_local
    vazio.fechar()
    cliente.enviar(b"alguem?", destino)  # UDP: o envio "dá certo"; o datagrama só se perde
    assert cliente.receber(timeout=0.1) == []


def test_resolver_converte_nome_em_ip():
    assert resolver("localhost", 5005) == ("127.0.0.1", 5005)


def test_gerenciador_de_contexto_fecha_o_socket():
    with TransporteUDP(("127.0.0.1", 0)) as transporte:
        porta = transporte.endereco_local[1]
    reaberto = TransporteUDP(("127.0.0.1", porta))  # a porta foi liberada
    reaberto.fechar()
