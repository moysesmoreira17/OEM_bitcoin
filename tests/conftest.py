import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))


def series_sinteticas(inicio="2020-01-01", dias=1500, semente=7) -> dict[str, pd.Series]:
    """Séries diárias plausíveis para testar sem internet."""
    rng = np.random.default_rng(semente)
    datas = pd.date_range(inicio, periods=dias, freq="D").as_unit("ns")
    t = np.arange(dias)
    preco = 30_000 * np.exp(np.cumsum(rng.normal(0.0004, 0.03, dias)))
    dias_uteis = datas[datas.day_of_week < 5]
    semanas = datas[datas.day_of_week == 0]
    return {
        "Preco": pd.Series(preco, index=datas),
        "Juro": pd.Series(1.5 + 0.5 * np.sin(np.arange(len(dias_uteis)) / 200), index=dias_uteis),
        "M2": pd.Series(20_000 + 3 * np.arange(len(semanas)), index=semanas),
        "DXY": pd.Series(100 + 3 * np.sin(np.arange(len(dias_uteis)) / 90), index=dias_uteis),
        "BRL": pd.Series(5.0 + 0.3 * np.sin(np.arange(len(dias_uteis)) / 120), index=dias_uteis),
        "NDX": pd.Series(15_000 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, len(dias_uteis)))), index=dias_uteis),
        "USD_CNY": pd.Series(7.1 + 0.1 * np.sin(np.arange(len(dias_uteis)) / 60), index=dias_uteis),
        "Diff": pd.Series(15 + t * 0.08, index=datas),
    }


@pytest.fixture
def series():
    return series_sinteticas()


@pytest.fixture
def base(series):
    from oem import dados

    return dados.combinar_fontes(series)
