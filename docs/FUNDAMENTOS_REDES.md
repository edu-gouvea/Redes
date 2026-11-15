# Fundamentos de Redes no Arena em Rede

Guia para mostrar ao professor **onde cada conceito de redes aparece no projeto**:
o conceito, a referência no livro, o lugar no código, como ele aparece na demo e uma
frase pronta para a apresentação.

Este documento foi atualizado a cada etapa, junto com o código. Cada conceito indica
sua **situação** e a etapa em que entrou. Todo nome de arquivo, função, teste e
configuração citado aqui é conferido por `testes/test_referencias_doc.py`: se alguém
renomear algo no código, o teste quebra antes da apresentação.

> **Sobre as referências ao livro.** Kurose & Ross, *Redes de Computadores e a
> Internet: uma abordagem top-down*, **9ª edição** (a usada pelo professor). As
> citações indicam o capítulo e o nome do tópico, nunca o número da seção. Os nomes
> dos tópicos podem estar escritos de forma um pouco diferente na tradução. Os pontos
> marcados com *(conferir na 9ª ed.)* são assuntos que mudaram de lugar entre edições
> e precisam ser confirmados no exemplar antes da apresentação.

---

## 1. Visão geral

O **Arena em Rede** é um jogo multiplayer simples em tempo real: cada jogador controla
uma bolinha numa arena 2D e coleta moedas, e o placar aparece para todos. O jogo é o
pretexto. O objetivo é **ver na prática** atraso, perda, jitter, a escolha entre UDP e
TCP e os mecanismos de transferência confiável.

```text
        CLIENTE (pygame ou robô)                          SERVIDOR (autoritativo)
 ┌─────────────────────────────────┐              ┌─────────────────────────────────┐
 │ jogo: teclado, desenho,         │              │ jogo.py: movimento, colisão,    │
 │ previsão local, suavização      │              │ moedas, placar (não sabe que    │
 │                                 │              │ existe rede)                    │
 ├─────────────────────────────────┤              ├─────────────────────────────────┤
 │ SessaoCliente                   │  APLICAÇÃO   │ servidor.py: tabela de clientes │
 │ mensagens · sequencia ·         │◄────────────►│ por (IP, porta), laço de 20 Hz  │
 │ confiavel · registro            │ JSON ≤1200 B │ mensagens · sequencia ·         │
 │                                 │              │ confiavel · registro            │
 ├─────────────────────────────────┤              ├─────────────────────────────────┤
 │ transporte.py (UDP ou TCP)      │  TRANSPORTE  │ transporte.py (UDP ou TCP)      │
 │ socket, porta efêmera           │◄────────────►│ socket na porta 5005            │
 └─────────────────────────────────┘    UDP/IP    └─────────────────────────────────┘
```

- **Camada de aplicação, que é nossa:** o protocolo do jogo (`protocolo/mensagens.py`,
  contrato em `docs/PROTOCOLO.md`), os números de sequência (`protocolo/sequencia.py`)
  e a confiabilidade seletiva (`protocolo/confiavel.py`).
- **Camada de transporte, que é do sistema operacional:** UDP, ou TCP para comparação,
  acessada pela API de sockets em `protocolo/transporte.py`, a única parte do código
  que mexe em socket.
- O cliente envia **intenções** (COMANDO, 20 por segundo). O servidor calcula tudo e
  devolve **snapshots** (ESTADO, 20 por segundo). **Eventos** importantes (ponto,
  entrada, saída) usam ACK e retransmissão.

---

## 2. Mapa de conceitos

Mapa rápido (detalhes em cada seção abaixo):

| Conceito | Onde está | Como mostrar |
|---|---|---|
| 2.1 Cliente-servidor | `servidor/servidor.py`, `protocolo/sessao.py` | servidor e 2 robôs |
| 2.2 Sockets | `protocolo/transporte.py` | `ss -uanp` |
| 2.3 Protocolo de aplicação | `protocolo/mensagens.py`, `docs/PROTOCOLO.md` | log `--log INFO` e Wireshark |
| 2.4 Demultiplexação | `servidor/clientes.py` | portas diferentes dos robôs |
| 2.5 UDP | `protocolo/transporte.py` | perda sem travar o jogo |
| 2.6 Números de sequência | `protocolo/sequencia.py` | "fora de ordem" com `--variacao 40` |
| 2.7 ACK, timeout, retransmissão | `protocolo/confiavel.py` | `--perda 30`: `retransmite EVENTO` |
| 2.8 Duplicatas | `protocolo/confiavel.py` | robô `--log INFO`: `duplicata de EVENTO` |
| 2.9 RTT e timeout adaptativo | `protocolo/confiavel.py`, `protocolo/sessao.py` | `--atraso 30`: RTT de 63 ms |
| 2.10 Atraso, perda, jitter | `ferramentas/perda.py`, `protocolo/registro.py` | `--perda`, `--atraso`, `--variacao` |
| 2.11 MTU e fragmentação | `protocolo/mensagens.py` | teste do pior caso; Wireshark |
| 2.12 TCP e enquadramento | `protocolo/transporte.py` | `--transporte tcp`; `ss -tn` |
| 2.13 HOL do TCP | troca de transporte | experimento do Integrante 3 |

### 2.1 Modelo cliente-servidor

*Situação: pronto (etapa b: servidor; etapa c: SessaoCliente e robô).*

- **O conceito.** Um processo **servidor** fica sempre ligado num endereço conhecido
  (IP + porta 5005) e os **clientes** é que tomam a iniciativa de falar com ele. Os
  clientes nunca conversam entre si: tudo passa pelo servidor.
- **No livro.** Cap. 2, princípios de aplicações de rede: arquiteturas de aplicação
  (cliente-servidor × P2P).
- **No código.**
  - `servidor/servidor.py`, classe `Servidor`: `ao_receber_datagrama()` trata o que
    chega e `tick()` simula e responde;
  - `protocolo/sessao.py`, classe `SessaoCliente`: o lado cliente, usado pelo robô e
    pelo cliente pygame;
  - `servidor/jogo.py`: a regra do jogo, que só existe no servidor.
- **Na demo.** `python -m servidor` num terminal e `python -m ferramentas.robo
  --quantidade 2` em outro (ou dois notebooks). Todos veem as mesmas moedas e o mesmo
  placar; o log do servidor mostra cada jogador entrando com o seu endereço.
- **Frase pronta.** "Escolhemos cliente-servidor com servidor autoritativo porque o
  jogo precisa de uma única verdade. Se cada cliente calculasse a própria posição,
  dois jogadores poderiam pegar a mesma moeda ao mesmo tempo, e qualquer um poderia
  trapacear dizendo onde está."

### 2.2 Sockets e a API de sockets

