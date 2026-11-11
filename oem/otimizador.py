"""Otimização walk-forward: escolhe os parâmetros no período de treino e mede o
resultado num período de teste que o otimizador nunca viu."""
from __future__ import annotations

import itertools
from dataclasses import dataclass, replace
from typing import Callable

import numpy as np
import pandas as pd

from oem import estrategia, metricas
from oem.estrategia import Parametros

GRADE_PADRAO = {
    "janelas": [7, 14, 21, 30],
    "riscos": [1.0, 2.0, 3.0, 4.0, 5.0],
    "sensibilidades": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0],
    "compras": [0.3, 0.6, 0.9],
    "vendas": [0.1, 0.3, 0.6],
}
MIN_DIAS_POR_PERIODO = 60


@dataclass(frozen=True)
class Carteira:
    capital_inicial: float
    cripto_inicial: float
    aporte_mensal: float
    taxa: float


@dataclass
class ResultadoOtimizacao:
    grade: pd.DataFrame  # todas as combinações, com a nota de treino
    melhores: pd.DataFrame  # top N, com treino e teste lado a lado
    benchmark_teste: metricas.Metricas
    inicio_treino: pd.Timestamp
    fim_treino: pd.Timestamp
    inicio_teste: pd.Timestamp
    fim_teste: pd.Timestamp


def indice_corte(n: int, fracao_treino: float) -> int:
    corte = int(round(n * fracao_treino))
    if corte < MIN_DIAS_POR_PERIODO or n - corte < MIN_DIAS_POR_PERIODO:
        raise ValueError(
            f"Período curto demais: são necessários pelo menos {MIN_DIAS_POR_PERIODO} dias "
            "de treino e de teste. Aumente a janela histórica."
        )
    return corte


def _simular_trecho(df, momentum, inicio, fim, p: Parametros, carteira: Carteira):
    fatia = slice(inicio, fim)
    return estrategia.simular_estrategia(
        df["Mercado"].to_numpy()[fatia],
        df["OEM"].to_numpy()[fatia],
        df["Z_Score"].to_numpy()[fatia],
        momentum[fatia],
        estrategia.chaves_mes(df.index)[fatia],
        p,
        carteira.capital_inicial,
        carteira.cripto_inicial,
        carteira.aporte_mensal,
        carteira.taxa,
    )


def otimizar_walk_forward(
    df: pd.DataFrame,
    fracao_treino: float,
    carteira: Carteira,
    base: Parametros,
    grade: dict | None = None,
    top_n: int = 5,
    progresso: Callable[[float], None] | None = None,
) -> ResultadoOtimizacao:
    """Para cada combinação (risco, modulador, tetos), roda o treino com todas as
    janelas de momentum; a nota é a média do Sortino entre as janelas (consenso)
    e a janela aplicada é a melhor delas. Só o período de treino entra na nota.
    As `top_n` melhores são então simuladas no período de teste."""
    grade = grade or GRADE_PADRAO
    n = len(df)
    corte = indice_corte(n, fracao_treino)
    momentum = {j: estrategia.calcular_momentum(df["Mercado"], j) for j in grade["janelas"]}

    combinacoes = list(itertools.product(grade["riscos"], grade["sensibilidades"], grade["compras"], grade["vendas"]))
    linhas = []
    for k, (risco, sens, compra, venda) in enumerate(combinacoes):
        sortinos = []
        for janela in grade["janelas"]:
            p = replace(base, risco=risco, sensibilidade=sens, max_compra=compra, max_venda=venda, janela_momentum=janela)
            resultado = _simular_trecho(df, momentum[janela], 0, corte, p, carteira)
            sortinos.append(metricas.calcular_metricas(resultado).sortino)
        linhas.append(
            {
                "Janela Resiliente (Dias)": int(grade["janelas"][int(np.argmax(sortinos))]),
                "Agressividade Base": float(risco),
                "Força do Modulador": float(sens),
                "Teto Compra (%)": float(compra),
                "Teto Venda (%)": float(venda),
                "Sortino Treino (Consenso)": float(np.mean(sortinos)),
            }
        )
        if progresso and (k % 10 == 0 or k == len(combinacoes) - 1):
            progresso((k + 1) / len(combinacoes))

    tabela = (
        pd.DataFrame(linhas)
        .sort_values("Sortino Treino (Consenso)", ascending=False, kind="stable")
        .reset_index(drop=True)
    )

    melhores = tabela.head(top_n).copy()
    resultados_teste = []
    for _, linha in melhores.iterrows():
        p = parametros_da_linha(linha, base)
        m = metricas.calcular_metricas(_simular_trecho(df, momentum[p.janela_momentum], corte, n, p, carteira))
        resultados_teste.append(
            {"Sortino Teste": m.sortino, "Retorno TWR Teste": m.retorno_twr, "Drawdown Teste": m.drawdown_maximo}
        )
    melhores = pd.concat([melhores, pd.DataFrame(resultados_teste, index=melhores.index)], axis=1)

    precos_teste = df["Mercado"].to_numpy()[corte:]
    benchmark = metricas.calcular_metricas(
        estrategia.simular_dca(
            precos_teste,
            estrategia.chaves_mes(df.index)[corte:],
            carteira.capital_inicial,
            carteira.cripto_inicial,
            carteira.aporte_mensal,
            carteira.taxa,
        )
    )
    return ResultadoOtimizacao(
        grade=tabela,
        melhores=melhores,
        benchmark_teste=benchmark,
        inicio_treino=df.index[0],
        fim_treino=df.index[corte - 1],
        inicio_teste=df.index[corte],
        fim_teste=df.index[-1],
    )


def parametros_da_linha(linha: pd.Series, base: Parametros) -> Parametros:
    return replace(
        base,
        janela_momentum=int(linha["Janela Resiliente (Dias)"]),
        risco=float(linha["Agressividade Base"]),
        sensibilidade=float(linha["Força do Modulador"]),
        max_compra=float(linha["Teto Compra (%)"]),
        max_venda=float(linha["Teto Venda (%)"]),
    )
