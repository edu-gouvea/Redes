"""Cliente com servidor real: previsão contra o servidor de verdade e a janela pygame sem tela."""

import threading
import time

import pytest

import config
from cliente.previsao import Previsor
from protocolo.registro import RegistroPacotes
from protocolo.sessao import SessaoCliente, Situacao
from protocolo.transporte import TransporteUDP
from servidor.jogo import Partida
from servidor.servidor import Servidor


@pytest.fixture
def servidor_real():
    transporte = TransporteUDP(("127.0.0.1", 0))
    servidor = Servidor(transporte, Partida(semente=3))
    thread = threading.Thread(target=servidor.executar, daemon=True)
    thread.start()
    yield servidor, transporte.endereco_local
    servidor.parar()
    thread.join(timeout=2)
    transporte.fechar()


def test_sessao_avisa_cada_comando_enviado_com_o_seq(servidor_real):
    _, endereco = servidor_real
    sessao = SessaoCliente(TransporteUDP.cliente(), endereco, "ana")
    vistos = []
    sessao.observadores_comando.append(lambda seq, dx, dy, agora: vistos.append((seq, dx, dy)))
    try:
        sessao.definir_direcao(1, -1)
        limite = time.monotonic() + 3
        while len(vistos) < 3 and time.monotonic() < limite:
            sessao.processar(timeout=0.005)
        assert len(vistos) >= 3
        assert all((dx, dy) == (1, -1) for _, dx, dy in vistos)
        seqs = [seq for seq, _, _ in vistos]
        assert seqs == sorted(set(seqs))
    finally:
        sessao.sair()
        sessao.transporte.fechar()


def test_previsao_coincide_com_o_servidor_em_rede_perfeita(servidor_real):
    """Sem perda nem jitter, a previsão acerta: nenhuma correção em 1,5 s de movimento."""
    _, endereco = servidor_real
    sessao = SessaoCliente(TransporteUDP.cliente(), endereco, "ana")
    previsor = None
    sessao.observadores_comando.append(
        lambda seq, dx, dy, agora: previsor and previsor.ao_enviar_comando(seq, dx, dy, agora))
    vistos = 0
    try:
        inicio = time.monotonic()
        while time.monotonic() - inicio < 2.0:
            agora = time.monotonic()
            # Muda de direção a cada 0,4 s, inclusive na diagonal.
            sessao.definir_direcao(*[(1, 0), (1, 1), (0, -1), (-1, 0), (-1, 1)][int((agora - inicio) / 0.4) % 5])
            sessao.processar(timeout=0.005)
            if sessao.parametros and previsor is None:
                previsor = Previsor(sessao.parametros, 1 / config.TAXA_COMANDOS)
            if sessao.estados_recebidos != vistos and sessao.meu_jogador():
                vistos = sessao.estados_recebidos
                eu = sessao.meu_jogador()
                previsor.ao_receber_estado(eu["x"], eu["y"], sessao.estado["ultimo_comando"], agora)
        assert vistos >= 25
        assert previsor.maior_correcao <= 1.0  # no máximo o arredondamento de x e y
    finally:
        sessao.sair()
        sessao.transporte.fechar()


# --- Janela pygame, sem tela (SDL "dummy") -----------------------------------

@pytest.fixture
def pygame_sem_tela(monkeypatch):
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    monkeypatch.setenv("SDL_AUDIODRIVER", "dummy")
    return pytest.importorskip("pygame")


def test_janela_entra_joga_e_sai(servidor_real, pygame_sem_tela):
    from cliente.jogo import Aplicacao, Conexao

    servidor, endereco = servidor_real
    registro = RegistroPacotes()
    app = Aplicacao(Conexao(nome="ana", servidor=f"127.0.0.1:{endereco[1]}"), registro, entrar_direto=True)
    app._direcao = lambda: (1, 0)
    try:
        limite = time.monotonic() + 3
        while time.monotonic() < limite and app.partida.sessao.estados_recebidos < 10:
            app.passo()
            time.sleep(0.01)
        partida = app.partida
        assert partida.sessao.situacao is Situacao.EM_JOGO
        assert partida.previsor.pronto and partida.interpolador is not None
        assert partida.log, "o observador do registro alimenta o log na tela"
        for modo in (1, 2):  # desenha com log, fantasma e ajuda sem erro
            app.exibicao.modo_log, app.exibicao.fantasma, app.exibicao.ajuda = modo, True, True
            app.passo()
    finally:
        app.fechar()
    # O fechamento manda SAIR: o servidor tira o jogador na hora, sem esperar a inatividade.
    limite = time.monotonic() + 2
    while servidor.partida.jogadores and time.monotonic() < limite:
        time.sleep(0.02)
    assert not servidor.partida.jogadores


def test_tela_de_entrada_respeita_o_limite_do_nome(pygame_sem_tela):
    pygame = pygame_sem_tela
    from cliente.jogo import Conexao, TelaEntrada

    tela = TelaEntrada(Conexao())
    for letra in "guilherme_sousa":
        tela.ao_evento(pygame.event.Event(pygame.TEXTINPUT, text=letra))
    assert tela.conexao.nome == "guilherme_so"
    assert len(tela.conexao.nome) == config.TAMANHO_MAXIMO_NOME
    assert "12" in tela.mensagem


def test_validacao_da_tela_de_entrada(pygame_sem_tela):
    from cliente.jogo import Conexao, validar_nome

    assert validar_nome("  ana ") == "ana"
    for invalido in ("", "   ", "a\nb", "x" * (config.TAMANHO_MAXIMO_NOME + 1)):
        with pytest.raises(ValueError):
            validar_nome(invalido)
    assert Conexao(servidor="10.0.0.2:6000").endereco() == ("10.0.0.2", 6000)
    assert Conexao(servidor=":5005").endereco() == ("127.0.0.1", 5005)
    for invalido in ("10.0.0.2", "host:abc", "host:70000"):
        with pytest.raises(ValueError):
            Conexao(servidor=invalido).endereco()


def test_cliente_sem_pygame_explica_como_instalar(monkeypatch, capsys):
    import builtins

    import cliente.__main__ as principal

    original = builtins.__import__

    def sem_pygame(nome, *args, **kwargs):
        if nome == "cliente.jogo":
            raise ModuleNotFoundError("No module named 'pygame'", name="pygame")
        return original(nome, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", sem_pygame)
    assert principal.main(["--nome", "ana"]) == 1
    assert "pip install" in capsys.readouterr().out