*Situação: pronto (etapa b).*

- **O conceito.** O socket é a "porta" entre o nosso processo e a camada de transporte
  do sistema operacional: escrevemos e lemos bytes nele, e o sistema cuida do resto.
  No UDP é `socket(AF_INET, SOCK_DGRAM)`. O servidor faz `bind` na porta 5005, e cada
  `sendto`/`recvfrom` leva o endereço junto, porque não há conexão.
- **No livro.** Cap. 2, programação de sockets: criando aplicações de rede
  (programação de sockets com UDP).
- **No código.** `protocolo/transporte.py`, classe `TransporteUDP`, o único lugar
  que chama `socket`:
  - `__init__`: `socket(AF_INET, SOCK_DGRAM)`, `bind()` (porta 5005 no servidor,
    porta 0, ou seja efêmera, no cliente) e `setblocking(False)`;
  - `receber(timeout)`: o `selectors` espera até o prazo pelo aviso do sistema
    operacional de que há datagrama, e depois o `recvfrom()` lê até esvaziar a fila;
  - `enviar()`: `sendto(dados, destino)`. Um erro de envio vira uma perda registrada
    no log, nunca uma queda.
  - Detalhe de Windows tratado em `receber()`: depois que um cliente fecha, o ICMP
    "porta inalcançável" gerado por um envio aparece como `ConnectionResetError` no
    próximo `recvfrom`. Sem esse tratamento, o servidor cairia.
- **Na demo.** `ss -uanp | grep 5005` mostra o socket UDP do servidor escutando, sem
  nenhum estado de conexão. Com `--transporte tcp`, `ss -tn` mostra uma linha `ESTAB`
  por cliente, além do socket de escuta.
- **Frase pronta.** "Todo o tráfego do jogo passa por um único socket UDP no servidor.
  Cada `recvfrom` devolve os bytes e quem mandou; cada `sendto` diz para quem vai."

### 2.3 Protocolo de aplicação: formato, tipos e semântica

*Situação: pronto (etapa a).*

- **O conceito.** Um protocolo de aplicação define **que mensagens existem** (tipos),
  **como são escritas** (sintaxe: campos e codificação), **o que significam**
  (semântica) e **quando enviar e como responder** (regras). Nós definimos o nosso: 10
  tipos de mensagem em JSON, com um cabeçalho comum `v`, `tipo`, `seq`, `ts` e `dados`.
- **No livro.** Cap. 2, protocolos da camada de aplicação (e, como exemplos do mesmo
  formato de definição, HTTP e DNS no mesmo capítulo).
- **No código.**
  - `protocolo/mensagens.py`: `Tipo`, `Mensagem`, `codificar()`, `decodificar()`;
  - contrato completo em `docs/PROTOCOLO.md`;
  - `testes/test_protocolo_doc.py` garante que os exemplos do contrato são válidos.
- **Na demo.**
  - No Wireshark, a carga útil UDP é JSON legível: `{"v":1,"tipo":"COMANDO","seq":42,...}`.
  - O log do servidor (`--log INFO`, `protocolo/registro.py`) mostra o mesmo pacote:
    `rx ENTRAR seq=1 76 B 127.0.0.1:45029 -> 0.0.0.0:5095`. No Wireshark, o
    `udp.length` desse pacote é 84, os 76 da mensagem mais os 8 do cabeçalho UDP.
  - Uma mensagem malformada mandada de propósito é descartada e aparece no log como
    `INVALIDO`, e o servidor continua de pé.
- **Frase pronta.** "O protocolo do jogo é nosso. Cada datagrama é um JSON com
  versão, tipo, número de sequência, carimbo de tempo e dados. Qualquer coisa fora do
  formato é descartada e registrada, nunca derruba o servidor."

### 2.4 Multiplexação e demultiplexação por IP e porta

*Situação: pronto (etapa b).*

- **O conceito.** Vários processos na mesma máquina usam a rede ao mesmo tempo; o
  sistema operacional entrega cada datagrama ao socket certo olhando a **porta de
  destino** (demultiplexação). No UDP, um único socket recebe datagramas de **todos**
  os clientes, e o nosso servidor faz uma segunda "demultiplexação" na aplicação: pelo
  **(IP, porta) de origem** descobre de qual jogador é a mensagem.
- **No livro.** Cap. 3, multiplexação e demultiplexação (o socket UDP é identificado
  pelo par IP e porta de destino; o TCP, pela quádrupla).
- **No código.**
  - `servidor/clientes.py`: `TabelaClientes`, um dicionário indexado por `(ip, porta)`
    de origem. Cada `Cliente` guarda o próprio `CanalConfiavel` (numeração, ACKs, RTT)
    e o filtro de COMANDO;
  - `Servidor.ao_receber_datagrama()` faz `self.clientes.buscar(origem)` para cada
    datagrama;
  - no cliente, `SessaoCliente._ao_receber()` só aceita datagramas vindos do endereço
    do servidor.
  - Sobre TCP, quem separa é o próprio sistema operacional: `accept()` cria um socket
    por conexão, identificado pela quádrupla. O `TransporteTCP` usa o (IP, porta) do
    outro lado da conexão como "endereço", e a `TabelaClientes` funciona igual.
- **Na demo.** Dois robôs na **mesma máquina** têm o mesmo IP, mas portas efêmeras
  diferentes, e por isso são dois jogadores diferentes. O robô imprime "porta local
  38527" e o servidor registra "entrou de 127.0.0.1:38527". Sobre TCP, `ss -tn` mostra
  as quádruplas: `127.0.0.1:5093 ↔ 127.0.0.1:59780` e `127.0.0.1:5093 ↔
  127.0.0.1:59792`.
- **Frase pronta.** "O sistema operacional entrega ao nosso socket tudo o que chega na
  porta 5005; quem separa os jogadores somos nós, pelo IP e pela porta de origem, do
  mesmo jeito que o TCP separa conexões pela quádrupla."

### 2.5 UDP: sem conexão, sem garantias, cabeçalho pequeno, e por que foi escolhido

*Situação: pronto (etapa b).*

- **O conceito.** O UDP só acrescenta portas, comprimento e checksum (**8 bytes** de
  cabeçalho) ao serviço do IP. Não tem apresentação (handshake), não garante entrega
  nem ordem e não tem controle de congestionamento. Para um jogo isso é uma vantagem:
  o dado chega assim que a rede deixa e nada fica esperando um pacote antigo.
- **No livro.**
  - Cap. 3, transporte não orientado para conexão: UDP (estrutura do segmento UDP e
    checksum);
  - Cap. 2, serviços de transporte disponíveis para aplicações. A tabela de requisitos
    das aplicações coloca os jogos interativos como **tolerantes a perda** e
    **sensíveis ao tempo**.
