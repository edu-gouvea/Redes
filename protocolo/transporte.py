"""Camada de transporte do jogo: a única parte do código que mexe em socket.

Servidor, SessaoCliente e lógica do jogo só conhecem a interface `Transporte`
(bytes + endereço). Trocar UDP por TCP (etapa f) é trocar a classe criada aqui,
sem mexer em mais nada.

Cap. 2, programação de sockets: o socket é a porta entre o processo e a camada
de transporte do sistema operacional.
"""

from __future__ import annotations

import logging
import selectors
import socket
from abc import ABC, abstractmethod

import config

Endereco = tuple[str, int]          # (IP, porta)
Pacote = tuple[bytes, Endereco]     # (dados, origem)

log = logging.getLogger("arena.transporte")

# Quantos datagramas ler por chamada a receber(): sob uma enxurrada de
# pacotes, o servidor ainda volta ao laço a tempo do próximo tick.
MAX_PACOTES_POR_LEITURA = 256


def resolver(host: str, porta: int) -> Endereco:
    """Nome ("localhost", "notebook-ana.local") -> (IP, porta).

    O recvfrom informa a origem sempre como IP; para comparar "veio do
    servidor?", o endereço guardado também precisa ser IP.
    """
    return (socket.gethostbyname(host), porta)


class Transporte(ABC):
    """Interface única de transporte: envia e recebe bytes endereçados."""

    @abstractmethod
    def enviar(self, dados: bytes, destino: Endereco) -> None:
        """Envia uma mensagem já codificada. Não garante entrega."""

    @abstractmethod
    def receber(self, timeout: float) -> list[Pacote]:
        """Espera até `timeout` segundos e devolve todas as mensagens que chegaram.

        Devolve lista vazia se nada chegou no prazo. Com timeout 0, só verifica
        e volta na hora (uso típico no laço de um jogo).
        """

    @abstractmethod
    def fechar(self) -> None:
        """Libera o socket."""

    @property
    @abstractmethod
    def endereco_local(self) -> Endereco:
        """(IP, porta) em que este lado está escutando."""

    def __enter__(self) -> Transporte:
        return self

    def __exit__(self, *_: object) -> None:
        self.fechar()


class TransporteUDP(Transporte):
    """Um socket UDP: cada enviar() é um datagrama, cada mensagem recebida também.

    Cap. 3, UDP: sem conexão. Não há accept() nem connect(); o mesmo socket
    conversa com todos os clientes, e cada sendto() diz para quem vai.
    """

    def __init__(self, endereco_local: Endereco = ("0.0.0.0", 0)) -> None:
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # bind: associa o socket a uma porta local. Porta 0 = o sistema operacional
        # escolhe uma porta efêmera livre (é o que o cliente faz).
        self._socket.bind(endereco_local)
        self._socket.setblocking(False)
        # O seletor pede ao SO para avisar quando houver datagrama para ler,
        # com prazo: o servidor espera pacotes só até a hora do próximo tick.
        self._seletor = selectors.DefaultSelector()
        self._seletor.register(self._socket, selectors.EVENT_READ)

    @classmethod
    def servidor(cls, porta: int = config.PORTA_PADRAO, endereco: str = config.ENDERECO_ESCUTA) -> TransporteUDP:
        """Socket na porta conhecida, que os clientes precisam saber de antemão."""
        return cls((endereco, porta))

    @classmethod
    def cliente(cls) -> TransporteUDP:
        """Socket numa porta efêmera: o servidor descobre qual pela origem do datagrama."""
        return cls(("0.0.0.0", 0))

    @property
    def endereco_local(self) -> Endereco:
        ip, porta = self._socket.getsockname()[:2]
        return (ip, porta)

    def enviar(self, dados: bytes, destino: Endereco) -> None:
        try:
            self._socket.sendto(dados, destino)
        except OSError as erro:
            # Buffer de envio cheio, rede inalcançável etc. Para o UDP isso é só
            # mais uma perda: registra e segue, sem derrubar o processo.
            log.warning("falha ao enviar %d bytes para %s:%d: %s", len(dados), *destino, erro)

    def receber(self, timeout: float) -> list[Pacote]:
        if not self._seletor.select(max(0.0, timeout)):
            return []
        pacotes: list[Pacote] = []
        while len(pacotes) < MAX_PACOTES_POR_LEITURA:
            try:
                dados, origem = self._socket.recvfrom(config.TAMANHO_BUFFER_RECEPCAO)
            except BlockingIOError:
                break  # não há mais datagramas na fila do socket
            except ConnectionResetError:
                # Só no Windows: quando um envio anterior gerou um ICMP "porta
                # inalcançável" (o cliente fechou), o erro aparece aqui no recvfrom.
                # Sem este tratamento o servidor cairia quando um cliente fecha.
                continue
            pacotes.append((dados, (origem[0], origem[1])))
        return pacotes

    def fechar(self) -> None:
        self._seletor.close()
        self._socket.close()
