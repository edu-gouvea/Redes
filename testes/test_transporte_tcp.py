"""TransporteTCP com sockets reais: enquadramento sobre o fluxo de bytes (protocolo/transporte.py)."""

import socket
import struct
import threading
import time

import pytest

from ferramentas.perda import TransporteComPerda
from protocolo.mensagens import Tipo
from protocolo.sessao import SessaoCliente, Situacao
from protocolo.transporte import TransporteTCP, TransporteUDP
from servidor.jogo import Partida
from servidor.servidor import Servidor


@pytest.fixture
def tcp_servidor():
    transporte = TransporteTCP.servidor(0, "127.0.0.1")
    yield transporte
    transporte.fechar()


def receber_ate(transporte, quantidade, prazo=2.0):
    pacotes = []
    limite = time.monotonic() + prazo
    while len(pacotes) < quantidade and time.monotonic() < limite:
        pacotes += transporte.receber(timeout=0.02)
    return pacotes


def quadro(dados: bytes) -> bytes:
    return struct.pack("!H", len(dados)) + dados


def test_garante_entrega_so_no_tcp():
    assert TransporteTCP.garante_entrega
    assert not TransporteUDP.garante_entrega
    assert not TransporteComPerda(TransporteUDP(("127.0.0.1", 0))).garante_entrega


def test_mensagens_chegam_separadas_e_com_o_endereco_da_conexao(tcp_servidor):
    cliente = TransporteTCP.cliente(tcp_servidor.endereco_local)
    try:
        for texto in (b"um", b"dois", b"tres"):
            cliente.enviar(texto, tcp_servidor.endereco_local)
        pacotes = receber_ate(tcp_servidor, 3)
        assert [dados for dados, _ in pacotes] == [b"um", b"dois", b"tres"]
        assert {origem for _, origem in pacotes} == {cliente.endereco_local}
    finally:
        cliente.fechar()


def test_resposta_do_servidor_chega_ao_cliente(tcp_servidor):
    cliente = TransporteTCP.cliente(tcp_servidor.endereco_local)
    try:
        cliente.enviar(b"oi", tcp_servidor.endereco_local)
        [(_, origem)] = receber_ate(tcp_servidor, 1)
        tcp_servidor.enviar(b"ola", origem)
        assert receber_ate(cliente, 1) == [(b"ola", tcp_servidor.endereco_local)]
    finally:
        cliente.fechar()


def test_mensagem_picada_em_varios_pedacos_e_remontada(tcp_servidor):
    with socket.create_connection(tcp_servidor.endereco_local) as cru:
        cru.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        recebidas = []
        for byte in quadro(b"mensagem inteira"):
            cru.send(bytes([byte]))  # um byte por segmento
            recebidas += tcp_servidor.receber(timeout=0.001)
        recebidas += receber_ate(tcp_servidor, 1 - len(recebidas))
        assert [dados for dados, _ in recebidas] == [b"mensagem inteira"]


def test_duas_mensagens_grudadas_num_send_sao_separadas(tcp_servidor):
    with socket.create_connection(tcp_servidor.endereco_local) as cru:
        cru.sendall(quadro(b"primeira") + quadro(b"segunda") + quadro(b"terc")[:3])
        assert [dados for dados, _ in receber_ate(tcp_servidor, 2)] == [b"primeira", b"segunda"]
        cru.sendall(quadro(b"terc")[3:])  # o resto da terceira chega depois
        assert [dados for dados, _ in receber_ate(tcp_servidor, 1)] == [b"terc"]


def test_prefixo_absurdo_fecha_a_conexao(tcp_servidor):
    with socket.create_connection(tcp_servidor.endereco_local) as cru:
        cru.sendall(struct.pack("!H", 60000) + b"x" * 10)
        receber_ate(tcp_servidor, 1, prazo=0.3)
        cru.settimeout(1)
        assert cru.recv(10) == b""  # o servidor fechou: fluxo corrompido não tem conserto


def test_muitas_mensagens_chegam_todas_e_em_ordem(tcp_servidor):
    cliente = TransporteTCP.cliente(tcp_servidor.endereco_local)
    try:
        for i in range(2000):
            cliente.enviar(str(i).encode(), tcp_servidor.endereco_local)
            if i % 200 == 0:
                cliente.receber(timeout=0)  # dá vazão ao que o send() não aceitou de primeira
        recebidas = []
        limite = time.monotonic() + 3
        while len(recebidas) < 2000 and time.monotonic() < limite:
            cliente.receber(timeout=0)
            recebidas += [int(d) for d, _ in tcp_servidor.receber(timeout=0.01)]
        assert recebidas == list(range(2000))
    finally:
        cliente.fechar()


def test_conexao_encerrada_pelo_cliente_some_do_servidor(tcp_servidor):
    cliente = TransporteTCP.cliente(tcp_servidor.endereco_local)
    cliente.enviar(b"tchau", tcp_servidor.endereco_local)
    [(_, origem)] = receber_ate(tcp_servidor, 1)
    cliente.fechar()
    receber_ate(tcp_servidor, 1, prazo=0.3)  # lê o FIN
    tcp_servidor.enviar(b"alguem?", origem)  # sem conexão: só descarta, sem erro


def test_cliente_sem_servidor_recebe_erro_de_conexao():
    vazio = socket.socket()
    vazio.bind(("127.0.0.1", 0))
    porta = vazio.getsockname()[1]
    vazio.close()
    with pytest.raises(OSError):
        TransporteTCP.cliente(("127.0.0.1", porta), timeout=1)


# --- O jogo inteiro sobre TCP -----------------------------------------------

@pytest.fixture
def servidor_tcp():
    transporte = TransporteTCP.servidor(0, "127.0.0.1")
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


def test_partida_sobre_tcp_sem_ack_da_aplicacao(servidor_tcp):
    servidor, endereco = servidor_tcp
    ana = SessaoCliente(TransporteTCP.cliente(endereco), endereco, "ana")
    bia = SessaoCliente(TransporteTCP.cliente(endereco), endereco, "bia")
    try:
        assert not servidor.confiabilidade and not ana.confiabilidade  # automático: TCP já garante
        assert rodar([ana, bia], lambda: all(s.estado and len(s.estado["jogadores"]) == 2 for s in (ana, bia)))
        assert rodar([ana], lambda: ana.ultimo_rtt_ms is not None)
        bia.sair(prazo=0)
        assert rodar([ana], lambda: len(ana.estado["jogadores"]) == 1)
        assert {"evento": "saiu", "jogador": bia.meu_id, "motivo": "pediu"} in ana.consumir_eventos()
        assert ana.registro.pacotes[("tx", str(Tipo.ACK))] == 0  # nenhum ACK de aplicação trafegou
    finally:
        ana.transporte.fechar()
        bia.transporte.fechar()


def test_confiabilidade_da_aplicacao_pode_ser_ligada_sobre_tcp():
    transporte = TransporteTCP.servidor(0, "127.0.0.1")
    servidor = Servidor(transporte, Partida(semente=3), confiabilidade=True)
    thread = threading.Thread(target=servidor.executar, daemon=True)
    thread.start()
    endereco = transporte.endereco_local
    ana = SessaoCliente(TransporteTCP.cliente(endereco), endereco, "ana", confiabilidade=True)
    try:
        assert rodar([ana], lambda: ana.situacao is Situacao.EM_JOGO)
        assert ana.registro.pacotes[("tx", str(Tipo.ACK))] >= 1  # confirmou o ENTRAR_OK
    finally:
        ana.transporte.fechar()
        servidor.parar()
        thread.join(timeout=2)
        transporte.fechar()
