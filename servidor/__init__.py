"""Servidor autoritativo do Arena em Rede.

- servidor: laço com taxa fixa, despacho das mensagens, envio de ESTADO e EVENTO
- clientes: tabela de clientes por (IP, porta) — a demultiplexação da aplicação
- jogo:     regras do jogo, sem nenhuma dependência de rede

Execução: python -m servidor --porta 5005
"""
