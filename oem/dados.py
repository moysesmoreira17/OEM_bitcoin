"""Coleta e alinhamento dos dados de mercado (sem dependência do Streamlit).

Regras adotadas:
- Nenhum valor "padrão" inventado: se uma fonte obrigatória falhar, a falha é
  informada e o cálculo não é feito com números falsos.
- Todas as datas são dias em UTC, sem fuso, para que as fontes se alinhem
  independentemente do fuso horário da máquina que roda o app.
- Séries macro são deslocadas pela defasagem de publicação (ver config.py).
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Callable

import pandas as pd
import requests

from oem import config
from oem.config import ParametrosAtivo

log = logging.getLogger(__name__)

URL_FRED = "https://api.stlouisfed.org/fred/series/observations"
URL_BINANCE_KLINES = "https://data-api.binance.vision/api/v3/klines"
URL_BINANCE_PRECO = "https://data-api.binance.vision/api/v3/ticker/price"
URL_DIFICULDADE = "https://api.blockchain.info/charts/difficulty"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

MS_POR_DIA = 86_400_000


class ErroFonteDados(RuntimeError):
    """Uma fonte de dados não respondeu ou respondeu algo inutilizável."""


# ==========================================
# Utilitários de data
# ==========================================
def indice_diario(indice) -> pd.DatetimeIndex:
    """Converte para DatetimeIndex diário, sem fuso, em nanossegundos."""
    idx = pd.DatetimeIndex(indice)
    if idx.tz is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    return idx.normalize().as_unit("ns")


def ms_para_dias_utc(milissegundos) -> pd.DatetimeIndex:
    """Timestamps em ms (Binance) -> dias UTC. Não depende do fuso da máquina."""
    return indice_diario(pd.to_datetime(milissegundos, unit="ms", utc=True))


def segundos_para_dias_utc(segundos) -> pd.DatetimeIndex:
    """Timestamps em segundos (blockchain.info) -> dias UTC."""
    return indice_diario(pd.to_datetime(segundos, unit="s", utc=True))


def _serie_limpa(valores, indice, nome: str) -> pd.Series:
    serie = pd.Series(valores, index=indice_diario(indice), name=nome, dtype="float64")
    serie = serie.dropna()
    serie = serie[~serie.index.duplicated(keep="last")]
    return serie.sort_index()


def aplicar_defasagem(serie: pd.Series, dias: int) -> pd.Series:
    """Move cada observação para a data em que ela passa a ser conhecida."""
    deslocada = serie.copy()
    deslocada.index = deslocada.index + pd.Timedelta(days=dias)
    return deslocada


# ==========================================
# HTTP
# ==========================================
def _get_json(url: str, params: dict, nome: str, tentativas: int = 3, timeout: int = 12):
    """GET com novas tentativas. A mensagem de erro nunca inclui a URL completa,
    para não expor chaves de API na tela."""
    motivo = "sem resposta"
    for tentativa in range(tentativas):
        try:
            resposta = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
            if resposta.status_code == 200:
                return resposta.json()
            motivo = f"HTTP {resposta.status_code}"
        except requests.Timeout:
            motivo = "tempo esgotado"
        except requests.RequestException as erro:
            motivo = type(erro).__name__
        except ValueError:
            motivo = "resposta não é JSON"
        log.warning("%s: tentativa %d falhou (%s)", nome, tentativa + 1, motivo)
        if tentativa < tentativas - 1:
            time.sleep(1.5)
    raise ErroFonteDados(f"{nome}: {motivo}")


# ==========================================
# Fontes
# ==========================================
def buscar_fred(series_id: str, api_key: str | None, inicio: str) -> pd.Series:
    if not api_key:
        raise ErroFonteDados(f"FRED {series_id}: FRED_API_KEY não configurada")
    dados = _get_json(
        URL_FRED,
        {
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "observation_start": inicio,
        },
        f"FRED {series_id}",
    )
    observacoes = dados.get("observations", []) if isinstance(dados, dict) else []
    if not observacoes:
        raise ErroFonteDados(f"FRED {series_id}: nenhuma observação")
    datas = [o["date"] for o in observacoes]
    valores = pd.to_numeric([o["value"] for o in observacoes], errors="coerce")  # "." = vazio
    serie = _serie_limpa(valores, pd.to_datetime(datas), series_id)
    if serie.empty:
        raise ErroFonteDados(f"FRED {series_id}: só valores vazios")
    return serie


def buscar_klines_binance(simbolo: str, inicio: str, fim: pd.Timestamp | None = None) -> pd.Series:
    """Fechamentos diários (candle das 00:00 UTC) da Binance."""
    inicio_ms = int(pd.Timestamp(inicio, tz="UTC").timestamp() * 1000)
    fim_ms = int((fim or pd.Timestamp.now(tz="UTC")).timestamp() * 1000)
    candles: list = []
    while inicio_ms < fim_ms:
        lote = _get_json(
            URL_BINANCE_KLINES,
            {"symbol": simbolo, "interval": "1d", "startTime": inicio_ms, "endTime": fim_ms, "limit": 1000},
            f"Binance {simbolo}",
        )
        if not isinstance(lote, list) or not lote:
            break
        candles.extend(lote)
        inicio_ms = int(lote[-1][0]) + MS_POR_DIA
        time.sleep(0.2)
    if not candles:
        raise ErroFonteDados(f"Binance {simbolo}: nenhum candle")
    return _serie_limpa(
        [float(c[4]) for c in candles], ms_para_dias_utc([int(c[0]) for c in candles]), "Preco"
    )


def buscar_dificuldade(inicio: str) -> pd.Series:
    """Dificuldade de mineração do Bitcoin, em trilhões (T)."""
    meses = math.ceil((pd.Timestamp.now() - pd.Timestamp(inicio)).days / 30.44) + 1
    dados = _get_json(
        URL_DIFICULDADE,
        {"timespan": f"{meses}months", "format": "json", "sampled": "true"},
        "Blockchain.com dificuldade",
    )
    pontos = dados.get("values", []) if isinstance(dados, dict) else []
    if not pontos:
        raise ErroFonteDados("Blockchain.com dificuldade: nenhum ponto")
    return _serie_limpa(
        [float(p["y"]) / 1e12 for p in pontos], segundos_para_dias_utc([p["x"] for p in pontos]), "Diff"
    )


def buscar_yahoo(ticker: str, inicio: str, nome: str, tentativas: int = 3) -> pd.Series:
    import yfinance as yf

    motivo = "resposta vazia"
    for tentativa in range(tentativas):
        try:
            historico = yf.Ticker(ticker).history(start=inicio)
            if not historico.empty and "Close" in historico:
                # Barras diárias: a data da barra é o pregão no fuso da bolsa.
                indice = historico.index
                if indice.tz is not None:
                    indice = indice.tz_localize(None)
                return _serie_limpa(historico["Close"].to_numpy(), indice, nome)
            motivo = "resposta vazia"
        except Exception as erro:  # o yfinance levanta muitos tipos diferentes
            motivo = type(erro).__name__
        log.warning("Yahoo %s: tentativa %d falhou (%s)", ticker, tentativa + 1, motivo)
        if tentativa < tentativas - 1:
            time.sleep(1.5)
    raise ErroFonteDados(f"Yahoo {ticker}: {motivo}")


def preco_atual_binance(simbolo: str) -> float:
    dados = _get_json(URL_BINANCE_PRECO, {"symbol": simbolo}, f"Binance {simbolo} live", tentativas=1, timeout=5)
    try:
        return float(dados["price"])
    except (KeyError, TypeError, ValueError) as erro:
        raise ErroFonteDados(f"Binance {simbolo} live: resposta inesperada") from erro


def ultimo_fechamento_yahoo(ticker: str) -> float:
    serie = buscar_yahoo(ticker, (pd.Timestamp.now() - pd.Timedelta(days=7)).strftime("%Y-%m-%d"), ticker, tentativas=1)
    return float(serie.iloc[-1])


# ==========================================
# Montagem da base
# ==========================================
@dataclass(frozen=True)
class Fonte:
    coluna: str
    descricao: str
    obrigatoria: bool


TICKERS_YAHOO = {"DXY": "DX-Y.NYB", "BRL": "BRL=X", "NDX": "^NDX", "USD_CNY": "CNY=X"}


def fontes_para(ativo: ParametrosAtivo) -> list[Fonte]:
    fontes = [
        Fonte("Preco", f"Binance {ativo.simbolo_binance}", True),
        Fonte("Juro", "FRED juro real (DFII10)", True),
        Fonte("M2", "FRED M2 (WM2NS)", True),
        Fonte("DXY", "Yahoo DXY", True),
        Fonte("BRL", "Yahoo USD/BRL", True),
        Fonte("NDX", "Yahoo Nasdaq 100", False),
        Fonte("USD_CNY", "Yahoo USD/CNY", False),
    ]
    if ativo.usa_dificuldade:
        fontes.append(Fonte("Diff", "Blockchain.com dificuldade", True))
    return fontes


def buscar_fonte(coluna: str, simbolo: str | None, inicio: str, api_key_fred: str | None) -> pd.Series:
    """Busca uma coluna da base, já com a defasagem de publicação aplicada."""
    if coluna == "Preco":
        return buscar_klines_binance(simbolo, inicio)
    if coluna == "Juro":
        return aplicar_defasagem(buscar_fred("DFII10", api_key_fred, inicio), config.DEFASAGEM_JURO_DIAS)
    if coluna == "M2":
        return aplicar_defasagem(buscar_fred("WM2NS", api_key_fred, inicio), config.DEFASAGEM_M2_DIAS)
    if coluna == "Diff":
        return buscar_dificuldade(inicio)
    if coluna in TICKERS_YAHOO:
        return buscar_yahoo(TICKERS_YAHOO[coluna], inicio, coluna)
    raise ValueError(f"Coluna desconhecida: {coluna}")


def combinar_fontes(series: dict[str, pd.Series]) -> pd.DataFrame:
    """Uma linha por dia com preço; as demais séries entram com o último valor
    já conhecido naquele dia (forward-fill)."""
    preco = series["Preco"]
    base = pd.concat([s.rename(nome) for nome, s in series.items()], axis=1).sort_index().ffill()
    base = base.loc[base.index.isin(preco.index)]
    return base.dropna()  # descarta o início, antes de todas as séries terem dados


BuscadorFonte = Callable[[str, "str | None", str, "str | None"], pd.Series]  # (coluna, símbolo, início, chave FRED)


def carregar_base(
    ativo: ParametrosAtivo,
    inicio: str,
    api_key_fred: str | None,
    buscar: BuscadorFonte | None = None,
) -> tuple[pd.DataFrame | None, list[str], list[str]]:
    """Retorna (base, falhas_obrigatorias, falhas_opcionais).

    Se alguma fonte obrigatória falhar, a base é None. Fontes opcionais que
    falharem simplesmente não aparecem como coluna.
    """
    buscar = buscar or buscar_fonte
    series: dict[str, pd.Series] = {}
    falhas_obrigatorias: list[str] = []
    falhas_opcionais: list[str] = []
    for fonte in fontes_para(ativo):
        simbolo = ativo.simbolo_binance if fonte.coluna == "Preco" else None
        chave = api_key_fred if fonte.coluna in ("Juro", "M2") else None
        try:
            series[fonte.coluna] = buscar(fonte.coluna, simbolo, inicio, chave)
        except ErroFonteDados as erro:
            (falhas_obrigatorias if fonte.obrigatoria else falhas_opcionais).append(str(erro))
    if falhas_obrigatorias:
        return None, falhas_obrigatorias, falhas_opcionais
    base = combinar_fontes(series)
    if base.empty:
        return None, ["As fontes não têm datas em comum"], falhas_opcionais
    return base, [], falhas_opcionais
