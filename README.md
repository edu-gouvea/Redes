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

O servidor, o robô e os testes da rede usam só a biblioteca padrão. O cliente precisa
do pygame:

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt                     # pygame e pytest
```

```bash
python -m servidor                                  # porta 5005, todas as interfaces
python -m cliente                                   # janela do jogo (tela de entrada)
python -m cliente --nome ana --servidor 192.168.0.10:5005
python -m servidor --porta 6000 --log DEBUG         # DEBUG mostra cada pacote
python -m ferramentas.robo --quantidade 2           # 2 robôs andando ao acaso
python -m ferramentas.robo --modo cacador --servidor 192.168.0.10:5005
python -m pytest                                    # testes
```

Rede ruim simulada, sem netem (funciona também no Windows), em qualquer um dos dois:

```bash
python -m servidor --perda 30 --atraso 40 --variacao 20   # % e ms, em cada sentido
python -m ferramentas.robo --log INFO                     # INFO mostra duplicatas
```

A versão TCP, para comparação (cliente e servidor precisam usar o mesmo transporte):

```bash
python -m servidor --transporte tcp
python -m ferramentas.robo --transporte tcp --quantidade 2
```

Registro de cada pacote em JSON Lines, para análise e para comparar com o Wireshark
(formato em `docs/PROTOCOLO.md`, seção 15):

```bash
python -m servidor --registro servidor.jsonl
python -m ferramentas.robo --registro robos.jsonl
```

Todas as opções aparecem com `--help`. Para jogar entre máquinas, libere a porta UDP
5005 no firewall de quem roda o servidor.

## Cliente pygame (Integrante 2)

`python -m cliente` abre a janela: tela de entrada (nome, servidor e transporte),
arena, placar, painel de rede ao vivo (RTT, jitter, perda, retransmissões, buffer de
interpolação) e log de pacotes (tecla L). O próprio jogador usa **previsão local com
reconciliação** e os outros, **interpolação com atraso de reprodução adaptativo**.
As teclas P, I e G ligam e desligam cada técnica, para mostrar o efeito na
apresentação. Detalhes, teclas e roteiro da demonstração estão em
[`docs/CLIENTE.md`](docs/CLIENTE.md).

O cliente aceita as mesmas opções de rede do robô: `--transporte tcp`, `--perda`,
`--atraso`, `--variacao`, `--registro` e `--log`.

A `SessaoCliente` (`protocolo/sessao.py`) cuida de toda a rede do lado do cliente, e
`ferramentas/robo.py` é outro exemplo de uso dela, sem interface gráfica.
