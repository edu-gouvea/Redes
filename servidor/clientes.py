"""Tabela de clientes do servidor, indexada pelo endereço de origem (IP, porta).

Cap. 3, multiplexação e demultiplexação: o sistema operacional entrega ao
socket do servidor tudo o que chega na porta 5005, de qualquer cliente. Quem
separa "de qual jogador é este datagrama" é esta tabela, pelo endereço de
origem. Dois clientes na mesma máquina têm o mesmo IP e portas diferentes, e
por isso são entradas diferentes. É o papel que a quádrupla tem no TCP.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from protocolo.confiavel import CanalConfiavel
from protocolo.sequencia import FiltroMaisRecente
from protocolo.transporte import Endereco


@dataclass
class Cliente:
    endereco: Endereco
    visto_em: float                       # time.monotonic() da última mensagem válida
    # Numeração (um espaço de seq por par), ACKs, retransmissões e RTT deste cliente.
    canal: CanalConfiavel
    jogador_id: int | None = None         # None: endereço recusado (partida cheia)
    # Último seq de COMANDO aceito; COMANDO mais velho chegou fora de ordem.
    comandos: FiltroMaisRecente = field(default_factory=FiltroMaisRecente)

    @property
    def em_jogo(self) -> bool:
        return self.jogador_id is not None

    @property
    def ultimo_comando(self) -> int:
        return self.comandos.ultimo or 0


class TabelaClientes:
    def __init__(self) -> None:
        self._por_endereco: dict[Endereco, Cliente] = {}

    def buscar(self, endereco: Endereco) -> Cliente | None:
        return self._por_endereco.get(endereco)

    def adicionar(self, endereco: Endereco, agora: float, canal: CanalConfiavel) -> Cliente:
        cliente = Cliente(endereco=endereco, visto_em=agora, canal=canal)
        self._por_endereco[endereco] = cliente
        return cliente

    def remover(self, endereco: Endereco) -> Cliente | None:
        return self._por_endereco.pop(endereco, None)

    def todos(self) -> list[Cliente]:
        return list(self._por_endereco.values())

    def em_jogo(self) -> list[Cliente]:
        return [c for c in self._por_endereco.values() if c.em_jogo]

    def inativos(self, agora: float, timeout: float) -> list[Cliente]:
        """UDP não avisa quando o outro lado some: só o silêncio prolongado indica isso."""
        return [c for c in self._por_endereco.values() if agora - c.visto_em > timeout]

    def __len__(self) -> int:
        return len(self._por_endereco)
