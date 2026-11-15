"""Confere que o código citado nos documentos existe de verdade.

docs/FUNDAMENTOS_REDES.md é o roteiro da apresentação: se ele cita
`Servidor.passo()` ou `test_estado_chega_na_taxa_configurada`, esses nomes
precisam existir. Este teste quebra quando alguém renomeia algo no código e
esquece do documento.
"""

import re
import selectors
import socket
import time
from pathlib import Path

import pytest

import config

RAIZ = Path(__file__).resolve().parent.parent
DOCUMENTOS = [RAIZ / "docs" / "FUNDAMENTOS_REDES.md", RAIZ / "docs" / "PROTOCOLO.md", RAIZ / "README.md"]
FONTES = {
    caminho: caminho.read_text(encoding="utf-8")
    for caminho in RAIZ.rglob("*.py")
    if not {"cliente", "experimentos", ".venv", "venv"} & set(caminho.relative_to(RAIZ).parts)
}
CODIGO = "\n".join(FONTES.values())
# Funções da biblioteca padrão citadas nos textos (recv(), sendto(), accept()...).
DA_BIBLIOTECA = set(dir(socket.socket)) | set(dir(socket)) | set(dir(selectors.DefaultSelector)) | set(dir(time))


def trechos_de_codigo() -> list[tuple[str, str]]:
    trechos = []
    for documento in DOCUMENTOS:
        for trecho in re.findall(r"`([^`\n]+)`", documento.read_text(encoding="utf-8")):
            trechos.append((documento.name, trecho))
    return trechos


TRECHOS = trechos_de_codigo()


def _ids(pares):
    return [f"{doc}: {trecho}" for doc, trecho in pares]


ARQUIVOS = sorted({(d, t) for d, t in TRECHOS if re.fullmatch(r"[\w/]+\.py", t)})
CHAMADAS = sorted({(d, m) for d, t in TRECHOS for m in re.findall(r"\b([A-Za-z_][\w.]*)\(\)", t)})
TESTES = sorted({(d, t) for d, t in TRECHOS if re.fullmatch(r"test_\w+", t)})
# Dois caracteres ou mais: "config.X" no texto é só um marcador de exemplo.
CONFIGS = sorted({(d, m) for d, t in TRECHOS for m in re.findall(r"\bconfig\.([A-Z][A-Z_]+)\b", t)})


@pytest.mark.parametrize("documento, caminho", ARQUIVOS, ids=_ids(ARQUIVOS))
def test_arquivo_citado_existe(documento, caminho):
    if "/" in caminho:
        assert (RAIZ / caminho).is_file()
    else:
        assert any(p.name == caminho for p in FONTES), f"nenhum arquivo {caminho} no projeto"


@pytest.mark.parametrize("documento, chamada", CHAMADAS, ids=_ids(CHAMADAS))
def test_funcao_ou_metodo_citado_existe(documento, chamada):
    partes = chamada.split(".")
    nome = partes[-1]
    if partes[0][:1].isupper() and len(partes) == 2:
        classe = partes[0]
        arquivo = next((texto for texto in FONTES.values() if f"class {classe}" in texto), None)
        assert arquivo is not None, f"classe {classe} não encontrada"
        assert re.search(rf"\bdef {nome}\(", arquivo), f"{classe} não tem {nome}()"
    else:
        assert re.search(rf"\bdef {nome}\(", CODIGO) or nome in DA_BIBLIOTECA, f"{nome}() não encontrado"


@pytest.mark.parametrize("documento, teste", TESTES, ids=_ids(TESTES))
def test_teste_citado_existe(documento, teste):
    assert re.search(rf"\bdef {teste}\(", CODIGO), f"teste {teste} não encontrado"


@pytest.mark.parametrize("documento, nome", CONFIGS, ids=_ids(CONFIGS))
def test_configuracao_citada_existe(documento, nome):
    assert hasattr(config, nome), f"config.{nome} não existe"


def test_ha_referencias_suficientes_para_o_teste_valer_algo():
    assert len(ARQUIVOS) > 10 and len(CHAMADAS) > 20 and len(TESTES) >= 4 and len(CONFIGS) > 10
