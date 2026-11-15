"""Codificação, decodificação e validação das mensagens (protocolo/mensagens.py)."""

import json

import pytest

import config
from protocolo.mensagens import (
    TIPOS_CONFIAVEIS,
    Mensagem,
    MensagemGrandeDemais,
    MensagemInvalida,
    Tipo,
    VersaoDesconhecida,
    codificar,
    decodificar,
)

TS = 1795564800123

EXEMPLOS_VALIDOS = [
    Mensagem(Tipo.ENTRAR, 1, {"nome": "ana"}, TS),
    Mensagem(Tipo.ENTRAR_OK, 2, {"id": 3, "largura": 800, "altura": 600, "raio_jogador": 15,
                                 "raio_moeda": 8, "velocidade": 200, "taxa": 20}, TS),
    Mensagem(Tipo.ENTRAR_NEGADO, 2, {"motivo": "partida cheia"}, TS),
    Mensagem(Tipo.SAIR, 9, {}, TS),
    Mensagem(Tipo.COMANDO, 42, {"dx": -1, "dy": 1}, TS),
    Mensagem(Tipo.ESTADO, 120, {"tick": 7, "ultimo_comando": 0,
                                "jogadores": [{"id": 1, "nome": "zé", "x": 10, "y": 20.5, "pontos": 0}],
                                "moedas": [[1.5, 2]]}, TS),
    Mensagem(Tipo.EVENTO, 40, {"evento": "ponto", "jogador": 3, "pontos": 5}, TS),
    Mensagem(Tipo.EVENTO, 41, {"evento": "entrou", "jogador": 4, "nome": "robo-1"}, TS),
    Mensagem(Tipo.EVENTO, 42, {"evento": "saiu", "jogador": 4, "motivo": "pediu"}, TS),
    Mensagem(Tipo.ACK, 43, {"ack": 40}, TS),
    Mensagem(Tipo.PING, 77, {}, TS),
    Mensagem(Tipo.PONG, 130, {"ts_eco": TS}, TS + 1),
]


def corpo_valido(**alteracoes):
    corpo = {"v": 1, "tipo": "COMANDO", "seq": 5, "ts": TS, "dados": {"dx": 0, "dy": 0}}
    corpo.update(alteracoes)
    return corpo


def bytes_de(corpo) -> bytes:
    return json.dumps(corpo).encode("utf-8")


# --- Ida e volta ------------------------------------------------------------

@pytest.mark.parametrize("mensagem", EXEMPLOS_VALIDOS, ids=lambda m: f"{m.tipo}-{m.seq}")
def test_ida_e_volta_preserva_a_mensagem(mensagem):
    assert decodificar(codificar(mensagem)) == mensagem


def test_todos_os_tipos_tem_exemplo():
    assert {m.tipo for m in EXEMPLOS_VALIDOS} == set(Tipo)


def test_codificacao_e_json_compacto_com_cabecalho_na_ordem():
    datagrama = codificar(Mensagem(Tipo.COMANDO, 42, {"dx": 1, "dy": 0}, TS))
    assert datagrama == b'{"v":1,"tipo":"COMANDO","seq":42,"ts":1795564800123,"dados":{"dx":1,"dy":0}}'


def test_acentos_vao_como_utf8_e_contam_em_bytes():
    datagrama = codificar(Mensagem(Tipo.ENTRAR, 1, {"nome": "joão"}, TS))
    assert "joão".encode("utf-8") in datagrama
    assert len("joão".encode("utf-8")) == 5  # "ã" ocupa 2 bytes


def test_ts_e_carimbado_na_criacao():
    mensagem = Mensagem(Tipo.PING, 1)
    assert mensagem.ts > 1_700_000_000_000  # ms desde a época, não segundos


def test_tipo_pode_ser_dado_como_texto():
    assert Mensagem("PING", 1).tipo is Tipo.PING


def test_confiabilidade_e_propriedade_do_tipo():
    assert TIPOS_CONFIAVEIS == {Tipo.ENTRAR, Tipo.ENTRAR_OK, Tipo.ENTRAR_NEGADO, Tipo.SAIR, Tipo.EVENTO}
    assert Mensagem(Tipo.EVENTO, 1, {"evento": "ponto", "jogador": 1, "pontos": 1}).confiavel
    assert not Mensagem(Tipo.ESTADO, 1, {"tick": 0, "ultimo_comando": 0, "jogadores": [], "moedas": []}).confiavel