- **No código.**
  - `protocolo/transporte.py` (`TransporteUDP`);
  - a falta de conexão aparece em `TabelaClientes.inativos()` e
    `Servidor._remover_inativos()`: como o UDP não avisa quando o outro lado some, o
    servidor remove quem fica `config.TIMEOUT_INATIVIDADE` (5 s) calado;
  - a confiabilidade que falta ao UDP é acrescentada só onde precisa, em
    `protocolo/confiavel.py` (etapa d).
- **Na demo.** No Wireshark, o cabeçalho UDP tem 8 bytes, contra no mínimo 20 do TCP.
  Com perda induzida, o jogo continua andando e só "pula" um pouco.
- **Frase pronta.** "Jogo em tempo real tolera perder um estado, porque outro vem 50 ms
  depois, mas não tolera esperar. Por isso usamos UDP e colocamos confiabilidade só no
  que não pode se perder."

### 2.6 Números de sequência e entrega fora de ordem

*Situação: pronto (etapas a, b e c).*

- **O conceito.** A rede não garante ordem: um datagrama pode ficar preso numa fila e
  chegar depois de outro enviado mais tarde. Numerando cada mensagem (`seq`), o
  destinatário percebe que ela está velha. No COMANDO e no ESTADO, uma mensagem com
  `seq` menor ou igual ao último aceito é descartada, para o jogo nunca "voltar no
  tempo". Cada par remetente → destinatário tem sua própria numeração, como cada
  sentido de uma conexão TCP.
- **No livro.** Cap. 3, princípios da transferência confiável de dados (o número de
  sequência aparece no rdt2.1 para distinguir dado novo de retransmissão) e Cap. 3,
  números de sequência e números de reconhecimento do TCP.
- **No código.**
  - `protocolo/sequencia.py`: `ContadorSequencia` gera 1, 2, 3, ... por par;
    `FiltroMaisRecente.aceitar(seq)` descarta o que está velho e conta os descartes;
  - `Servidor._ao_comandar()` descarta COMANDO antigo (contador
    `comandos_fora_de_ordem`);
  - `SessaoCliente._ao_estado()` descarta ESTADO antigo (contador
    `estados_fora_de_ordem`);
  - o `CanalConfiavel` de cada `Cliente` (`servidor/clientes.py`) tem um
    `ContadorSequencia`: é o espaço de sequência do servidor para aquele cliente.
- **Na demo.** A linha "rede:" do robô mostra `ESTADOs N (fora de ordem M)`. Em
  localhost, M fica em 0; com atraso variável (`python -m servidor --atraso 60
  --variacao 40`, ou `tc netem delay 100ms 50ms`), M sobe, e o jogo não dá saltos para
  trás.
- **Frase pronta.** "Como o UDP pode entregar fora de ordem, todo pacote leva um número
  de sequência. Se chega um estado mais velho que o último que aplicamos, ele é
  jogado fora: aplicar estado velho seria voltar no tempo."

### 2.7 ACK, timeout e retransmissão (rdt3.0)

*Situação: pronto (etapa d).*

- **O conceito.** Num canal que pode perder pacotes, o remetente só tem certeza de que
  o dado chegou quando recebe um **ACK**. Ele liga um **temporizador** a cada envio:
  se o ACK não vem a tempo, **retransmite**. Esse é o rdt3.0. No nosso caso, cada
  mensagem confiável tem seu próprio temporizador e seu próprio ACK, e várias podem
  estar pendentes ao mesmo tempo, como na repetição seletiva.
- **No livro.** Cap. 3, princípios da transferência confiável de dados: rdt3.0 (canal
  com perda e erros de bits) e protocolos com paralelismo: repetição seletiva (SR).
- **No código.** `protocolo/confiavel.py`, classe `CanalConfiavel`. Existe um por
  par: no servidor, um por cliente (`Cliente.canal`); na `SessaoCliente`, um para o
  servidor.
  - `enviar()`: numera e, se o tipo é confiável, guarda como `Pendente`, com o prazo
    do temporizador;
  - `verificar_timeouts(agora)`: para cada pendente vencida, `_retransmitir()` reenvia
    a **mesma** mensagem (mesmo `seq`, mesmo `ts`) ou, depois de
    `config.MAX_TENTATIVAS` transmissões, `_desistir()` avisa quem chamou;
  - o laço do servidor (`Servidor.passo()`) dorme só até o próximo prazo, seja o tick
    ou uma retransmissão (`_proximo_prazo()`). Assim a retransmissão sai na hora
    certa, e não só no próximo tick.
- **Na demo.** `python -m servidor --perda 30` e dois robôs. O log do servidor
  mostra, por exemplo, `retransmite EVENTO seq=41 (transmissão 2 de 5; próximo timeout
  443 ms)`, e o placar continua igual nos dois robôs.
- **Frase pronta.** "Para o ponto marcado, fizemos o rdt3.0 do livro sobre UDP: o
  servidor manda o evento, espera o ACK e, se o temporizador estoura, manda de novo.
  Só fazemos isso para mensagens que não podem se perder."

### 2.8 Detecção de duplicatas

*Situação: pronto (etapa d).*

- **O conceito.** Se o **ACK** se perde, o remetente acha que o dado se perdeu e
  retransmite, e o destinatário recebe a mesma mensagem duas vezes. Pelo `seq` ele
  percebe que já a processou. Então **confirma de novo** (senão o remetente continua
  retransmitindo), mas **não processa de novo** (senão o ponto contaria duas vezes).
- **No livro.** Cap. 3, princípios da transferência confiável de dados: rdt2.1 e
  rdt2.2 (números de sequência para tratar pacotes duplicados).
- **No código.** `protocolo/confiavel.py`:
  - `CanalConfiavel.ao_receber()` responde ACK **antes** de saber se é duplicata, e só
    devolve `True` (pode processar) se o `seq` for novo;
  - `JanelaDuplicatas.registrar()` lembra os últimos `config.JANELA_DUPLICATAS` `seq`;
  - contador `duplicadas_recebidas`;
  - teste `test_ack_perdido_gera_duplicata_que_e_confirmada_mas_nao_processada`.
- **Na demo.** `python -m ferramentas.robo --log INFO` com o servidor em `--perda 30`
  mostra `duplicata de EVENTO seq=70: confirmada de novo e não processada`, e o placar
  não pula 2 pontos de uma vez. A linha "rede:" do robô mostra "duplicadas recebidas".
- **Frase pronta.** "Se o ACK some, o servidor manda o ponto de novo. O cliente
  reconhece pelo número de sequência que já viu aquele ponto, confirma de novo e não
  soma duas vezes."

### 2.9 Estimativa de RTT e timeout adaptativo

