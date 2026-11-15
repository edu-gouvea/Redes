"""Cliente-robô sem interface: entra na partida, anda sozinho e imprime o que acontece.

Serve para testar o servidor sem o cliente pygame e para encher a partida de
jogadores. Cada robô tem o próprio socket (porta efêmera própria), então para
o servidor eles são clientes diferentes, mesmo rodando no mesmo processo.

    python -m ferramentas.robo                                 # 1 robô em 127.0.0.1:5005
    python -m ferramentas.robo --quantidade 2 --modo cacador   # 2 robôs que vão atrás das moedas
    python -m ferramentas.robo --servidor 192.168.0.10:5005 --nome bot --duracao 30
"""

from __future__ import annotations

import argparse
import logging
import math
import random
import time
from datetime import datetime

import config
from ferramentas import perda
from protocolo.sessao import SessaoCliente, Situacao
from protocolo.transporte import TransporteUDP, resolver

INTERVALO_PLACAR = 2.0   # segundos entre impressões do placar
PAUSA_DO_LACO = 0.005    # segundos; o robô não precisa de mais precisão que isso


class Robo:
    def __init__(self, sessao: SessaoCliente, modo: str, aleatorio: random.Random) -> None:
        self.sessao = sessao
        self.modo = modo
        self._aleatorio = aleatorio
        self._proxima_troca = 0.0
        self._situacao_anunciada: Situacao | None = None

    def decidir(self, agora: float) -> None:
        if self.sessao.situacao is not Situacao.EM_JOGO:
            return
        if self.modo == "cacador":
            self.sessao.definir_direcao(*self._rumo_da_moeda_mais_proxima())
        elif agora >= self._proxima_troca:
            self.sessao.definir_direcao(self._aleatorio.choice((-1, 0, 1)), self._aleatorio.choice((-1, 0, 1)))
            self._proxima_troca = agora + self._aleatorio.uniform(0.5, 2.0)

    def _rumo_da_moeda_mais_proxima(self) -> tuple[int, int]:
        eu = self.sessao.meu_jogador()
        if eu is None or not self.sessao.estado["moedas"]:
            return (0, 0)
        mx, my = min(self.sessao.estado["moedas"], key=lambda m: math.hypot(m[0] - eu["x"], m[1] - eu["y"]))
        return (_sinal(mx - eu["x"]), _sinal(my - eu["y"]))

    def anunciar_mudanca(self) -> None:
        sessao = self.sessao
        if sessao.situacao is self._situacao_anunciada:
            return
        self._situacao_anunciada = sessao.situacao
        if sessao.situacao is Situacao.EM_JOGO:
            imprimir(f"{sessao.nome} | entrou como jogador {sessao.meu_id} "
                     f"(porta local {sessao.transporte.endereco_local[1]})")
        elif sessao.situacao is Situacao.RECUSADO:
            imprimir(f"{sessao.nome} | recusado: {sessao.motivo_recusa}")
        elif sessao.situacao is Situacao.SEM_RESPOSTA:
            imprimir(f"{sessao.nome} | sem resposta do servidor {sessao.servidor[0]}:{sessao.servidor[1]}")


def _sinal(diferenca: float, folga: float = 3.0) -> int:
    if abs(diferenca) <= folga:
        return 0
    return 1 if diferenca > 0 else -1


# --- Saída na tela ----------------------------------------------------------

