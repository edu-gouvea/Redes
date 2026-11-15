"""Números de sequência: geração no remetente e descarte de mensagens velhas no destinatário.

Cada par remetente -> destinatário tem seu próprio espaço de sequência, como
cada sentido de uma conexão TCP (Cap. 3, números de sequência e de
reconhecimento do TCP). O cliente tem um contador (só fala com o servidor);
o servidor tem um contador para cada cliente.
"""

from __future__ import annotations


class ContadorSequencia:
    """Gera os `seq` de um par remetente -> destinatário: 1, 2, 3, ...

    Começa em 1 para que 0 signifique "nada ainda" (ex.: `ultimo_comando` do
    ESTADO). O TCP sorteia o número inicial para não confundir segmentos de
    conexões antigas; aqui isso foi simplificado, porque um cliente reiniciado
    normalmente ganha outra porta efêmera e vira outro par (a limitação está
    descrita em docs/PROTOCOLO.md).

    JSON não tem limite de tamanho de inteiro, então o `seq` nunca "dá a volta"
    (no TCP ele tem 32 bits e dá).
    """

    def __init__(self, inicial: int = 1) -> None:
        self._proximo = inicial

    def proximo(self) -> int:
        seq = self._proximo
        self._proximo += 1
        return seq


class FiltroMaisRecente:
    """Aceita uma mensagem só se ela for mais nova que a última aceita.

    Para mensagens não confiáveis do tipo "o mais recente substitui os
    antigos" (COMANDO no servidor, ESTADO no cliente). O UDP não garante
    ordem: um datagrama atrasado pode chegar depois de um mais novo e, se
    fosse aplicado, faria o jogo "voltar no tempo". Usar `<=` (e não `<`)
    descarta também cópias duplicadas pela rede.
    """

    def __init__(self) -> None:
        self.ultimo: int | None = None
        self.descartadas = 0

    def aceitar(self, seq: int) -> bool:
        if self.ultimo is not None and seq <= self.ultimo:
            self.descartadas += 1
            return False
        self.ultimo = seq
        return True