*Situação: pronto (etapa d: estimador e timeout; etapa e: PING/PONG).*

- **O conceito.** Qual timeout usar? Curto demais causa retransmissões à toa; longo
  demais deixa a recuperação lenta. Como o TCP, medimos o RTT e ajustamos:
  `EstimatedRTT` é uma média móvel exponencial das amostras, `DevRTT` mede o quanto
  elas variam, e `TimeoutInterval = EstimatedRTT + 4·DevRTT`. Mensagens retransmitidas
  não geram amostra (algoritmo de Karn), porque não dá para saber a qual das
  transmissões o ACK responde.
- **No livro.**
  - Cap. 3, estimativa do tempo de ida e volta e do intervalo de timeout do TCP
    (α = 0,125, β = 0,25, timeout inicial de 1 s; o livro também explica que o TCP
    não calcula `SampleRTT` de segmentos retransmitidos);
  - Cap. 3, transferência confiável de dados do TCP (duplicação do intervalo de timeout
    após cada expiração).
- **No código.**
  - `protocolo/confiavel.py`, `EstimadorRTT.amostrar()`: as três fórmulas, com o
    limite `[TIMEOUT_MINIMO, TIMEOUT_MAXIMO]`;
  - Karn em `CanalConfiavel._ao_receber_ack()`: só amostra se `transmissoes == 1`;
  - backoff em `CanalConfiavel._retransmitir()`: o timeout daquela mensagem dobra;
  - `config.py`: `ALFA_RTT`, `BETA_RTT`, `TIMEOUT_INICIAL`, `TIMEOUT_MINIMO`,
    `TIMEOUT_MAXIMO`;
  - testes com as contas feitas à mão em `test_segunda_amostra_com_alfa_e_beta_do_livro`;
  - PING/PONG: `SessaoCliente._enviar_ping_se_for_hora()` manda 1 PING por segundo;
    `Servidor._responder_ping()` responde **no tratamento do próprio datagrama**, sem
    esperar o tick; `SessaoCliente._ao_pong()` calcula `agora_ms() − ts_eco` com o
    relógio do próprio cliente.
- **Na demo.** Com `python -m servidor --atraso 30` (30 ms em cada sentido), a linha
  "rede:" do robô mostra `RTT (PING) 63 ms` e `EstimatedRTT 63.1 ms, timeout 189 ms`:
  as duas medidas, por PING e pelos ACKs, concordam. No log do servidor (com
  `--perda 30 --atraso 40`), as linhas `retransmite ...` mostram o timeout de cada
  cliente começando em 1000 ms (sem amostra) e caindo para uns 375 ms depois que
  chegam ACKs de mensagens não retransmitidas.
- **Frase pronta.** "O timeout não é fixo: usamos as mesmas fórmulas do TCP. Ele
  aprende o RTT do caminho e deixa uma folga de quatro desvios. E, como no TCP, não
  medimos o RTT de mensagem retransmitida, porque o ACK é ambíguo."

### 2.10 Atraso, perda e jitter e seus efeitos no jogo

*Situação: `ts` pronto (etapa a); ESTADO a taxa fixa pronto (etapa b); simulador de
perda e atraso pronto (etapa d); medição no cliente e experimentos com o Integrante 3.*

- **O conceito.** O atraso de cada pacote é a soma de processamento, **fila**,
  transmissão e propagação. Quando a fila de um roteador enche, o pacote é
  **perdido**. Como a fila varia de pacote para pacote, o atraso também varia: isso é
  o **jitter**. No jogo:
  - o atraso faz a bolinha responder tarde à tecla;
  - a perda faz o jogo "pular" (ESTADO perdido) ou dispara retransmissões (EVENTO);
  - o jitter deixa o movimento irregular, mesmo com o servidor mandando estados a
    intervalos certinhos de 50 ms.
- **No livro.** Cap. 1, atraso, perda e vazão em redes de comutação de pacotes (tipos
  de atraso, atraso de fila e perda de pacote). A fórmula de jitter usada nas métricas
  é a do RTP (RFC 3550).
- **No código.**
  - `ts` de envio em toda mensagem (`protocolo/mensagens.py`);
  - ESTADO a taxa fixa: `Servidor.passo()` agenda cada tick somando 50 ms ao
    horário *previsto* (`_agendar`), sem deriva. Como a saída é regular, qualquer
    irregularidade na chegada é jitter da rede. O teste
    `test_estado_chega_na_taxa_configurada` mede 20 por segundo com sockets reais;
  - `ferramentas/perda.py` (`TransporteComPerda`);
  - os dados brutos para o Integrante 3 calcular perda e jitter: o registro de cada
    pacote com `instante`, `seq` e `ts` (`--registro pacotes.jsonl`) e os contadores
    (`Servidor.contadores()`, `SessaoCliente`).
- **Na demo.**
  - Sem netem (funciona também no Windows): `python -m servidor --perda 10 --atraso 60
    --variacao 40`. O `TransporteComPerda` (`ferramentas/perda.py`) envolve o socket e
    sorteia perda e atraso para cada pacote, nos dois sentidos.
  - Com netem: `tc netem delay 100ms 30ms loss 10%`.
  - O painel mostra RTT, perda e jitter subindo, e a suavização do cliente disfarça o
    jitter.
  - Detalhe que vale mostrar: só há reordenação quando a variação do atraso supera o
    intervalo entre pacotes. Com ESTADO a cada 50 ms, `--variacao 20` dá "fora de
    ordem 0", e `--variacao 40` já dá alguns.
- **Frase pronta.** "Atraso o jogador sente como demora para responder, perda como
  salto, e jitter como movimento irregular. O jitter vem da fila: cada pacote pega a
  fila de um tamanho diferente."

### 2.11 MTU e fragmentação

*Situação: pronto (etapa a).*

- **O conceito.** Cada enlace tem um tamanho máximo de quadro, o **MTU** (1500 bytes
  na Ethernet). Um datagrama IP maior é **fragmentado**, e se **um** fragmento se
  perde, o datagrama inteiro se perde: com 5% de perda por pacote, uma mensagem em 2
  fragmentos se perde 9,75% das vezes. Por isso toda mensagem nossa tem **no máximo
  1200 bytes**, o que cabe em qualquer caminho comum, inclusive no MTU mínimo do IPv6
  (1280).
- **No livro.**
  - Cap. 4, camada de rede: o datagrama IP e a fragmentação (*conferir na 9ª ed.* em
    que tópico a fragmentação do IPv4 aparece) e o IPv6, cujos roteadores não
    fragmentam;
  - Cap. 3, a conexão TCP, onde o MSS é definido a partir do MTU.
