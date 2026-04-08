import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime
from dateutil.relativedelta import relativedelta
import requests
import math
import time

# ==========================================
# 1. CONFIGURAÇÃO E DADOS BASE
# ==========================================
st.set_page_config(page_title="Terminal OEM v3.1", layout="wide")

FRED_API_KEY = 'e13422aa86d75b68c58b6ced02d8fb31'
DATA_HALVING = datetime(2024, 4, 19)
DATA_GENESIS = datetime(2009, 1, 3)
DATA_PICO_EXCHANGES = datetime(2020, 3, 1)

# Constantes Recalibradas para o Histórico Longo
ALPHA = 3.4   
BETA = 0.18   
DELTA = 0.5   

@st.cache_data(ttl=3600)
def carregar_dados_mercado(meses):
    try:
        hoje = datetime.now()
        inicio = hoje - relativedelta(months=meses)
        inicio_str = inicio.strftime('%Y-%m-%d')
        
        # 1. Busca Juros e M2 (FRED)
        url_j = f"https://api.stlouisfed.org/fred/series/observations?series_id=DFII10&api_key={FRED_API_KEY}&file_type=json&observation_start={inicio_str}"
        url_m = f"https://api.stlouisfed.org/fred/series/observations?series_id=WM2NS&api_key={FRED_API_KEY}&file_type=json&observation_start={inicio_str}"
        
        resp_j = requests.get(url_j).json().get('observations', [])
        resp_m = requests.get(url_m).json().get('observations', [])
        if not resp_j or not resp_m: return None

        # 2. Busca Preço Histórico Paginado (Binance - Suporta até 120+ meses)
        start_ms = int(inicio.timestamp() * 1000)
        end_ms = int(hoje.timestamp() * 1000)
        dados_btc = []
        
        while start_ms < end_ms:
            url_b = f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&startTime={start_ms}&endTime={end_ms}&limit=1000"
            resp_b = requests.get(url_b).json()
            if not resp_b or isinstance(resp_b, dict): break
            for c in resp_b:
                dados_btc.append({"date": datetime.fromtimestamp(c[0]/1000.0), "Preco": float(c[4])})
            start_ms = resp_b[-1][0] + 86400000 # Avança 1 dia para a próxima página
            time.sleep(0.1) # Pausa rápida para a Binance não bloquear seu IP
        
        # 3. Busca Dificuldade de Mineração (Blockchain)
        url_d = f"https://api.blockchain.info/charts/difficulty?timespan={meses}months&format=json&sampled=true"
        resp_d = requests.get(url_d).json().get('values', [])

        # 4. Tratamento de Dados (Pandas)
        df_j = pd.DataFrame(resp_j)[['date', 'value']].rename(columns={'value':'Juro'}).dropna()
        df_j['date'], df_j['Juro'] = pd.to_datetime(df_j['date']), pd.to_numeric(df_j['Juro'], errors='coerce')
        
        df_m = pd.DataFrame(resp_m)[['date', 'value']].rename(columns={'value':'M2'}).dropna()
        df_m['date'], df_m['M2'] = pd.to_datetime(df_m['date']), pd.to_numeric(df_m['M2'], errors='coerce')
        
        df_btc = pd.DataFrame(dados_btc)
        df_btc['date'] = pd.to_datetime(df_btc['date'])
        
        df_diff = pd.DataFrame([{"date": datetime.fromtimestamp(p['x']), "Diff": p['y']/1e12} for p in resp_d])
        df_diff['date'] = pd.to_datetime(df_diff['date'])

        df_final = df_j.set_index('date').join(
                   df_m.set_index('date'), how='outer').join(
                   df_btc.set_index('date'), how='outer').join(
                   df_diff.set_index('date'), how='outer').ffill().dropna()
        return df_final
    except Exception as e:
        st.error(f"Erro na extração de dados: {e}")
        return None

