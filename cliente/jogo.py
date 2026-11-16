"""Janela do cliente: tela de entrada, arena, placar, painel de rede e log de pacotes.

Único módulo que importa pygame. A rede fica toda na SessaoCliente; aqui só se
lê o teclado, se chama `processar()` uma vez por frame e se desenha.

Teclas na partida:
    setas / WASD   mover
    P              previsão local do próprio jogador (liga/desliga)
    I              interpolação dos outros jogadores (liga/desliga)
    G              "fantasma": a posição crua do último ESTADO, para comparar
    Tab            painel de rede detalhado / resumido
    L              log de pacotes: desligado → controle → todos
    H              ajuda
    Esc            sair da partida (manda SAIR) e voltar à tela de entrada
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Any

import pygame

import config
from cliente.interpolacao import Interpolador
from cliente.metricas import MedidorRede
from cliente.previsao import Previsor
from protocolo.registro import RegistroPacote, RegistroPacotes
from protocolo.sessao import SessaoCliente, Situacao
from protocolo.transporte import Transporte, TransporteTCP, TransporteUDP, resolver

LARGURA_PAINEL = 320
ALTURA_MINIMA = 600
FPS_PADRAO = 60

# --- Cores ------------------------------------------------------------------

FUNDO = (18, 20, 28)
ARENA = (28, 32, 44)
GRADE = (36, 41, 56)
BORDA = (70, 78, 100)
TEXTO = (225, 228, 236)
TEXTO_FRACO = (140, 147, 165)
DESTAQUE = (120, 200, 255)
BOM = (110, 210, 140)
ALERTA = (240, 190, 80)
RUIM = (240, 100, 100)
MOEDA = (250, 205, 60)
MOEDA_BORDA = (190, 140, 20)
# Uma cor por jogador (pelo id), distinguíveis entre si no fundo escuro.
CORES_JOGADORES = [
    (90, 170, 255), (255, 120, 120), (120, 220, 130), (230, 150, 255),
    (255, 170, 80), (80, 220, 220), (240, 230, 110), (200, 200, 210),
]

TIPOS_FREQUENTES = {"COMANDO", "ESTADO"}
MODOS_LOG = ("desligado", "controle", "todos")
DURACAO_AVISO = 4.0       # segundos que um evento fica na tela
DURACAO_FLUTUANTE = 0.9   # segundos do "+1" ao pegar moeda


def cor_do_jogador(jogador_id: int) -> tuple[int, int, int]:
    return CORES_JOGADORES[(jogador_id - 1) % len(CORES_JOGADORES)]


# --- Configuração de uma conexão -------------------------------------------

@dataclass
class Conexao:
    nome: str = ""
    servidor: str = f"127.0.0.1:{config.PORTA_PADRAO}"
    transporte: str = "udp"
    confiabilidade: bool | None = None   # None = automático (ligado em UDP, desligado em TCP)
    nagle: bool = False
    perda: float = 0.0                   # %, só UDP (ferramentas/perda.py)
    atraso: float = 0.0                  # ms
    variacao: float = 0.0                # ms

    def endereco(self) -> tuple[str, int]:
        host, _, porta = self.servidor.strip().rpartition(":")
        if not porta.isdigit() or not 0 < int(porta) < 65536:
            raise ValueError("servidor deve ser host:porta, ex.: 192.168.0.10:5005")
        return (host or "127.0.0.1", int(porta))


def validar_nome(nome: str) -> str:
    """Mesmas regras da SessaoCliente e do servidor (docs/PROTOCOLO.md, seção 7)."""
    nome = nome.strip()
    if not nome:
        raise ValueError("digite um nome")
    if not nome.isprintable():
        raise ValueError("o nome tem caracteres inválidos")
    if len(nome) > config.TAMANHO_MAXIMO_NOME:
        raise ValueError(f"o nome tem no máximo {config.TAMANHO_MAXIMO_NOME} caracteres")
    return nome


def criar_transporte(conexao: Conexao, servidor: tuple[str, int]) -> Transporte:
    from ferramentas.perda import TransporteComPerda

    if conexao.transporte == "tcp":
        base: Transporte = TransporteTCP.cliente(servidor, nodelay=not conexao.nagle)
    else:
        base = TransporteUDP.cliente()
    if conexao.perda or conexao.atraso or conexao.variacao:
        return TransporteComPerda(base, perda=conexao.perda / 100, atraso=conexao.atraso / 1000,
                                  variacao=conexao.variacao / 1000)
    return base


# --- Fontes -----------------------------------------------------------------

class Fontes:
    def __init__(self) -> None:
        mono = "dejavusansmono,consolas,menlo,liberationmono,couriernew"
        sans = "dejavusans,segoeui,helvetica,arial,liberationsans"
        self.titulo = pygame.font.SysFont(sans, 30, bold=True)
        self.grande = pygame.font.SysFont(sans, 20, bold=True)
        self.normal = pygame.font.SysFont(sans, 16)
        self.pequena = pygame.font.SysFont(sans, 13)
        self.mono = pygame.font.SysFont(mono, 13)
        self.mono_pequena = pygame.font.SysFont(mono, 12)


def escrever(tela: pygame.Surface, fonte: pygame.font.Font, texto: str, cor, posicao,
             ancora: str = "topleft") -> pygame.Rect:
    imagem = fonte.render(texto, True, cor)
    retangulo = imagem.get_rect(**{ancora: posicao})
    tela.blit(imagem, retangulo)
    return retangulo


def caixa_translucida(tela: pygame.Surface, retangulo: pygame.Rect, alfa: int = 190) -> None:
    camada = pygame.Surface(retangulo.size, pygame.SRCALPHA)
    camada.fill((10, 12, 18, alfa))
    tela.blit(camada, retangulo.topleft)


# --- PartidaCliente ------------------------------------------------------------------

@dataclass
class Aviso:
    texto: str
    cor: tuple[int, int, int]
    expira: float


@dataclass
class Flutuante:
    texto: str
    jogador_id: int
    inicio: float


@dataclass
class Exibicao:
    """O que está ligado na tela (as teclas P, I, G, Tab, L e H)."""

    prever: bool = True
    interpolar: bool = True
    fantasma: bool = False
    painel_detalhado: bool = True
    modo_log: int = 0
    ajuda: bool = False


class PartidaCliente:
    """Uma sessão com o servidor e tudo o que o cliente calcula em cima dela."""

    def __init__(self, conexao: Conexao, sessao: SessaoCliente) -> None:
        self.conexao = conexao
        self.sessao = sessao
        self.medidor = MedidorRede()
        self.log: deque[RegistroPacote] = deque(maxlen=300)
        self.previsor: Previsor | None = None
        self.interpolador: Interpolador | None = None
        self.avisos: deque[Aviso] = deque(maxlen=6)
        self.flutuantes: list[Flutuante] = []
        self._estados_vistos = 0
        self._pongs_vistos = 0
        sessao.observadores_comando.append(self._ao_enviar_comando)

    # --- Rede ---------------------------------------------------------------

    def ao_pacote(self, pacote: RegistroPacote) -> None:
        self.medidor.ao_pacote(pacote)
        self.log.append(pacote)

    def _ao_enviar_comando(self, seq: int, dx: int, dy: int, agora: float) -> None:
        if self.previsor is not None:
            self.previsor.ao_enviar_comando(seq, dx, dy, agora)

    def atualizar(self, agora: float, dx: int, dy: int) -> None:
        sessao = self.sessao
        sessao.definir_direcao(dx, dy)
        sessao.processar()
        if sessao.parametros is not None and self.previsor is None:
            # O intervalo de COMANDO é o da SessaoCliente (config.TAXA_COMANDOS);
            # o resto vem do servidor, pelo ENTRAR_OK.
            self.previsor = Previsor(sessao.parametros, 1.0 / config.TAXA_COMANDOS)
            self.interpolador = Interpolador(1.0 / sessao.parametros["taxa"])
        if sessao.estados_recebidos != self._estados_vistos and sessao.estado is not None:
            self._estados_vistos = sessao.estados_recebidos
            self._ao_novo_estado(sessao.estado, agora)
        if sessao.pongs_recebidos != self._pongs_vistos and sessao.ultimo_rtt_ms is not None:
            self._pongs_vistos = sessao.pongs_recebidos
            self.medidor.registrar_rtt(sessao.ultimo_rtt_ms)
        for evento in sessao.consumir_eventos():
            self._ao_evento(evento, agora)
        self.medidor.avancar_relogio(time.time())
        while self.avisos and self.avisos[0].expira < agora:
            self.avisos.popleft()
        self.flutuantes = [f for f in self.flutuantes if agora - f.inicio < DURACAO_FLUTUANTE]

    def _ao_novo_estado(self, estado: dict[str, Any], agora: float) -> None:
        assert self.previsor is not None and self.interpolador is not None
        eu = self.sessao.meu_jogador()
        if eu is not None:
            self.previsor.ao_receber_estado(eu["x"], eu["y"], estado["ultimo_comando"], agora)
        self.interpolador.adicionar(estado["tick"], estado["jogadores"], agora)

    def _ao_evento(self, evento: dict[str, Any], agora: float) -> None:
        nomes = self.nomes()
        quem = nomes.get(evento["jogador"]) or evento.get("nome") or f"jogador {evento['jogador']}"
        meu = evento["jogador"] == self.sessao.meu_id
        if evento["evento"] == "ponto":
            texto = f"{'você' if meu else quem} pegou uma moeda ({evento['pontos']})"
            self.flutuantes.append(Flutuante("+1", evento["jogador"], agora))
            cor = MOEDA if meu else TEXTO_FRACO
        elif evento["evento"] == "entrou":
            texto, cor = f"{evento['nome']} entrou", BOM
        else:
            texto, cor = f"{quem} saiu ({evento.get('motivo', '?')})", ALERTA
        self.avisos.append(Aviso(texto, cor, agora + DURACAO_AVISO))

    # --- Consultas para o desenho -------------------------------------------

    def nomes(self) -> dict[int, str]:
        estado = self.sessao.estado
        return {} if estado is None else {j["id"]: j["nome"] for j in estado["jogadores"]}

    def posicoes(self, agora: float, exibicao: Exibicao) -> dict[int, tuple[float, float]]:
        """Onde desenhar cada jogador, conforme a previsão e a interpolação estão ligadas."""
        estado = self.sessao.estado
        if estado is None:
            return {}
        posicoes = {j["id"]: (j["x"], j["y"]) for j in estado["jogadores"]}
        if exibicao.interpolar and self.interpolador is not None:
            interpoladas = self.interpolador.posicoes(agora)
            posicoes.update({i: p for i, p in interpoladas.items() if i in posicoes})
        meu = self.sessao.meu_id
        if exibicao.prever and self.previsor is not None and meu in posicoes:
            prevista = self.previsor.posicao(agora)
            if prevista is not None:
                posicoes[meu] = prevista
        return posicoes

    def fechar(self) -> None:
        if self.sessao.situacao in (Situacao.ENTRANDO, Situacao.EM_JOGO):
            self.sessao.sair()
        self.sessao.transporte.fechar()


# --- Tela de entrada ----------------------------------------------------------

class TelaEntrada:
    CAMPOS = ("nome", "servidor", "transporte")

    def __init__(self, conexao: Conexao) -> None:
        self.conexao = conexao
        self.foco = 0 if not conexao.nome else 1
        self.mensagem = ""
        self.cor_mensagem = RUIM

    def ao_evento(self, evento: pygame.event.Event) -> bool:
        """Trata teclado; devolve True quando o jogador confirma (Enter)."""
        campo = self.CAMPOS[self.foco]
        if evento.type == pygame.TEXTINPUT and campo != "transporte":
            if campo == "nome":
                novo = self.conexao.nome + evento.text
                if len(novo) <= config.TAMANHO_MAXIMO_NOME:
                    self.conexao.nome = novo
                    self.mensagem = ""
                else:
                    self.mostrar(f"o nome tem no máximo {config.TAMANHO_MAXIMO_NOME} caracteres")
            else:
                self.conexao.servidor += evento.text
            return False
        if evento.type != pygame.KEYDOWN:
            return False
        if evento.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            return True
        if evento.key in (pygame.K_TAB, pygame.K_DOWN):
            sentido = -1 if evento.mod & pygame.KMOD_SHIFT else 1
            self.foco = (self.foco + sentido) % len(self.CAMPOS)
        elif evento.key == pygame.K_UP:
            self.foco = (self.foco - 1) % len(self.CAMPOS)
        elif campo == "transporte" and evento.key in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_SPACE):
            self.conexao.transporte = "tcp" if self.conexao.transporte == "udp" else "udp"
        elif evento.key == pygame.K_BACKSPACE and campo != "transporte":
            setattr(self.conexao, campo, getattr(self.conexao, campo)[:-1])
        return False

    def mostrar(self, texto: str, cor=RUIM) -> None:
        self.mensagem, self.cor_mensagem = texto, cor

    def desenhar(self, tela: pygame.Surface, fontes: Fontes, agora: float) -> None:
        tela.fill(FUNDO)
        centro_x = tela.get_width() // 2
        escrever(tela, fontes.titulo, "Arena em Rede", TEXTO, (centro_x, 110), "midtop")
        escrever(tela, fontes.normal, "colete moedas · servidor autoritativo sobre UDP", TEXTO_FRACO,
                 (centro_x, 152), "midtop")
        largura = 420
        x = centro_x - largura // 2
        y = 210
        rotulos = {"nome": f"Nome (até {config.TAMANHO_MAXIMO_NOME} caracteres)",
                   "servidor": "Servidor (host:porta)", "transporte": "Transporte"}
        cursor = "|" if int(agora * 2) % 2 == 0 else " "
        for indice, campo in enumerate(self.CAMPOS):
            focado = indice == self.foco
            escrever(tela, fontes.pequena, rotulos[campo], DESTAQUE if focado else TEXTO_FRACO, (x, y))
            caixa = pygame.Rect(x, y + 20, largura, 36)
            pygame.draw.rect(tela, ARENA, caixa, border_radius=6)
            pygame.draw.rect(tela, DESTAQUE if focado else BORDA, caixa, 2, border_radius=6)
            if campo == "transporte":
                for i, opcao in enumerate(("udp", "tcp")):
                    escolhida = self.conexao.transporte == opcao
                    alvo = pygame.Rect(caixa.x + 6 + i * (largura // 2 - 3), caixa.y + 5, largura // 2 - 9, 26)
                    if escolhida:
                        pygame.draw.rect(tela, DESTAQUE, alvo, border_radius=4)
                    escrever(tela, fontes.normal, opcao.upper(), FUNDO if escolhida else TEXTO_FRACO,
                             alvo.center, "center")
            else:
                valor = getattr(self.conexao, campo)
                texto = valor + (cursor if focado else "")
                escrever(tela, fontes.grande, texto, TEXTO, (caixa.x + 10, caixa.centery), "midleft")
                if campo == "nome":
                    escrever(tela, fontes.pequena, f"{len(valor)}/{config.TAMANHO_MAXIMO_NOME}",
                             TEXTO_FRACO, (caixa.right - 10, caixa.centery), "midright")
            y += 76
        if self.mensagem:
            escrever(tela, fontes.normal, self.mensagem, self.cor_mensagem, (centro_x, y + 6), "midtop")
        dicas = "Tab / ↑↓ trocar campo · ←→ escolher transporte · Enter entrar · Esc sair"
        escrever(tela, fontes.pequena, dicas, TEXTO_FRACO, (centro_x, tela.get_height() - 40), "midtop")
        simulada = self.conexao.perda or self.conexao.atraso or self.conexao.variacao
        if simulada:
            texto = (f"rede simulada: perda {self.conexao.perda:g}% · atraso {self.conexao.atraso:g} ms"
                     f" · variação {self.conexao.variacao:g} ms")
            escrever(tela, fontes.pequena, texto, ALERTA, (centro_x, tela.get_height() - 62), "midtop")


# --- Desenho da partida -------------------------------------------------------

class DesenhoPartida:
    def __init__(self, fontes: Fontes) -> None:
        self.f = fontes

    def desenhar(self, tela: pygame.Surface, partida: PartidaCliente, exibicao: Exibicao,
                 agora: float, fps: float) -> None:
        tela.fill(FUNDO)
        sessao = partida.sessao
        parametros = sessao.parametros or {
            "largura": config.LARGURA_ARENA, "altura": config.ALTURA_ARENA,
            "raio_jogador": config.RAIO_JOGADOR, "raio_moeda": config.RAIO_MOEDA}
        arena = pygame.Rect(0, 0, parametros["largura"], parametros["altura"])
        self._arena(tela, arena)
        if sessao.estado is not None and sessao.situacao is Situacao.EM_JOGO:
            self._moedas(tela, sessao.estado["moedas"], parametros["raio_moeda"])
            posicoes = partida.posicoes(agora, exibicao)
            if exibicao.fantasma:
                self._fantasmas(tela, sessao.estado, parametros["raio_jogador"])
            self._jogadores(tela, partida, posicoes, parametros["raio_jogador"])
            self._flutuantes(tela, partida, posicoes, agora)
        self._avisos(tela, partida)
        if exibicao.modo_log:
            self._log(tela, partida, arena, MODOS_LOG[exibicao.modo_log])
        self._situacao(tela, partida, arena)
        self._painel(tela, partida, exibicao, arena, fps)
        if exibicao.ajuda:
            self._ajuda(tela, arena)

    # --- Arena --------------------------------------------------------------

    def _arena(self, tela: pygame.Surface, arena: pygame.Rect) -> None:
        pygame.draw.rect(tela, ARENA, arena)
        for x in range(50, arena.width, 50):
            pygame.draw.line(tela, GRADE, (x, 0), (x, arena.height))
        for y in range(50, arena.height, 50):
            pygame.draw.line(tela, GRADE, (0, y), (arena.width, y))
        pygame.draw.rect(tela, BORDA, arena, 2)

    def _moedas(self, tela, moedas, raio: int) -> None:
        for x, y in moedas:
            pygame.draw.circle(tela, MOEDA, (round(x), round(y)), raio)
            pygame.draw.circle(tela, MOEDA_BORDA, (round(x), round(y)), raio, 2)

    def _fantasmas(self, tela, estado, raio: int) -> None:
        for j in estado["jogadores"]:
            pygame.draw.circle(tela, cor_do_jogador(j["id"]), (round(j["x"]), round(j["y"])), raio, 1)

    def _jogadores(self, tela, partida: PartidaCliente, posicoes, raio: int) -> None:
        meu = partida.sessao.meu_id
        nomes = partida.nomes()
        # O próprio jogador por último, para ficar por cima.
        for jogador_id in sorted(posicoes, key=lambda i: i == meu):
            x, y = posicoes[jogador_id]
            centro = (round(x), round(y))
            cor = cor_do_jogador(jogador_id)
            pygame.draw.circle(tela, cor, centro, raio)
            if jogador_id == meu:
                pygame.draw.circle(tela, TEXTO, centro, raio + 3, 2)
            escrever(tela, self.f.pequena, nomes.get(jogador_id, "?"), TEXTO,
                     (centro[0], centro[1] - raio - 4), "midbottom")

    def _flutuantes(self, tela, partida: PartidaCliente, posicoes, agora: float) -> None:
        for f in partida.flutuantes:
            if f.jogador_id not in posicoes:
                continue
            progresso = (agora - f.inicio) / DURACAO_FLUTUANTE
            x, y = posicoes[f.jogador_id]
            imagem = self.f.grande.render(f.texto, True, MOEDA)
            imagem.set_alpha(round(255 * (1 - progresso)))
            tela.blit(imagem, imagem.get_rect(midbottom=(round(x), round(y - 30 - 30 * progresso))))

    def _avisos(self, tela, partida: PartidaCliente) -> None:
        y = 10
        for aviso in partida.avisos:
            imagem = self.f.pequena.render(aviso.texto, True, aviso.cor)
            caixa_translucida(tela, imagem.get_rect(topleft=(8, y)).inflate(8, 2), 150)
            tela.blit(imagem, (8, y))
            y += 19

    def _situacao(self, tela, partida: PartidaCliente, arena: pygame.Rect) -> None:
        sessao = partida.sessao
        endereco = f"{sessao.servidor[0]}:{sessao.servidor[1]}"
        if sessao.situacao is Situacao.EM_JOGO and sessao.estado is not None:
            return
        if sessao.situacao in (Situacao.ENTRANDO, Situacao.EM_JOGO):
            linhas = [(f"Entrando em {endereco}...", TEXTO),
                      (f"ENTRAR enviado; retransmissões: {sessao.canal.contadores().retransmitidas}",
                       TEXTO_FRACO)]
        elif sessao.situacao is Situacao.RECUSADO:
            linhas = [(f"O servidor recusou a entrada: {sessao.motivo_recusa}", RUIM),
                      ("R tentar de novo · Esc voltar", TEXTO_FRACO)]
        elif sessao.situacao is Situacao.SEM_RESPOSTA:
            linhas = [(f"Sem resposta do servidor {endereco}", RUIM),
                      ("Ele está rodando? A porta UDP está liberada no firewall?", TEXTO_FRACO),
                      ("R tentar de novo · Esc voltar", TEXTO_FRACO)]
        else:
            linhas = [("Sessão encerrada", TEXTO_FRACO)]
        caixa = pygame.Rect(0, 0, arena.width - 120, 30 + 26 * len(linhas))
        caixa.center = arena.center
        caixa_translucida(tela, caixa, 220)
        pygame.draw.rect(tela, BORDA, caixa, 1, border_radius=6)
        for i, (texto, cor) in enumerate(linhas):
            escrever(tela, self.f.normal, texto, cor, (caixa.centerx, caixa.y + 16 + 26 * i), "midtop")

    def _log(self, tela, partida: PartidaCliente, arena: pygame.Rect, modo: str) -> None:
        linhas = 14
        pacotes = [p for p in partida.log if modo == "todos" or p.tipo not in TIPOS_FREQUENTES][-linhas:]
        altura = 22 + 15 * linhas
        caixa = pygame.Rect(0, arena.height - altura, arena.width, altura)
        caixa_translucida(tela, caixa)
        titulo = (f"log de pacotes ({modo}) · tx {partida.sessao.registro.total('tx')} "
                  f"· rx {partida.sessao.registro.total('rx')} · L muda o filtro")
        escrever(tela, self.f.mono_pequena, titulo, DESTAQUE, (8, caixa.y + 4))
        for i, pacote in enumerate(pacotes):
            cor = BOM if pacote.direcao == "tx" else TEXTO
            if pacote.tipo == "INVALIDO":
                cor = RUIM
            elif pacote.tipo in ("EVENTO", "ENTRAR", "ENTRAR_OK", "ENTRAR_NEGADO", "SAIR"):
                cor = ALERTA
            escrever(tela, self.f.mono_pequena, pacote.como_texto(), cor, (8, caixa.y + 20 + 15 * i))

    def _ajuda(self, tela, arena: pygame.Rect) -> None:
        linhas = [
            ("setas / WASD", "mover"),
            ("P", "previsão local do seu jogador (sem ela, a resposta atrasa 1 RTT)"),
            ("I", "interpolação dos outros (sem ela, andam aos pulos de 50 ms)"),
            ("G", "fantasma: posição crua do último ESTADO"),
            ("Tab", "painel de rede detalhado / resumido"),
            ("L", "log de pacotes: desligado → controle → todos"),
            ("H", "fecha esta ajuda"),
            ("Esc", "sair da partida (envia SAIR)"),
        ]
        caixa = pygame.Rect(0, 0, arena.width - 80, 60 + 24 * len(linhas))
        caixa.center = arena.center
        caixa_translucida(tela, caixa, 230)
        pygame.draw.rect(tela, BORDA, caixa, 1, border_radius=6)
        escrever(tela, self.f.grande, "Teclas", TEXTO, (caixa.x + 20, caixa.y + 14))
        for i, (tecla, texto) in enumerate(linhas):
            y = caixa.y + 50 + 24 * i
            escrever(tela, self.f.mono, tecla, DESTAQUE, (caixa.x + 20, y))
            escrever(tela, self.f.pequena, texto, TEXTO, (caixa.x + 140, y))

    # --- Painel lateral -----------------------------------------------------

    def _painel(self, tela, partida: PartidaCliente, exibicao: Exibicao, arena: pygame.Rect, fps: float) -> None:
        sessao = partida.sessao
        x = arena.right + 16
        y = 14
        escrever(tela, self.f.grande, "Arena em Rede", TEXTO, (x, y))
        y += 28
        transporte = "TCP" if sessao.transporte.garante_entrega else "UDP"
        escrever(tela, self.f.pequena, f"{sessao.nome} · {transporte} · {sessao.servidor[0]}:{sessao.servidor[1]}",
                 TEXTO_FRACO, (x, y))
        y += 26

        y = self._secao(tela, "PLACAR", x, y)
        if sessao.estado is not None:
            ranking = sorted(sessao.estado["jogadores"], key=lambda j: (-j["pontos"], j["id"]))
            for posicao, j in enumerate(ranking, start=1):
                meu = j["id"] == sessao.meu_id
                pygame.draw.circle(tela, cor_do_jogador(j["id"]), (x + 6, y + 9), 6)
                fonte = self.f.normal
                escrever(tela, fonte, f"{posicao}. {j['nome']}", TEXTO if meu else TEXTO_FRACO, (x + 18, y))
                escrever(tela, fonte, str(j["pontos"]), MOEDA if meu else TEXTO, (x + LARGURA_PAINEL - 40, y),
                         "topright")
                y += 21
        y += 8

        y = self._secao(tela, "REDE", x, y)
        for rotulo, valor, cor in self._linhas_rede(partida, exibicao, fps):
            escrever(tela, self.f.mono_pequena, rotulo, TEXTO_FRACO, (x, y))
            escrever(tela, self.f.mono_pequena, valor, cor, (x + LARGURA_PAINEL - 40, y), "topright")
            y += 16
        if exibicao.painel_detalhado and len(partida.medidor.rtts) > 1:
            y = self._grafico_rtt(tela, partida, x, y + 6)

        rodape = [
            f"P previsão {'ON' if exibicao.prever else 'off'}  "
            f"I interp. {'ON' if exibicao.interpolar else 'off'}  G {'ON' if exibicao.fantasma else 'off'}",
            "Tab painel · L log · H ajuda · Esc sair",
        ]
        for i, texto in enumerate(rodape):
            escrever(tela, self.f.pequena, texto, TEXTO_FRACO, (x, tela.get_height() - 42 + 18 * i))

    def _secao(self, tela, titulo: str, x: int, y: int) -> int:
        escrever(tela, self.f.pequena, titulo, DESTAQUE, (x, y))
        pygame.draw.line(tela, BORDA, (x, y + 18), (x + LARGURA_PAINEL - 40, y + 18))
        return y + 24

    def _linhas_rede(self, partida: PartidaCliente, exibicao: Exibicao, fps: float):
        sessao = partida.sessao
        medidor = partida.medidor
        rtt = sessao.ultimo_rtt_ms
        perdidos, esperados = medidor.perda()
        perda_pct = medidor.perda_pct()
        estados_s = medidor.estados_por_segundo()
        taxa_esperada = (sessao.parametros or {}).get("taxa", config.TAXA_ATUALIZACAO)
        linhas = [
            ("RTT (PING)", "—" if rtt is None else f"{rtt} ms",
             TEXTO if rtt is None or rtt < 100 else ALERTA if rtt < 200 else RUIM),
            ("jitter (RFC 3550)", f"{medidor.jitter_ms:.1f} ms", TEXTO if medidor.jitter_ms < 10 else ALERTA),
            ("perda (5 s)", f"{perda_pct:.1f}% ({perdidos}/{esperados})",
             BOM if perdidos == 0 else ALERTA if perda_pct < 10 else RUIM),
            ("ESTADO/s", f"{estados_s}/{taxa_esperada}", TEXTO if estados_s >= taxa_esperada - 1 else ALERTA),
        ]
        if not exibicao.painel_detalhado:
            return linhas
        contadores = sessao.canal.contadores()
        linhas += [
            ("fora de ordem", str(sessao.estados_fora_de_ordem), TEXTO),
            ("taxa ↓ / ↑", f"{medidor.taxa_bytes('rx') / 1000:.1f} / {medidor.taxa_bytes('tx') / 1000:.1f} kB/s",
             TEXTO),
        ]
        if sessao.confiabilidade:
            estimado = "—" if contadores.rtt_estimado_ms is None else f"{contadores.rtt_estimado_ms:.0f}"
            desvio = "—" if contadores.dev_rtt_ms is None else f"{contadores.dev_rtt_ms:.0f}"
            linhas += [
                ("EstimatedRTT/Dev", f"{estimado} / {desvio} ms", TEXTO),
                ("timeout", f"{contadores.timeout_ms:.0f} ms", TEXTO),
                ("confiáveis env/retx", f"{contadores.enviadas} / {contadores.retransmitidas}",
                 TEXTO if contadores.retransmitidas == 0 else ALERTA),
                ("duplicatas/desist.", f"{contadores.duplicadas_recebidas} / {contadores.desistencias}", TEXTO),
            ]
        else:
            linhas.append(("confiabilidade", "a cargo do TCP", TEXTO_FRACO))
        if partida.previsor is not None:
            p = partida.previsor
            linhas += [
                ("COMANDOs em trânsito", str(p.pendentes), TEXTO),
                ("correções", f"{p.correcoes} (últ. {p.ultima_correcao:.1f} px)", TEXTO),
            ]
        if partida.interpolador is not None:
            i = partida.interpolador
            linhas.append(("buffer interp.", f"{i.atraso_reproducao * 1000:.0f} ms ({i.esgotamentos} esvaz.)",
                           TEXTO))
        linhas.append(("FPS", f"{fps:.0f}", TEXTO))
        return linhas

    def _grafico_rtt(self, tela, partida: PartidaCliente, x: int, y: int) -> int:
        largura, altura = LARGURA_PAINEL - 40, 46
        amostras = list(partida.medidor.rtts)
        maximo = max(max(amostras), 10)
        escrever(tela, self.f.mono_pequena, f"RTT dos últimos {len(amostras)} PINGs · máx {maximo} ms",
                 TEXTO_FRACO, (x, y))
        y += 16
        caixa = pygame.Rect(x, y, largura, altura)
        pygame.draw.rect(tela, ARENA, caixa)
        passo = largura / max(1, len(amostras) - 1)
        pontos = [(x + i * passo, y + altura - 2 - (altura - 6) * v / maximo) for i, v in enumerate(amostras)]
        pygame.draw.lines(tela, DESTAQUE, False, pontos, 2)
        return y + altura + 6


# --- Aplicação ---------------------------------------------------------------

class Aplicacao:
    def __init__(self, conexao: Conexao, registro: RegistroPacotes, fps: int = FPS_PADRAO,
                 entrar_direto: bool = False) -> None:
        pygame.init()
        pygame.display.set_caption("Arena em Rede")
        self.tamanho = (config.LARGURA_ARENA + LARGURA_PAINEL, max(config.ALTURA_ARENA, ALTURA_MINIMA))
        self.tela = pygame.display.set_mode(self.tamanho)
        self.fontes = Fontes()
        self.relogio = pygame.time.Clock()
        self.fps = fps
        self.registro = registro
        registro.observadores.append(self._ao_pacote)
        self.entrada = TelaEntrada(conexao)
        self.desenho = DesenhoPartida(self.fontes)
        self.exibicao = Exibicao()
        self.partida: PartidaCliente | None = None
        self.rodando = True
        if entrar_direto:
            self._conectar()
        if self.partida is None:
            pygame.key.start_text_input()

    def _ao_pacote(self, pacote: RegistroPacote) -> None:
        if self.partida is not None:
            self.partida.ao_pacote(pacote)

    # --- Laço ---------------------------------------------------------------

    def executar(self, duracao: float | None = None) -> None:
        """Laço principal; `duracao` (s) serve para testes e demonstrações automáticas."""
        inicio = time.monotonic()
        try:
            while self.rodando and (duracao is None or time.monotonic() - inicio < duracao):
                self.passo()
                self.relogio.tick(self.fps)
        finally:
            self.fechar()

    def passo(self) -> None:
        agora = time.monotonic()
        for evento in pygame.event.get():
            self._ao_evento(evento)
        if self.partida is None:
            self.entrada.desenhar(self.tela, self.fontes, agora)
        else:
            dx, dy = self._direcao()
            self.partida.atualizar(agora, dx, dy)
            self._ajustar_janela()
            self.desenho.desenhar(self.tela, self.partida, self.exibicao, agora, self.relogio.get_fps())
        pygame.display.flip()

    def _direcao(self) -> tuple[int, int]:
        if not pygame.key.get_focused() or self.exibicao.ajuda:
            return (0, 0)
        teclas = pygame.key.get_pressed()
        direita = teclas[pygame.K_RIGHT] or teclas[pygame.K_d]
        esquerda = teclas[pygame.K_LEFT] or teclas[pygame.K_a]
        baixo = teclas[pygame.K_DOWN] or teclas[pygame.K_s]
        cima = teclas[pygame.K_UP] or teclas[pygame.K_w]
        return (int(direita) - int(esquerda), int(baixo) - int(cima))

    def _ajustar_janela(self) -> None:
        parametros = self.partida.sessao.parametros if self.partida else None
        if parametros is None:
            return
        tamanho = (parametros["largura"] + LARGURA_PAINEL, max(parametros["altura"], ALTURA_MINIMA))
        if tamanho != self.tamanho:
            self.tamanho = tamanho
            self.tela = pygame.display.set_mode(tamanho)

    # --- Teclado ------------------------------------------------------------

    def _ao_evento(self, evento: pygame.event.Event) -> None:
        if evento.type == pygame.QUIT:
            self.rodando = False
            return
        if self.partida is None:
            if evento.type == pygame.KEYDOWN and evento.key == pygame.K_ESCAPE:
                self.rodando = False
            elif self.entrada.ao_evento(evento):
                self._conectar()
            return
        if evento.type != pygame.KEYDOWN:
            return
        situacao = self.partida.sessao.situacao
        tecla = evento.key
        if tecla == pygame.K_ESCAPE:
            if self.exibicao.ajuda:
                self.exibicao.ajuda = False
            else:
                self._voltar_para_entrada()
        elif tecla == pygame.K_r and situacao in (Situacao.RECUSADO, Situacao.SEM_RESPOSTA):
            self._voltar_para_entrada()
            self._conectar()
        elif tecla == pygame.K_p:
            self.exibicao.prever = not self.exibicao.prever
        elif tecla == pygame.K_i:
            self.exibicao.interpolar = not self.exibicao.interpolar
        elif tecla == pygame.K_g:
            self.exibicao.fantasma = not self.exibicao.fantasma
        elif tecla == pygame.K_TAB:
            self.exibicao.painel_detalhado = not self.exibicao.painel_detalhado
        elif tecla == pygame.K_l:
            self.exibicao.modo_log = (self.exibicao.modo_log + 1) % len(MODOS_LOG)
        elif tecla in (pygame.K_h, pygame.K_F1):
            self.exibicao.ajuda = not self.exibicao.ajuda

    # --- Conexão ------------------------------------------------------------

    def _conectar(self) -> None:
        conexao = self.entrada.conexao
        try:
            conexao.nome = validar_nome(conexao.nome)
            servidor = resolver(*conexao.endereco())
        except (ValueError, OSError) as erro:
            self.entrada.mostrar(str(erro))
            return
        if conexao.transporte == "tcp":
            # A conexão TCP (três vias) bloqueia até 3 s: avisa antes de travar a tela.
            self.entrada.mostrar(f"conectando por TCP a {servidor[0]}:{servidor[1]}...", TEXTO)
            self.entrada.desenhar(self.tela, self.fontes, time.monotonic())
            pygame.display.flip()
        try:
            transporte = criar_transporte(conexao, servidor)
        except OSError as erro:
            self.entrada.mostrar(f"não foi possível conectar a {servidor[0]}:{servidor[1]}: {erro}")
            return
        sessao = SessaoCliente(transporte, servidor, conexao.nome, registro=self.registro,
                               confiabilidade=conexao.confiabilidade)
        self.partida = PartidaCliente(conexao, sessao)
        self.entrada.mostrar("")
        pygame.key.stop_text_input()
        pygame.display.set_caption(f"Arena em Rede — {sessao.nome} ({conexao.transporte.upper()})")

    def _voltar_para_entrada(self) -> None:
        if self.partida is not None:
            self.partida.fechar()
            self.partida = None
        self.exibicao.ajuda = False
        pygame.display.set_caption("Arena em Rede")
        pygame.key.start_text_input()

    def fechar(self) -> None:
        if self.partida is not None:
            self.partida.fechar()
            self.partida = None
        if self._ao_pacote in self.registro.observadores:
            self.registro.observadores.remove(self._ao_pacote)
        pygame.quit()
