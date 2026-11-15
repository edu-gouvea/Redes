"""Ponto de entrada: python -m servidor --porta 5005"""

from __future__ import annotations

import argparse
import logging

import config
from protocolo.transporte import TransporteUDP
from servidor.jogo import Partida
from servidor.servidor import Servidor

log = logging.getLogger("arena.servidor")


def ler_argumentos(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m servidor", description="Servidor UDP do Arena em Rede.")
    parser.add_argument("--porta", type=int, default=config.PORTA_PADRAO)
    parser.add_argument("--endereco", default=config.ENDERECO_ESCUTA,
                        help="interface de escuta (0.0.0.0 = todas; 127.0.0.1 = só esta máquina)")
    parser.add_argument("--taxa", type=int, default=config.TAXA_ATUALIZACAO, help="ticks (ESTADOs) por segundo")
    parser.add_argument("--semente", type=int, default=config.SEMENTE_ALEATORIA,
                        help="semente das posições aleatórias, para partidas reproduzíveis")
    parser.add_argument("--log", default=config.NIVEL_LOG, choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="DEBUG mostra cada pacote enviado e recebido")
    return parser.parse_args(argv)


def configurar_log(nivel: str) -> None:
    logging.basicConfig(
        level=nivel,
        format="%(asctime)s.%(msecs)03d %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main(argv: list[str] | None = None) -> None:
    args = ler_argumentos(argv)
    configurar_log(args.log)
    transporte = TransporteUDP.servidor(args.porta, args.endereco)
    servidor = Servidor(transporte, Partida(semente=args.semente), taxa=args.taxa)
    ip, porta = transporte.endereco_local
    log.info("servidor UDP escutando em %s:%d, %d ticks/s (Ctrl+C para encerrar)", ip, porta, args.taxa)
    try:
        servidor.executar()
    except KeyboardInterrupt:
        log.info("encerrando")
    finally:
        transporte.fechar()


if __name__ == "__main__":
    main()
