import numpy as np
import pandas as pd
import pytest

from oem import modelo, otimizador, previsao
from oem.config import ATIVOS
from oem.estrategia import Parametros

BTC = ATIVOS["Bitcoin (BTC)"]
GRADE_PEQUENA = {"janelas": [7, 14], "riscos": [1.0, 3.0], "sensibilidades": [1.0, 5.0], "compras": [0.3, 0.9], "vendas": [0.1, 0.6]}
CARTEIRA = otimizador.Carteira(5000.0, 0.0, 1000.0, 0.001)


@pytest.fixture
def df(base):
    return modelo.calcular_oem(base, BTC, calibrar_alpha=False).iloc[400:]


def test_otimizador_nao_usa_o_periodo_de_teste(df):
    a = otimizador.otimizar_walk_forward(df, 0.7, CARTEIRA, Parametros(), grade=GRADE_PEQUENA)
    corte = otimizador.indice_corte(len(df), 0.7)
    alterado = df.copy()
    alterado.iloc[corte:, alterado.columns.get_loc("Mercado")] *= 3  # muda só o teste
    b = otimizador.otimizar_walk_forward(alterado, 0.7, CARTEIRA, Parametros(), grade=GRADE_PEQUENA)

    pd.testing.assert_frame_equal(a.grade, b.grade)  # ranking idêntico
    assert not np.allclose(a.melhores["Retorno TWR Teste"], b.melhores["Retorno TWR Teste"])
    assert a.fim_treino < a.inicio_teste
    assert len(a.melhores) == 5


def test_otimizador_recusa_periodo_curto(df):
    with pytest.raises(ValueError, match="curto"):
        otimizador.otimizar_walk_forward(df.iloc[:100], 0.7, CARTEIRA, Parametros(), grade=GRADE_PEQUENA)


def test_indices_de_treino_respeitam_o_horizonte():
    validos = np.ones(100, dtype=bool)
    idx = previsao.indices_treino(validos, origem=50, horizonte=7)
    assert idx.max() == 43  # alvo de 43 termina em 50, já conhecido


def test_previsao_na_origem_nao_depende_do_futuro(df):
    memoria, horizonte = 7, 3
    X = previsao.montar_features(df, memoria).to_numpy(dtype=float)
    y = previsao.montar_alvo(df, horizonte).to_numpy(dtype=float)
    origens = np.arange(500, 510)
    a = previsao.previsoes_rolling_origin(X, y, origens, horizonte)

    futuro = df.copy()
    futuro.iloc[510:, futuro.columns.get_loc("Mercado")] *= 2
    Xf = previsao.montar_features(futuro, memoria).to_numpy(dtype=float)
    yf = previsao.montar_alvo(futuro, horizonte).to_numpy(dtype=float)
    b = previsao.previsoes_rolling_origin(Xf, yf, origens, horizonte)
    np.testing.assert_allclose(a["previsto"], b["previsto"])


def test_avaliacao_compara_com_referencias():
    prev = pd.DataFrame({"previsto": [0.1, -0.1, 0.1, 0.1], "real": [0.2, -0.2, -0.1, 0.1], "referencia_direcao": [1.0] * 4})
    av = previsao.avaliar(prev)
    assert av.acerto_modelo == pytest.approx(0.75)
    assert av.acerto_referencia == pytest.approx(0.5)  # "sempre alta" acerta 2 de 4
    assert av.n == 4


def test_projetar_ponta_a_ponta(df):
    res = previsao.projetar(df)
    assert res.memoria in previsao.OPCOES_MEMORIA and res.horizonte in previsao.OPCOES_HORIZONTE
    assert res.teste.n == previsao.DIAS_TESTE
    assert res.preco_inferior <= res.preco_previsto <= res.preco_superior
    assert len(res.candidatos) == len(previsao.OPCOES_MEMORIA) * len(previsao.OPCOES_HORIZONTE)


def test_projetar_exige_historico(df):
    with pytest.raises(ValueError, match="pelo menos"):
        previsao.projetar(df.iloc[:200])
