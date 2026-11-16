# Cliente pygame do Arena em Rede (Integrante 2)

A janela do jogo: tela de entrada, arena, placar, painel de rede ao vivo e log de
pacotes. Toda a rede fica na `SessaoCliente` (`protocolo/sessao.py`). O cliente cuida
do que o jogador **vê**: a previsão local do próprio jogador, a interpolação dos
outros e as medidas na tela.

## 1. Como rodar

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt                     # pygame e pytest

python -m servidor                                  # em um terminal
python -m cliente                                   # abre a tela de entrada
python -m cliente --nome ana                        # entra direto em 127.0.0.1:5005
python -m cliente --nome ana --servidor 192.168.0.10:5005
python -m cliente --nome ana --transporte tcp       # servidor também com --transporte tcp
python -m cliente --nome ana --atraso 80 --variacao 30 --perda 10   # rede ruim simulada
python -m cliente --nome ana --registro cliente.jsonl               # cada pacote em JSON Lines
```

`--perda`, `--atraso` e `--variacao` são os mesmos de `ferramentas/perda.py`: valem
em cada sentido, no próprio cliente, e funcionam também no Windows. Todas as opções
aparecem com `python -m cliente --help`.

## 2. Telas e teclas

**Tela de entrada:** nome (até 12 caracteres, com contador), servidor (`host:porta`)
e transporte (UDP ou TCP). Tab ou as setas trocam de campo e Enter entra. Erros
(nome vazio, porta inválida, servidor TCP fora do ar) aparecem na própria tela.

**Partida:**

| Tecla | O que faz |
|---|---|
| setas / WASD | mover |
| P | liga ou desliga a previsão local do próprio jogador |
| I | liga ou desliga a interpolação dos outros jogadores |
| G | "fantasma": círculo vazado na posição crua do último ESTADO |
| Tab | painel de rede detalhado ou resumido |
| L | log de pacotes: desligado → controle (sem COMANDO e ESTADO) → todos |
| H ou F1 | ajuda |
| Esc | sai da partida (envia SAIR) e volta à tela de entrada |
| R | depois de recusa ou de "sem resposta", tenta entrar de novo |

As situações da `SessaoCliente` aparecem na arena: *entrando* (com o número de
retransmissões do ENTRAR), *recusado* (com o motivo do ENTRAR_NEGADO) e *sem
resposta*. Os EVENTOs aparecem como avisos no canto ("robo-1 entrou", "você pegou uma
moeda") e, a cada moeda, sobe um "+1" sobre o jogador.

## 3. Organização

```text
cliente/
  __main__.py      linha de comando (python -m cliente)
  jogo.py          pygame: Aplicacao (laço), TelaEntrada, PartidaCliente, DesenhoPartida
  previsao.py      Previsor: previsão local + reconciliação         (sem pygame)
  interpolacao.py  Interpolador: atraso de reprodução adaptativo     (sem pygame)
  metricas.py      MedidorRede: perda, jitter, taxa, RTT do painel   (sem pygame)
