import math
from dataclasses import replace
from datetime import datetime

import numpy as np
import pandas as pd

from oem import modelo
from oem.config import ATIVOS

BTC = ATIVOS["Bitcoin (BTC)"]
ETH = ATIVOS["Ethereum (ETH)"]


def _formula_original(d: datetime, r: pd.Series) -> float:
    """Cópia linha a linha da fórmula do app antigo (BTC), para garantir que a
    versão vetorizada calcula exatamente a mesma coisa."""
    anos_g = max(0.1, (d - datetime(2009, 1, 3)).days / 365.25)
    fator_dxy = 100.0 / max(50.0, r["DXY"])
    m2_g = (r["M2"] / 1000) * 4.8
    penet = 0.05 / (1 + math.exp(-0.4 * (anos_g - 10)))
    liq_e = m2_g * penet * 100
    m_halv = (d - datetime(2024, 4, 19)).days / 30.44
    f_amort = 1 + math.log10(max(1, anos_g / 4))
    f_ciclo = 1 + ((0.18 / f_amort) * math.cos((2 * math.pi * m_halv) / 48))
    f_esc = 1 + (0.02 * max(0, (d - datetime(2020, 3, 12)).days / 365.25))
    den = max(0.1, r["Juro"] + 0.5)
    return 3.4 * (liq_e / den) * f_ciclo * r["Diff"] * f_esc * fator_dxy


def test_btc_vetorizado_igual_a_formula_original(base):
    df = modelo.calcular_oem(base, BTC, calibrar_alpha=False)
    for d in df.index[::97]:
        assert math.isclose(df.at[d, "OEM_USD"], _formula_original(d.to_pydatetime(), base.loc[d]), rel_tol=1e-12)


def test_eth_nao_usa_dificuldade_nem_halving(base):
    alterada = base.copy()
    alterada["Diff"] = alterada["Diff"] * 10
    a = modelo.valor_bruto_usd(base, ETH)
    b = modelo.valor_bruto_usd(alterada, ETH)
    pd.testing.assert_series_equal(a, b)
    # Sem ciclo: o bruto do ETH é só o núcleo macro.
    sem_ciclo = modelo.valor_bruto_usd(base, replace(BTC, usa_ciclo_halving=False, usa_dificuldade=False, data_genesis=ETH.data_genesis))
    pd.testing.assert_series_equal(a, sem_ciclo)


def test_alpha_calibrado_nao_olha_o_futuro(base):
    corte = base.index[700]
    alterada = base.copy()
    alterada.loc[alterada.index > corte, "Preco"] *= 5  # muda só o futuro
    a = modelo.calcular_oem(base, ETH, calibrar_alpha=True)
    b = modelo.calcular_oem(alterada, ETH, calibrar_alpha=True)
    ate_corte = a.index <= corte + pd.Timedelta(days=1)  # alpha de t usa até t-1
    pd.testing.assert_series_equal(a.loc[ate_corte, "Alpha"], b.loc[ate_corte, "Alpha"])
    assert a["Alpha"].iloc[: modelo.MIN_DIAS_CALIBRACAO].isna().all()


def test_recortar_janela_descarta_dias_sem_alpha(base):
    df = modelo.calcular_oem(base, ETH, calibrar_alpha=True)
    janela = modelo.recortar_janela(df, base.index[0])
    assert janela["OEM"].notna().all()
    assert len(janela) == len(df) - modelo.MIN_DIAS_CALIBRACAO


def test_zscore_preco_constante_e_zero():
    preco = pd.Series(100.0, index=pd.date_range("2024-01-01", periods=400))
    assert (modelo.zscore_preco(preco) == 0).all()


def test_oem_com_dxy_atual():
    assert math.isclose(modelo.oem_com_dxy_atual(1000.0, 100.0, 110.0), 1000.0 * 100 / 110)
    assert np.isclose(modelo.oem_com_dxy_atual(1000.0, 40.0, 45.0), 1000.0)  # piso de 50 nos dois
