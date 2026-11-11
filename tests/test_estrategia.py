import numpy as np
import pandas as pd
import pytest

from oem import estrategia, metricas
from oem.estrategia import COMPRA, DCA, SATURACAO, VENDA, Parametros, decidir_acao

P = Parametros()


def test_regimes_basicos():
    assert decidir_acao(0.20, 0.0, 0.0, 1000, 1, P).regime == COMPRA
    assert decidir_acao(0.0, 0.0, 0.0, 1000, 1, P).regime == DCA
    assert decidir_acao(-0.10, 0.0, 0.0, 1000, 1, P).regime == VENDA  # limite inclusivo
    assert decidir_acao(-0.09, 0.0, 0.0, 1000, 1, P).regime == DCA
    assert decidir_acao(0.5, 0.0, P.z_limite, 1000, 1, P).regime == SATURACAO


def test_saturacao_nunca_compra_mesmo_sem_saldo():
    d = decidir_acao(0.5, 0.0, P.z_limite + 1, caixa=1000, saldo_cripto=0, p=P)
    assert d == (SATURACAO, 0.0, 0.0)


def test_compra_respeita_teto_e_caixa_minimo():
    d = decidir_acao(0.9, -1.0, 0.0, 1000, 0, P)
    assert d.frac_compra == pytest.approx(P.max_compra)
    assert decidir_acao(0.9, 0.0, 0.0, P.caixa_minimo, 0, P).frac_compra == 0.0
    assert decidir_acao(0.0, 0.0, 0.0, P.caixa_minimo, 0, P).frac_compra == 0.0


def test_formula_de_compra_e_venda():
    d = decidir_acao(0.10, 0.02, 0.0, 1000, 1, P)
    assert d.frac_compra == pytest.approx(0.10 * (P.risco / 2) * (1 - 0.02 * P.sensibilidade))
    v = decidir_acao(-0.20, 0.02, 0.0, 1000, 1, P)
    assert v.frac_venda == pytest.approx(min(P.max_venda, 0.20 * (P.risco / 2) * (1 + 0.02 * P.sensibilidade)))


def _simular(precos, justos, aporte=1000.0, capital=5000.0):
    datas = pd.date_range("2024-01-01", periods=len(precos))
    return estrategia.simular_estrategia(
        precos, justos, np.zeros(len(precos)), np.zeros(len(precos)), estrategia.chaves_mes(datas),
        P, capital, 0.0, aporte, 0.0,
    )


def test_aportes_nao_contam_como_retorno():
    # Preço constante e igual ao valor justo: nenhum ganho real é possível.
    precos = np.full(120, 100.0)
    res = _simular(precos, precos)
    assert res.fluxos.sum() == pytest.approx(3000.0)  # fev, mar, abr
    m = metricas.calcular_metricas(res)
    assert m.retorno_twr == pytest.approx(0.0, abs=1e-12)
    assert m.sharpe == 0.0 and m.drawdown_maximo == pytest.approx(0.0, abs=1e-12)
    assert m.retorno_sobre_investido == pytest.approx(0.0, abs=1e-12)


def test_twr_manual():
    r = metricas.retornos_twr(np.array([100.0, 1100.0, 1210.0]), np.array([0.0, 1000.0, 0.0]))
    np.testing.assert_allclose(r, [0.0, 0.10])


def test_drawdown():
    assert metricas.drawdown_maximo(np.array([0.1, -0.5, 0.2])) == pytest.approx(-0.5)


def test_sortino_sem_perdas_e_zero():
    assert metricas.sortino(np.array([0.01, 0.02, 0.0])) == 0.0


def test_dca_benchmark():
    precos = np.array([100.0, 100.0, 200.0])
    datas = pd.DatetimeIndex(["2024-01-30", "2024-01-31", "2024-02-01"])
    res = estrategia.simular_dca(precos, estrategia.chaves_mes(datas), 1000.0, 0.0, 500.0, 0.0)
    assert res.cripto_final == pytest.approx(10 + 2.5)
    assert res.total_investido == pytest.approx(1500.0)
    assert res.patrimonio[-1] == pytest.approx(12.5 * 200)


def test_correlacao_semanal_de_retornos():
    datas = pd.date_range("2023-01-01", periods=400)
    rng = np.random.default_rng(1)
    a = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 400)))
    b = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 400)))
    df = pd.DataFrame({"A": a, "B": b, "A2": a * 3}, index=datas)
    assert metricas.correlacao_retornos_semanais(df, "A", "A2") == pytest.approx(1.0)
    assert abs(metricas.correlacao_retornos_semanais(df, "A", "B")) < 0.5
    assert np.isnan(metricas.correlacao_retornos_semanais(df, "A", "X"))
