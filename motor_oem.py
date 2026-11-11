"""Motor OEM em linha de comando: gera a tabela histórica do modelo sem abrir o app.

Usa exatamente o mesmo código do app (pacote `oem`): mesmo modelo, mesmos dados
e a mesma regra de decisão.

Exemplos:
    FRED_API_KEY=sua_chave python motor_oem.py --meses 48
    FRED_API_KEY=sua_chave python motor_oem.py --ativo ETH --semanal --saida eth.csv --grafico eth.html
"""
from __future__ import annotations

import argparse
import os
import sys

import pandas as pd
import plotly.graph_objects as go
from dateutil.relativedelta import relativedelta

from oem import config, dados, estrategia, modelo

ATIVOS_POR_TICKER = {a.ticker: a for a in config.ATIVOS.values()}


def montar_tabela(df: pd.DataFrame, p: estrategia.Parametros) -> pd.DataFrame:
    momentum = estrategia.calcular_momentum(df["Mercado"], p.janela_momentum)
    regimes = []
    for (_, linha), mom in zip(df.iterrows(), momentum):
        delta = estrategia.calcular_delta(linha["OEM"], linha["Mercado"])
        # Saldo e caixa simbólicos: aqui só interessa o regime (compra/venda/...).
        regimes.append(estrategia.decidir_acao(delta, mom, linha["Z_Score"], 1e9, 1.0, p).regime)
    return pd.DataFrame(
        {
            "Data": df.index.strftime("%d/%m/%Y"),
            "Preco_OEM_USD": df["OEM_USD"].round(2),
            "Preco_Mercado_USD": df["Preco"].round(2),
            "Preco_OEM_BRL": df["OEM"].round(2),
            "Preco_Mercado_BRL": df["Mercado"].round(2),
            "Delta_%": ((df["OEM"] - df["Mercado"]) / df["OEM"] * 100).round(2),
            "Z_Score_Preco": df["Z_Score"].round(2),
            "Alpha": df["Alpha"].round(4),
            "Regime": regimes,
        }
    )


def salvar_grafico(df: pd.DataFrame, caminho: str, titulo: str) -> None:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df.index, y=df["OEM_USD"], name="OEM (Valor Justo)", line=dict(color="#F7931A", width=2.5)))
    fig.add_trace(go.Scatter(x=df.index, y=df["Preco"], name="Preço Mercado", line=dict(color="white", dash="dash")))
    fig.update_layout(template="plotly_dark", title=titulo, yaxis_title="Preço (USD)", hovermode="x unified")
    fig.write_html(caminho)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ativo", choices=sorted(ATIVOS_POR_TICKER), default="BTC")
    parser.add_argument("--meses", type=int, default=48, help="meses de histórico (padrão: 48)")
    parser.add_argument("--saida", default="Historico_OEM.xlsx", help="arquivo .xlsx ou .csv")
    parser.add_argument("--semanal", action="store_true", help="só as sextas-feiras")
    parser.add_argument("--calibrar-alpha", action="store_true", help="ALPHA calibrado só com dados passados")
    parser.add_argument("--grafico", help="salva um gráfico interativo .html")
    args = parser.parse_args(argv)

    ativo = ATIVOS_POR_TICKER[args.ativo]
    hoje = pd.Timestamp.now().normalize()
    inicio_busca = (hoje - relativedelta(months=max(args.meses, config.MESES_MAXIMOS), days=config.DIAS_AQUECIMENTO)).strftime("%Y-%m-%d")

    print(f"[*] Buscando dados de {ativo.nome} desde {inicio_busca}...")
    base, falhas, avisos = dados.carregar_base(ativo, inicio_busca, os.environ.get("FRED_API_KEY"))
    for aviso in avisos:
        print(f"[aviso] fonte opcional indisponível: {aviso}")
    if base is None:
        for falha in falhas:
            print(f"[ERRO] {falha}", file=sys.stderr)
        return 1

    df = modelo.recortar_janela(modelo.calcular_oem(base, ativo, args.calibrar_alpha), hoje - relativedelta(months=args.meses))
    tabela = montar_tabela(df, estrategia.Parametros())  # momentum calculado com dados diários
    if args.semanal:
        sextas = df.index.day_of_week == 4
        tabela, df = tabela[sextas], df[sextas]

    if args.saida.lower().endswith(".csv"):
        tabela.to_csv(args.saida, index=False)
    else:
        tabela.to_excel(args.saida, index=False)
    print(f"[SUCESSO] {len(tabela)} linhas salvas em: {args.saida}")

    if args.grafico:
        salvar_grafico(df, args.grafico, f"Modelo OEM - {ativo.nome}")
        print(f"[SUCESSO] Gráfico salvo em: {args.grafico}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
