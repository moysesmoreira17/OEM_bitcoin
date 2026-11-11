"""Parâmetros do modelo OEM por ativo e constantes globais."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

DATA_HALVING_BTC = datetime(2024, 4, 19)
DATA_PICO_EXCHANGES = datetime(2020, 3, 12)  # Início do dreno de liquidez das exchanges

# Quanto histórico buscar, no máximo. A busca é sempre feita a partir da mesma data
# (e não a partir do slider de meses) para que o Z-Score e a calibração do ALPHA
# não mudem quando você muda a janela exibida.
MESES_MAXIMOS = 120
DIAS_AQUECIMENTO = 400  # dados extras antes da janela (média móvel de 365 dias etc.)

# Defasagens de publicação: no backtest, um dado só pode ser usado a partir do dia
# em que ele foi publicado, não a partir da data a que ele se refere.
# DFII10 (juro real de 10 anos): sai no dia útil seguinte.
DEFASAGEM_JURO_DIAS = 1
# WM2NS (M2 semanal): o Fed publica o relatório H.6 uma vez por mês, então uma
# semana pode levar de ~2 a ~7 semanas para aparecer. Usamos o pior caso.
DEFASAGEM_M2_DIAS = 49


@dataclass(frozen=True)
class ParametrosAtivo:
    nome: str
    ticker: str
    simbolo_binance: str
    data_genesis: datetime
    alpha_fixo: float | None  # None => ALPHA sempre calibrado com dados passados
    beta: float  # amplitude do ciclo de halving
    delta: float  # amortecedor de juros negativos
    usa_dificuldade: bool  # multiplica pela dificuldade de mineração do Bitcoin
    usa_ciclo_halving: bool  # aplica o ciclo de 4 anos do halving do Bitcoin
    taxa_escassez: float = 0.02  # prêmio de escassez por ano desde 2020


ATIVOS: dict[str, ParametrosAtivo] = {
    "Bitcoin (BTC)": ParametrosAtivo(
        nome="Bitcoin (BTC)",
        ticker="BTC",
        simbolo_binance="BTCUSDT",
        data_genesis=datetime(2009, 1, 3),
        alpha_fixo=3.4,
        beta=0.18,
        delta=0.5,
        usa_dificuldade=True,
        usa_ciclo_halving=True,
    ),
    # O Ethereum é Proof-of-Stake desde set/2022 (sem mineração) e não tem halving.
    # Por isso o modelo do ETH usa só o núcleo macro (liquidez, juro real, DXY e
    # escassez), e o ALPHA é calibrado com dados passados.
    "Ethereum (ETH)": ParametrosAtivo(
        nome="Ethereum (ETH)",
        ticker="ETH",
        simbolo_binance="ETHUSDT",
        data_genesis=datetime(2015, 7, 30),
        alpha_fixo=None,
        beta=0.0,
        delta=0.5,
        usa_dificuldade=False,
        usa_ciclo_halving=False,
    ),
}
