"""Telas (abas) do Terminal OEM."""
from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from oem import carregamento, estrategia, metricas, modelo, otimizador, previsao
from oem.config import ParametrosAtivo
from oem.estrategia import Parametros

AVISO_LEGAL = (
    "⚠️ Ferramenta de estudo. Backtests e projeções usam dados passados e não garantem "
    "resultados futuros. Nada aqui é recomendação de investimento."
)


@dataclass
class Contexto:
    ativo: ParametrosAtivo
    df: pd.DataFrame  # janela analisada
    df_completo: pd.DataFrame  # janela + aquecimento (para cálculos que olham para trás)
    meses: int
    params: Parametros
    caixa: float
    saldo_cripto: float
    alpha_calibrado: bool


def _pct(valor: float, casas: int = 1) -> str:
    return "—" if valor is None or not np.isfinite(valor) else f"{valor * 100:.{casas}f}%"


def _num(valor: float, casas: int = 2) -> str:
    return "—" if valor is None or not np.isfinite(valor) else f"{valor:.{casas}f}"


def _entradas_carteira(prefixo: str, ticker: str) -> otimizador.Carteira:
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        capital = st.number_input("Valor Investido Inicial (BRL)", min_value=0.0, value=5000.0, step=500.0, key=f"{prefixo}_capital")
    with c2:
        cripto = st.number_input(f"Saldo Inicial ({ticker})", min_value=0.0, value=0.0, step=0.01, format="%.4f", key=f"{prefixo}_cripto")
    with c3:
        aporte = st.number_input("Aporte Mensal (BRL)", min_value=0.0, value=1000.0, step=100.0, key=f"{prefixo}_aporte")
    with c4:
        taxa = st.number_input("Taxa da Corretora (%)", min_value=0.0, value=0.10, step=0.05, key=f"{prefixo}_taxa") / 100.0
    return otimizador.Carteira(capital, cripto, aporte, taxa)


# ==========================================
# ABA 1: MONITORAMENTO LIVE
# ==========================================
def _descrever_decisao(decisao: estrategia.Decisao, ctx: Contexto, preco_brl: float) -> tuple[str, str, str]:
    tk, p = ctx.ativo.ticker, ctx.params
    sem_caixa = f"Caixa abaixo do mínimo de R$ {p.caixa_minimo:,.0f} para comprar."
    sem_saldo = f"Sem saldo em {tk} para vender."

    if decisao.regime == estrategia.SATURACAO:
        qtd = ctx.saldo_cripto * decisao.frac_venda
        texto = (
            f"Preço muito acima da média de 365 dias: venda {qtd:.4f} {tk} (~R$ {qtd * preco_brl:,.2f}). Não compre agora."
            if qtd > 0
            else f"{sem_saldo} Não compre agora."
        )
        return "🚨 SATURAÇÃO (Z-Score de preço)", "#FF00FF", texto
    if decisao.regime == estrategia.COMPRA:
        valor = ctx.caixa * decisao.frac_compra
        texto = f"Compre R$ {valor:,.2f} ({decisao.frac_compra * 100:.1f}% do Caixa)" if valor > 0 else sem_caixa
        return "🟢 COMPRA", "#00FF00", texto
    if decisao.regime == estrategia.VENDA:
        qtd = ctx.saldo_cripto * decisao.frac_venda
        texto = f"Venda {qtd:.4f} {tk} (Receba ~R$ {qtd * preco_brl:,.2f})" if qtd > 0 else sem_saldo
        return "🔴 VENDA PARCIAL", "#FF0000", texto
    valor = ctx.caixa * decisao.frac_compra
    texto = f"Compre apenas R$ {valor:,.2f} ({p.dca_passivo * 100:.0f}% do Caixa)" if valor > 0 else sem_caixa
    return "🔵 DCA PASSIVO", "#00BFFF", texto