def imprimir(texto: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {texto}", flush=True)


def nomes_por_id(sessao: SessaoCliente) -> dict[int, str]:
    if sessao.estado is None:
        return {}
    return {j["id"]: j["nome"] for j in sessao.estado["jogadores"]}


def descrever_evento(evento: dict, nomes: dict[int, str]) -> str:
    quem = nomes.get(evento["jogador"], evento.get("nome", f"jogador {evento['jogador']}"))
    if evento["evento"] == "ponto":
        total = evento["pontos"]
        return f"EVENTO: {quem} pegou uma moeda ({total} ponto{'' if total == 1 else 's'})"
    if evento["evento"] == "entrou":
        return f"EVENTO: {evento['nome']} entrou"
    return f"EVENTO: {quem} saiu ({evento['motivo']})"


def descrever_placar(sessao: SessaoCliente) -> str:
    jogadores = sorted(sessao.estado["jogadores"], key=lambda j: (-j["pontos"], j["id"]))
    placar = " | ".join(f"{j['nome']} {j['pontos']}" for j in jogadores)
    return f"placar (tick {sessao.estado['tick']}): {placar}"


def descrever_rede(sessao: SessaoCliente) -> str:
    c = sessao.canal.contadores()
    rtt = "?" if c.rtt_estimado_ms is None else f"{c.rtt_estimado_ms:.1f} ms"
    return (f"rede: ESTADOs {sessao.estados_recebidos} (fora de ordem {sessao.estados_fora_de_ordem}) | "
            f"confiáveis: enviadas {c.enviadas}, retransmitidas {c.retransmitidas}, "
            f"duplicadas recebidas {c.duplicadas_recebidas}, desistências {c.desistencias} | "
            f"EstimatedRTT {rtt}, timeout {c.timeout_ms:.0f} ms")


# --- Execução ---------------------------------------------------------------

def executar(robos: list[Robo], duracao: float | None) -> None:
    """Laço único para todos os robôs: cada um processa a própria sessão sem bloquear."""
    inicio = time.monotonic()
    proximo_placar = inicio + INTERVALO_PLACAR
    # Só o primeiro robô imprime eventos e placar; os outros veriam os mesmos.
    observador = robos[0].sessao
    while duracao is None or time.monotonic() - inicio < duracao:
        agora = time.monotonic()
        for robo in robos:
            robo.sessao.processar()
            robo.decidir(agora)
            robo.anunciar_mudanca()
        for evento in observador.consumir_eventos():
            imprimir(f"{observador.nome} | {descrever_evento(evento, nomes_por_id(observador))}")
        for robo in robos[1:]:
            robo.sessao.consumir_eventos()
        if agora >= proximo_placar and observador.estado is not None:
            imprimir(f"{observador.nome} | {descrever_placar(observador)}")
            imprimir(f"{observador.nome} | {descrever_rede(observador)}")
            proximo_placar = agora + INTERVALO_PLACAR
        if all(r.sessao.situacao in (Situacao.RECUSADO, Situacao.SEM_RESPOSTA) for r in robos):
            break
        time.sleep(PAUSA_DO_LACO)


def encerrar(robos: list[Robo]) -> None:
    for robo in robos:
        robo.sessao.sair()
        robo.sessao.transporte.fechar()


def ler_argumentos(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m ferramentas.robo", description="Robôs do Arena em Rede.")
    parser.add_argument("--servidor", default=f"127.0.0.1:{config.PORTA_PADRAO}", help="host:porta")
    parser.add_argument("--nome", default="robo", help="prefixo do nome (vira robo-1, robo-2, ...)")
    parser.add_argument("--quantidade", type=int, default=1)
    parser.add_argument("--modo", choices=["aleatorio", "cacador"], default="aleatorio",
                        help="aleatorio: muda de direção ao acaso; cacador: vai na moeda mais próxima")
    parser.add_argument("--duracao", type=float, default=None, help="segundos (padrão: até Ctrl+C)")
    parser.add_argument("--semente", type=int, default=None)
    parser.add_argument("--log", default="WARNING", choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="INFO mostra retransmissões e duplicatas")
    perda.adicionar_argumentos(parser)
    return parser.parse_args(argv)


def separar_host_porta(texto: str) -> tuple[str, int]:
    host, _, porta = texto.rpartition(":")
    return (host or "127.0.0.1", int(porta))


def main(argv: list[str] | None = None) -> int:
    args = ler_argumentos(argv)
    logging.basicConfig(level=args.log, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    servidor = resolver(*separar_host_porta(args.servidor))
    aleatorio = random.Random(args.semente)
    robos = [
        Robo(SessaoCliente(perda.envolver_se_pedido(TransporteUDP.cliente(), args), servidor, f"{args.nome}-{i}"),
             args.modo, aleatorio)
        for i in range(1, args.quantidade + 1)
    ]
    imprimir(f"{len(robos)} robô(s) entrando em {servidor[0]}:{servidor[1]} (modo {args.modo})")
    try:
        executar(robos, args.duracao)
    except KeyboardInterrupt:
        pass
    finally:
        encerrar(robos)
    ok = all(r.sessao.situacao is Situacao.ENCERRADA and r.sessao.meu_id is not None for r in robos)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
