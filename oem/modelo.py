"""Modelo de valor justo OEM (vetorizado)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from oem.config import DATA_HALVING_BTC, DATA_PICO_EXCHANGES, ParametrosAtivo

MIN_DIAS_CALIBRACAO = 180


def _dias_desde(indice: pd.DatetimeIndex, data) -> np.ndarray:
    return ((indice - pd.Timestamp(data)) / pd.Timedelta(days=1)).to_numpy(dtype=float)


def valor_bruto_usd(base: pd.DataFrame, ativo: ParametrosAtivo) -> pd.Series:
    """Valor justo OEM em USD sem o ALPHA.

    P = ALPHA × (Liquidez / (Juro + DELTA)) × Ciclo × Dificuldade × Escassez × (100 / DXY)
    """
    indice = base.index
    anos_genesis = np.maximum(0.1, _dias_desde(indice, ativo.data_genesis) / 365.25)

    m2_global = base["M2"].to_numpy(dtype=float) / 1000 * 4.8
    penetracao = 0.05 / (1 + np.exp(-0.4 * (anos_genesis - 10)))  # curva logística de adoção
    liquidez = m2_global * penetracao * 100
    denominador = np.maximum(0.1, base["Juro"].to_numpy(dtype=float) + ativo.delta)
    fator_dxy = 100.0 / np.maximum(50.0, base["DXY"].to_numpy(dtype=float))
    fator_escassez = 1 + ativo.taxa_escassez * np.maximum(0.0, _dias_desde(indice, DATA_PICO_EXCHANGES) / 365.25)

    bruto = liquidez / denominador * fator_escassez * fator_dxy

    if ativo.usa_ciclo_halving:
        meses_halving = _dias_desde(indice, DATA_HALVING_BTC) / 30.44
        amortecimento = 1 + np.log10(np.maximum(1.0, anos_genesis / 4))
        bruto = bruto * (1 + (ativo.beta / amortecimento) * np.cos(2 * np.pi * meses_halving / 48))

    if ativo.usa_dificuldade:
        bruto = bruto * base["Diff"].to_numpy(dtype=float)

    return pd.Series(bruto, index=indice, name="OEM_bruto_USD")


def alpha_sem_olhar_futuro(preco_usd: pd.Series, bruto_usd: pd.Series, min_dias: int = MIN_DIAS_CALIBRACAO) -> pd.Series:
    """ALPHA calibrado em cada dia só com os dias anteriores.

    ALPHA_t = exp(mediana de log(preço / bruto) do início dos dados até t-1).
    Usar a mediana em log deixa a calibração pouco sensível a bolhas e crashes.
    """
    log_razao = np.log(preco_usd / bruto_usd)
    return np.exp(log_razao.expanding(min_periods=min_dias).median().shift(1)).rename("Alpha")


def zscore_preco(preco: pd.Series, janela: int = 365, min_periodos: int = 30) -> pd.Series:
    """Distância do preço até a média de 365 dias, em desvios-padrão.
    (Não é o MVRV: o MVRV usa o "realized cap" on-chain.)"""
    media = preco.rolling(window=janela, min_periods=min_periodos).mean()
    desvio = preco.rolling(window=janela, min_periods=min_periodos).std()
    return ((preco - media) / desvio).fillna(0.0).rename("Z_Score")


def calcular_oem(base: pd.DataFrame, ativo: ParametrosAtivo, calibrar_alpha: bool) -> pd.DataFrame:
    """Acrescenta à base: Alpha, OEM_USD, OEM (BRL), Mercado (BRL) e Z_Score."""
    df = base.copy()
    bruto = valor_bruto_usd(df, ativo)
    if calibrar_alpha or ativo.alpha_fixo is None:
        df["Alpha"] = alpha_sem_olhar_futuro(df["Preco"], bruto)
    else:
        df["Alpha"] = float(ativo.alpha_fixo)
    df["OEM_USD"] = df["Alpha"] * bruto
    df["Mercado"] = df["Preco"] * df["BRL"]
    df["OEM"] = df["OEM_USD"] * df["BRL"]
    df["Z_Score"] = zscore_preco(df["Preco"])
    df["1_DXY"] = 1 / df["DXY"]
    if "USD_CNY" in df:
        df["CNY_USD"] = 1 / df["USD_CNY"]
    return df


def recortar_janela(df: pd.DataFrame, inicio) -> pd.DataFrame:
    """Recorta a janela exibida, descartando dias sem valor justo (ALPHA ainda
    sem histórico suficiente)."""
    return df.loc[df.index >= pd.Timestamp(inicio)].dropna(subset=["OEM"])


def oem_com_dxy_atual(oem_usd: float, dxy_historico: float, dxy_atual: float) -> float:
    """Reaplica o fator DXY com a cotação ao vivo."""
    return oem_usd * (max(50.0, dxy_historico) / max(50.0, dxy_atual))