def tela_monitoramento(ctx: Contexto) -> None:
    ativo, tk, p = ctx.ativo, ctx.ativo.ticker, ctx.params
    df = ctx.df.copy()
    st.title(f"📡 Terminal OEM - {ativo.nome}")

    ultimo = df.index[-1]
    sem_live: list[str] = []

    def live_ou_fechamento(valor: float | None, coluna: str, rotulo: str) -> float:
        if valor is None:
            sem_live.append(rotulo)
            return float(df.at[ultimo, coluna])
        return valor

    preco_usd = live_ou_fechamento(carregamento.preco_live(ativo.simbolo_binance), "Preco", f"preço {tk}")
    brl = live_ou_fechamento(carregamento.yahoo_live("BRL"), "BRL", "USD/BRL")
    dxy = live_ou_fechamento(carregamento.yahoo_live("DXY"), "DXY", "DXY")
    if sem_live:
        st.caption(f"ℹ️ Sem cotação ao vivo para {', '.join(sem_live)}; usando o último fechamento diário.")

    oem_usd = modelo.oem_com_dxy_atual(float(df.at[ultimo, "OEM_USD"]), float(df.at[ultimo, "DXY"]), dxy)
    df.loc[ultimo, ["Preco", "BRL", "DXY", "1_DXY", "OEM_USD"]] = [preco_usd, brl, dxy, 1 / dxy, oem_usd]
    df.loc[ultimo, "Mercado"] = preco_usd * brl
    df.loc[ultimo, "OEM"] = oem_usd * brl
    for coluna, inversa in (("NDX", None), ("USD_CNY", "CNY_USD")):
        if coluna in df:
            valor = carregamento.yahoo_live(coluna)
            if valor is not None:
                df.loc[ultimo, coluna] = valor
                if inversa:
                    df.loc[ultimo, inversa] = 1 / valor

    u = df.loc[ultimo]
    historico = ctx.df_completo["Mercado"].copy()
    historico.loc[ultimo] = u["Mercado"]
    momentum = float(estrategia.calcular_momentum(historico, p.janela_momentum)[-1])
    delta = estrategia.calcular_delta(u["OEM"], u["Mercado"])
    decisao = estrategia.decidir_acao(delta, momentum, float(u["Z_Score"]), ctx.caixa, ctx.saldo_cripto, p)
    status, cor, recomendacao = _descrever_decisao(decisao, ctx, float(u["Mercado"]))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Preço Justo OEM (BRL)", f"R$ {u['OEM']:,.0f}")
    c2.metric(f"Mercado {tk} (BRL)", f"R$ {u['Mercado']:,.0f}", f"{delta * 100:.2f}% (Delta OEM)")
    c3.metric("Z-Score de Preço (365d)", f"{u['Z_Score']:.2f}", f"Limite em {p.z_limite:.1f}", delta_color="inverse")
    with c4:
        st.markdown(f"<h4 style='text-align: center; color: {cor}; margin-bottom: 0px;'>{status}</h4>", unsafe_allow_html=True)
        st.markdown(f"<p style='text-align: center; font-size: 14px;'><b>Ação:</b> {recomendacao}</p>", unsafe_allow_html=True)

    origem_alpha = "calibrado só com dados passados" if ctx.alpha_calibrado else "fixo"
    st.caption(f"ALPHA em uso: {u['Alpha']:.4g} ({origem_alpha}).")
    st.markdown("---")

    st.markdown(f"#### 🔗 Correlação de retornos semanais ({ctx.meses} meses)")
    pares = [(f"{tk} vs OEM", "OEM"), (f"{tk} vs Nasdaq", "NDX"), (f"{tk} vs 1/DXY", "1_DXY"), (f"{tk} vs Yuan", "CNY_USD")]
    for coluna_st, (rotulo, coluna) in zip(st.columns(len(pares)), pares):
        coluna_st.metric(rotulo, _num(metricas.correlacao_retornos_semanais(df, "Mercado", coluna)))
    st.caption("Correlação entre as variações semanais, não entre os níveis de preço (níveis com tendência sempre parecem correlacionados).")

    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.06, row_heights=[0.5, 0.25, 0.25],
        specs=[[{"secondary_y": True}], [{"secondary_y": True}], [{"secondary_y": True}]],
    )
    fig.add_trace(go.Scatter(x=df.index, y=df["OEM"], name="Valor Justo (R$)", line=dict(color="#F7931A", width=3)), row=1, col=1, secondary_y=False)
    fig.add_trace(go.Scatter(x=df.index, y=df["Mercado"], name=f"Mercado {tk}", line=dict(color="white", width=1.5, dash="dash")), row=1, col=1, secondary_y=False)
    if "NDX" in df:
        fig.add_trace(go.Scatter(x=df.index, y=df["NDX"], name="Nasdaq 100", line=dict(color="#00FFFF", width=2)), row=1, col=1, secondary_y=True)

    fig.add_trace(go.Scatter(x=df.index, y=df["Z_Score"], fill="tozeroy", name="Z-Score de Preço", line=dict(color="#FF00FF")), row=2, col=1, secondary_y=False)
    fig.add_hline(y=p.z_limite, line_dash="dash", line_color="red", annotation_text="Limite Crítico", row=2, col=1, secondary_y=False)
    fig.add_hline(y=0, line_dash="solid", line_color="rgba(255, 255, 255, 0.3)", row=2, col=1, secondary_y=False)
    fig.add_trace(go.Scatter(x=df.index, y=df["1_DXY"], name="1/DXY (Liquidez)", line=dict(color="#00BFFF", width=1, dash="dot"), opacity=0.4), row=2, col=1, secondary_y=True)

    fig.add_trace(go.Scatter(x=df.index, y=df["BRL"], name="USD/BRL", line=dict(color="#00FF00", width=2)), row=3, col=1, secondary_y=False)
    fig.add_trace(go.Scatter(x=df.index, y=df["Mercado"], name=f"Preço {tk} (BRL)", line=dict(color="white", width=1.5, dash="dot"), opacity=0.6), row=3, col=1, secondary_y=True)

    fig.update_layout(template="plotly_dark", height=850, margin=dict(l=0, r=0, t=10, b=0), hovermode="x unified")
    fig.update_yaxes(title_text=f"Preço {tk}", row=1, col=1, secondary_y=False)
    fig.update_yaxes(title_text="Nasdaq 100", row=1, col=1, secondary_y=True, showgrid=False)
    fig.update_yaxes(title_text="Z-Score de Preço", row=2, col=1, secondary_y=False)
    fig.update_yaxes(title_text="1/DXY", row=2, col=1, secondary_y=True, showgrid=False)
    fig.update_yaxes(title_text="Câmbio (R$)", row=3, col=1, secondary_y=False)
    fig.update_yaxes(title_text=f"Preço {tk} (R$)", row=3, col=1, secondary_y=True, showgrid=False)
    st.plotly_chart(fig, width="stretch")