```

Só `cliente/jogo.py` importa pygame. Os outros três são lógica pura e têm testes
próprios (seção 8). Assim dá para testar a parte de redes sem abrir janela.

Cada quadro (60 por segundo), `Aplicacao.passo()` faz:

1. lê os eventos do pygame e o estado das teclas → `definir_direcao(dx, dy)`;
2. `PartidaCliente.atualizar()` chama `processar()`, que recebe tudo o que chegou e envia o
   COMANDO, se for a hora (20 por segundo). Ao enviar, a sessão avisa os
   `observadores_comando` com o `seq`, e o `Previsor` registra o COMANDO;
3. se chegou ESTADO novo, faz a reconciliação da previsão e o alimenta no
   `Interpolador`;
4. lê os EVENTOs e as amostras de RTT;
5. desenha.

**Acréscimo na `SessaoCliente`:** a lista `observadores_comando`, chamada a cada
COMANDO enviado com `(seq, dx, dy, agora)`. É só um acréscimo, e nada do que já
existia mudou. A reconciliação precisa saber o `seq` de cada COMANDO para casar com o
`ultimo_comando` do ESTADO.

## 4. Previsão local com reconciliação (`cliente/previsao.py`)

**Problema.** O servidor é a autoridade, então a posição oficial só volta no ESTADO,
depois de o COMANDO ir e o ESTADO voltar. Ou seja, um RTT inteiro mais até um tick
(50 ms). Com 100 ms de RTT, a bolinha demoraria ~150 ms para reagir à tecla.

**Solução** (a dos jogos comerciais, com as regras da seção 10 do `docs/PROTOCOLO.md`):

1. Ao enviar cada COMANDO, o cliente move a própria cópia da posição na hora, com as
   mesmas contas do servidor (`Previsor.mover()` reproduz `Partida._mover()`:
   velocidade, diagonal dividida por √2 e paredes). O COMANDO fica numa lista de
   pendentes.
2. Quando chega um ESTADO, `ultimo_comando` diz até qual COMANDO o servidor já
   aplicou. `Previsor.ao_receber_estado()` descarta os pendentes até esse `seq`,
   parte da posição oficial e reaplica os que ainda estão em trânsito.
3. Se a previsão errou, a diferença vira um **erro visual** que some com constante de
   tempo de 100 ms. Assim a bolinha não dá trancos. Erros maiores que 4 raios
   (depois de uma pausa longa, por exemplo) são aplicados na hora.

O desenho anda de uma previsão à seguinte ao longo dos 50 ms do COMANDO
(`Previsor.posicao()`). Isso não atrasa a resposta: o COMANDO enviado em *t* move o
jogador no servidor justamente durante [*t*, *t* + 50 ms].

**Quando a previsão erra.** Em rede perfeita ela acerta sempre: o teste
`test_previsao_coincide_com_o_servidor_em_rede_perfeita` anda 2 s em várias direções
contra o servidor real e não registra nenhuma correção. Ela erra quando:

- um COMANDO **se perde**: o servidor repete o anterior por um tick;
- dois COMANDOs **chegam no mesmo tick** (por jitter): o servidor guarda só o mais
  recente (seção 7 do protocolo), e um passo previsto não acontece.

Os dois casos aparecem no painel em "correções". Com `--perda 10 --variacao 30`, são
algumas por segundo, cada uma de até um passo (10 px), e a suavização esconde.

**Para mostrar:** rode com `--atraso 100` e aperte P. Sem previsão, a bolinha passa a
responder com atraso visível. Com G ligado, o círculo vazado (posição do último
ESTADO) fica um RTT atrás da bolinha prevista.

## 5. Interpolação dos outros jogadores (`cliente/interpolacao.py`)

**Problema.** O ESTADO chega 20 vezes por segundo e a tela desenha 60. Desenhando só
o último ESTADO, os outros andam aos pulos de 10 px. Com jitter, os pulos ficam
irregulares: dois ESTADOs juntos, depois um buraco.

**Solução:** é o **atraso de reprodução adaptativo** do áudio em VoIP (Kurose, cap.
9). O servidor gera o ESTADO do tick *i* no instante tᵢ = tick · 50 ms, no relógio
dele, e o ESTADO chega em rᵢ, no relógio do cliente. Com u = 0,05:

```text
dᵢ = (1 − u)·dᵢ₋₁ + u·(rᵢ − tᵢ)                  atraso médio (+ defasagem dos relógios)
vᵢ = (1 − u)·vᵢ₋₁ + u·|rᵢ − tᵢ − dᵢ|             desvio do atraso
instante desenhado = agora − d − 50 ms − 4·v
```

O cliente desenha os outros jogadores interpolando os dois ESTADOs em volta desse
instante. Os termos da fórmula:

- `d` desconta a defasagem entre os relógios, que não precisam estar sincronizados,
  porque só a diferença rᵢ − tᵢ entra na conta;
- os 50 ms garantem que sempre exista um ESTADO depois do instante desenhado;
- 4·v absorve o jitter. Quanto mais jitter, maior o buffer, automaticamente.

Se o buffer esvazia (o instante desenhado passou do último ESTADO), os outros ficam
parados na última posição, e o painel conta um "esvaziamento". Se rᵢ − tᵢ pula mais
de 1 s (o servidor reiniciou), a estimativa recomeça do zero.

**O preço:** os outros jogadores aparecem cerca de RTT/2 + 50 ms + 4·v "no passado".
É a troca clássica: suavidade em troca de atraso. O próprio jogador não sofre isso,
porque usa a previsão.

**Para mostrar:** rode com `--variacao 40`, aperte I e compare. O painel mostra o
buffer crescendo ("buffer interp.").

## 6. Painel de rede (`cliente/metricas.py` e `DesenhoPartida`)

| Linha | Como é calculada |
|---|---|
| RTT (PING) | `ultimo_rtt_ms` da sessão, 1 PING por segundo, e o gráfico dos últimos 60 |
| jitter (RFC 3550) | D = (Rⱼ − Rᵢ) − (Sⱼ − Sᵢ) entre ESTADOs consecutivos, J ← J + (\|D\| − J)/16, com S = `ts` e R = `instante` |
| perda (5 s) | lacunas de `seq` em tudo o que o servidor manda a este cliente; retransmissão não conta duas vezes, reordenado não conta como perda, e os últimos 200 ms ficam de fora (podem estar a caminho) |
| ESTADO/s | ESTADOs aceitos no último segundo, contra a `taxa` do ENTRAR_OK |
| fora de ordem | `estados_fora_de_ordem` da sessão |
| taxa ↓ / ↑ | bytes de carga útil por segundo (no fio, somar 28 B de IP + UDP por pacote) |
| EstimatedRTT/Dev, timeout | `canal.contadores()`: o estimador do cap. 3 usado nas retransmissões |
| confiáveis, duplicatas, desistências | `canal.contadores()` (em TCP aparece "a cargo do TCP") |
| COMANDOs em trânsito | pendentes da previsão: ≈ RTT / 50 ms |
| correções | quantas vezes a previsão errou e o tamanho da última correção |
| buffer interp. | atraso de reprodução atual e quantas vezes o buffer esvaziou |

Os números do painel servem para a demonstração ao vivo. A análise dos experimentos
(Integrante 3) usa o `--registro`, que traz os mesmos dados brutos.

**Medida de RTT e FPS:** o PONG espera no socket até o próximo `processar()`. A 60 FPS,
o RTT medido pode ganhar até ~16 ms. Para medir com mais precisão, use `--fps 240`.

## 7. Decisão: nome do jogador com até 12 caracteres

**Decidido (Integrante 2, 16/11/2026): mantém 12 caracteres**
(`config.TAMANHO_MAXIMO_NOME`).

- **Cabe no datagrama com folga.** O pior caso do ESTADO (8 jogadores, nomes de 12
  emojis e inteiros de 10 dígitos) dá 1138 B, abaixo dos 1200 B. Com 16 caracteres
  daria 1234 B e passaria do limite. O teste
  `test_estado_no_pior_caso_cabe_em_um_datagrama` garante isso.
- **Cabe na tela.** O nome aparece sobre a bolinha (30 px de diâmetro) e no placar do
  painel lateral. Com 12 caracteres, o rótulo tem até ~90 px e não cobre os vizinhos.
- **É suficiente.** Dá para um primeiro nome, um apelido ou "robo-12".

O limite é contado em **caracteres**, como no protocolo, e a tela de entrada não
deixa digitar além dele (contador "n/12"). Um limite em bytes deixaria nomes com
acento ou emoji mais curtos sem motivo aparente para o jogador.

## 8. Testes

```bash
python -m pytest testes/test_previsao.py testes/test_interpolacao.py \
                 testes/test_metricas_cliente.py testes/test_cliente.py
