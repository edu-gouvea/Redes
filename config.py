"""Configurações do Arena em Rede, num único lugar.

Servidor, cliente, ferramentas e experimentos importam daqui. Os valores que o
cliente precisa saber para desenhar a arena também são enviados pelo servidor na
mensagem ENTRAR_OK (o servidor é a autoridade), então um cliente com um
config.py desatualizado continua desenhando a arena certa.
"""

# --- Rede e protocolo -------------------------------------------------------

VERSAO_PROTOCOLO = 1

# "0.0.0.0" aceita datagramas em todas as interfaces (necessário para a demo
# entre notebooks na mesma rede); "127.0.0.1" aceitaria só da própria máquina.
ENDERECO_ESCUTA = "0.0.0.0"
PORTA_PADRAO = 5005

# Carga útil máxima de um datagrama UDP do jogo. Fica abaixo do MTU de
# qualquer caminho comum para evitar fragmentação IP (ver docs/PROTOCOLO.md).
TAMANHO_MAXIMO_DATAGRAMA = 1200

# Buffer do recvfrom bem maior que o limite: no Linux um datagrama maior que o
# buffer é truncado em silêncio e, no Windows, gera erro. Lendo tudo, o
# protocolo consegue detectar e descartar o datagrama grande demais.
TAMANHO_BUFFER_RECEPCAO = 65535

# --- Simulação (servidor autoritativo) --------------------------------------

TAXA_ATUALIZACAO = 20        # ticks por segundo: 1 ESTADO a cada 50 ms
TAXA_COMANDOS = 20           # COMANDOs por segundo que o cliente deve enviar
LARGURA_ARENA = 800          # pixels
ALTURA_ARENA = 600           # pixels
RAIO_JOGADOR = 15            # pixels
RAIO_MOEDA = 8               # pixels
VELOCIDADE_JOGADOR = 200     # pixels por segundo
NUMERO_MOEDAS = 5
TAMANHO_MAXIMO_NOME = 12     # caracteres (ver o pior caso do ESTADO em docs/PROTOCOLO.md)

# Limita o tamanho do ESTADO: com este número de jogadores o snapshot ainda
# cabe em TAMANHO_MAXIMO_DATAGRAMA (verificado em testes/test_mensagens.py).
MAX_JOGADORES = 8

# Semente do gerador aleatório (posição das moedas). None = aleatório de verdade;
# um número fixo torna as partidas reproduzíveis nos experimentos.
SEMENTE_ALEATORIA = None

# --- Clientes ---------------------------------------------------------------

# UDP não tem conexão: o servidor só sabe que um cliente sumiu porque ele
# parou de mandar mensagens. Passado este tempo em silêncio, ele é removido.
TIMEOUT_INATIVIDADE = 5.0    # segundos

# Provisório até a etapa d: sem ACK ainda, o cliente reenvia ENTRAR neste
# intervalo até receber ENTRAR_OK (até MAX_TENTATIVAS vezes).
INTERVALO_REENVIO_ENTRAR = 0.5   # segundos

# --- Confiabilidade (só para mensagens confiáveis) --------------------------

# Cap. 3, estimativa do RTT e timeout do TCP. Valores recomendados no livro.
ALFA_RTT = 0.125
BETA_RTT = 0.25
TIMEOUT_INICIAL = 1.0        # segundos, antes da primeira amostra de RTT
TIMEOUT_MINIMO = 0.1         # segundos; evita retransmissões espúrias em localhost
TIMEOUT_MAXIMO = 3.0         # segundos; teto para o backoff exponencial
MAX_TENTATIVAS = 5           # transmissões (a original + 4 retransmissões)
JANELA_DUPLICATAS = 1024     # quantos seq recentes o destinatário lembra

# --- Log --------------------------------------------------------------------

NIVEL_LOG = "INFO"           # DEBUG mostra também cada COMANDO e ESTADO
