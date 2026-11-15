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
python -m servidor                                  # porta 5005, todas as interfaces
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

## Para o cliente pygame (Integrante 2)

A `SessaoCliente` (`protocolo/sessao.py`) cuida de toda a rede do lado do cliente:
entrar na partida, COMANDO a 20/s, descarte de ESTADO velho e eventos. Ela não
bloqueia; basta chamar uma vez por frame:

```python
from protocolo.sessao import SessaoCliente, Situacao
from protocolo.transporte import TransporteUDP, resolver

sessao = SessaoCliente(TransporteUDP.cliente(), resolver("127.0.0.1", 5005), "ana")
while rodando:
    sessao.definir_direcao(dx, dy)            # -1, 0 ou 1 (bool do pygame também serve)
    sessao.processar()
    for evento in sessao.consumir_eventos():  # {"evento": "ponto", "jogador": 3, "pontos": 5}
        ...
    if sessao.situacao is Situacao.EM_JOGO and sessao.estado:
        ...                                   # desenhar sessao.estado["jogadores"] e ["moedas"]
sessao.sair()                                 # espera até 1 s pelo ACK do SAIR
```

O que mais ela expõe:
- `sessao.meu_id` e `sessao.meu_jogador()`;
- `sessao.parametros`: arena, raios, velocidade e taxa, vindos do ENTRAR_OK;
- `sessao.situacao`: `entrando`, `em_jogo`, `recusado`, `sem_resposta` ou `encerrada`;
- os contadores `estados_recebidos` e `estados_fora_de_ordem`;
- `sessao.canal.contadores()`: enviadas, retransmitidas, duplicadas recebidas,
  desistências, `rtt_estimado_ms`, `dev_rtt_ms` e `timeout_ms`.

Para o log de pacotes na tela, registre um observador:
`sessao.registro.observadores.append(lambda p: linhas.append(p.como_texto()))`. A cada
datagrama ele recebe um `RegistroPacote` (direção, tipo, seq, tamanho, endereços). O RTT
do último PING fica em `sessao.ultimo_rtt_ms`.

`ferramentas/robo.py` é um exemplo completo de uso.
