"""Simulador de perda e atraso (ferramentas/perda.py), com relógio controlado."""

import pytest
from falsos import Relogio, TransporteFalso

from ferramentas.perda import TransporteComPerda
from protocolo.mensagens import Mensagem, Tipo, codificar, decodificar
from protocolo.sequencia import FiltroMaisRecente

DESTINO = ("10.0.0.10", 5005)
ORIGEM = ("10.0.0.10", 5005)


def pacote(seq: int) -> bytes:
    return codificar(Mensagem(Tipo.PING, seq, {}, ts=0))


def seqs(pacotes) -> list[int]:
    return [decodificar(dados).seq for dados, _ in pacotes]


@pytest.fixture
def relogio():
    return Relogio()


def simulador(relogio, **opcoes):
    interno = TransporteFalso()
    return TransporteComPerda(interno, semente=1, relogio=relogio, **opcoes), interno


def test_sem_perda_nem_atraso_passa_direto(relogio):
    rede, interno = simulador(relogio)
    rede.enviar(pacote(1), DESTINO)
    assert [m.seq for m in interno.para(DESTINO)] == [1]
    interno.entregar(pacote(2), ORIGEM)
    assert seqs(rede.receber(0)) == [2]


def test_perda_total_nao_deixa_nada_passar(relogio):
    rede, interno = simulador(relogio, perda=1.0)
    for seq in range(10):
        rede.enviar(pacote(seq), DESTINO)
        interno.entregar(pacote(seq), ORIGEM)
    assert interno.enviados == []
    assert rede.receber(0) == []
    assert (rede.perdidos_envio, rede.perdidos_recepcao) == (10, 10)


def test_taxa_de_perda_proxima_da_configurada(relogio):
    rede, interno = simulador(relogio, perda=0.3, sentido="envio")
    for seq in range(2000):
        rede.enviar(pacote(seq), DESTINO)
    assert rede.perdidos_envio / 2000 == pytest.approx(0.3, abs=0.04)
    assert len(interno.enviados) + rede.perdidos_envio == 2000


def test_atraso_na_recepcao(relogio):
    rede, interno = simulador(relogio, atraso=0.1, sentido="recepcao")
    interno.entregar(pacote(1), ORIGEM)
    assert rede.receber(0) == []       # chegou ao socket, mas a "rede" ainda o segura
    relogio.avancar(0.099)
    assert rede.receber(0) == []
    relogio.avancar(0.002)
    assert seqs(rede.receber(0)) == [1]


def test_atraso_no_envio(relogio):
    rede, interno = simulador(relogio, atraso=0.05, sentido="envio")
    rede.enviar(pacote(1), DESTINO)
    assert interno.enviados == []
    relogio.avancar(0.06)
    rede.receber(0)                    # o laço do programa chama receber() o tempo todo
    assert [m.seq for m in interno.para(DESTINO)] == [1]


def test_variacao_do_atraso_muda_a_ordem_de_chegada(relogio):
    rede, interno = simulador(relogio, atraso=0.05, variacao=0.05, sentido="recepcao")
    chegada = []
    for seq in range(1, 101):
        interno.entregar(pacote(seq), ORIGEM)
        chegada += seqs(rede.receber(0))
        relogio.avancar(0.005)         # um pacote a cada 5 ms
    relogio.avancar(1.0)
    chegada += seqs(rede.receber(0))
    assert sorted(chegada) == list(range(1, 101))  # nenhum se perdeu...
    assert chegada != sorted(chegada)              # ...mas a ordem mudou


def test_reordenacao_e_percebida_pelo_filtro_de_seq(relogio):
    rede, interno = simulador(relogio, atraso=0.05, variacao=0.05, sentido="recepcao")
    filtro = FiltroMaisRecente()
    for seq in range(1, 201):
        interno.entregar(pacote(seq), ORIGEM)
        for s in seqs(rede.receber(0)):
            filtro.aceitar(s)
        relogio.avancar(0.005)
    relogio.avancar(1.0)
    for s in seqs(rede.receber(0)):
        filtro.aceitar(s)
    assert filtro.descartadas > 0      # chegaram fora de ordem e foram descartadas


def test_sentido_recepcao_nao_mexe_no_envio(relogio):
    rede, interno = simulador(relogio, perda=1.0, sentido="recepcao")
    rede.enviar(pacote(1), DESTINO)
    assert len(interno.enviados) == 1


def test_mesma_semente_mesmas_perdas(relogio):
    resultados = []
    for _ in range(2):
        rede, interno = simulador(relogio, perda=0.5, sentido="envio")
        for seq in range(50):
            rede.enviar(pacote(seq), DESTINO)
        resultados.append([m.seq for m in interno.para(DESTINO)])
    assert resultados[0] == resultados[1]


@pytest.mark.parametrize("opcoes", [{"perda": 1.5}, {"perda": -0.1}, {"sentido": "lado"}])
def test_parametros_invalidos(relogio, opcoes):
    with pytest.raises(ValueError):
        TransporteComPerda(TransporteFalso(), **opcoes)
