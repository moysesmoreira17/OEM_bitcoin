"""Cache do Streamlit em volta das fontes de dados.

Cada fonte é guardada em cache separadamente e só quando dá certo: o
`st.cache_data` não guarda exceções, então um dado ruim nunca fica preso no
cache. Para não deixar o app lento enquanto uma fonte está fora do ar, depois
de uma falha a fonte só é tentada de novo após alguns minutos (ou quando o
usuário clica em "Tentar novamente").
"""
from __future__ import annotations

import time
from typing import Callable, TypeVar

import pandas as pd
import streamlit as st
from dateutil.relativedelta import relativedelta

from oem import config, dados
from oem.config import ParametrosAtivo

ESPERA_APOS_FALHA_S = 120
_falhas_recentes: dict[tuple, tuple[float, str]] = {}

T = TypeVar("T")


def _com_pausa_apos_falha(chave: tuple, buscar: Callable[[], T]) -> T:
    falha = _falhas_recentes.get(chave)
    if falha and time.monotonic() - falha[0] < ESPERA_APOS_FALHA_S:
        raise dados.ErroFonteDados(falha[1])
    try:
        resultado = buscar()
    except dados.ErroFonteDados as erro:
        _falhas_recentes[chave] = (time.monotonic(), str(erro))
        raise
    _falhas_recentes.pop(chave, None)
    return resultado


def limpar_falhas() -> None:
    _falhas_recentes.clear()


@st.cache_data(ttl=3600, show_spinner=False)
def _buscar_fonte_em_cache(coluna: str, simbolo: str | None, inicio: str, api_key_fred: str | None) -> pd.Series:
    return dados.buscar_fonte(coluna, simbolo, inicio, api_key_fred)


def _buscar_fonte(coluna: str, simbolo: str | None, inicio: str, api_key_fred: str | None) -> pd.Series:
    return _com_pausa_apos_falha(
        ("historico", coluna, simbolo, inicio), lambda: _buscar_fonte_em_cache(coluna, simbolo, inicio, api_key_fred)
    )


def inicio_busca(hoje: pd.Timestamp | None = None) -> str:
    """Data fixa de início da busca (independe do slider de meses)."""
    hoje = hoje or pd.Timestamp.now()
    inicio = hoje - relativedelta(months=config.MESES_MAXIMOS, days=config.DIAS_AQUECIMENTO)
    return inicio.strftime("%Y-%m-%d")


def carregar_base(ativo: ParametrosAtivo, api_key_fred: str | None):
    return dados.carregar_base(ativo, inicio_busca(), api_key_fred, buscar=_buscar_fonte)


@st.cache_data(ttl=60, show_spinner=False)
def _preco_live(simbolo: str) -> float:
    return dados.preco_atual_binance(simbolo)


@st.cache_data(ttl=60, show_spinner=False)
def _yahoo_live(ticker: str) -> float:
    return dados.ultimo_fechamento_yahoo(ticker)


def preco_live(simbolo: str) -> float | None:
    """Cotação ao vivo, ou None (o painel então usa o último fechamento)."""
    try:
        return _com_pausa_apos_falha(("live", simbolo), lambda: _preco_live(simbolo))
    except dados.ErroFonteDados:
        return None


def yahoo_live(coluna: str) -> float | None:
    ticker = dados.TICKERS_YAHOO[coluna]
    try:
        return _com_pausa_apos_falha(("live", ticker), lambda: _yahoo_live(ticker))
    except dados.ErroFonteDados:
        return None
