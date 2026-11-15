"""Registro estruturado de pacotes, igual no servidor e no cliente.

Uma entrada por datagrama enviado (tx) ou recebido (rx), com o que é preciso
para achar o mesmo pacote numa captura do Wireshark: horário, direção, tipo,
seq, ts, tamanho e os dois endereços.

Três saídas, todas opcionais:
- log de texto (logger "arena.pacotes"). COMANDO e ESTADO, que são 20 por
  segundo por jogador, saem em DEBUG; os outros tipos, em INFO. Assim
  `--log INFO` mostra só o tráfego interessante e `--log DEBUG` mostra tudo:
      tx EVENTO        seq=41     101 B  0.0.0.0:5005 -> 127.0.0.1:39924
- arquivo JSON Lines (um objeto JSON por linha), para análise em planilha ou
  pandas pelo Integrante 3;
- observadores: funções chamadas a cada pacote (painel e log na tela do
  cliente pygame, métricas).

PROPOSTA de formato, pendente de aprovação do Integrante 3 (docs/PROTOCOLO.md,
seção 13). Os campos ficam todos em RegistroPacote.como_dict().
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, TextIO

from protocolo.mensagens import Mensagem, Tipo
from protocolo.transporte import Endereco

log = logging.getLogger("arena.pacotes")

# Alto volume: só aparecem no log com --log DEBUG (no arquivo, sempre).
TIPOS_FREQUENTES = frozenset({Tipo.COMANDO, Tipo.ESTADO})
INVALIDO = "INVALIDO"


@dataclass(frozen=True)
class RegistroPacote:
    instante: float      # time.time(): segundos desde a época, como o "Epoch Time" do Wireshark
    direcao: str         # "tx" (enviado) ou "rx" (recebido)
    tipo: str            # tipo da mensagem, ou "INVALIDO" se não passou na validação
    seq: int | None
    ts: int | None       # campo ts da mensagem (ms); com o seq, identifica o pacote na captura
    ack: int | None      # só em ACK: o seq confirmado
    tamanho: int         # bytes da mensagem = carga útil UDP (no Wireshark: udp.length − 8)
    local: str           # "ip:porta" deste lado
    remoto: str          # "ip:porta" do outro lado

    def como_dict(self) -> dict[str, Any]:
        return asdict(self)

    def como_texto(self) -> str:
        origem, destino = (self.local, self.remoto) if self.direcao == "tx" else (self.remoto, self.local)
        seq = "-" if self.seq is None else self.seq
        extra = f" ack={self.ack}" if self.ack is not None else ""
        return f"{self.direcao} {self.tipo:<13} seq={seq:<6}{extra} {self.tamanho:>5} B  {origem} -> {destino}"


class RegistroPacotes:
    def __init__(self, arquivo: str | Path | None = None) -> None:
        self.observadores: list[Callable[[RegistroPacote], None]] = []
        self.pacotes: Counter[tuple[str, str]] = Counter()  # (direcao, tipo) -> quantidade
        self.bytes: Counter[str] = Counter()                 # direcao -> bytes
        self._arquivo: TextIO | None = None
        if arquivo is not None:
            # buffering=1: uma linha gravada por pacote, mesmo se o programa cair.
            self._arquivo = open(arquivo, "a", encoding="utf-8", buffering=1)

    def enviado(self, mensagem: Mensagem, tamanho: int, local: Endereco, remoto: Endereco) -> None:
        self._registrar("tx", mensagem, tamanho, local, remoto)

    def recebido(self, mensagem: Mensagem | None, tamanho: int, local: Endereco, remoto: Endereco) -> None:
        """`mensagem` None: o datagrama chegou mas foi descartado por inválido."""
        self._registrar("rx", mensagem, tamanho, local, remoto)

    def total(self, direcao: str) -> int:
        return sum(n for (d, _), n in self.pacotes.items() if d == direcao)

    def fechar(self) -> None:
        if self._arquivo is not None:
            self._arquivo.close()
            self._arquivo = None

    def _registrar(self, direcao: str, mensagem: Mensagem | None, tamanho: int,
                   local: Endereco, remoto: Endereco) -> None:
        registro = RegistroPacote(
            instante=time.time(),
            direcao=direcao,
            tipo=INVALIDO if mensagem is None else str(mensagem.tipo),
            seq=None if mensagem is None else mensagem.seq,
            ts=None if mensagem is None else mensagem.ts,
            ack=mensagem.dados["ack"] if mensagem is not None and mensagem.tipo is Tipo.ACK else None,
            tamanho=tamanho,
            local=_texto(local),
            remoto=_texto(remoto),
        )
        self.pacotes[(direcao, registro.tipo)] += 1
        self.bytes[direcao] += tamanho
        nivel = logging.DEBUG if mensagem is not None and mensagem.tipo in TIPOS_FREQUENTES else logging.INFO
        if log.isEnabledFor(nivel):
            log.log(nivel, registro.como_texto())
        if self._arquivo is not None:
            self._arquivo.write(json.dumps(registro.como_dict(), ensure_ascii=False) + "\n")
        for observador in self.observadores:
            observador(registro)


def _texto(endereco: Endereco) -> str:
    return f"{endereco[0]}:{endereco[1]}"