- **No código.**
  - `protocolo/mensagens.py`, `_exigir_tamanho()`, chamado em `codificar()` e em
    `decodificar()`;
  - `config.TAMANHO_MAXIMO_DATAGRAMA` e `config.MAX_JOGADORES`;
  - `testes/test_mensagens.py`, `test_estado_no_pior_caso_cabe_em_um_datagrama`.
- **Na demo.** No Wireshark, um ESTADO com vários robôs ocupa um único quadro, sem
  "Fragmented IP protocol".
- **Frase pronta.** "Nenhuma mensagem passa de 1200 bytes, e um teste garante isso
  para o pior caso do estado. Assim nunca há fragmentação IP, que multiplicaria a
  chance de perder a mensagem inteira."

### 2.12 TCP como fluxo de bytes e o enquadramento

*Situação: pronto (etapa f).*

- **O conceito.** O UDP preserva fronteiras: um `sendto` corresponde a um `recvfrom`.
  O TCP não: ele entrega um **fluxo de bytes**, e um `recv` pode trazer meia mensagem
  ou duas grudadas. Para usar TCP, a aplicação precisa marcar onde cada mensagem
  começa e termina. Nós colocamos **2 bytes de tamanho** antes de cada mensagem.
- **No livro.**
  - Cap. 3, a conexão TCP (o TCP vê os dados como um fluxo de bytes ordenado);
  - Cap. 3, gerenciamento da conexão TCP (apresentação de três vias no `connect`,
    FIN no encerramento);
  - Cap. 3, controle de fluxo (por que o `send` pode aceitar menos bytes);
  - Cap. 2, programação de sockets com TCP (socket de escuta e `accept`).
- **No código.** `protocolo/transporte.py`, classe `TransporteTCP`:
  - `enviar()` põe `PREFIXO.pack(len(dados))` antes de cada mensagem: são 2 bytes,
    `struct` com `"!H"`, em que `!` é a ordem de bytes da rede (big-endian);
  - `_desenquadrar()` junta os bytes no buffer da conexão e só entrega mensagens
    completas. Um prefixo absurdo fecha a conexão, porque não há como ressincronizar
    um fluxo;
  - `_escrever()` lida com o `send()` parcial: o que não coube fica em `saida` e sai
    quando o seletor avisar que dá para escrever;
  - `_ler()`: `recv()` devolvendo `b""` é o FIN do outro lado;
  - `cliente()`: o `create_connection` só volta depois da apresentação de três vias;
  - `_adicionar_conexao()` liga `TCP_NODELAY` (desliga o algoritmo de Nagle, RFC
    896), para que mensagens pequenas não fiquem esperando para ser agrupadas;
  - testes: `test_mensagem_picada_em_varios_pedacos_e_remontada` (um byte por
    segmento) e `test_duas_mensagens_grudadas_num_send_sao_separadas`.
- **Na demo.** `python -m servidor --transporte tcp` e `python -m ferramentas.robo
  --transporte tcp --quantidade 2`: o mesmo jogo, sem mudar uma linha da lógica. A
  comparação com o UDP sob perda é feita pelo Integrante 3.
- **Frase pronta.** "No TCP não existe 'mensagem', só bytes em sequência. Por isso cada
  mensagem vai precedida do seu tamanho, e o receptor junta os bytes até completar."

### 2.13 Bloqueio de cabeça de fila (HOL) do TCP em tempo real

*Situação: transporte TCP pronto (etapa f); a medição do efeito é do Integrante 3.*

- **O conceito.** O TCP entrega tudo **em ordem**. Se um segmento se perde, os
  seguintes, mesmo já tendo chegado, ficam retidos no buffer do receptor até a
  retransmissão chegar. Num jogo, isso significa esperar um estado velho que nem
  interessa mais para poder ver o novo, que já chegou. No UDP, o próximo ESTADO é
  entregue na hora.
- **No livro.** Cap. 3, transferência confiável de dados do TCP (entrega ordenada).
  O HOL entre fluxos aparece na discussão do HTTP/2 (Cap. 2) e do QUIC (Cap. 3)
  *(conferir na 9ª ed.)*.
  **Cuidado:** o "bloqueio de cabeça de fila" do Cap. 4, nas portas de entrada do
  roteador, é outro fenômeno com o mesmo nome.
- **No código.**
  - A troca de `TransporteUDP` por `TransporteTCP` não muda `servidor/jogo.py`. O
    servidor só pergunta `transporte.garante_entrega` para desligar o ACK da
    aplicação, que seria redundante.
  - O que causa o HOL não está no nosso código, e sim no TCP do sistema operacional:
    o `recv()` simplesmente não devolve nada enquanto falta um segmento anterior.
- **Na demo.** *Resultado esperado, a ser medido pelo Integrante 3:* com `tc netem
  loss 10%`, sobre TCP (`--transporte tcp`) o movimento trava e depois "pula" de uma
  vez; sobre UDP, continua fluido. O registro em JSONL dos dois casos mostra os
  intervalos entre ESTADOs recebidos.
- **Frase pronta.** "Com TCP, um pacote perdido segura todos os que vêm atrás, mesmo os
  que já chegaram. Para um jogo, preferimos perder um estado velho a esperar por ele."

---

## 3. Decisões de projeto

