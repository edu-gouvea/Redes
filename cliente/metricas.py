"""Medidas de rede para o painel do cliente, calculadas ao vivo.

São só para mostrar na tela durante a demo. A análise completa (gráficos,
estatísticas, UDP × TCP) é do Integrante 3, a partir do --registro em JSON Lines,
que traz os mesmos dados brutos.

Alimentado por um observador do registro de pacotes:

    medidor = MedidorRede()
    sessao.registro.observadores.append(medidor.ao_pacote)

- Perda: cada mensagem do servidor para este cliente consome um seq (ESTADO,
  EVENTO, ACK, PONG...). Numa janela recente, esperados = maior − menor + 1 e
  recebidos = seqs distintos; a diferença são as lacunas, ou seja, as perdas.
  Os últimos 200 ms ficam de fora do topo da janela, porque uma lacuna ali
  pode ser só um pacote reordenado ainda a caminho.
  Uma retransmissão reaproveita o seq e não conta duas vezes; um seq que chega
  atrasado (reordenação) preenche a lacuna e não conta como perda.
- Jitter: o ESTADO sai a intervalos fixos e leva o ts de envio. Fórmula da
  RFC 3550 (RTP): D = (R_j − R_i) − (S_j − S_i); J ← J + (|D| − J) / 16. Os
  relógios não precisam estar sincronizados: a defasagem se cancela em D.
- Taxa: bytes de carga útil por segundo em cada sentido (no fio, some 28 B de
  cabeçalho IP + UDP por pacote).
- RTT: as amostras vêm da SessaoCliente (ultimo_rtt_ms a cada PONG).
"""

from __future__ import annotations

from collections import deque

from protocolo.registro import RegistroPacote

JANELA_PERDA = 5.0     # segundos
JANELA_TAXA = 1.0      # segundos
# Um seq que falta entre os que chegaram há menos que isto ainda pode estar a
# caminho (reordenação): só conta como perdido depois desse prazo.
PRAZO_REORDENACAO = 0.2  # segundos
AMOSTRAS_RTT = 60      # um minuto de PINGs no gráfico


class MedidorRede:
    def __init__(self) -> None:
        self.jitter_ms = 0.0
        self.rtts: deque[int] = deque(maxlen=AMOSTRAS_RTT)
        self._recebidos: deque[tuple[float, int]] = deque()   # (instante, seq) na janela de perda
        self._bytes: dict[str, deque[tuple[float, int]]] = {"tx": deque(), "rx": deque()}
        self._estados: deque[float] = deque()
        self._ultimo_estado: tuple[float, int] | None = None  # (chegada em ms, ts)
        self._agora = 0.0

    def ao_pacote(self, pacote: RegistroPacote) -> None:
        agora = pacote.instante
        self._agora = max(self._agora, agora)
        self._bytes[pacote.direcao].append((agora, pacote.tamanho))
        if pacote.direcao != "rx" or pacote.seq is None:
            return
        self._recebidos.append((agora, pacote.seq))
        if pacote.tipo == "ESTADO":
            self._estados.append(agora)
            self._jitter(agora * 1000, pacote.ts)
        self._limpar()

    def registrar_rtt(self, rtt_ms: int) -> None:
        self.rtts.append(rtt_ms)

    # --- Leituras -----------------------------------------------------------

    def avancar_relogio(self, agora: float) -> None:
        """Instante atual (time.time()), para as janelas andarem mesmo sem pacotes chegando."""
        self._agora = max(self._agora, agora)
        self._limpar()

    def perda(self) -> tuple[int, int]:
        """(perdidos, esperados) na janela recente, pelas lacunas de seq."""
        self._limpar()
        maduros = [seq for instante, seq in self._recebidos if instante <= self._agora - PRAZO_REORDENACAO]
        if not maduros:
            return (0, 0)
        menor, maior = min(maduros), max(maduros)
        # Os recentes também contam: podem ser justamente a lacuna chegando atrasada.
        recebidos = {seq for _, seq in self._recebidos if menor <= seq <= maior}
        esperados = maior - menor + 1
        return (esperados - len(recebidos), esperados)

    def perda_pct(self) -> float:
        perdidos, esperados = self.perda()
        return 100.0 * perdidos / esperados if esperados else 0.0

    def estados_por_segundo(self) -> int:
        self._limpar()
        return len(self._estados)

    def taxa_bytes(self, direcao: str) -> int:
        """Bytes de carga útil no último segundo ("tx" ou "rx")."""
        self._limpar()
        return sum(tamanho for _, tamanho in self._bytes[direcao])

    # --- Interno ------------------------------------------------------------

    def _jitter(self, chegada_ms: float, ts: int | None) -> None:
        if ts is None:
            return
        if self._ultimo_estado is not None:
            chegada_anterior, ts_anterior = self._ultimo_estado
            d = (chegada_ms - chegada_anterior) - (ts - ts_anterior)
            self.jitter_ms += (abs(d) - self.jitter_ms) / 16
        self._ultimo_estado = (chegada_ms, ts)

    def _limpar(self) -> None:
        agora = self._agora
        while self._recebidos and self._recebidos[0][0] < agora - JANELA_PERDA:
            self._recebidos.popleft()
        while self._estados and self._estados[0] < agora - JANELA_TAXA:
            self._estados.popleft()
        for fila in self._bytes.values():
            while fila and fila[0][0] < agora - JANELA_TAXA:
                fila.popleft()
