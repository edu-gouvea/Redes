"""Medidas do painel de rede do cliente (cliente/metricas.py)."""

import pytest

from cliente.metricas import MedidorRede
from protocolo.registro import RegistroPacote

INICIO = 1_800_000_000.0


def pacote(instante, seq, tipo="ESTADO", direcao="rx", ts=None, tamanho=300):
    return RegistroPacote(instante=instante, direcao=direcao, tipo=tipo, seq=seq,
                          ts=ts if ts is not None else round(instante * 1000), ack=None,
                          tamanho=tamanho, local="127.0.0.1:50000", remoto="127.0.0.1:5005")


def receber(medidor, seqs, intervalo=0.05):
    for i, seq in enumerate(seqs):
        medidor.ao_pacote(pacote(INICIO + i * intervalo, seq))
    medidor.avancar_relogio(INICIO + len(seqs) * intervalo + 1.0)


def test_sem_lacunas_sem_perda():
    medidor = MedidorRede()
    receber(medidor, range(1, 51))
    assert medidor.perda() == (0, 50)


def test_lacunas_de_seq_sao_perdas():
    medidor = MedidorRede()
    receber(medidor, [s for s in range(1, 51) if s % 10 != 0 or s == 50])
    assert medidor.perda() == (4, 50)
    assert medidor.perda_pct() == pytest.approx(8.0)


def test_reordenacao_nao_e_perda():
    medidor = MedidorRede()
    receber(medidor, [1, 2, 4, 3, 5, 6])
    assert medidor.perda() == (0, 6)


def test_retransmissao_com_o_mesmo_seq_nao_conta_duas_vezes():
    medidor = MedidorRede()
    receber(medidor, [1, 2, 3, 3, 4])
    assert medidor.perda() == (0, 4)


def test_lacuna_recente_ainda_pode_chegar():
    medidor = MedidorRede()
    for i, seq in enumerate([1, 2, 3, 5]):
        medidor.ao_pacote(pacote(INICIO + i * 0.1, seq))
    medidor.avancar_relogio(INICIO + 0.35)  # o 5 chegou há 50 ms: o 4 pode estar a caminho
    assert medidor.perda() == (0, 2)
    medidor.avancar_relogio(INICIO + 0.6)   # passou o prazo e o 4 não veio: perdido
    assert medidor.perda() == (1, 5)


def test_janela_esquece_o_passado():
    medidor = MedidorRede()
    receber(medidor, [1, 5])   # 3 perdidos lá atrás
    medidor.avancar_relogio(INICIO + 60)
    medidor.ao_pacote(pacote(INICIO + 60, 100))
    medidor.ao_pacote(pacote(INICIO + 60.05, 101))
    medidor.avancar_relogio(INICIO + 61)
    assert medidor.perda() == (0, 2)


def test_jitter_zero_com_intervalos_regulares():
    medidor = MedidorRede()
    receber(medidor, range(1, 40))
    assert medidor.jitter_ms == pytest.approx(0.0, abs=0.01)


def test_jitter_rfc3550_com_chegadas_irregulares():
    medidor = MedidorRede()
    # Enviados a cada 50 ms; chegam alternando +0 e +20 ms de atraso: |D| = 20 sempre.
    for i in range(200):
        enviado = INICIO + i * 0.05
        medidor.ao_pacote(pacote(enviado + (0.02 if i % 2 else 0.0), i + 1, ts=round(enviado * 1000)))
    assert medidor.jitter_ms == pytest.approx(20.0, abs=0.5)


def test_relogios_defasados_nao_afetam_o_jitter():
    medidor = MedidorRede()
    for i in range(50):
        enviado_ms = 5_000 + i * 50   # relógio do servidor, sem relação com o nosso
        medidor.ao_pacote(pacote(INICIO + i * 0.05, i + 1, ts=enviado_ms))
    assert medidor.jitter_ms == pytest.approx(0.0, abs=0.01)


def test_estados_por_segundo_e_taxa():
    medidor = MedidorRede()
    for i in range(40):
        medidor.ao_pacote(pacote(INICIO + i * 0.05, i + 1, tamanho=300))
        medidor.ao_pacote(pacote(INICIO + i * 0.05, i + 1, tipo="COMANDO", direcao="tx", tamanho=76))
    medidor.avancar_relogio(INICIO + 1.99)
    assert medidor.estados_por_segundo() == 20
    assert medidor.taxa_bytes("rx") == 20 * 300
    assert medidor.taxa_bytes("tx") == 20 * 76


def test_amostras_de_rtt_ficam_limitadas():
    medidor = MedidorRede()
    for rtt in range(100):
        medidor.registrar_rtt(rtt)
    assert len(medidor.rtts) == 60
    assert medidor.rtts[-1] == 99
