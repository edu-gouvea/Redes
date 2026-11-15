"""Protocolo de aplicação do Arena em Rede: tipos, formato e validação das mensagens.

Cap. 2, protocolos da camada de aplicação: um protocolo define os tipos de
mensagem trocados, a sintaxe (quais campos e como são escritos), a semântica
(o que cada campo significa) e as regras de quando enviar e como responder.
Este módulo cuida dos tipos e da sintaxe; a semântica e as regras estão em
docs/PROTOCOLO.md, que é o contrato da equipe.

Cada mensagem é um objeto JSON em UTF-8 e ocupa exatamente um datagrama UDP.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable

import config


class Tipo(StrEnum):
    ENTRAR = "ENTRAR"
    ENTRAR_OK = "ENTRAR_OK"
    ENTRAR_NEGADO = "ENTRAR_NEGADO"
    SAIR = "SAIR"
    COMANDO = "COMANDO"
    ESTADO = "ESTADO"
    EVENTO = "EVENTO"
    ACK = "ACK"
    PING = "PING"
    PONG = "PONG"


# Confiabilidade seletiva: só estes tipos exigem ACK e são retransmitidos.
# COMANDO e ESTADO não entram porque o próximo (50 ms depois) já substitui o
# perdido; retransmitir um dado velho só atrasaria o novo.
TIPOS_CONFIAVEIS = frozenset({
    Tipo.ENTRAR,
    Tipo.ENTRAR_OK,
    Tipo.ENTRAR_NEGADO,
    Tipo.SAIR,
    Tipo.EVENTO,
})

CAMPOS_CABECALHO = ("v", "tipo", "seq", "ts", "dados")


class MensagemInvalida(ValueError):
    """Datagrama fora do protocolo. Quem recebe descarta e registra no log."""


class VersaoDesconhecida(MensagemInvalida):
    """Campo `v` diferente de config.VERSAO_PROTOCOLO."""


class MensagemGrandeDemais(MensagemInvalida):
    """Mensagem que, codificada, passa de config.TAMANHO_MAXIMO_DATAGRAMA bytes."""


def agora_ms() -> int:
    """Relógio de parede em milissegundos desde a época Unix (valor do campo `ts`)."""
    return time.time_ns() // 1_000_000


@dataclass(frozen=True)
class Mensagem:
    tipo: Tipo
    seq: int
    dados: dict[str, Any] = field(default_factory=dict)
    # Carimbado na criação. Uma retransmissão reenvia o mesmo objeto, logo o
    # mesmo `seq` e o mesmo `ts`: é o mesmo pacote, não um pacote novo.
    ts: int = field(default_factory=agora_ms)
    v: int = config.VERSAO_PROTOCOLO

    def __post_init__(self) -> None:
        object.__setattr__(self, "tipo", Tipo(self.tipo))

    @property
    def confiavel(self) -> bool:
        return self.tipo in TIPOS_CONFIAVEIS


# --- Codificação ------------------------------------------------------------

def codificar(mensagem: Mensagem) -> bytes:
    """Mensagem -> bytes prontos para um datagrama. Valida antes de enviar."""
    _validar(mensagem)
    corpo = {
        "v": mensagem.v,
        "tipo": str(mensagem.tipo),
        "seq": mensagem.seq,
        "ts": mensagem.ts,
        "dados": mensagem.dados,
    }
    try:
        # separators sem espaços e ensure_ascii=False: menos bytes por datagrama
        # ("é" vira 2 bytes em UTF-8 em vez dos 6 de "é").
        texto = json.dumps(corpo, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as erro:
        raise MensagemInvalida(f"dados não serializáveis em JSON: {erro}") from None
    datagrama = texto.encode("utf-8")
    _exigir_tamanho(datagrama)
    return datagrama


def decodificar(datagrama: bytes) -> Mensagem:
    """bytes recebidos -> Mensagem. Levanta MensagemInvalida se o datagrama não segue o protocolo."""
    _exigir_tamanho(datagrama)
    try:
        texto = datagrama.decode("utf-8")
    except UnicodeDecodeError:
        raise MensagemInvalida("datagrama não é UTF-8 válido") from None
    try:
        corpo = json.loads(texto, parse_constant=_rejeitar_constante)
    except (ValueError, RecursionError) as erro:
        raise MensagemInvalida(f"JSON inválido: {erro}") from None
    mensagem = _montar(corpo)
    _validar(mensagem)
    return mensagem


def _exigir_tamanho(datagrama: bytes) -> None:
    # Cap. 4, fragmentação: um datagrama maior que o MTU do caminho é quebrado
    # em fragmentos IP, e perder qualquer um deles perde a mensagem inteira.
    # O limite é em bytes, não em caracteres: "ç" ocupa 2 bytes em UTF-8.
    limite = config.TAMANHO_MAXIMO_DATAGRAMA
    if len(datagrama) > limite:
        raise MensagemGrandeDemais(f"{len(datagrama)} bytes excede o limite de {limite} bytes")


def _rejeitar_constante(nome: str) -> None:
    # O json do Python aceita NaN e Infinity por padrão; uma posição NaN
    # vinda da rede quebraria a simulação.
    raise ValueError(f"constante {nome} não é permitida")


def _montar(corpo: Any) -> Mensagem:
    if not isinstance(corpo, dict):
        raise MensagemInvalida("a mensagem deve ser um objeto JSON")
    faltando = [campo for campo in CAMPOS_CABECALHO if campo not in corpo]
    if faltando:
        raise MensagemInvalida(f"faltam campos no cabeçalho: {', '.join(faltando)}")
    _exigir_versao(corpo["v"])
    try:
        tipo = Tipo(corpo["tipo"])
    except ValueError:
        raise MensagemInvalida(f"tipo desconhecido: {corpo['tipo']!r}") from None
    # Campos extras no cabeçalho são ignorados: uma versão futura pode
    # acrescentar campos sem quebrar quem só entende esta.
    return Mensagem(tipo=tipo, seq=corpo["seq"], dados=corpo["dados"], ts=corpo["ts"], v=corpo["v"])


# --- Validação --------------------------------------------------------------

def _validar(mensagem: Mensagem) -> None:
    _exigir_versao(mensagem.v)
    if not _eh_natural(mensagem.seq):
        raise MensagemInvalida(f"seq deve ser inteiro >= 0, veio {mensagem.seq!r}")
    if not _eh_natural(mensagem.ts):
        raise MensagemInvalida(f"ts deve ser inteiro >= 0, veio {mensagem.ts!r}")
    if not isinstance(mensagem.dados, dict):
        raise MensagemInvalida("dados deve ser um objeto JSON")
    _VALIDADORES_DADOS[mensagem.tipo](mensagem.dados)


def _exigir_versao(versao: Any) -> None:
    if not _eh_inteiro(versao) or versao != config.VERSAO_PROTOCOLO:
        raise VersaoDesconhecida(
            f"versão {versao!r} desconhecida (esta implementação fala a versão {config.VERSAO_PROTOCOLO})"
        )


def _eh_inteiro(valor: Any) -> bool:
    # Em Python, bool é subclasse de int: sem esta checagem, `true` do JSON
    # passaria como o número 1.
    return isinstance(valor, int) and not isinstance(valor, bool)


def _eh_natural(valor: Any) -> bool:
    return _eh_inteiro(valor) and valor >= 0


def _eh_numero(valor: Any) -> bool:
    if _eh_inteiro(valor):
        return True
    # "1e999" vira infinito no json.loads; isfinite barra isso. (Não se chama
    # isfinite num int: um inteiro gigante estouraria a conversão para float.)
    return isinstance(valor, float) and math.isfinite(valor)


def _eh_texto(valor: Any) -> bool:
    return isinstance(valor, str)


def _eh_nome(valor: Any) -> bool:
    # isprintable() barra quebras de linha e caracteres de controle, que
    # permitiriam a um cliente forjar linhas falsas no log do servidor.
    return (
        isinstance(valor, str)
        and 1 <= len(valor) <= config.TAMANHO_MAXIMO_NOME
        and valor.isprintable()
    )


def _eh_direcao(valor: Any) -> bool:
    return _eh_inteiro(valor) and valor in (-1, 0, 1)


def _eh_sequencia(valor: Any) -> bool:
    return isinstance(valor, (list, tuple))


def _eh_ponto(valor: Any) -> bool:
    return _eh_sequencia(valor) and len(valor) == 2 and all(_eh_numero(c) for c in valor)


def _exigir(dados: dict[str, Any], campo: str, verificador: Callable[[Any], bool], esperado: str) -> None:
    if campo not in dados or not verificador(dados[campo]):
        raise MensagemInvalida(f"campo '{campo}' ausente ou inválido (esperado: {esperado})")


def _sem_campos(dados: dict[str, Any]) -> None:
    """SAIR e PING não carregam dados; campos extras são ignorados."""


def _validar_entrar(dados: dict[str, Any]) -> None:
    _exigir(dados, "nome", _eh_nome, f"texto imprimível de 1 a {config.TAMANHO_MAXIMO_NOME} caracteres")


CAMPOS_ENTRAR_OK = ("id", "largura", "altura", "raio_jogador", "raio_moeda", "velocidade", "taxa")


def _validar_entrar_ok(dados: dict[str, Any]) -> None:
    for campo in CAMPOS_ENTRAR_OK:
        _exigir(dados, campo, _eh_natural, "inteiro >= 0")


def _validar_entrar_negado(dados: dict[str, Any]) -> None:
    _exigir(dados, "motivo", _eh_texto, "texto")


def _validar_comando(dados: dict[str, Any]) -> None:
    for eixo in ("dx", "dy"):
        _exigir(dados, eixo, _eh_direcao, "-1, 0 ou 1")


def _validar_estado(dados: dict[str, Any]) -> None:
    _exigir(dados, "tick", _eh_natural, "inteiro >= 0")
    _exigir(dados, "ultimo_comando", _eh_natural, "inteiro >= 0")
    _exigir(dados, "jogadores", _eh_sequencia, "lista")
    _exigir(dados, "moedas", _eh_sequencia, "lista")
    for jogador in dados["jogadores"]:
        _validar_jogador(jogador)
    for moeda in dados["moedas"]:
        if not _eh_ponto(moeda):
            raise MensagemInvalida(f"moeda inválida: {moeda!r} (esperado: [x, y])")


def _validar_jogador(jogador: Any) -> None:
    if not isinstance(jogador, dict):
        raise MensagemInvalida("cada jogador do ESTADO deve ser um objeto")
    _exigir(jogador, "id", _eh_natural, "inteiro >= 0")
    _exigir(jogador, "nome", _eh_nome, "nome válido")
    _exigir(jogador, "x", _eh_numero, "número")
    _exigir(jogador, "y", _eh_numero, "número")
    _exigir(jogador, "pontos", _eh_natural, "inteiro >= 0")


# Campos obrigatórios de cada tipo de EVENTO, além de "evento" e "jogador".
CAMPOS_EVENTO: dict[str, tuple[tuple[str, Callable[[Any], bool], str], ...]] = {
    "ponto": (("pontos", _eh_natural, "inteiro >= 0"),),
    "entrou": (("nome", _eh_nome, "nome válido"),),
    "saiu": (("motivo", _eh_texto, "texto"),),
}


def _validar_evento(dados: dict[str, Any]) -> None:
    _exigir(dados, "evento", lambda e: _eh_texto(e) and e in CAMPOS_EVENTO, " ou ".join(CAMPOS_EVENTO))
    _exigir(dados, "jogador", _eh_natural, "inteiro >= 0")
    for campo, verificador, esperado in CAMPOS_EVENTO[dados["evento"]]:
        _exigir(dados, campo, verificador, esperado)


def _validar_ack(dados: dict[str, Any]) -> None:
    _exigir(dados, "ack", _eh_natural, "seq confirmado, inteiro >= 0")


def _validar_pong(dados: dict[str, Any]) -> None:
    _exigir(dados, "ts_eco", _eh_natural, "ts do PING, inteiro >= 0")


_VALIDADORES_DADOS: dict[Tipo, Callable[[dict[str, Any]], None]] = {
    Tipo.ENTRAR: _validar_entrar,
    Tipo.ENTRAR_OK: _validar_entrar_ok,
    Tipo.ENTRAR_NEGADO: _validar_entrar_negado,
    Tipo.SAIR: _sem_campos,
    Tipo.COMANDO: _validar_comando,
    Tipo.ESTADO: _validar_estado,
    Tipo.EVENTO: _validar_evento,
    Tipo.ACK: _validar_ack,
    Tipo.PING: _sem_campos,
    Tipo.PONG: _validar_pong,
}
