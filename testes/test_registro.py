"""Registro estruturado de pacotes (protocolo/registro.py)."""

import json
import logging

from protocolo.mensagens import Mensagem, Tipo
from protocolo.registro import RegistroPacotes

SERVIDOR = ("0.0.0.0", 5005)
CLIENTE = ("127.0.0.1", 39924)


def evento(seq=41):
    return Mensagem(Tipo.EVENTO, seq, {"evento": "ponto", "jogador": 1, "pontos": 2}, ts=1795564806441)


def test_registro_tem_os_campos_para_achar_o_pacote_no_wireshark():
    registro = RegistroPacotes()
    vistos = []
    registro.observadores.append(vistos.append)
    registro.enviado(evento(), 101, SERVIDOR, CLIENTE)
    [pacote] = vistos
    assert pacote.direcao == "tx"
    assert (pacote.tipo, pacote.seq, pacote.ts, pacote.ack) == ("EVENTO", 41, 1795564806441, None)
    assert (pacote.tamanho, pacote.local, pacote.remoto) == (101, "0.0.0.0:5005", "127.0.0.1:39924")
    assert pacote.instante > 1_700_000_000  # época Unix em segundos, como o "Epoch Time" do Wireshark


def test_texto_mostra_origem_e_destino_na_ordem_do_pacote():
    registro = RegistroPacotes()
    vistos = []
    registro.observadores.append(vistos.append)
    registro.enviado(evento(), 101, SERVIDOR, CLIENTE)
    registro.recebido(Mensagem(Tipo.ACK, 7, {"ack": 41}), 67, SERVIDOR, CLIENTE)
    envio, recepcao = (p.como_texto() for p in vistos)
    assert envio.startswith("tx EVENTO") and envio.endswith("0.0.0.0:5005 -> 127.0.0.1:39924")
    assert "ack=41" in recepcao and recepcao.endswith("127.0.0.1:39924 -> 0.0.0.0:5005")


def test_datagrama_invalido_e_registrado_como_invalido():
    registro = RegistroPacotes()
    vistos = []
    registro.observadores.append(vistos.append)
    registro.recebido(None, 10, SERVIDOR, CLIENTE)
    assert (vistos[0].tipo, vistos[0].seq) == ("INVALIDO", None)


def test_arquivo_json_lines(tmp_path):
    caminho = tmp_path / "pacotes.jsonl"
    registro = RegistroPacotes(caminho)
    registro.enviado(evento(41), 101, SERVIDOR, CLIENTE)
    registro.recebido(Mensagem(Tipo.ACK, 7, {"ack": 41}), 67, SERVIDOR, CLIENTE)
    registro.fechar()
    linhas = [json.loads(linha) for linha in caminho.read_text(encoding="utf-8").splitlines()]
    assert [(l["direcao"], l["tipo"], l["seq"], l["ack"]) for l in linhas] == [
        ("tx", "EVENTO", 41, None),
        ("rx", "ACK", 7, 41),
    ]
    assert set(linhas[0]) == {"instante", "direcao", "tipo", "seq", "ts", "ack", "tamanho", "local", "remoto"}


def test_contagem_por_direcao_e_tipo():
    registro = RegistroPacotes()
    registro.enviado(evento(1), 100, SERVIDOR, CLIENTE)
    registro.enviado(evento(2), 100, SERVIDOR, CLIENTE)
    registro.recebido(None, 7, SERVIDOR, CLIENTE)
    assert registro.total("tx") == 2 and registro.total("rx") == 1
    assert registro.pacotes[("tx", "EVENTO")] == 2
    assert registro.bytes["tx"] == 200


def test_comando_e_estado_so_aparecem_no_log_em_debug(caplog):
    registro = RegistroPacotes()
    comando = Mensagem(Tipo.COMANDO, 3, {"dx": 1, "dy": 0})
    with caplog.at_level(logging.INFO, logger="arena.pacotes"):
        registro.recebido(comando, 76, SERVIDOR, CLIENTE)
        registro.enviado(evento(), 101, SERVIDOR, CLIENTE)
    assert [r.getMessage().split()[1] for r in caplog.records] == ["EVENTO"]
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="arena.pacotes"):
        registro.recebido(comando, 76, SERVIDOR, CLIENTE)
    assert "COMANDO" in caplog.records[0].getMessage()
