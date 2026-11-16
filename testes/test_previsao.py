"""Previsão local com reconciliação (cliente/previsao.py), sem rede e sem pygame."""

import math

import pytest

from cliente.previsao import Previsor
from servidor.jogo import Partida

PARAMETROS = {"largura": 800, "altura": 600, "raio_jogador": 15, "raio_moeda": 8, "velocidade": 200, "taxa": 20}
INTERVALO = 0.05
PASSO = 200 * INTERVALO  # 10 px por COMANDO


def previsor_em(x, y, **opcoes):
    p = Previsor(PARAMETROS, INTERVALO, **opcoes)
    p.ao_receber_estado(x, y, ultimo_comando=0, agora=0.0)
    return p


@pytest.mark.parametrize("dx, dy, x, y", [
    (1, 0, 400, 300), (1, 1, 400, 300), (-1, 1, 400, 300),
    (1, 0, 780, 300),     # parede direita
    (-1, -1, 20, 20),     # canto
])
def test_mover_segue_exatamente_as_regras_do_servidor(dx, dy, x, y):
    partida = Partida(semente=1)
    jogador = partida.adicionar_jogador("ana")
    jogador.x, jogador.y = x, y
    partida.definir_direcao(jogador.id, dx, dy)
    partida.avancar(INTERVALO)
    previsto = previsor_em(x, y).mover((x, y), dx, dy)
    assert previsto == pytest.approx((jogador.x, jogador.y))


def test_sem_estado_nao_ha_previsao():
    p = Previsor(PARAMETROS, INTERVALO)
    p.ao_enviar_comando(1, 1, 0, 0.0)
    assert not p.pronto
    assert p.posicao(0.0) is None
    assert p.pendentes == 0


def test_comando_move_na_hora_sem_esperar_o_servidor():
    p = previsor_em(400, 300)
    p.ao_enviar_comando(1, 1, 0, agora=1.0)
    assert p.prevista == (400 + PASSO, 300)
    assert p.pendentes == 1


def test_desenho_caminha_ao_longo_do_intervalo_do_comando():
    p = previsor_em(400, 300)
    p.ao_enviar_comando(1, 1, 0, agora=1.0)
    assert p.posicao(1.0)[0] == pytest.approx(400)
    assert p.posicao(1.025)[0] == pytest.approx(405)
    assert p.posicao(1.05)[0] == pytest.approx(410)
    assert p.posicao(2.0)[0] == pytest.approx(410)  # não passa do previsto


def test_reconciliacao_descarta_confirmados_e_reaplica_os_pendentes():
    p = previsor_em(400, 300)
    for seq in (1, 2, 3):
        p.ao_enviar_comando(seq, 1, 0, agora=seq * INTERVALO)
    # O servidor aplicou até o COMANDO 1: posição oficial = 1 passo.
    p.ao_receber_estado(400 + PASSO, 300, ultimo_comando=1, agora=0.16)
    assert p.pendentes == 2
    assert p.prevista == pytest.approx((400 + 3 * PASSO, 300))
    assert p.correcoes == 0  # a previsão estava certa


def test_previsao_errada_e_corrigida_sem_tranco():
    p = previsor_em(400, 300)
    p.ao_enviar_comando(1, 1, 0, agora=0.0)
    # O COMANDO se perdeu: o servidor não andou e ainda não aplicou nada.
    # Mas o COMANDO 1 continua pendente; só quando o 2 for aplicado ele sai.
    p.ao_enviar_comando(2, 0, 0, agora=0.05)
    p.ao_receber_estado(400, 300, ultimo_comando=2, agora=0.1)
    assert p.prevista == (400, 300)
    assert p.correcoes == 1
    assert p.ultima_correcao == pytest.approx(PASSO)
    # O desenho não pula: continua perto de onde estava e converge.
    assert p.posicao(0.1)[0] == pytest.approx(400 + PASSO)
    assert p.posicao(0.2)[0] == pytest.approx(400 + PASSO * math.exp(-1), abs=0.01)
    assert p.posicao(1.0)[0] == pytest.approx(400, abs=0.01)


def test_correcao_grande_e_aplicada_na_hora():
    p = previsor_em(400, 300)
    p.ao_enviar_comando(1, 0, 0, agora=0.0)
    p.ao_receber_estado(100, 100, ultimo_comando=1, agora=0.05)
    assert p.posicao(0.05) == (100, 100)


def test_pendentes_nao_crescem_sem_limite_se_o_servidor_parar_de_confirmar():
    p = previsor_em(400, 300, max_pendentes=10)
    for seq in range(1, 100):
        p.ao_enviar_comando(seq, 0, 0, agora=seq * INTERVALO)
    assert p.pendentes == 10


def test_posicao_desenhada_fica_dentro_da_arena():
    p = previsor_em(784, 300)
    p.ao_enviar_comando(1, 1, 0, agora=0.0)
    for agora in (0.0, 0.03, 0.05, 0.5):
        x, y = p.posicao(agora)
        assert 15 <= x <= 785 and 15 <= y <= 585
