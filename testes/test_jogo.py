"""Regras do jogo sem rede: movimento, limites, colisão e placar (servidor/jogo.py)."""

import math

import pytest

from servidor.jogo import Moeda, Partida, Ponto

DT = 0.05  # um tick a 20 Hz


@pytest.fixture
def partida():
    return Partida(largura=800, altura=600, raio_jogador=15, raio_moeda=8,
                   velocidade=200, numero_moedas=5, semente=42)


def no_centro(partida, nome="ana"):
    jogador = partida.adicionar_jogador(nome)
    jogador.x, jogador.y = 400.0, 300.0
    partida.moedas = [Moeda(50.0, 50.0)]  # longe, para não interferir
    return jogador


def test_ids_crescem_e_jogador_nasce_dentro_da_arena(partida):
    ana, bia = partida.adicionar_jogador("ana"), partida.adicionar_jogador("bia")
    assert (ana.id, bia.id) == (1, 2)
    for j in (ana, bia):
        assert 15 <= j.x <= 785 and 15 <= j.y <= 585


def test_moedas_nascem_dentro_da_arena(partida):
    assert len(partida.moedas) == 5
    assert all(8 <= m.x <= 792 and 8 <= m.y <= 592 for m in partida.moedas)


def test_parado_nao_se_move(partida):
    ana = no_centro(partida)
    partida.avancar(DT)
    assert (ana.x, ana.y) == (400.0, 300.0)


@pytest.mark.parametrize("dx, dy, esperado", [
    (1, 0, (410.0, 300.0)),
    (-1, 0, (390.0, 300.0)),
    (0, -1, (400.0, 290.0)),  # y cresce para baixo, como na tela
    (0, 1, (400.0, 310.0)),
])
def test_movimento_em_linha_reta(partida, dx, dy, esperado):
    ana = no_centro(partida)
    partida.definir_direcao(ana.id, dx, dy)
    partida.avancar(DT)
    assert (ana.x, ana.y) == pytest.approx(esperado)


def test_diagonal_nao_e_mais_rapida(partida):
    ana = no_centro(partida)
    partida.definir_direcao(ana.id, 1, 1)
    partida.avancar(DT)
    assert math.hypot(ana.x - 400, ana.y - 300) == pytest.approx(200 * DT)


def test_jogador_nao_sai_da_arena(partida):
    ana = no_centro(partida)
    ana.x = 20.0
    partida.definir_direcao(ana.id, -1, 0)
    for _ in range(10):
        partida.avancar(DT)
    assert ana.x == 15  # o centro para a um raio da parede


def test_tocar_moeda_da_ponto_e_a_moeda_reaparece(partida):
    ana = no_centro(partida)
    partida.moedas = [Moeda(400.0 + 15 + 8, 300.0)]  # encostando: distância = soma dos raios
    pontos = partida.avancar(DT)
    assert pontos == [Ponto(jogador_id=ana.id, pontos=1)]
    assert ana.pontos == 1
    nova = partida.moedas[0]
    assert math.hypot(nova.x - ana.x, nova.y - ana.y) > 15 + 8  # não nasce em cima de quem pegou


def test_sem_encostar_nao_ha_ponto(partida):
    ana = no_centro(partida)
    partida.moedas = [Moeda(400.0 + 15 + 8 + 0.5, 300.0)]
    assert partida.avancar(DT) == []
    assert ana.pontos == 0


def test_disputa_pela_mesma_moeda_so_um_leva(partida):
    ana = no_centro(partida, "ana")
    bia = partida.adicionar_jogador("bia")
    bia.x, bia.y = 400.0, 300.0
    partida.moedas = [Moeda(400.0, 300.0)]
    pontos = partida.avancar(DT)
    assert [p.jogador_id for p in pontos] == [ana.id]
    assert (ana.pontos, bia.pontos) == (1, 0)


def test_ranking_ordena_por_pontos(partida):
    ana, bia = partida.adicionar_jogador("ana"), partida.adicionar_jogador("bia")
    bia.pontos = 3
    assert [j.nome for j in partida.ranking()] == ["bia", "ana"]


def test_remover_jogador(partida):
    ana = partida.adicionar_jogador("ana")
    partida.remover_jogador(ana.id)
    partida.remover_jogador(ana.id)  # remover duas vezes não é erro
    assert partida.jogadores == {}


def test_tick_conta_os_passos(partida):
    for _ in range(3):
        partida.avancar(DT)
    assert partida.tick == 3


def test_mesma_semente_mesma_partida():
    a, b = Partida(semente=7), Partida(semente=7)
    assert a.moedas == b.moedas
    assert (a.adicionar_jogador("x").x, a.adicionar_jogador("y").y) == \
           (b.adicionar_jogador("x").x, b.adicionar_jogador("y").y)
