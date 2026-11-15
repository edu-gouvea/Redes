"""Dublês de teste: um transporte em memória e um relógio controlável."""

from __future__ import annotations

from protocolo.mensagens import Mensagem, Tipo, codificar, decodificar
from protocolo.transporte import Endereco, Pacote, Transporte


class TransporteFalso(Transporte):
    """Guarda o que foi enviado e entrega o que o teste mandar, sem socket.

    Tudo o que passa por enviar() é decodificado: se o código sob teste gerar
    uma mensagem fora do protocolo, o teste quebra ali mesmo.
    """

    def __init__(self, endereco: Endereco = ("127.0.0.1", 5005)) -> None:
        self._endereco = endereco
        self._entrada: list[Pacote] = []
        self.enviados: list[tuple[Mensagem, Endereco]] = []
        self.fechado = False

    @property
    def endereco_local(self) -> Endereco:
        return self._endereco

    def enviar(self, dados: bytes, destino: Endereco) -> None:
        self.enviados.append((decodificar(dados), destino))

    def receber(self, timeout: float) -> list[Pacote]:
        pacotes, self._entrada = self._entrada, []
        return pacotes

    def fechar(self) -> None:
        self.fechado = True

    # --- Ajudantes para os testes ---

    def entregar(self, mensagem: Mensagem | bytes, origem: Endereco) -> None:
        dados = mensagem if isinstance(mensagem, bytes) else codificar(mensagem)
        self._entrada.append((dados, origem))

    def para(self, destino: Endereco, tipo: Tipo | None = None) -> list[Mensagem]:
        return [m for m, d in self.enviados if d == destino and (tipo is None or m.tipo == tipo)]

    def limpar(self) -> None:
        self.enviados.clear()


class Relogio:
    """Relógio de mentira: o teste decide quanto tempo passou."""

    def __init__(self, inicio: float = 1000.0) -> None:
        self.agora = inicio

    def __call__(self) -> float:
        return self.agora

    def avancar(self, segundos: float) -> None:
        self.agora += segundos