```

- `testes/test_previsao.py`: `Previsor.mover()` coincide com o servidor (diagonal,
  paredes, cantos); reconciliação; correção suave; correção grande aplicada na hora.
- `testes/test_interpolacao.py`: interpolação sem jitter, buffer que cresce com
  jitter, buffer vazio, jogador que entra e sai, servidor reiniciado.
- `testes/test_metricas_cliente.py`: perda por lacunas (com reordenação e
  retransmissão), jitter da RFC 3550 com relógios defasados, taxa.
- `testes/test_cliente.py`: com o servidor real em 127.0.0.1, a previsão sem
  correções, a janela pygame sem tela (SDL "dummy") entrando, jogando e saindo com
  SAIR, e o limite do nome na tela de entrada. Os testes de janela são pulados se o
  pygame não estiver instalado.

## 9. Roteiro da demonstração (parte do Integrante 2)

1. **Dois clientes e um robô:** `python -m servidor`, `python -m cliente --nome ana`,
   `python -m cliente --nome bia` e `python -m ferramentas.robo --modo cacador`. O
   placar e os EVENTOs aparecem nas duas janelas.
2. **Previsão:** um cliente com `--atraso 100`. Liga G e desliga P: sem previsão, a
   resposta atrasa um RTT.
3. **Interpolação:** um cliente com `--variacao 40`. Desliga I e os outros andam aos
   trancos. Religa e o buffer cresce no painel.
4. **Perda:** um cliente com `--perda 20`. O painel mostra a perda e as
   retransmissões. Com L no modo "controle", aparecem os EVENTOs retransmitidos e os
   ACKs.
5. **TCP:** servidor e cliente com `--transporte tcp`. A confiabilidade da aplicação
   desliga ("a cargo do TCP").