# ==========================================
# ABA 2: BACKTEST
# ==========================================
def _coluna_metricas(titulo: str, m: metricas.Metricas) -> None:
    st.subheader(titulo)
    st.metric("Retorno s/ Capital Investido", _pct(m.retorno_sobre_investido))
    st.metric("Retorno da Estratégia (TWR)", _pct(m.retorno_twr))
    st.metric("Risco (Drawdown Máx)", _pct(m.drawdown_maximo), delta_color="inverse")
    st.metric("Sharpe | Sortino", f"{m.sharpe:.2f} | {m.sortino:.2f}")


def tela_backtest(ctx: Contexto) -> None:
    tk, df, p = ctx.ativo.ticker, ctx.df, ctx.params
    st.title(f"🧪 Backtest Histórico ({ctx.ativo.nome})")
    carteira = _entradas_carteira("bt", tk)

    meses = estrategia.chaves_mes(df.index)
    res_oem = estrategia.simular_estrategia(
        df["Mercado"], df["OEM"], df["Z_Score"], estrategia.calcular_momentum(df["Mercado"], p.janela_momentum),
        meses, p, carteira.capital_inicial, carteira.cripto_inicial, carteira.aporte_mensal, carteira.taxa,
    )
    res_dca = estrategia.simular_dca(df["Mercado"], meses, carteira.capital_inicial, carteira.cripto_inicial, carteira.aporte_mensal, carteira.taxa)
    m_oem, m_dca = metricas.calcular_metricas(res_oem), metricas.calcular_metricas(res_dca)

    st.markdown("---")
    c1, c2, c3 = st.columns(3)
    with c1:
        _coluna_metricas("Benchmark (DCA)", m_dca)
    with c2:
        _coluna_metricas("Estratégia OEM (Ativo + Z-Score)", m_oem)
    with c3:
        st.subheader("Carteira Final OEM")
        st.metric("Caixa Restante", f"R$ {res_oem.caixa_final:,.2f}")
        st.metric(f"Saldo em {tk}", f"{res_oem.cripto_final:.5f} {tk}")
        st.metric("Total Injetado", f"R$ {res_oem.total_investido:,.2f}")
    st.caption(
        "TWR, drawdown, Sharpe e Sortino descontam os aportes mensais: dinheiro novo entrando na carteira "
        "não conta como lucro. \"Retorno s/ capital investido\" compara o patrimônio final com tudo o que foi aportado."
    )

    st.markdown("---")
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08, row_heights=[0.7, 0.3])
    fig.add_trace(go.Scatter(x=df.index, y=res_dca.patrimonio, name="Benchmark (DCA)", line=dict(color="#888888", dash="dash")), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=res_oem.patrimonio, name="Estratégia OEM", line=dict(color="#00FF00", width=3)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["Z_Score"], fill="tozeroy", name="Z-Score de Preço", line=dict(color="#FF00FF")), row=2, col=1)
    fig.add_hline(y=p.z_limite, line_dash="dash", line_color="red", annotation_text="Gatilho de Saturação", row=2, col=1)

    treino = st.session_state.get("opt_treino")
    if treino and treino[0] == tk:
        inicio, fim = max(treino[1], df.index[0]), min(treino[2], df.index[-1])
        if inicio < fim:
            fig.add_vrect(x0=inicio, x1=fim, fillcolor="#FFD700", opacity=0.08, line_width=0, annotation_text="Treino do otimizador", annotation_position="top left")
            st.info(
                f"Os parâmetros atuais foram escolhidos pelo otimizador com dados de {treino[1]:%d/%m/%Y} a {treino[2]:%d/%m/%Y} "
                "(faixa amarela). Nesse trecho o resultado é **dentro da amostra** e tende a parecer melhor do que seria na prática."
            )

    fig.update_layout(template="plotly_dark", title="Patrimônio (com aportes) vs Saturação de Preço", hovermode="x unified", height=650)
    st.plotly_chart(fig, width="stretch")


