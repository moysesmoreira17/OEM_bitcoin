"""Regra de decisão única da estratégia OEM e simulação histórica.

A mesma função `decidir_acao` é usada pelo painel live, pelo backtest e pelo
otimizador: o que é testado é exatamente o que roda ao vivo.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple, Sequence

import numpy as np
import pandas as pd

SATURACAO = "SATURACAO"
COMPRA = "COMPRA"
VENDA = "VENDA"
DCA = "DCA"


@dataclass(frozen=True)
class Parametros:
    risco: float = 3.0  # agressividade dinâmica base
    sensibilidade: float = 5.0  # força do modulador de momentum
    max_compra: float = 0.90  # fração máxima do caixa por compra
    max_venda: float = 0.10  # fração máxima do saldo cripto por venda
    z_limite: float = 4.0  # Z-Score de preço que dispara a venda de saturação
    janela_momentum: int = 14  # dias usados no momentum
    limiar_compra: float = 0.02  # desconto mínimo frente ao valor justo para comprar
    limiar_venda: float = -0.10  # prêmio a partir do qual vende
    dca_passivo: float = 0.01  # fração do caixa comprada na zona neutra
    caixa_minimo: float = 30.0  # abaixo disso não há compra (ordem mínima)


class Decisao(NamedTuple):
    regime: str
    frac_compra: float = 0.0  # fração do caixa a comprar
    frac_venda: float = 0.0  # fração do saldo cripto a vender


def _limitar(valor: float, minimo: float, maximo: float) -> float:
    return minimo if valor < minimo else maximo if valor > maximo else valor


def calcular_delta(valor_justo: float, preco: float) -> float:
    """Desconto (+) ou prêmio (-) do preço frente ao valor justo."""
    return (valor_justo - preco) / valor_justo


def decidir_acao(delta: float, momentum: float, z_score: float, caixa: float, saldo_cripto: float, p: Parametros) -> Decisao:
    if z_score >= p.z_limite:
        return Decisao(SATURACAO, frac_venda=p.max_venda if saldo_cripto > 0 else 0.0)

    if delta > p.limiar_compra:
        modulador = _limitar(1 - momentum * p.sensibilidade, 0.2, 2.0)
        fracao = min(p.max_compra, delta * (p.risco / 2) * modulador)
        return Decisao(COMPRA, frac_compra=fracao if caixa > p.caixa_minimo else 0.0)

    if delta <= p.limiar_venda:
        modulador = _limitar(1 + momentum * p.sensibilidade, 0.2, 2.0)
        fracao = min(p.max_venda, abs(delta) * (p.risco / 2) * modulador)
        return Decisao(VENDA, frac_venda=fracao if saldo_cripto > 0 else 0.0)

    return Decisao(DCA, frac_compra=p.dca_passivo if caixa > p.caixa_minimo else 0.0)


def calcular_momentum(precos: pd.Series, janela: int) -> np.ndarray:
    """Variação percentual em `janela` dias (só usa o passado)."""
    return precos.pct_change(periods=janela).fillna(0.0).to_numpy(dtype=float)


def chaves_mes(datas: pd.DatetimeIndex | pd.Series) -> np.ndarray:
    datas = pd.DatetimeIndex(datas)
    return (datas.year * 12 + datas.month).to_numpy()


@dataclass
class ResultadoSimulacao:
    patrimonio: np.ndarray
    fluxos: np.ndarray  # aportes externos de cada dia (já incluídos no patrimônio)
    caixa: np.ndarray
    valor_cripto: np.ndarray
    caixa_final: float
    cripto_final: float
    total_investido: float


def simular_estrategia(
    precos: Sequence[float],
    valores_justos: Sequence[float],
    z_scores: Sequence[float],
    momentum: Sequence[float],
    meses: Sequence[int],
    p: Parametros,
    capital_inicial: float,
    cripto_inicial: float,
    aporte_mensal: float,
    taxa: float,
) -> ResultadoSimulacao:
    precos = np.asarray(precos, dtype=float).tolist()
    justos = np.asarray(valores_justos, dtype=float).tolist()
    zs = np.asarray(z_scores, dtype=float).tolist()
    moms = np.asarray(momentum, dtype=float).tolist()
    meses = np.asarray(meses).tolist()
    n = len(precos)

    caixa, cripto = float(capital_inicial), float(cripto_inicial)
    total_investido = capital_inicial + cripto_inicial * precos[0]
    mes_anterior = meses[0]
    patrimonio, fluxos, hist_caixa, hist_cripto = [0.0] * n, [0.0] * n, [0.0] * n, [0.0] * n

    for i in range(n):
        preco = precos[i]
        if meses[i] != mes_anterior:
            caixa += aporte_mensal
            total_investido += aporte_mensal
            fluxos[i] = aporte_mensal
            mes_anterior = meses[i]

        decisao = decidir_acao(calcular_delta(justos[i], preco), moms[i], zs[i], caixa, cripto, p)
        if decisao.frac_venda > 0:
            quantidade = cripto * decisao.frac_venda
            caixa += quantidade * preco * (1 - taxa)
            cripto -= quantidade
        elif decisao.frac_compra > 0:
            valor = caixa * decisao.frac_compra
            cripto += valor * (1 - taxa) / preco
            caixa -= valor

        hist_caixa[i] = caixa
        hist_cripto[i] = cripto * preco
        patrimonio[i] = caixa + cripto * preco

    return ResultadoSimulacao(
        patrimonio=np.array(patrimonio),
        fluxos=np.array(fluxos),
        caixa=np.array(hist_caixa),
        valor_cripto=np.array(hist_cripto),
        caixa_final=caixa,
        cripto_final=cripto,
        total_investido=total_investido,
    )


def simular_dca(
    precos: Sequence[float],
    meses: Sequence[int],
    capital_inicial: float,
    cripto_inicial: float,
    aporte_mensal: float,
    taxa: float,
) -> ResultadoSimulacao:
    """Benchmark: compra tudo no início e todo aporte mensal no dia em que entra."""
    precos = np.asarray(precos, dtype=float)
    meses = np.asarray(meses)
    n = len(precos)
    fluxos = np.zeros(n)
    novos_meses = np.r_[False, meses[1:] != meses[:-1]]
    fluxos[novos_meses] = aporte_mensal

    compras = fluxos * (1 - taxa) / precos
    compras[0] += capital_inicial * (1 - taxa) / precos[0]
    quantidade = cripto_inicial + np.cumsum(compras)
    patrimonio = quantidade * precos
    total_investido = capital_inicial + cripto_inicial * precos[0] + fluxos.sum()
    return ResultadoSimulacao(
        patrimonio=patrimonio,
        fluxos=fluxos,
        caixa=np.zeros(n),
        valor_cripto=patrimonio,
        caixa_final=0.0,
        cripto_final=float(quantidade[-1]),
        total_investido=float(total_investido),
    )
