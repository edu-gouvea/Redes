"""Interpolação com atraso de reprodução adaptativo (cliente/interpolacao.py)."""

import random

import pytest

from cliente.interpolacao import Interpolador

INTERVALO = 0.05
DEFASAGEM = 1000.0  # relógio do servidor e do cliente não batem: não pode importar


def jogadores(**posicoes):
    return [{"id": int(i[1:]), "x": x, "y": y} for i, (x, y) in posicoes.items()]


def alimentar(interp, ticks, atraso=0.03, jitter=0.0, semente=1):
    """ESTADO do tick t gerado em t·50 ms; jogador 1 anda 10 px por tick."""
    aleatorio = random.Random(semente)
    chegada = 0.0
    for tick in ticks:
        chegada = DEFASAGEM + tick * INTERVALO + atraso + aleatorio.uniform(0, jitter)
        interp.adicionar(tick, jogadores(j1=(tick * 10.0, 0.0)), chegada)
    return chegada


def test_sem_estado_nao_ha_posicoes():
    assert Interpolador(INTERVALO).posicoes(0.0) == {}


def test_interpola_entre_dois_estados_sem_jitter():
    interp = Interpolador(INTERVALO)
    ultima_chegada = alimentar(interp, range(1, 21))
    # Sem jitter, d = atraso + defasagem, v = 0: desenha 1 intervalo atrás do último gerado.
    assert interp.atraso_reproducao == pytest.approx(INTERVALO)
    x, _ = interp.posicoes(ultima_chegada)[1]
    assert x == pytest.approx(190.0)
    x, _ = interp.posicoes(ultima_chegada + INTERVALO / 2)[1]
    assert x == pytest.approx(195.0)


def test_buffer_cresce_com_jitter():
    sem = Interpolador(INTERVALO)
    com = Interpolador(INTERVALO)
    alimentar(sem, range(1, 100))
    alimentar(com, range(1, 100), jitter=0.04)
    assert com.atraso_reproducao > sem.atraso_reproducao + 0.02


def test_com_jitter_quase_nunca_esvazia_o_buffer():
    interp = Interpolador(INTERVALO)
    aleatorio = random.Random(7)
    chegadas = sorted((DEFASAGEM + t * INTERVALO + 0.03 + aleatorio.uniform(0, 0.04), t) for t in range(1, 400))
    ultimo = 0
    agora = chegadas[0][0]
    for chegada, tick in chegadas:
        while agora < chegada:  # quadros a 60 FPS entre uma chegada e outra
            interp.posicoes(agora)
            agora += 1 / 60
        if tick > ultimo:  # a SessaoCliente já descarta os fora de ordem
            interp.adicionar(tick, jogadores(j1=(tick, 0.0)), chegada)
            ultimo = tick
    assert interp.esgotamentos <= 5


def test_buffer_vazio_segura_a_ultima_posicao_e_conta_o_esgotamento():
    interp = Interpolador(INTERVALO)
    ultima = alimentar(interp, range(1, 11))
    assert interp.posicoes(ultima + 1.0)[1] == (100.0, 0.0)
    assert interp.esgotamentos == 1
    interp.posicoes(ultima + 1.1)
    assert interp.esgotamentos == 1  # conta a vez, não cada quadro


def test_jogador_novo_aparece_e_quem_saiu_some():
    interp = Interpolador(INTERVALO)
    interp.adicionar(1, jogadores(j1=(0.0, 0.0), j2=(50.0, 50.0)), DEFASAGEM + 0.08)
    interp.adicionar(2, jogadores(j1=(10.0, 0.0), j3=(70.0, 70.0)), DEFASAGEM + 0.13)
    # d = 30 ms; o instante desenhado é agora − d − 50 ms = 75 ms: entre os dois ESTADOs.
    posicoes = interp.posicoes(DEFASAGEM + 0.155)
    assert set(posicoes) == {1, 3}
    assert posicoes[3] == (70.0, 70.0)


def test_servidor_reiniciado_recomeca_a_estimativa():
    interp = Interpolador(INTERVALO)
    alimentar(interp, range(500, 520))
    # Servidor novo: o tick volta para 1, e (chegada − gerado) dá um salto enorme.
    interp.adicionar(1, jogadores(j1=(5.0, 5.0)), DEFASAGEM + 600.0)
    assert interp.posicoes(DEFASAGEM + 600.0)[1] == (5.0, 5.0)