# ==========================================
# ABA 3: OTIMIZADOR WALK-FORWARD
# ==========================================
def _rotulo_pct(valor: float) -> str:
    return f"{int(round(valor * 100))}%"


def _heatmap(grade: pd.DataFrame, linhas: str, colunas: str, titulo: str, escala: str, rotulo_linha, rotulo_coluna):
    pivot = grade.pivot_table(index=linhas, columns=colunas, values="Sortino Treino (Consenso)", aggfunc="max")
    fig = go.Figure(
        data=go.Heatmap(
            z=pivot.values, x=[rotulo_coluna(c) for c in pivot.columns], y=[rotulo_linha(i) for i in pivot.index],
            colorscale=escala, text=np.round(pivot.values, 2), texttemplate="%{text}",
        )
    )
    fig.update_layout(template="plotly_dark", title=titulo, height=500)
    st.plotly_chart(fig, width="stretch")


def tela_otimizador(ctx: Contexto) -> None:
    tk = ctx.ativo.ticker
    st.title(f"🔥 Otimizador Walk-Forward ({tk})")
    st.markdown(
        "Os parâmetros são escolhidos **só no período de treino** (início da janela). Depois, as melhores "
        "configurações são medidas no **período de teste** (final da janela), que o otimizador nunca viu. "
        "Ir bem no treino e mal no teste é sinal de sobreajuste."
    )
    carteira = _entradas_carteira("otm", tk)
    fracao = st.slider("% da janela usada para treino", 50, 90, 70, step=5) / 100.0

    if st.button("🚀 Processar Otimização Walk-Forward", width="stretch"):
        barra = st.progress(0.0, text="Testando combinações no período de treino...")
        try:
            resultado = otimizador.otimizar_walk_forward(
                ctx.df, fracao, carteira, ctx.params, progresso=lambda x: barra.progress(x, text=f"Testando combinações... {x:.0%}")
            )
        except ValueError as erro:
            barra.empty()
            st.warning(str(erro))
            return
        barra.empty()
        st.session_state.resultado_otimizacao = (tk, ctx.meses, resultado)

    salvo = st.session_state.get("resultado_otimizacao")
    if not salvo or salvo[0] != tk or salvo[1] != ctx.meses:
        return
    res: otimizador.ResultadoOtimizacao = salvo[2]

    st.markdown(
        f"**Treino:** {res.inicio_treino:%d/%m/%Y} → {res.fim_treino:%d/%m/%Y} &nbsp;|&nbsp; "
        f"**Teste:** {res.inicio_teste:%d/%m/%Y} → {res.fim_teste:%d/%m/%Y}"
    )
    tabela = res.melhores.copy()
    for coluna in ("Teto Compra (%)", "Teto Venda (%)"):
        tabela[coluna] = tabela[coluna].map(_rotulo_pct)
    for coluna in ("Retorno TWR Teste", "Drawdown Teste"):
        tabela[coluna] = tabela[coluna].map(_pct)
    for coluna in ("Sortino Treino (Consenso)", "Sortino Teste"):
        tabela[coluna] = tabela[coluna].map(_num)
    st.markdown("### 🏆 Top 5 Configurações (escolhidas no treino, medidas no teste)")
    st.dataframe(tabela, width="stretch")

    b = res.benchmark_teste
    st.caption(f"Benchmark DCA no período de teste: Sortino {b.sortino:.2f} | Retorno TWR {_pct(b.retorno_twr)} | Drawdown {_pct(b.drawdown_maximo)}")
    melhor = res.melhores.iloc[0]
    if melhor["Sortino Teste"] < b.sortino:
        st.warning("A melhor configuração do treino **perdeu para o DCA simples** no período de teste.")
    if 0 < melhor["Sortino Treino (Consenso)"] and melhor["Sortino Teste"] < 0.5 * melhor["Sortino Treino (Consenso)"]:
        st.warning("O desempenho caiu mais da metade do treino para o teste: provável sobreajuste.")

    st.markdown("---")
    if st.button("🎯 Aplicar Configuração ao Painel Live", type="primary", width="stretch"):
        p = otimizador.parametros_da_linha(melhor, ctx.params)
        st.session_state.opt_janela = p.janela_momentum
        st.session_state.opt_risco = p.risco
        st.session_state.opt_sens = p.sensibilidade
        st.session_state.opt_buy = int(round(p.max_compra * 100))
        st.session_state.opt_sell = int(round(p.max_venda * 100))
        st.session_state.opt_treino = (tk, res.inicio_treino, res.fim_treino)
        st.rerun()

    st.caption("Mapas de calor: Sortino médio no período de treino.")
    c1, c2, c3 = st.columns(3)
    with c1:
        _heatmap(res.grade, "Força do Modulador", "Agressividade Base", "Estabilidade: Motor vs Freio", "Viridis",
                 lambda i: f"Modulador {i}", lambda c: f"Risco {c}")
    with c2:
        _heatmap(res.grade, "Força do Modulador", "Teto Compra (%)", "Absorção de Quedas", "Plasma",
                 lambda i: f"Modulador {i}", _rotulo_pct)
    with c3:
        _heatmap(res.grade, "Teto Venda (%)", "Teto Compra (%)", "Calibragem de Bolso", "Magma", _rotulo_pct, _rotulo_pct)


