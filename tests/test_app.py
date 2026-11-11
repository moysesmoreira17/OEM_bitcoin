"""Roda o app inteiro (Streamlit AppTest) com as fontes de dados simuladas."""
import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from oem import carregamento, dados
from tests.conftest import RAIZ, series_sinteticas

ABAS = ["Monitoramento Live", "🧪 Backtest Histórico", "🔥 Otimizador Walk-Forward", "🧠 IA Auto-Tuning (MLP Neural)"]


@pytest.fixture
def fontes_simuladas(monkeypatch):
    hoje = pd.Timestamp.now().normalize()
    series = series_sinteticas(inicio=hoje - pd.Timedelta(days=1599), dias=1600)
    falhar: set[str] = set()

    def buscar(coluna, simbolo, inicio, chave):
        if coluna in falhar:
            raise dados.ErroFonteDados(f"{coluna}: fora do ar")
        return series[coluna]

    monkeypatch.setattr(dados, "buscar_fonte", buscar)
    monkeypatch.setattr(dados, "preco_atual_binance", lambda simbolo: float(series["Preco"].iloc[-1]) * 1.01)
    monkeypatch.setattr(dados, "ultimo_fechamento_yahoo", lambda ticker: {"BRL=X": 5.5, "DX-Y.NYB": 101.0}.get(ticker, 1.0))
    st.cache_data.clear()
    carregamento.limpar_falhas()
    yield falhar
    st.cache_data.clear()
    carregamento.limpar_falhas()


def _app(**estado) -> AppTest:
    at = AppTest.from_file(str(RAIZ / "app_oem.py"), default_timeout=300)
    at.secrets["FRED_API_KEY"] = "teste"
    at.session_state["autenticado"] = True
    for chave, valor in estado.items():
        at.session_state[chave] = valor
    return at


def test_login_continua_exigido(fontes_simuladas):
    at = AppTest.from_file(str(RAIZ / "app_oem.py"), default_timeout=60).run()
    assert not at.exception
    assert at.title[0].value == "🔐 Terminal OEM"
    assert not at.sidebar.radio  # nada do terminal aparece antes do login


@pytest.mark.parametrize("ativo", ["Bitcoin (BTC)", "Ethereum (ETH)"])
def test_todas_as_abas_rodam(fontes_simuladas, ativo):
    at = _app().run()
    at.sidebar.selectbox[0].set_value(ativo).run()
    for aba in ABAS:
        at.sidebar.radio[0].set_value(aba).run()
        assert not at.exception, (aba, at.exception)
        assert not at.error, (aba, [e.value for e in at.error])


def test_monitoramento_mostra_decisao(fontes_simuladas):
    at = _app().run()
    assert not at.exception
    rotulos = [m.label for m in at.metric]
    assert "Preço Justo OEM (BRL)" in rotulos and "Z-Score de Preço (365d)" in rotulos
    assert any("Ação:" in m.value for m in at.markdown)


def test_fonte_obrigatoria_fora_do_ar_para_o_app(fontes_simuladas):
    fontes_simuladas.add("BRL")
    at = _app().run()
    assert not at.exception
    assert any("fontes obrigatórias falharam" in e.value for e in at.error)
    assert not at.metric  # nenhum número com valor inventado


def test_fonte_opcional_fora_do_ar_so_avisa(fontes_simuladas):
    fontes_simuladas.add("NDX")
    at = _app().run()
    assert not at.exception and not at.error
    assert any("Fontes opcionais indisponíveis" in w.value for w in at.warning)


def test_otimizador_aplica_parametros(fontes_simuladas):
    at = _app().run()
    at.sidebar.radio[0].set_value("🔥 Otimizador Walk-Forward").run()
    [b for b in at.button if "Processar" in b.label][0].click().run()
    assert not at.exception
    assert at.dataframe, "tabela do top 5 não apareceu"
    [b for b in at.button if "Aplicar" in b.label][0].click().run()
    assert not at.exception
    assert at.session_state["opt_treino"][0] == "BTC"

    at.sidebar.radio[0].set_value("🧪 Backtest Histórico").run()
    assert not at.exception
    assert any("Treino do otimizador" in i.value or "dentro da amostra" in i.value for i in at.info)


def test_previsao_neural(fontes_simuladas):
    at = _app().run()
    at.sidebar.radio[0].set_value("🧠 IA Auto-Tuning (MLP Neural)").run()
    [b for b in at.button if "Projeção Neural" in b.label][0].click().run()
    assert not at.exception
    assert any("Escolhido na validação" in s.value for s in at.success)
    assert [m.label for m in at.metric][:1] == ["Acerto de direção (teste)"]


def test_fonte_que_falhou_espera_antes_de_tentar_de_novo(fontes_simuladas, monkeypatch):
    chamadas = []
    original = dados.buscar_fonte

    def contar(coluna, *args):
        chamadas.append(coluna)
        return original(coluna, *args)

    monkeypatch.setattr(dados, "buscar_fonte", contar)
    fontes_simuladas.add("BRL")
    at = _app().run()
    assert chamadas.count("BRL") == 1
    at.run()  # nova interação: não tenta o BRL de novo, nem rebusca o que deu certo
    assert chamadas.count("BRL") == 1 and chamadas.count("Preco") == 1

    fontes_simuladas.discard("BRL")
    [b for b in at.button if "Tentar novamente" in b.label][0].click().run()
    assert chamadas.count("BRL") == 2
    assert not at.exception and not at.error and at.metric
