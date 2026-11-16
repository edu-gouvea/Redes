"""Cliente pygame do Arena em Rede (Integrante 2).

A rede inteira fica na SessaoCliente (protocolo/sessao.py). Aqui ficam:

- previsao.py: previsão local do próprio jogador, com reconciliação;
- interpolacao.py: interpolação dos outros jogadores, com atraso de reprodução adaptativo;
- metricas.py: medidas para o painel de rede (perda, jitter, taxa, RTT);
- jogo.py: janela, teclado e desenho (só este módulo importa pygame);
- __main__.py: linha de comando (python -m cliente).

Os três primeiros não dependem de pygame e são testados em testes/.
"""

import os

# Sem o banner "Hello from the pygame community" no terminal.
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