| Decisão | Alternativa descartada | Motivo |
|---|---|---|
| **UDP** como transporte | TCP | O jogo é tolerante a perda e sensível ao atraso. O TCP retransmite tudo e entrega em ordem, o que causa bloqueio de cabeça de fila (2.13). O TCP fica como comparação (etapa f). |
| **Confiabilidade seletiva**: ACK só para ENTRAR, ENTRAR_OK, ENTRAR_NEGADO, SAIR e EVENTO | Tudo confiável (ou usar TCP) | COMANDO e ESTADO ficam velhos em 50 ms. Retransmiti-los só atrasaria os novos. |
| **JSON** em UTF-8 | Binário com `struct` | É legível no Wireshark e nos logs (ótimo para a apresentação) e fácil de usar no cliente. Custa de 2 a 5 vezes mais bytes que um binário, mas cabe com folga em 1200 B. |
| **Servidor autoritativo** | Cada cliente calcula a própria posição | Uma única verdade: não há conflito na disputa por uma moeda e não há como trapacear mudando a própria posição. |
| **20 atualizações por segundo** | 60/s ou 10/s | Com 3 jogadores, o ESTADO tem cerca de 372 B, e o servidor envia cerca de 190 kbit/s no total (~890 kbit/s com 8). A 60/s seria o triplo de tráfego; a 10/s o movimento fica travado mesmo com suavização. |
| **Limite de 1200 B por datagrama** | Até 1472 B (Ethernet) | Cabe até no MTU mínimo do IPv6 e em túneis/VPN; é o mesmo valor do QUIC. |
| **No máximo 8 jogadores e nomes de até 12 caracteres** | Sem limite | São os limites que garantem que o pior caso do ESTADO (1138 B) caiba num datagrama. Com nomes de 16 caracteres, ficaria em 1234 B. |
| **`seq` por par** remetente → destinatário | Um contador global no servidor | Espelha o TCP e permite medir perda pelas lacunas de `seq` no cliente. |
| **COMANDO com o estado das teclas**, 20/s | Só enviar quando a tecla muda | Se o único "soltou a tecla" se perdesse, o jogador andaria sozinho. Enviando o estado continuamente, a perda se corrige sozinha, e o envio ainda serve de sinal de vida. |
| **ACK individual** (repetição seletiva) | ACK cumulativo (como no TCP) | As mensagens confiáveis são esparsas e independentes, e não há entrega ordenada a preservar. |
| **Uma thread só, com `selectors`** | Uma thread por cliente, ou uma thread recebendo e outra simulando | Sem locks e sem condições de corrida. O laço espera pacotes só até a hora do próximo tick, e o PONG (etapa e) é respondido na leitura, sem esperar o tick. |
| **Tick agendado sem deriva** (`_agendar`) | `sleep(0.05)` depois de cada tick | O `sleep` soma o tempo de processamento a cada tick e a taxa real cai abaixo de 20/s. Somar 50 ms ao horário previsto mantém 20/s exatos em média. |
| **Lógica do jogo separada da rede** (`servidor/jogo.py`) | Tudo dentro do servidor | Dá para testar as regras sem socket, e trocar UDP por TCP não toca na regra do jogo. |
| **ENTRAR idempotente** (mesmo endereço, mesmo id) | Criar um jogador por ENTRAR | Até a etapa c, o ENTRAR era reenviado pelo cliente a cada 0,5 s; desde a etapa d, é confiável como os outros. A idempotência continua valendo para um ENTRAR *novo* (outro `seq`) de quem já é jogador. |
| **Um temporizador por mensagem confiável**, com backoff por mensagem | Um único temporizador, como no TCP | As mensagens confiáveis são poucas e independentes (é a repetição seletiva). Um temporizador só faria sentido com ACK cumulativo. |
| **Desistir após 5 transmissões** e avisar quem chamou | Tentar para sempre | Se o outro lado sumiu, o timeout de inatividade resolve. Desistir não significa que a mensagem não chegou: podem ter sido só os ACKs que se perderam. |
| **Janela de 1024 `seq` para duplicatas** | Lembrar todos os `seq` | A memória fica limitada. Uma retransmissão nunca chega tão atrasada: no pior caso (timeout já no teto de 3 s), a última sai 12 s depois da primeira, e 1024 `seq` equivalem a mais de 50 s de ESTADOs. |
| **Simulador de perda dentro da aplicação** (`ferramentas/perda.py`) | Só `tc netem` | Roda no Windows e no Linux, e nos testes automatizados, com semente reproduzível. O netem continua sendo usado nos experimentos: ele age no kernel e afeta também o TCP. |
| **`sair()` espera até 1 s pelo ACK do SAIR** | Não esperar, ou esperar até a desistência (12 s) | 1 s cobre vários RTTs sem travar o fechamento da janela. Se não vier ACK, a inatividade remove o jogador. |
| **Buffer do `recvfrom` de 64 KiB** | Buffer de 1200 B | Com buffer pequeno, o Linux trunca o datagrama grande em silêncio e o Windows dá erro. Lendo tudo, o protocolo detecta e descarta o datagrama grande demais. |
| **O cliente só aceita datagramas do servidor** | Aceitar de qualquer origem | Impede que outro programa na rede injete um ESTADO falso. |
| **RTT medido de dois jeitos**: PING/PONG a 1/s e as amostras dos ACKs | Só os ACKs | Só há amostras de ACK quando há mensagens confiáveis, que são raras; o PING mede continuamente, mesmo sem eventos. |
| **PONG respondido na leitura do datagrama** | Responder no tick | No tick, o RTT ganharia até 50 ms de espera que não são da rede. |
| **Log em dois níveis**: COMANDO e ESTADO em DEBUG, o resto em INFO | Tudo no mesmo nível | São 40 pacotes por segundo por jogador. Em INFO, o log mostra o que interessa na apresentação: entradas, ACKs, eventos, PINGs. |
| **Arquivo em JSON Lines** | CSV | Cada linha é independente (sobrevive a uma queda no meio), aceita campos opcionais (`ack`) e abre direto no pandas. |
| **SIGTERM encerra como o Ctrl+C** | Morrer sem resumo | Scripts de experimento param o servidor com `kill` ou `timeout`, e o resumo e o arquivo de registro precisam ficar completos. |
| **Enquadramento por prefixo de 2 bytes** no TCP | Delimitador (ex.: `\n` no fim de cada JSON) | O prefixo não depende do conteúdo: o receptor sabe quantos bytes esperar e não precisa procurar o delimitador byte a byte, nem se preocupar com ele aparecer dentro de um texto. |
| **Buffer de saída por conexão TCP**, com limite | `sendall()` bloqueante | Com `sendall`, um cliente lento (janela de recepção cheia) travaria o laço e, com ele, todos os outros jogadores. |
| **ACK da aplicação desligado sobre TCP** (automático, *proposta*) | Mantê-lo ligado | É redundante e, sob perda, poderia retransmitir em dobro enquanto o TCP recupera. A comparação com o UDP fica mais limpa. `--confiabilidade sim` liga de novo. |
| **`TCP_NODELAY` por padrão** (*proposta*) | Nagle ligado | O Nagle segura mensagens pequenas para juntá-las num segmento: economiza cabeçalhos, mas atrasa justamente os COMANDOs. `--nagle` religa, para experimentos. |
| **`ts` em relógio de parede** (ms desde a época) | Relógio monotônico | Permite correlacionar com o horário do Wireshark. Para RTT e jitter só usamos diferenças no mesmo relógio. Os temporizadores internos usam o relógio monotônico. |

---

## 4. Roteiro da minha fala (modelagem, ferramentas e conceitos — 4 min)

São cerca de 520 palavras a uns 130 palavras por minuto. Ensaie em voz alta com
cronômetro e corte exemplos, não conceitos, se passar do tempo.

**Deixe aberto na tela:** o diagrama da seção 1, o diagrama "Evento retransmitido" do
`docs/PROTOCOLO.md` (seção 11) e um terminal com o log do servidor em `--log INFO`.

