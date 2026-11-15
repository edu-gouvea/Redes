"""Garante que os exemplos do contrato (docs/PROTOCOLO.md) são mensagens válidas.

Se alguém mudar o formato no código e esquecer do documento (ou o contrário),
este teste quebra.
"""

import re
from pathlib import Path

import pytest

from protocolo.mensagens import Tipo, codificar, decodificar

DOCUMENTO = Path(__file__).resolve().parent.parent / "docs" / "PROTOCOLO.md"
EXEMPLOS = re.findall(r"```json\n(.*?)\n```", DOCUMENTO.read_text(encoding="utf-8"), re.DOTALL)


def test_documento_tem_exemplos():
    assert len(EXEMPLOS) >= 10


@pytest.mark.parametrize("exemplo", EXEMPLOS)
def test_exemplo_do_documento_e_mensagem_valida(exemplo):
    mensagem = decodificar(exemplo.encode("utf-8"))
    assert decodificar(codificar(mensagem)) == mensagem


def test_documento_tem_exemplo_de_todos_os_tipos():
    tipos = {decodificar(exemplo.encode("utf-8")).tipo for exemplo in EXEMPLOS}
    assert tipos == set(Tipo)