def buscar_preco_live():
    """Busca o preço exato do segundo atual sem passar pelo cache"""
    try: 
        return float(requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT").json()['price'])
    except: 
        return None

# ==========================================
# 2. INTERFACE E PROCESSAMENTO DA TEORIA OEM
# ==========================================
st.sidebar.title("⚙️ Controle OEM")
aba_selecionada = st.sidebar.radio("Modo de Operação", ["Monitoramento Live", "Prova Matemática (Backtest)"])

# SLIDER ATUALIZADO PARA 120 MESES (10 ANOS)
meses = st.sidebar.slider("Janela de Análise (Meses)", 12, 120, 48, step=12)

df_hist = carregar_dados_mercado(meses)

if df_hist is not None:
    # --- CÁLCULO DO MOTOR OEM ---
    dados_oem = []
    for d, r in df_hist.iterrows():
        # Curva Logística de Adoção
        anos_g = (d - DATA_GENESIS).days / 365.25
        m2_g = (r['M2']/1000)*4.8
        penet = 0.05 / (1 + math.exp(-0.4 * (anos_g - 10)))
        liq_e = m2_g * penet * 100 
        
        # Amortecimento do Halving
        m_halv = (d - DATA_HALVING).days / 30.44
        f_amort = 1 + math.log10(max(1, anos_g/4))
        f_ciclo = 1 + ((BETA/f_amort) * math.cos((2*math.pi*m_halv)/48))
        
        # Choque de Oferta (Escassez On-Chain)
        f_esc = 1 + (0.02 * max(0, (d - DATA_PICO_EXCHANGES).days/365.25)) 
        
        # Motor Base
        den = max(0.1, r['Juro'] + DELTA)
        p_oem = ALPHA * (liq_e/den) * f_ciclo * r['Diff'] * f_esc
        
        dados_oem.append({"Data": d, "OEM": p_oem, "Mercado": r['Preco']})
    
    df_plot = pd.DataFrame(dados_oem)

    # ==========================================
    # ABA 1: MONITORAMENTO LIVE
    # ==========================================
    if aba_selecionada == "Monitoramento Live":
        st.title("📡 Terminal OEM - Tempo Real")
        caixa = st.sidebar.number_input("Caixa (USD)", value=10000.0)
        risco = st.sidebar.slider("Agressividade", 1, 5, 3)
        
        # Injeta o preço do segundo atual na última linha do gráfico
        preco_agora = buscar_preco_live()
        if preco_agora: df_plot.iloc[-1, df_plot.columns.get_loc('Mercado')] = preco_agora

        u = df_plot.iloc[-1]
        delta = (u['OEM'] - u['Mercado']) / u['OEM']
        aporte = caixa * max(0, delta * (risco/5))

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("OEM (Valor Justo)", f"US$ {u['OEM']:,.0f}")
        c2.metric("Preço Mercado", f"US$ {u['Mercado']:,.0f}", f"{delta*100:.2f}%")
        with c3:
            if delta > 0.10: st.success("🟢 COMPRA FORTE")
            elif delta > 0.0: st.warning("🟡 ACUMULAR")
            elif delta > -0.10: st.info("🔵 DCA (MANUTENÇÃO)")
            else: st.error("🔴 BOLHA / REALIZAR LUCRO")
        c4.metric("Aporte Sugerido", f"US$ {aporte:,.2f}")

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=df_plot['Data'], y=df_plot['OEM'], name='Valor Justo (OEM)', line=dict(color='#F7931A', width=3)))
        fig.add_trace(go.Scatter(x=df_plot['Data'], y=df_plot['Mercado'], name='Preço Mercado', line=dict(color='white', width=1.5, dash='dash')))
        fig.update_layout(template="plotly_dark", height=600, margin=dict(l=0, r=0, t=30, b=0))
        st.plotly_chart(fig, use_container_width=True)

    # ==========================================
    # ABA 2: BACKTEST (A PROVA MATEMÁTICA)
    # ==========================================
    elif aba_selecionada == "Prova Matemática (Backtest)":
        st.title("🧪 Motor de Backtest Institucional")
        st.markdown(f"Simulando investimento de **US$ 10.000** ao longo de **{meses} meses** com aportes dinâmicos (Tranches).")
        
        capital_inicial = 10000.0
        
        # --- Simulação 1: Buy & Hold ---
        preco_compra_bnh = df_plot.iloc[0]['Mercado']
        qtd_btc_bnh = capital_inicial / preco_compra_bnh
        df_plot['Patrimonio_BnH'] = df_plot['Mercado'] * qtd_btc_bnh
        
        # --- Simulação 2: Estratégia OEM ---
        caixa_oem = capital_inicial
        btc_oem = 0.0
        patrimonio_hist_oem = []

        for _, row in df_plot.iterrows():
            p_mercado = row['Mercado']
            p_justo = row['OEM']
            delta = (p_justo - p_mercado) / p_justo
            
            # REGRAS DE COMPRA (Escalonamento Dinâmico)
            if caixa_oem > 10: 
                if delta > 0.15: valor_compra = caixa_oem * 0.25 # Compra Agressiva 
                elif delta > 0.05: valor_compra = caixa_oem * 0.10 # Compra Moderada 
                elif delta > -0.05: valor_compra = caixa_oem * 0.02 # Pinga-pinga (Manutenção)
                else: valor_compra = 0
                
                if valor_compra > 0:
                    btc_oem += valor_compra / p_mercado
                    caixa_oem -= valor_compra
                
            # REGRAS DE VENDA (Realização de Lucro)
            if btc_oem > 0:
                if delta < -0.30: qtd_vender = btc_oem * 0.50 # Despejo na Bolha Severa
                elif delta < -0.15: qtd_vender = btc_oem * 0.15 # Venda leve de topo
                else: qtd_vender = 0
                    
                if qtd_vender > 0:
                    valor_venda = qtd_vender * p_mercado
                    caixa_oem += valor_venda
                    btc_oem -= qtd_vender
                
            patrimonio_hist_oem.append(caixa_oem + (btc_oem * p_mercado))
            
        df_plot['Patrimonio_OEM'] = patrimonio_hist_oem

        # --- Cálculo das Métricas de Risco ---
        lucro_bnh = ((df_plot['Patrimonio_BnH'].iloc[-1] - capital_inicial) / capital_inicial) * 100
        lucro_oem = ((df_plot['Patrimonio_OEM'].iloc[-1] - capital_inicial) / capital_inicial) * 100
        
        dd_bnh = ((df_plot['Patrimonio_BnH'] / df_plot['Patrimonio_BnH'].cummax()) - 1).min() * 100
        dd_oem = ((df_plot['Patrimonio_OEM'] / df_plot['Patrimonio_OEM'].cummax()) - 1).min() * 100

        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Estratégia Buy & Hold")
            st.metric("Retorno Final", f"{lucro_bnh:.1f}%")
            st.metric("Drawdown Máximo (Risco)", f"{dd_bnh:.1f}%", delta_color="inverse")
        with c2:
            st.subheader("Estratégia Teoria OEM")
            st.metric("Retorno Final", f"{lucro_oem:.1f}%")
            st.metric("Drawdown Máximo (Risco)", f"{dd_oem:.1f}%", delta_color="inverse")

        # Gráfico Comparativo de Patrimônio
        fig_bt = go.Figure()
        fig_bt.add_trace(go.Scatter(x=df_plot['Data'], y=df_plot['Patrimonio_BnH'], name='Buy & Hold', line=dict(color='#888888', dash='dash')))
        fig_bt.add_trace(go.Scatter(x=df_plot['Data'], y=df_plot['Patrimonio_OEM'], name='Estratégia OEM', line=dict(color='#00FF00', width=3)))
        fig_bt.update_layout(template="plotly_dark", title="Crescimento de Patrimônio (US$)", yaxis_title="Saldo em Dólar", hovermode="x unified")
        st.plotly_chart(fig_bt, use_container_width=True)

else:
    st.error("Falha ao carregar APIs (Possível instabilidade no Banco Central). Aguarde um minuto e recarregue a página.")