"""Ponto de entrada: python -m cliente [--nome ana] [--servidor 192.168.0.10:5005]

Sem --nome, abre a tela de entrada (nome, servidor e transporte). Com --nome,
entra direto na partida.
"""

from __future__ import annotations

import argparse
import logging

import config
from ferramentas import perda
from protocolo.registro import RegistroPacotes

CONFIABILIDADE = {"auto": None, "sim": True, "nao": False}


def ler_argumentos(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m cliente", description="Cliente pygame do Arena em Rede.")
    parser.add_argument("--nome", default="",
                        help=f"até {config.TAMANHO_MAXIMO_NOME} caracteres; se dado, entra direto na partida")
    parser.add_argument("--servidor", default=f"127.0.0.1:{config.PORTA_PADRAO}", help="host:porta")
    parser.add_argument("--transporte", choices=["udp", "tcp"], default="udp", help="o mesmo do servidor")
    parser.add_argument("--confiabilidade", choices=list(CONFIABILIDADE), default="auto",
                        help="ACK e retransmissão da aplicação; auto = ligado em UDP, desligado em TCP")
    parser.add_argument("--nagle", action="store_true", help="(tcp) deixa o algoritmo de Nagle ligado")
    parser.add_argument("--fps", type=int, default=60)
    parser.add_argument("--duracao", type=float, default=None, help="fecha sozinho após N segundos")
    parser.add_argument("--log", default="WARNING", choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="log no terminal; INFO mostra pacotes de controle e retransmissões")
    parser.add_argument("--registro", metavar="ARQUIVO", help="grava cada pacote em JSON Lines")
    perda.adicionar_argumentos(parser)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = ler_argumentos(argv)
    logging.basicConfig(level=args.log, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        # Importado aqui para o --help funcionar mesmo sem o pygame instalado.
        from cliente.jogo import Aplicacao, Conexao
    except ModuleNotFoundError as erro:
        if erro.name != "pygame":
            raise
        print("O cliente precisa do pygame: pip install -r requirements.txt")
        return 1
    conexao = Conexao(nome=args.nome, servidor=args.servidor, transporte=args.transporte,
                      confiabilidade=CONFIABILIDADE[args.confiabilidade], nagle=args.nagle,
                      perda=args.perda, atraso=args.atraso, variacao=args.variacao)
    registro = RegistroPacotes(args.registro)
    try:
        aplicacao = Aplicacao(conexao, registro, fps=args.fps, entrar_direto=bool(args.nome))
        aplicacao.executar(args.duracao)
    except KeyboardInterrupt:
        pass
    finally:
        registro.fechar()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