def test_campos_extras_sao_ignorados():
    corpo = corpo_valido(extra="futuro")
    corpo["dados"]["turbo"] = True
    mensagem = decodificar(bytes_de(corpo))
    assert mensagem.tipo is Tipo.COMANDO


# --- Mensagens malformadas: sempre MensagemInvalida, nunca outra exceção ----

DATAGRAMAS_INVALIDOS = {
    "não é utf-8": b"\xff\xfe\x00",
    "não é json": b"isto nao e json",
    "json truncado": b'{"v":1,"tipo":"PING"',
    "json não é objeto": b"[1, 2, 3]",
    "vazio": b"",
    "falta dados": bytes_de({"v": 1, "tipo": "PING", "seq": 1, "ts": TS}),
    "tipo desconhecido": bytes_de(corpo_valido(tipo="TELETRANSPORTE")),
    "tipo não é texto": bytes_de(corpo_valido(tipo=["COMANDO"])),
    "seq negativo": bytes_de(corpo_valido(seq=-1)),
    "seq texto": bytes_de(corpo_valido(seq="5")),
    "seq booleano": bytes_de(corpo_valido(seq=True)),
    "seq fracionário": bytes_de(corpo_valido(seq=5.5)),
    "ts fracionário": bytes_de(corpo_valido(ts=1.5)),
    "dados não é objeto": bytes_de(corpo_valido(dados=[1, 2])),
    "comando dx fora do intervalo": bytes_de(corpo_valido(dados={"dx": 2, "dy": 0})),
    "comando dx booleano": bytes_de(corpo_valido(dados={"dx": True, "dy": 0})),
    "comando sem dy": bytes_de(corpo_valido(dados={"dx": 1})),
    "NaN": b'{"v":1,"tipo":"PONG","seq":1,"ts":1,"dados":{"ts_eco":NaN}}',
    "estado com x infinito": (
        b'{"v":1,"tipo":"ESTADO","seq":1,"ts":1,"dados":{"tick":1,"ultimo_comando":0,'
        b'"jogadores":[{"id":1,"nome":"a","x":1e999,"y":0,"pontos":0}],"moedas":[]}}'
    ),
    "estado com moeda de 3 coordenadas": bytes_de(corpo_valido(
        tipo="ESTADO", dados={"tick": 1, "ultimo_comando": 0, "jogadores": [], "moedas": [[1, 2, 3]]})),
    "estado com jogador que não é objeto": bytes_de(corpo_valido(
        tipo="ESTADO", dados={"tick": 1, "ultimo_comando": 0, "jogadores": [7], "moedas": []})),
    "entrar com nome vazio": bytes_de(corpo_valido(tipo="ENTRAR", dados={"nome": ""})),
    "entrar com nome longo": bytes_de(corpo_valido(tipo="ENTRAR", dados={"nome": "x" * (config.TAMANHO_MAXIMO_NOME + 1)})),
    "entrar com quebra de linha no nome": bytes_de(corpo_valido(tipo="ENTRAR", dados={"nome": "a\nFALSO"})),
    "evento desconhecido": bytes_de(corpo_valido(tipo="EVENTO", dados={"evento": "explodiu", "jogador": 1})),
    "evento não é texto": bytes_de(corpo_valido(tipo="EVENTO", dados={"evento": ["ponto"], "jogador": 1})),
    "evento ponto sem pontos": bytes_de(corpo_valido(tipo="EVENTO", dados={"evento": "ponto", "jogador": 1})),
    "ack sem campo ack": bytes_de(corpo_valido(tipo="ACK", dados={})),
    "entrar_ok com campo faltando": bytes_de(corpo_valido(tipo="ENTRAR_OK", dados={"id": 1})),
}


@pytest.mark.parametrize("datagrama", DATAGRAMAS_INVALIDOS.values(), ids=DATAGRAMAS_INVALIDOS.keys())
def test_datagrama_malformado_levanta_mensagem_invalida(datagrama):
    with pytest.raises(MensagemInvalida):
        decodificar(datagrama)


