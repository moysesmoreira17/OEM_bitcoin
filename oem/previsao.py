"""Projeção com rede neural (MLP) avaliada sem olhar o futuro.

Diferenças em relação à versão anterior:
- prevê o retorno (log) dos próximos `h` dias, não o nível do preço;
- a normalização é ajustada só nos dados de treino de cada etapa;
- a avaliação é "rolling origin": o modelo é re-treinado ao longo do tempo e
  faz centenas de previsões, cada uma só com o que se sabia naquele dia;
- a escolha de memória/horizonte usa um período de validação, e o resultado
  reportado vem de um período de teste posterior, que não influenciou a escolha;
- tudo é comparado com referências ingênuas.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.exceptions import ConvergenceWarning
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

OPCOES_MEMORIA = (7, 14, 30)
OPCOES_HORIZONTE = (3, 7, 14)
DIAS_VALIDACAO = 120
DIAS_TESTE = 90
RETREINO_A_CADA = 30
MIN_AMOSTRAS_TREINO = 365


def dias_minimos() -> int:
    return MIN_AMOSTRAS_TREINO + DIAS_VALIDACAO + DIAS_TESTE + max(OPCOES_HORIZONTE) + max(OPCOES_MEMORIA)


def montar_features(df: pd.DataFrame, memoria: int) -> pd.DataFrame:
    """Variáveis conhecidas no fechamento do dia t."""
    log_preco = np.log(df["Mercado"])
    retornos = log_preco.diff()
    colunas = {f"ret_{k}": retornos.shift(k) for k in range(memoria)}
    colunas["z_score"] = df["Z_Score"]
    colunas["dxy"] = np.log(df["DXY"]).diff(memoria)
    if "NDX" in df:
        colunas["ndx"] = np.log(df["NDX"]).diff(memoria)
    return pd.DataFrame(colunas, index=df.index)


def montar_alvo(df: pd.DataFrame, horizonte: int) -> pd.Series:
    """log(P[t+h] / P[t]): só é conhecido no dia t+h."""
    log_preco = np.log(df["Mercado"])
    return (log_preco.shift(-horizonte) - log_preco).rename("alvo")


def _novo_modelo():
    return make_pipeline(
        StandardScaler(),
        MLPRegressor(hidden_layer_sizes=(32, 16), alpha=1e-3, max_iter=500, early_stopping=True, random_state=42),
    )


def _treinar(X: np.ndarray, y: np.ndarray):
    modelo = _novo_modelo()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ConvergenceWarning)
        modelo.fit(X, y)
    return modelo


def indices_treino(validos: np.ndarray, origem: int, horizonte: int) -> np.ndarray:
    """Amostras cujo alvo já era conhecido no dia `origem` (t + h <= origem)."""
    limite = origem - horizonte
    return np.flatnonzero(validos[: max(0, limite + 1)])


def previsoes_rolling_origin(X: np.ndarray, y: np.ndarray, origens: np.ndarray, horizonte: int) -> pd.DataFrame:
    """Previsões fora da amostra para cada origem, re-treinando a cada bloco."""
    validos = ~np.isnan(X).any(axis=1) & ~np.isnan(y)
    linhas = []
    for inicio_bloco in range(0, len(origens), RETREINO_A_CADA):
        bloco = origens[inicio_bloco : inicio_bloco + RETREINO_A_CADA]
        treino = indices_treino(validos, int(bloco[0]), horizonte)
        if len(treino) < MIN_AMOSTRAS_TREINO // 2:
            continue
        modelo = _treinar(X[treino], y[treino])
        direcao_dominante = 1.0 if y[treino].mean() >= 0 else -1.0
        previstos = modelo.predict(X[bloco])
        for origem, previsto in zip(bloco, previstos):
            linhas.append(
                {"origem": int(origem), "previsto": float(previsto), "real": float(y[origem]), "referencia_direcao": direcao_dominante}
            )
    return pd.DataFrame(linhas)


@dataclass(frozen=True)
class Avaliacao:
    n: int
    acerto_modelo: float  # % de acerto da direção (alta/baixa)
    acerto_referencia: float  # % de acerto de "sempre a direção dominante do treino"
    razao_rmse: float  # RMSE do modelo / RMSE de "preço não muda" (< 1 = melhor que o ingênuo)
    desvio_erro: float  # desvio-padrão do erro de previsão (log-retorno)


def avaliar(previsoes: pd.DataFrame) -> Avaliacao:
    if previsoes.empty:
        return Avaliacao(0, float("nan"), float("nan"), float("nan"), float("nan"))
    real, previsto = previsoes["real"].to_numpy(), previsoes["previsto"].to_numpy()
    com_direcao = real != 0
    acerto = float(np.mean(np.sign(previsto[com_direcao]) == np.sign(real[com_direcao])))
    acerto_ref = float(np.mean(previsoes["referencia_direcao"].to_numpy()[com_direcao] == np.sign(real[com_direcao])))
    rmse_modelo = float(np.sqrt(np.mean((previsto - real) ** 2)))
    rmse_ingenuo = float(np.sqrt(np.mean(real**2)))
    return Avaliacao(
        n=len(previsoes),
        acerto_modelo=acerto,
        acerto_referencia=acerto_ref,
        razao_rmse=rmse_modelo / rmse_ingenuo if rmse_ingenuo > 0 else float("nan"),
        desvio_erro=float(np.std(previsto - real, ddof=1)) if len(previsoes) > 1 else float("nan"),
    )


def _origens_avaliacao(n: int, horizonte: int) -> tuple[np.ndarray, np.ndarray]:
    ultima = n - 1 - horizonte  # última origem cujo alvo já é conhecido hoje
    teste = np.arange(ultima - DIAS_TESTE + 1, ultima + 1)
    validacao = np.arange(teste[0] - DIAS_VALIDACAO, teste[0])
    return validacao, teste


@dataclass
class ResultadoPrevisao:
    candidatos: pd.DataFrame
    memoria: int
    horizonte: int
    validacao: Avaliacao
    teste: Avaliacao
    data_base: pd.Timestamp
    preco_base: float
    data_alvo: pd.Timestamp
    preco_previsto: float
    preco_inferior: float
    preco_superior: float


def projetar(df: pd.DataFrame) -> ResultadoPrevisao:
    n = len(df)
    if n < dias_minimos():
        raise ValueError(f"São necessários pelo menos {dias_minimos()} dias de histórico (use uma janela maior).")

    candidatos = []
    for memoria in OPCOES_MEMORIA:
        X = montar_features(df, memoria).to_numpy(dtype=float)
        for horizonte in OPCOES_HORIZONTE:
            y = montar_alvo(df, horizonte).to_numpy(dtype=float)
            origens_val, _ = _origens_avaliacao(n, horizonte)
            av = avaliar(previsoes_rolling_origin(X, y, origens_val, horizonte))
            candidatos.append(
                {
                    "Memória (dias)": memoria,
                    "Horizonte (dias)": horizonte,
                    "Acerto direção (validação)": av.acerto_modelo,
                    "Acerto referência": av.acerto_referencia,
                    "RMSE / ingênuo": av.razao_rmse,
                    "_av": av,
                }
            )

    # Escolha só pela validação: maior acerto de direção, desempate pelo menor erro.
    melhor = sorted(candidatos, key=lambda c: (-np.nan_to_num(c["Acerto direção (validação)"], nan=-1), np.nan_to_num(c["RMSE / ingênuo"], nan=np.inf)))[0]
    memoria, horizonte = melhor["Memória (dias)"], melhor["Horizonte (dias)"]

    X = montar_features(df, memoria).to_numpy(dtype=float)
    y = montar_alvo(df, horizonte).to_numpy(dtype=float)
    _, origens_teste = _origens_avaliacao(n, horizonte)
    av_teste = avaliar(previsoes_rolling_origin(X, y, origens_teste, horizonte))

    # Modelo final: todos os dados cujo alvo já é conhecido hoje.
    validos = ~np.isnan(X).any(axis=1) & ~np.isnan(y)
    modelo = _treinar(X[validos], y[validos])
    retorno_previsto = float(modelo.predict(X[[-1]])[0])
    preco_base = float(df["Mercado"].iloc[-1])
    margem = 1.96 * (av_teste.desvio_erro if np.isfinite(av_teste.desvio_erro) else 0.0)

    tabela = pd.DataFrame([{k: v for k, v in c.items() if k != "_av"} for c in candidatos])
    return ResultadoPrevisao(
        candidatos=tabela,
        memoria=memoria,
        horizonte=horizonte,
        validacao=melhor["_av"],
        teste=av_teste,
        data_base=df.index[-1],
        preco_base=preco_base,
        data_alvo=df.index[-1] + pd.Timedelta(days=horizonte),
        preco_previsto=preco_base * float(np.exp(retorno_previsto)),
        preco_inferior=preco_base * float(np.exp(retorno_previsto - margem)),
        preco_superior=preco_base * float(np.exp(retorno_previsto + margem)),
    )
