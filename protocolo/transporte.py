"""Camada de transporte do jogo: a única parte do código que mexe em socket.

Servidor, SessaoCliente e lógica do jogo só conhecem a interface `Transporte`
(bytes + endereço). Trocar UDP por TCP é trocar a classe criada aqui,
sem mexer em mais nada.

Cap. 2, programação de sockets: o socket é a porta entre o processo e a camada
de transporte do sistema operacional.
"""

from __future__ import annotations

import logging
import selectors
import socket
import struct
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

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

    # True se o transporte já entrega tudo, em ordem e sem duplicatas (TCP).
    # Servidor e SessaoCliente consultam isto para decidir se a confiabilidade
    # da aplicação (ACK e retransmissão) precisa ficar ligada; eles perguntam
    # pela garantia, não pelo tipo de transporte.
    garante_entrega: bool = False

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


# --- TCP (para a comparação com UDP) ----------------------------------------

# Enquadramento: 2 bytes de tamanho antes de cada mensagem, inteiro sem sinal
# em big-endian ("!" = ordem de bytes da rede, a mesma dos cabeçalhos IP/TCP).
PREFIXO = struct.Struct("!H")
TAMANHO_LEITURA = 65536
# Bytes aguardando envio numa conexão. Se o outro lado não lê (janela de
# recepção do TCP cheia), não adianta acumular para sempre: fecha a conexão.
LIMITE_SAIDA = 256 * 1024


@dataclass
class _Conexao:
    sock: socket.socket
    endereco: Endereco
    entrada: bytearray = field(default_factory=bytearray)  # bytes recebidos, ainda sem mensagem completa
    saida: bytearray = field(default_factory=bytearray)    # bytes que o send() ainda não aceitou
    eventos: int = selectors.EVENT_READ