# ==========================================
# ABA 4: PROJEÇÃO NEURAL (MLP)
# ==========================================
def tela_previsao(ctx: Contexto) -> None:
    tk = ctx.ativo.ticker
    st.title(f"🧠 Projeção Neural com Auto-Tuning ({tk})")
    st.markdown(
        "Rede neural (MLP) que prevê o **retorno** dos próximos dias. A escolha de memória e horizonte é feita "
        f"num período de **validação** ({previsao.DIAS_VALIDACAO} dias); o resultado reportado vem de um período de "
        f"**teste** posterior ({previsao.DIAS_TESTE} dias) que não influenciou a escolha. Em cada dia, o modelo só usa "
        "o que já se sabia naquele dia, e tudo é comparado com referências ingênuas."
    )

    if st.button("🚀 Iniciar Otimização e Projeção Neural", width="stretch"):
        with st.spinner(f"Treinando e avaliando a rede para {tk}. Isso pode levar até um minuto..."):
            try:
                resultado = previsao.projetar(ctx.df)
            except ValueError as erro:
                st.warning(str(erro))
                return
        st.session_state.resultado_previsao = (tk, ctx.meses, resultado)

    salvo = st.session_state.get("resultado_previsao")
    if not salvo or salvo[0] != tk or salvo[1] != ctx.meses:
        return
    res: previsao.ResultadoPrevisao = salvo[2]
    t = res.teste

    st.success(f"🎯 Escolhido na validação: memória de **{res.memoria} dias** e horizonte de **{res.horizonte} dias**.")
    c1, c2, c3 = st.columns(3)
    c1.metric("Acerto de direção (teste)", _pct(t.acerto_modelo), f"referência: {_pct(t.acerto_referencia)}", delta_color="off")
    c2.metric("Erro vs \"preço não muda\"", _num(t.razao_rmse), "< 1 = melhor que o ingênuo", delta_color="off")
    c3.metric("Previsões avaliadas", f"{t.n}")

    if not (t.acerto_modelo > t.acerto_referencia and t.razao_rmse < 1):
        st.warning(
            "No período de teste o modelo **não superou** as referências ingênuas. "
            "Trate a projeção abaixo como ilustração, não como sinal."
        )
    else:
        st.info(
            f"O modelo superou as referências no teste, mas com {t.n} previsões que se sobrepõem "
            f"(horizonte de {res.horizonte} dias) a evidência ainda é fraca."
        )

    historico = ctx.df["Mercado"].iloc[-90:]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=historico.index, y=historico.values, name="Histórico Real", line=dict(color="white", width=2)))
    fig.add_trace(go.Scatter(
        x=[res.data_base, res.data_alvo, res.data_alvo, res.data_base],
        y=[res.preco_base, res.preco_superior, res.preco_inferior, res.preco_base],
        fill="toself", fillcolor="rgba(0, 250, 154, 0.15)", line=dict(width=0), name="Faixa de erro (95%, teste)", hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=[res.data_base, res.data_alvo], y=[res.preco_base, res.preco_previsto],
        name=f"Projeção ({res.horizonte}d)", line=dict(color="#00FA9A", width=3, dash="dash"),
    ))
    fig.update_layout(template="plotly_dark", title=f"Projeção para {res.data_alvo:%d/%m/%Y}: R$ {res.preco_previsto:,.0f}", hovermode="x unified", height=500)
    st.plotly_chart(fig, width="stretch")

    with st.expander("Resultados de todas as combinações na validação"):
        tabela = res.candidatos.copy()
        for coluna in ("Acerto direção (validação)", "Acerto referência"):
            tabela[coluna] = tabela[coluna].map(_pct)
        tabela["RMSE / ingênuo"] = tabela["RMSE / ingênuo"].map(_num)
        st.dataframe(tabela, width="stretch")