| Tempo | Tópico | O que dizer (essência) | Mostrar |
|---|---|---|---|
| 0:00–0:30 | **Arquitetura** | Cliente-servidor com servidor autoritativo: o cliente só manda teclas e o servidor calcula tudo. A camada de aplicação é nossa; o transporte é o do sistema operacional, pela API de sockets, e pode ser trocado. | Diagrama da seção 1 |
| 0:30–1:05 | **Por que UDP** | Jogo é tolerante a perda e sensível ao tempo (a tabela de requisitos do Cap. 2). O TCP entregaria tudo em ordem e seguraria estados novos esperando um velho (HOL). | 2.5 e 2.13 |
| 1:05–1:45 | **Protocolo de aplicação** | JSON com cabeçalho `v`, `tipo`, `seq`, `ts`, `dados`; 10 tipos; a confiabilidade é propriedade do tipo. Nada passa de 1200 B, para nunca haver fragmentação IP. | Um COMANDO no log ou no Wireshark; 2.3 e 2.11 |
| 1:45–3:05 | **Confiabilidade seletiva (rdt3.0 sobre UDP)** | `seq` por par; ACK individual e um temporizador por mensagem, como na repetição seletiva. Retransmissão com o mesmo `seq`, duplicata confirmada mas não processada. Timeout adaptativo com as fórmulas do TCP, Karn e backoff. | Diagrama de retransmissão; fórmulas da 2.9 |
| 3:05–3:35 | **Ordem e demultiplexação** | Estado velho que chega atrasado é descartado pelo `seq`. Um único socket UDP: o sistema operacional demultiplexa pela porta e nós, pelo (IP, porta) de origem. | 2.4 e 2.6 |
| 3:35–4:00 | **Ferramentas e ponte para a demo** | Só biblioteca padrão (`socket`, `selectors`, `json`, `struct`), robôs, simulador de perda, mais de 300 testes automatizados, e a versão TCP com enquadramento para comparar. "Agora vamos ver isso funcionando." | `python -m pytest` (opcional) |

### Prévia de 25/11 (5 min): o que mostrar

Três terminais na raiz do repositório:

```bash
python -m servidor --perda 30                                # terminal 1
python -m ferramentas.robo --nome ana                        # terminal 2: anda ao acaso
python -m ferramentas.robo --nome bot --modo cacador         # terminal 3: vai atrás das moedas
```

| Tempo | O que mostrar |
|---|---|
| 0:00–1:00 | O projeto e a arquitetura (diagrama da seção 1): cliente-servidor sobre UDP, servidor autoritativo. |
| 1:00–2:00 | O protocolo: `docs/PROTOCOLO.md`, o exemplo de COMANDO e de ESTADO, e o limite de 1200 B por causa do MTU. |
| 2:00–4:00 | A demo. (1) Os robôs entram com **portas diferentes** e o servidor mostra os endereços: é a demultiplexação. (2) Os eventos e o placar são **iguais** nos dois terminais: uma única verdade. (3) Com 30% de perda simulada, o log do servidor mostra `retransmite EVENTO ...` e o placar continua certo: é o rdt3.0. (4) A linha "rede:" do robô mostra RTT, ESTADOs recebidos e retransmissões. (5) Lixo enviado ao servidor com `python -c "import socket; socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b'lixo', ('127.0.0.1', 5005))"` aparece como WARNING no log, e o jogo continua. |
| 4:00–5:00 | O que falta: integração com o cliente pygame (Integrante 2) e os experimentos UDP × TCP com netem e Wireshark (Integrante 3); a versão TCP já existe (`--transporte tcp`). |

Para jogar entre notebooks, use `--servidor IP_DO_SERVIDOR:5005` no robô e libere a porta
UDP 5005 no firewall da máquina do servidor.

---

## 5. Perguntas prováveis do professor

**Por que não usar TCP?**
Porque o TCP garante entrega e ordem para *tudo*. Se um ESTADO se perde, o TCP segura
os estados seguintes, que já chegaram, até retransmitir o perdido (bloqueio de cabeça
de fila). Num jogo, o estado perdido já está velho quando é retransmitido. Preferimos
UDP e confiabilidade só para os eventos. Ainda assim, implementamos a versão TCP para
comparar na prática.

**O que acontece se o ACK se perder?**
O remetente não sabe que o dado chegou. O temporizador estoura e ele retransmite com o
**mesmo `seq`**. O destinatário reconhece o `seq` como já visto, confirma de novo e não
processa de novo. É exatamente o cenário do rdt2.1/rdt3.0 (diagrama no PROTOCOLO.md).

**Como vocês medem o RTT?**
De duas formas, e elas concordam (63 ms e 63,1 ms com 30 ms de atraso em cada sentido).
- Pelo PING/PONG, 1 por segundo: o servidor devolve o `ts` do PING no campo `ts_eco`,
  e o cliente faz `agora − ts_eco` com o próprio relógio. Por isso os relógios não
  precisam estar sincronizados. O servidor responde assim que lê o PING, sem esperar o
  tick.
- Pelos ACKs das mensagens confiáveis, que alimentam o `EstimatedRTT` e o `DevRTT` do
  timeout adaptativo.

**Como vocês correlacionam o log com o Wireshark?**
Cada linha do registro tem o horário (`instante`, no mesmo relógio do "Epoch Time" do
Wireshark), a direção, os dois endereços, o tamanho e o `seq`/`ts` que estão dentro do
JSON. No Wireshark, filtrando pela porta do cliente e por `udp contains "\"seq\":41,"`,
aparece o mesmo pacote, com `udp.length` igual ao tamanho do log mais 8.

**Por que não tirar amostra de RTT de mensagem retransmitida?**
Porque o ACK é ambíguo: não dá para saber se ele responde à primeira transmissão ou à
retransmissão. Escolher errado infla ou encolhe a estimativa. O livro explica que o TCP
faz o mesmo; o nome técnico é algoritmo de Karn.

**O UDP não tem verificação de erro?**
Tem: o checksum de 16 bits do cabeçalho UDP. Um datagrama corrompido é descartado pelo
sistema operacional e, para nós, vira uma perda, que a confiabilidade já trata. Por
isso não precisamos de um checksum próprio.

**Se o UDP não tem conexão, como o servidor sabe que um jogador saiu?**
Se o jogador avisar, pelo SAIR (confiável). Se o programa travar ou a rede cair, pelo
**timeout de inatividade**: 5 s sem nenhuma mensagem e o jogador é removido. É por isso
que o cliente envia COMANDO continuamente, mesmo parado.

**Por que o servidor usa uma thread só, se há vários clientes?**
Porque com UDP não há uma conexão por cliente para atender: é um único socket. O laço
espera datagramas com prazo (até a hora do próximo tick), trata todos os que chegaram
e, no horário, simula e envia o estado. Sem threads, não há locks nem condições de
corrida. É o padrão de laço de eventos, feito com `selectors`.