def test_inteiro_gigante_nao_derruba_a_validacao():
    corpo = corpo_valido(tipo="ESTADO", dados={
        "tick": 1, "ultimo_comando": 0, "moedas": [],
        "jogadores": [{"id": 1, "nome": "a", "x": 10**400, "y": 0, "pontos": 0}],
    })
    decodificar(bytes_de(corpo))  # aceito como número; o importante é não estourar


def test_lixo_aleatorio_so_gera_mensagem_invalida():
    import random

    gerador = random.Random(1234)
    for _ in range(2000):
        datagrama = bytes(gerador.randrange(256) for _ in range(gerador.randrange(80)))
        try:
            decodificar(datagrama)
        except MensagemInvalida:
            pass


# --- Versão -----------------------------------------------------------------

@pytest.mark.parametrize("versao", [2, 0, "1", True, None])
def test_versao_desconhecida_e_rejeitada(versao):
    with pytest.raises(VersaoDesconhecida):
        decodificar(bytes_de(corpo_valido(v=versao)))


def test_versao_desconhecida_tambem_e_mensagem_invalida():
    assert issubclass(VersaoDesconhecida, MensagemInvalida)


# --- Tamanho (MTU / fragmentação) -------------------------------------------

def test_datagrama_no_limite_exato_e_aceito_e_um_byte_a_mais_nao():
    base = bytes_de(corpo_valido())
    # Espaços no fim são JSON válido: completam o datagrama até o tamanho desejado.
    no_limite = base + b" " * (config.TAMANHO_MAXIMO_DATAGRAMA - len(base))
    assert len(no_limite) == 1200
    decodificar(no_limite)
    with pytest.raises(MensagemGrandeDemais):
        decodificar(no_limite + b" ")


def test_codificar_recusa_mensagem_grande_demais():
    jogadores = [{"id": i, "nome": "x" * config.TAMANHO_MAXIMO_NOME, "x": 1.5, "y": 1.5, "pontos": 0}
                 for i in range(100)]
    grande = Mensagem(Tipo.ESTADO, 1, {"tick": 1, "ultimo_comando": 0, "jogadores": jogadores, "moedas": []})
    with pytest.raises(MensagemGrandeDemais):
        codificar(grande)


def test_estado_no_pior_caso_cabe_em_um_datagrama():
    """Garante que MAX_JOGADORES e NUMERO_MOEDAS não estouram o limite de 1200 bytes.

    Pior caso: todos os jogadores com nome só de emojis (4 bytes cada em UTF-8),
    coordenadas com 1 casa decimal no máximo da arena e todos os inteiros com
    10 dígitos (1 bilhão de ticks a 20/s são mais de 500 dias de servidor no ar).
    """
    grande = 10**9
    nome = "😀" * config.TAMANHO_MAXIMO_NOME
    x_max = round(config.LARGURA_ARENA - 0.1, 1)
    y_max = round(config.ALTURA_ARENA - 0.1, 1)
    jogadores = [
        {"id": grande + i, "nome": nome, "x": x_max, "y": y_max, "pontos": grande}
        for i in range(config.MAX_JOGADORES)
    ]
    moedas = [[x_max, y_max]] * config.NUMERO_MOEDAS
    estado = Mensagem(Tipo.ESTADO, grande,
                      {"tick": grande, "ultimo_comando": grande, "jogadores": jogadores, "moedas": moedas},
                      ts=9_999_999_999_999)
    assert len(codificar(estado)) <= config.TAMANHO_MAXIMO_DATAGRAMA


# --- O remetente também valida (erro de programação aparece cedo) -----------

@pytest.mark.parametrize("mensagem", [
    Mensagem(Tipo.COMANDO, 1, {"dx": 5, "dy": 0}),
    Mensagem(Tipo.COMANDO, -1, {"dx": 0, "dy": 0}),
    Mensagem(Tipo.ESTADO, 1, {"tick": 1, "ultimo_comando": 0, "jogadores": [], "moedas": [[float("nan"), 0]]}),
    Mensagem(Tipo.PING, 1, {"objeto": object()}),
], ids=["dx invalido", "seq negativo", "nan", "nao serializavel"])
def test_codificar_recusa_mensagem_invalida(mensagem):
    with pytest.raises(MensagemInvalida):
        codificar(mensagem)


def test_tipo_desconhecido_na_criacao_e_erro():
    with pytest.raises(ValueError):
        Mensagem("TELETRANSPORTE", 1)
