"""Números de sequência e descarte de mensagens fora de ordem (protocolo/sequencia.py)."""

from protocolo.sequencia import ContadorSequencia, FiltroMaisRecente


def test_contador_comeca_em_1_e_cresce_de_1_em_1():
    contador = ContadorSequencia()
    assert [contador.proximo() for _ in range(4)] == [1, 2, 3, 4]


def test_contadores_de_pares_diferentes_sao_independentes():
    para_ana, para_bia = ContadorSequencia(), ContadorSequencia()
    para_ana.proximo()
    para_ana.proximo()
    assert para_bia.proximo() == 1


def test_filtro_aceita_sequencia_crescente_mesmo_com_lacunas():
    filtro = FiltroMaisRecente()
    assert all(filtro.aceitar(seq) for seq in [1, 2, 5, 9])
    assert filtro.ultimo == 9
    assert filtro.descartadas == 0


def test_filtro_descarta_mensagem_atrasada():
    filtro = FiltroMaisRecente()
    filtro.aceitar(10)
    assert not filtro.aceitar(8)  # chegou depois da 10: está velha
    assert filtro.ultimo == 10
    assert filtro.descartadas == 1


def test_filtro_descarta_copia_duplicada():
    filtro = FiltroMaisRecente()
    filtro.aceitar(10)
    assert not filtro.aceitar(10)
    assert filtro.descartadas == 1


def test_filtro_cenario_de_reordenacao():
    filtro = FiltroMaisRecente()
    chegada = [1, 2, 4, 3, 5, 5, 6]  # 3 atrasou e chegou depois do 4; 5 duplicou
    aceitas = [seq for seq in chegada if filtro.aceitar(seq)]
    assert aceitas == [1, 2, 4, 5, 6]
    assert filtro.descartadas == 2