**O que acontece se o ENTRAR_OK se perder?**
O ENTRAR_OK é confiável: sem o ACK do cliente, o servidor o retransmite com o mesmo
`seq`. Se, em vez disso, se perder o ACK do *ENTRAR*, o cliente retransmite o ENTRAR, e
o servidor o reconhece como duplicata: confirma de novo e não cria outro jogador.

**Quantas vezes vocês retransmitem, e por quanto tempo?**
São até 5 transmissões. Sem amostra de RTT, o timeout começa em 1 s e dobra (1, 2 e
depois 3 s, que é o teto): as transmissões saem em 0, 1, 3, 6 e 9 s, e a desistência
vem em 12 s. Com o RTT já aprendido, tudo fica bem mais rápido: no teste com 40 ms de
atraso, o primeiro timeout era de cerca de 375 ms.

**Se o remetente desistiu, o destinatário não recebeu?**
Não necessariamente. Podem ter sido só os ACKs que se perderam: o destinatário recebeu,
processou uma vez e confirmou todas as cópias, e nenhuma confirmação voltou. O
remetente não tem como distinguir esses dois casos, e por isso a desistência só é
registrada: quem resolve um cliente que sumiu de verdade é o timeout de inatividade.

**Por que o timeout dobra a cada retransmissão?**
Porque, se o ACK não veio, pode ser que a rede esteja congestionada. Retransmitir no
mesmo ritmo só piora a situação. O livro descreve a mesma duplicação do intervalo no
TCP.

**Como vocês garantem 20 atualizações por segundo?**
O próximo tick é agendado somando 50 ms ao horário em que o tick *deveria* ter
acontecido, e não ao horário atual. Assim o tempo de processamento não se acumula.
Um teste com sockets reais confere o intervalo médio de 50 ms entre ESTADOs.

**Por que o TCP precisa de enquadramento e o UDP não?**
Porque o UDP preserva as fronteiras: cada `sendto` vira um datagrama, e cada
`recvfrom` devolve exatamente um. O TCP é um fluxo de bytes: dois `send` podem chegar
num `recv` só, ou um `send` pode chegar em pedaços. Então cada mensagem vai com 2 bytes
de tamanho na frente, e o receptor só entrega quando tiver todos.

**Sobre TCP vocês mantêm o ACK de vocês?**
Por padrão, não. O transporte informa que garante a entrega, e servidor e cliente
desligam o ACK e a retransmissão da aplicação, que seriam redundantes. Dá para ligar
de novo, para comparar.

**O que é o algoritmo de Nagle e por que desligaram?**
O Nagle faz o TCP segurar dados pequenos enquanto houver dado sem confirmação, para
juntá-los num segmento só. Isso economiza banda em transferências, mas num jogo atrasa
cada COMANDO. `TCP_NODELAY` desliga o Nagle, e `--nagle` religa, para comparar.

**Como um servidor TCP atende vários clientes numa thread só?**
O seletor vigia ao mesmo tempo o socket de escuta (avisa quando há conexão nova para
`accept`) e um socket por conexão (avisa quando há bytes para ler ou espaço para
escrever). É o mesmo laço do UDP, com mais sockets.

**Por que 1200 bytes?**
Para nunca haver fragmentação IP. Na Ethernet caberiam 1472 bytes de carga útil UDP,
mas túneis, VPN e o MTU mínimo do IPv6 (1280) reduzem isso. Com fragmentação, perder
um fragmento perde a mensagem toda. O QUIC usa o mesmo limite.

**Por que JSON, se binário seria menor?**
Para ficar legível no Wireshark e nos logs, o que ajuda a depurar e a apresentar. O
custo em bytes é aceitável: um COMANDO tem 76 B e um ESTADO com 3 jogadores, cerca de
370 B.

**E se o número de sequência der a volta?**
Não dá: no JSON o inteiro não tem tamanho fixo. No TCP, o `seq` tem 32 bits e dá a
volta, e por isso o TCP precisa de cuidados extras.

**Qual a diferença entre a confiabilidade de vocês e a do TCP?**
A nossa é seletiva (só alguns tipos), usa ACK individual (como a repetição seletiva) e
não reordena a entrega. Ela não tem controle de fluxo nem de congestionamento, porque
o volume de mensagens confiáveis é minúsculo. Usamos as mesmas fórmulas do TCP para o
timeout.

**Como um único socket atende todos os jogadores?**
O sistema operacional entrega ao socket tudo o que chega na porta 5005. O servidor
separa os jogadores pelo (IP, porta) de origem de cada datagrama, numa tabela. É uma
demultiplexação feita pela aplicação.

---

## 6. Registro de alterações

| Data | Mudança |
|---|---|
| 15/11/2026 | Etapa a: documento criado com todas as seções. Conceitos 2.3 (protocolo de aplicação), 2.6 (números de sequência, parte do protocolo) e 2.11 (MTU) marcados como prontos; os outros, como planejados com sua etapa. Primeiras versões das decisões de projeto, do roteiro e das perguntas. |
| 15/11/2026 | Etapas b e c: 2.1, 2.2, 2.4, 2.5 e 2.6 passam a prontos, com funções e classes do código; ESTADO a taxa fixa em 2.10. Seis decisões de projeto novas, roteiro da prévia de 25/11 e três perguntas novas. |
| 15/11/2026 | Etapa d: 2.7 (ACK, timeout e retransmissão), 2.8 (duplicatas) e o estimador de 2.9 passam a prontos, com funções, testes e linhas de log reais; o simulador de perda entra em 2.10, com a observação sobre variação × reordenação. Seis decisões de projeto novas (a do ENTRAR foi reescrita) e quatro perguntas novas ou atualizadas. |
| 15/11/2026 | Etapa e: PING/PONG em 2.9 (pronto), log de pacotes em 2.3 e dados brutos para métricas em 2.10. Cinco decisões de projeto novas; perguntas sobre RTT e Wireshark. O formato do log e dos contadores é proposta (PROTOCOLO.md, seção 15). |
| 15/11/2026 | Etapa f: 2.12 pronto (enquadramento, `send` parcial, FIN, três vias, Nagle); TCP em 2.2 e 2.4 (quádruplas no `ss`); 2.13 com o transporte pronto e a medição a cargo do Integrante 3. Quatro decisões novas (duas são proposta) e quatro perguntas sobre TCP. |
| 15/11/2026 | Etapa g (revisão final): mapa rápido dos conceitos; referências desatualizadas corrigidas (2.4, 2.6); resultado do HOL marcado como esperado (2.13); roteiro de 4 min com tempos e falas; roteiro da prévia atualizado para o que já está pronto; registro em ordem cronológica; conferência automática de referências (`testes/test_referencias_doc.py`). |