class TransporteTCP(Transporte):
    """As mesmas mensagens, sobre TCP, com enquadramento por prefixo de tamanho.

    Cap. 3, a conexão TCP: o TCP entrega um FLUXO DE BYTES, não mensagens. Um
    recv() pode trazer meia mensagem, ou duas grudadas, e um send() pode
    aceitar só parte dos bytes. Por isso cada mensagem vai precedida do seu
    tamanho em 2 bytes, e o receptor junta os bytes até ter a mensagem inteira.

    Para o resto do programa a interface é a mesma do UDP: o "endereço" de um
    cliente é o (IP, porta) da conexão dele. Entrega, ordem e retransmissão
    ficam por conta do TCP (garante_entrega = True).

    Servidor: TransporteTCP.servidor(porta). Cliente: TransporteTCP.cliente((ip, porta)).
    """

    garante_entrega = True

    def __init__(self, nodelay: bool = True) -> None:
        self._nodelay = nodelay
        self._seletor = selectors.DefaultSelector()
        self._escuta: socket.socket | None = None
        self._conexoes: dict[Endereco, _Conexao] = {}
        self._local: Endereco = ("0.0.0.0", 0)

    @classmethod
    def servidor(cls, porta: int = config.PORTA_PADRAO, endereco: str = config.ENDERECO_ESCUTA,
                 nodelay: bool = True) -> TransporteTCP:
        transporte = cls(nodelay)
        escuta = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if sys.platform != "win32":
            # Permite reabrir a porta logo depois de fechar o servidor, sem
            # esperar as conexões antigas saírem do estado TIME_WAIT. (No
            # Windows a mesma opção deixaria dois processos na mesma porta.)
            escuta.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        escuta.bind((endereco, porta))
        escuta.listen()
        escuta.setblocking(False)
        transporte._seletor.register(escuta, selectors.EVENT_READ, data=None)
        transporte._escuta = escuta
        transporte._local = escuta.getsockname()[:2]
        return transporte

    @classmethod
    def cliente(cls, servidor: Endereco, timeout: float = 3.0, nodelay: bool = True) -> TransporteTCP:
        """Conecta ao servidor. O connect() só volta depois da apresentação de três vias."""
        transporte = cls(nodelay)
        sock = socket.create_connection(servidor, timeout=timeout)
        transporte._adicionar_conexao(sock, servidor)
        transporte._local = sock.getsockname()[:2]
        return transporte

    @property
    def endereco_local(self) -> Endereco:
        return self._local

    def enviar(self, dados: bytes, destino: Endereco) -> None:
        conexao = self._conexoes.get(destino)
        if conexao is None:
            log.debug("sem conexão TCP com %s:%d: mensagem descartada", *destino)
            return
        conexao.saida += PREFIXO.pack(len(dados)) + dados
        if len(conexao.saida) > LIMITE_SAIDA:
            log.warning("%s:%d não está lendo (%d bytes parados): conexão fechada",
                        *destino, len(conexao.saida))
            self._fechar_conexao(conexao)
            return
        self._escrever(conexao)

    def receber(self, timeout: float) -> list[Pacote]:
        pacotes: list[Pacote] = []
        for chave, eventos in self._seletor.select(max(0.0, timeout)):
            if chave.data is None:
                self._aceitar()
                continue
            conexao: _Conexao = chave.data
            if eventos & selectors.EVENT_WRITE:
                self._escrever(conexao)
            if eventos & selectors.EVENT_READ and conexao.endereco in self._conexoes:
                pacotes += self._ler(conexao)
        return pacotes

    def fechar(self) -> None:
        for conexao in list(self._conexoes.values()):
            self._fechar_conexao(conexao)
        if self._escuta is not None:
            self._seletor.unregister(self._escuta)
            self._escuta.close()
        self._seletor.close()

    # --- Internos -----------------------------------------------------------

    def _aceitar(self) -> None:
        while True:
            try:
                sock, endereco = self._escuta.accept()
            except BlockingIOError:
                return
            self._adicionar_conexao(sock, (endereco[0], endereco[1]))
            log.info("conexão TCP aceita de %s:%d", endereco[0], endereco[1])

    def _adicionar_conexao(self, sock: socket.socket, endereco: Endereco) -> None:
        sock.setblocking(False)
        if self._nodelay:
            # Desliga o algoritmo de Nagle, que segura mensagens pequenas para
            # juntá-las num segmento só. Bom para economizar banda, ruim para
            # um jogo, onde cada COMANDO de ~80 bytes precisa sair na hora.
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        conexao = _Conexao(sock, endereco)
        self._conexoes[endereco] = conexao
        self._seletor.register(sock, selectors.EVENT_READ, data=conexao)

    def _ler(self, conexao: _Conexao) -> list[Pacote]:
        try:
            pedaco = conexao.sock.recv(TAMANHO_LEITURA)
        except BlockingIOError:
            return []
        except OSError as erro:  # ex.: ConnectionResetError (o outro lado mandou RST)
            log.info("conexão TCP com %s:%d caiu: %s", *conexao.endereco, erro)
            self._fechar_conexao(conexao)
            return []
        if not pedaco:
            # recv() devolvendo b"" é o FIN: o outro lado encerrou a conexão.
            log.info("conexão TCP com %s:%d encerrada pelo outro lado", *conexao.endereco)
            self._fechar_conexao(conexao)
            return []
        conexao.entrada += pedaco
        return self._desenquadrar(conexao)

    def _desenquadrar(self, conexao: _Conexao) -> list[Pacote]:
        """Tira do buffer todas as mensagens completas; o que sobra espera o próximo recv()."""
        pacotes: list[Pacote] = []
        while len(conexao.entrada) >= PREFIXO.size:
            (tamanho,) = PREFIXO.unpack_from(conexao.entrada)
            if tamanho > config.TAMANHO_MAXIMO_DATAGRAMA:
                # Num fluxo de bytes não há como achar o começo da próxima
                # mensagem se um prefixo está errado. A única saída é fechar.
                log.warning("prefixo de %d bytes vindo de %s:%d: fluxo corrompido, conexão fechada",
                            tamanho, *conexao.endereco)
                self._fechar_conexao(conexao)
                break
            fim = PREFIXO.size + tamanho
            if len(conexao.entrada) < fim:
                break  # mensagem ainda incompleta
            pacotes.append((bytes(conexao.entrada[PREFIXO.size:fim]), conexao.endereco))
            del conexao.entrada[:fim]
        return pacotes

    def _escrever(self, conexao: _Conexao) -> None:
        try:
            # send() pode aceitar só parte dos bytes (buffer de envio do SO
            # cheio, controle de fluxo do TCP): o resto fica para depois.
            enviados = conexao.sock.send(conexao.saida)
        except BlockingIOError:
            enviados = 0
        except OSError as erro:
            log.info("falha ao enviar para %s:%d: %s", *conexao.endereco, erro)
            self._fechar_conexao(conexao)
            return
        del conexao.saida[:enviados]
        self._atualizar_interesse(conexao)

    def _atualizar_interesse(self, conexao: _Conexao) -> None:
        """Pede aviso de "pode escrever" só enquanto há bytes parados na saída."""
        eventos = selectors.EVENT_READ | (selectors.EVENT_WRITE if conexao.saida else 0)
        if eventos != conexao.eventos:
            self._seletor.modify(conexao.sock, eventos, data=conexao)
            conexao.eventos = eventos

    def _fechar_conexao(self, conexao: _Conexao) -> None:
        if self._conexoes.pop(conexao.endereco, None) is None:
            return
        self._seletor.unregister(conexao.sock)
        conexao.sock.close()
