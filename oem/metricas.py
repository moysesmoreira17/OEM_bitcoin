"""Métricas de desempenho e correlações."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from oem.estrategia import ResultadoSimulacao

DIAS_ANO = 365  # cripto negocia todos os dias
EPSILON = 1e-12  # desvios menores que isso são ruído de ponto flutuante


def retornos_twr(patrimonio: np.ndarray, fluxos: np.ndarray) -> np.ndarray:
    """Retornos diários ponderados no tempo: os aportes são descontados, então
    dinheiro novo entrando na carteira não é contado como lucro."""
    patrimonio = np.asarray(patrimonio, dtype=float)
    fluxos = np.asarray(fluxos, dtype=float)
    anterior, atual, fluxo = patrimonio[:-1], patrimonio[1:], fluxos[1:]
    validos = anterior > 0
    return (atual[validos] - fluxo[validos]) / anterior[validos] - 1


def sharpe(retornos: np.ndarray, dias_ano: int = DIAS_ANO) -> float:
    if len(retornos) < 2:
        return 0.0
    desvio = np.std(retornos, ddof=1)
    return float(np.mean(retornos) / desvio * np.sqrt(dias_ano)) if desvio > EPSILON else 0.0


def sortino(retornos: np.ndarray, dias_ano: int = DIAS_ANO) -> float:
    """Sortino com desvio de perdas (downside deviation) sobre todos os dias."""
    if len(retornos) < 2:
        return 0.0
    desvio_perdas = np.sqrt(np.mean(np.minimum(retornos, 0.0) ** 2))
    return float(np.mean(retornos) / desvio_perdas * np.sqrt(dias_ano)) if desvio_perdas > EPSILON else 0.0


def indice_twr(retornos: np.ndarray) -> np.ndarray:
    """Evolução de R$ 1 aplicado no início, sem aportes."""
    return np.concatenate([[1.0], np.cumprod(1 + np.asarray(retornos, dtype=float))])


def drawdown_maximo(retornos: np.ndarray) -> float:
    indice = indice_twr(retornos)
    return float((indice / np.maximum.accumulate(indice) - 1).min())


@dataclass(frozen=True)
class Metricas:
    retorno_sobre_investido: float  # (patrimônio final - total investido) / total investido
    retorno_twr: float  # retorno da estratégia descontando aportes
    sharpe: float
    sortino: float
    drawdown_maximo: float


def calcular_metricas(resultado: ResultadoSimulacao) -> Metricas:
    retornos = retornos_twr(resultado.patrimonio, resultado.fluxos)
    investido = resultado.total_investido
    final = float(resultado.patrimonio[-1])
    return Metricas(
        retorno_sobre_investido=(final - investido) / investido if investido > 0 else 0.0,
        retorno_twr=float(indice_twr(retornos)[-1] - 1),
        sharpe=sharpe(retornos),
        sortino=sortino(retornos),
        drawdown_maximo=drawdown_maximo(retornos) if len(retornos) else 0.0,
    )


def correlacao_retornos_semanais(df: pd.DataFrame, coluna_a: str, coluna_b: str) -> float:
    """Correlação dos retornos semanais (log). Correlacionar níveis de preço dá
    valores altos só porque as duas séries têm tendência."""
    if coluna_a not in df or coluna_b not in df:
        return float("nan")
    semanal = df[[coluna_a, coluna_b]].resample("W-FRI").last()
    retornos = np.log(semanal).diff().dropna()
    if len(retornos) < 8:
        return float("nan")
    return float(retornos[coluna_a].corr(retornos[coluna_b]))
