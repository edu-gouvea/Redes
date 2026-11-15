"""Ponto de entrada: python -m servidor --porta 5005 [--transporte tcp]"""

from __future__ import annotations

import argparse
import logging
import signal

import config
from ferramentas import perda
from protocolo.registro import RegistroPacotes
from protocolo.transporte import Transporte, TransporteTCP, TransporteUDP
from servidor.jogo import Partida
from servidor.servidor import Servidor

log = logging.getLogger("arena.servidor")

CONFIABILIDADE = {"auto": None, "sim": True, "nao": False}


def ler_argumentos(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m servidor", description="Servidor do Arena em Rede.")
    parser.add_argument("--porta", type=int, default=config.PORTA_PADRAO)
    parser.add_argument("--transporte", choices=["udp", "tcp"], default="udp",
                        help="tcp: a versão para comparação (os clientes também precisam usar tcp)")
    parser.add_argument("--confiabilidade", choices=list(CONFIABILIDADE), default="auto",
                        help="ACK e retransmissão da aplicação; auto = ligado em UDP, desligado em TCP")
    parser.add_argument("--nagle", action="store_true",
                        help="(tcp) deixa o algoritmo de Nagle ligado; o padrão é TCP_NODELAY")
    parser.add_argument("--endereco", default=config.ENDERECO_ESCUTA,
                        help="interface de escuta (0.0.0.0 = todas; 127.0.0.1 = só esta máquina)")
    parser.add_argument("--taxa", type=int, default=config.TAXA_ATUALIZACAO, help="ticks (ESTADOs) por segundo")
    parser.add_argument("--semente", type=int, default=config.SEMENTE_ALEATORIA,
                        help="semente das posições aleatórias, para partidas reproduzíveis")
    parser.add_argument("--log", default=config.NIVEL_LOG, choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="INFO mostra os pacotes de controle; DEBUG, também cada COMANDO e ESTADO")
    parser.add_argument("--registro", metavar="ARQUIVO",
                        help="grava cada pacote em JSON Lines (um objeto por linha), para análise")
    perda.adicionar_argumentos(parser)
    return parser.parse_args(argv)


def configurar_log(nivel: str) -> None:
    logging.basicConfig(
        level=nivel,
        format="%(asctime)s.%(msecs)03d %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _encerrar_com_sigterm(_sinal: int, _quadro: object) -> None:
    # SIGTERM (kill, timeout, scripts de experimento) encerra como o Ctrl+C:
    # passa pelo finally, grava o resumo e fecha o arquivo de registro.
    raise KeyboardInterrupt


def criar_transporte(args: argparse.Namespace) -> Transporte:
    if args.transporte == "tcp":
        base: Transporte = TransporteTCP.servidor(args.porta, args.endereco, nodelay=not args.nagle)
    else:
        base = TransporteUDP.servidor(args.porta, args.endereco)
    return perda.envolver_se_pedido(base, args)


def main(argv: list[str] | None = None) -> None:
    args = ler_argumentos(argv)
    configurar_log(args.log)
    signal.signal(signal.SIGTERM, _encerrar_com_sigterm)
    transporte = criar_transporte(args)
    registro = RegistroPacotes(args.registro)
    servidor = Servidor(transporte, Partida(semente=args.semente), taxa=args.taxa, registro=registro,
                        confiabilidade=CONFIABILIDADE[args.confiabilidade])
    ip, porta = transporte.endereco_local
    log.info("servidor %s escutando em %s:%d, %d ticks/s (Ctrl+C para encerrar)",
             args.transporte.upper(), ip, porta, args.taxa)
    log.info("confiabilidade da aplicação (ACK e retransmissão): %s",
             "ligada" if servidor.confiabilidade else "desligada")
    if args.perda or args.atraso or args.variacao:
        log.info("rede simulada: perda %.0f%%, atraso %.0f ± %.0f ms em cada sentido",
                 args.perda, args.atraso, args.variacao)
        if args.transporte == "tcp":
            log.warning("perda simulada acima do TCP descarta mensagens que um TCP real nunca perderia; "
                        "para experimentos com TCP, use netem")
    try:
        servidor.executar()
    except KeyboardInterrupt:
        log.info("encerrando")
    finally:
        log.info("%s", servidor.resumo())
        transporte.fechar()
        registro.fechar()


if __name__ == "__main__":
    main()
