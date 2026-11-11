import os
import time

import pandas as pd
import pytest
import requests

from oem import dados
from oem.config import ATIVOS

BTC = ATIVOS["Bitcoin (BTC)"]
ETH = ATIVOS["Ethereum (ETH)"]


@pytest.fixture
def fuso_sao_paulo():
    anterior = os.environ.get("TZ")
    os.environ["TZ"] = "America/Sao_Paulo"
    time.tzset()
    yield
    if anterior is None:
        os.environ.pop("TZ")
    else:
        os.environ["TZ"] = anterior
    time.tzset()


def test_candle_binance_cai_no_dia_utc_certo(fuso_sao_paulo):
    # 2024-01-01 00:00 UTC = 2023-12-31 21:00 em São Paulo.
    idx = dados.ms_para_dias_utc([1704067200000])
    assert idx[0] == pd.Timestamp("2024-01-01")
    assert dados.segundos_para_dias_utc([1704067200])[0] == pd.Timestamp("2024-01-01")


def test_defasagem_de_publicacao():
    serie = pd.Series([1.0], index=pd.DatetimeIndex(["2024-01-01"]))
    assert dados.aplicar_defasagem(serie, 49).index[0] == pd.Timestamp("2024-02-19")


def test_combinar_usa_so_valores_ja_publicados(series):
    series = dict(series)
    m2 = series["M2"]
    series["M2"] = dados.aplicar_defasagem(m2, 49)
    base = dados.combinar_fontes(series)
    dia = base.index[300]
    esperado = m2[m2.index <= dia - pd.Timedelta(days=49)].iloc[-1]
    assert base.at[dia, "M2"] == esperado
    assert base.index.isin(series["Preco"].index).all()
    assert not base.isna().any().any()


def _buscador(series, falhar=()):
    def buscar(coluna, simbolo, inicio, chave):
        if coluna in falhar:
            raise dados.ErroFonteDados(f"{coluna}: fora do ar")
        return series[coluna]

    return buscar


def test_falha_obrigatoria_nao_inventa_valores(series):
    base, falhas, _ = dados.carregar_base(BTC, "2020-01-01", "x", buscar=_buscador(series, falhar={"BRL"}))
    assert base is None and falhas == ["BRL: fora do ar"]


def test_falha_opcional_so_remove_a_coluna(series):
    base, falhas, avisos = dados.carregar_base(BTC, "2020-01-01", "x", buscar=_buscador(series, falhar={"NDX"}))
    assert base is not None and not falhas and avisos == ["NDX: fora do ar"]
    assert "NDX" not in base


def test_eth_nem_busca_dificuldade(series):
    pedidas = []

    def buscar(coluna, simbolo, inicio, chave):
        pedidas.append(coluna)
        return series[coluna]

    dados.carregar_base(ETH, "2020-01-01", "x", buscar=buscar)
    assert "Diff" not in pedidas


def test_erro_do_fred_nao_expoe_a_chave(monkeypatch):
    class Resposta:
        status_code = 400

    monkeypatch.setattr(dados.requests, "get", lambda *a, **k: Resposta())
    monkeypatch.setattr(dados.time, "sleep", lambda s: None)
    with pytest.raises(dados.ErroFonteDados) as erro:
        dados.buscar_fred("DFII10", "CHAVE_SECRETA_123", "2024-01-01")
    assert "CHAVE_SECRETA_123" not in str(erro.value)
    assert "HTTP 400" in str(erro.value)


def test_timeout_vira_erro_de_fonte(monkeypatch):
    def estoura(*a, **k):
        raise requests.Timeout("lento")

    monkeypatch.setattr(dados.requests, "get", estoura)
    monkeypatch.setattr(dados.time, "sleep", lambda s: None)
    with pytest.raises(dados.ErroFonteDados, match="tempo esgotado"):
        dados.buscar_dificuldade("2024-01-01")


def test_sem_chave_fred():
    with pytest.raises(dados.ErroFonteDados, match="não configurada"):
        dados.buscar_fred("WM2NS", None, "2024-01-01")
