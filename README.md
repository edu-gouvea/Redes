# Arena em Rede

Jogo multiplayer simples em tempo real (bolinhas coletando moedas numa arena 2D),
cliente-servidor sobre **sockets UDP**. É o trabalho de Redes de Computadores I (UFPB):
o jogo é o pretexto para demonstrar atraso, perda, jitter, UDP × TCP e transferência
confiável de dados.

Requer **Python 3.11+**. A parte de rede usa só a biblioteca padrão. Rode tudo a partir
da raiz do repositório.

## Documentos

- [`docs/PROTOCOLO.md`](docs/PROTOCOLO.md): o contrato da equipe (formato de cada
  mensagem, quem envia, o que é confiável, diagramas).
- [`docs/FUNDAMENTOS_REDES.md`](docs/FUNDAMENTOS_REDES.md): onde cada conceito de redes
  aparece no projeto.

## Estrutura

```text
config.py      todas as configurações (porta, taxa, arena, timeouts)
protocolo/     mensagens, números de sequência, confiabilidade, transporte (Integrante 1)
servidor/      servidor autoritativo e lógica do jogo (Integrante 1)
ferramentas/   cliente-robô e simulador de perda (Integrante 1)
cliente/       cliente pygame (Integrante 2)
experimentos/  métricas, versão TCP, netem, Wireshark (Integrante 3)
testes/        testes automatizados (pytest)
```

## Como rodar

```bash
python -m pytest        # testes
```

Servidor e robô: a partir das etapas b e c (veja `docs/PROTOCOLO.md`, seção 13).
