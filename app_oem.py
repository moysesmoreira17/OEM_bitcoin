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
st.set_page_config(page_title="Terminal Quantitativo OEM", layout="wide")

FRED_API_KEY = st.secrets["FRED_API_KEY"]

DATA_HALVING = datetime(2024, 4, 19)
DATA_GENESIS = datetime(2009, 1, 3)
DATA_PICO_EXCHANGES = datetime(2020, 3, 12)

ALPHA = 3.4   
BETA = 0.18   
DELTA = 0.5   

@st.cache_data(ttl=3600)
def carregar_dados_mercado(meses):
    try:
        hoje = datetime.now()
        inicio = hoje - relativedelta(months=meses)
        inicio_str = inicio.strftime('%Y-%m-%d')
        
        url_j = f"https://api.stlouisfed.org/fred/series/observations?series_id=DFII10&api_key={FRED_API_KEY}&file_type=json&observation_start={inicio_str}"
        url_m = f"https://api.stlouisfed.org/fred/series/observations?series_id=WM2NS&api_key={FRED_API_KEY}&file_type=json&observation_start={inicio_str}"
        
        resp_j = requests.get(url_j).json().get('observations', [])
        resp_m = requests.get(url_m).json().get('observations', [])
        if not resp_j or not resp_m: 
            raise ValueError("Falha de conexão com o Banco Central Americano (FRED).")

        start_ms = int(inicio.timestamp() * 1000)
        end_ms = int(hoje.timestamp() * 1000)
        dados_btc = []
        
        headers_falsos = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        
        tentativas = 0
        while start_ms < end_ms and tentativas < 3:
            url_b = f"https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1d&startTime={start_ms}&endTime={end_ms}&limit=1000"
            resposta = requests.get(url_b, headers=headers_falsos)
            
            if resposta.status_code != 200:
                tentativas += 1
                time.sleep(2)
                continue
                
            resp_b = resposta.json()
            if not resp_b or isinstance(resp_b, dict): break
            
            for c in resp_b:
                dados_btc.append({"date": datetime.fromtimestamp(c[0]/1000.0), "Preco": float(c[4])})
                
            start_ms = resp_b[-1][0] + 86400000 
            time.sleep(0.3) 
            
        if not dados_btc:
            raise ValueError("A API da Binance bloqueou temporariamente o IP da nuvem (Rate Limit).")

        url_d = f"https://api.blockchain.info/charts/difficulty?timespan={meses}months&format=json&sampled=true"
        resp_d = requests.get(url_d).json().get('values', [])
        if not resp_d:
            raise ValueError("A API da Blockchain.info não retornou os dados de mineração.")

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
        st.error(f"🛑 Interceptação de Segurança: {e}")
        return None

def buscar_preco_live():
    try: 
        headers = {'User-Agent': 'Mozilla/5.0'}
        return float(requests.get("https://data-api.binance.vision/api/v3/ticker/price?symbol=BTCUSDT", headers=headers).json()['price'])
    except: 
        return None

# ==========================================
# 2. INTERFACE E PROCESSAMENTO
# ==========================================
st.sidebar.title("⚙️ Controle OEM")
aba_selecionada = st.sidebar.radio("Modo de Operação", ["Monitoramento Live", "Prova Matemática (Backtest)"])
meses = st.sidebar.slider("Janela de Análise (Meses)", 12, 120, 48, step=1)
risco = st.sidebar.slider("Agressividade Dinâmica", 1.0, 5.0, 3.0, step=0.5)

st.sidebar.markdown("---")
st.sidebar.subheader("💼 Seu Portfólio")
caixa = st.sidebar.number_input("Saldo em Caixa (USD)", value=10000.0, step=100.0)
saldo_btc = st.sidebar.number_input("Saldo em Bitcoin (BTC)", value=0.1000, step=0.0100, format="%.4f")

df_hist = carregar_dados_mercado(meses)

if df_hist is not None:
    dados_oem = []
    for d, r in df_hist.iterrows():
        anos_g = (d - DATA_GENESIS).days / 365.25
        m2_g = (r['M2']/1000)*4.8
        penet = 0.05 / (1 + math.exp(-0.4 * (anos_g - 10)))
        liq_e = m2_g * penet * 100 
        m_halv = (d - DATA_HALVING).days / 30.44
        f_amort = 1 + math.log10(max(1, anos_g/4))
        f_ciclo = 1 + ((BETA/f_amort) * math.cos((2*math.pi*m_halv)/48))
        f_esc = 1 + (0.02 * max(0, (d - DATA_PICO_EXCHANGES).days/365.25)) 
        den = max(0.1, r['Juro'] + DELTA)
        p_oem = ALPHA * (liq_e/den) * f_ciclo * r['Diff'] * f_esc
        dados_oem.append({"Data": d, "OEM": p_oem, "Mercado": r['Preco']})
    
    df_plot = pd.DataFrame(dados_oem)

    # ==========================================
    # ABA 1: LIVE (COM FUNÇÃO CONTÍNUA)
    # ==========================================
    if aba_selecionada == "Monitoramento Live":
        st.title("📡 Terminal OEM - Tempo Real")
        
        preco_agora = buscar_preco_live()
        if preco_agora: df_plot.iloc[-1, df_plot.columns.get_loc('Mercado')] = preco_agora

        u = df_plot.iloc[-1]
        delta = (u['OEM'] - u['Mercado']) / u['OEM']
        
        # --- CÁLCULO DINÂMICO E CONTÍNUO ---
        acao_cor = "white"
        
        if delta > 0.02:
            # COMPRA: % atrelada ao tamanho do Delta e limitada a 90%
            porcentagem = min(0.90, delta * (risco / 2))
            status = "🟢 COMPRA ELASTICA"
            recomendacao = f"Compre US$ {caixa * porcentagem:,.2f} ({porcentagem*100:.1f}% do Caixa)"
            acao_cor = "#00FF00"
            
        elif delta < -0.10:
            # VENDA: % atrelada ao tamanho do Delta (abs converte negativo pra positivo)
            porcentagem = min(0.90, abs(delta) * (risco / 2))
            status = "🔴 VENDA ELASTICA"
            qtd_venda = saldo_btc * porcentagem
            recomendacao = f"Venda {qtd_venda:.4f} BTC (Receba ~US$ {qtd_venda * u['Mercado']:,.2f})"
            acao_cor = "#FF0000"
            
        else:
            # ZONA NEUTRA
            status = "🔵 DCA (MANUTENÇÃO)"
            recomendacao = f"Compre apenas US$ {caixa * 0.01:,.2f} (1% do Caixa)"
            acao_cor = "#00BFFF"

        c1, c2, c3 = st.columns(3)
        c1.metric("Preço Justo (OEM)", f"US$ {u['OEM']:,.0f}")
        c2.metric("Preço Mercado (Binance)", f"US$ {u['Mercado']:,.0f}", f"{delta*100:.2f}% (Delta)")
        
        with c3:
            st.markdown(f"<h3 style='text-align: center; color: {acao_cor}; margin-bottom: 0px;'>{status}</h3>", unsafe_allow_html=True)
            st.markdown(f"<p style='text-align: center; font-size: 18px;'><b>Ação:</b> {recomendacao}</p>", unsafe_allow_html=True)

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=df_plot['Data'], y=df_plot['OEM'], name='Valor Justo (OEM)', line=dict(color='#F7931A', width=3)))
        fig.add_trace(go.Scatter(x=df_plot['Data'], y=df_plot['Mercado'], name='Preço Mercado', line=dict(color='white', width=1.5, dash='dash')))
        fig.update_layout(template="plotly_dark", height=600, margin=dict(l=0, r=0, t=30, b=0))
        st.plotly_chart(fig, use_container_width=True)

    # ==========================================
    # ABA 2: BACKTEST (COM FUNÇÃO CONTÍNUA)
    # ==========================================
    elif aba_selecionada == "Prova Matemática (Backtest)":
        st.title("🧪 Motor de Backtest Institucional")
        st.markdown(f"Simulando investimento de **US$ 10.000** ao longo de **{meses} meses** usando alocação dinâmica elástica.")
        
        capital_inicial = 10000.0
        
        preco_compra_bnh = df_plot.iloc[0]['Mercado']
        qtd_btc_bnh = capital_inicial / preco_compra_bnh
        df_plot['Patrimonio_BnH'] = df_plot['Mercado'] * qtd_btc_bnh
        
        caixa_oem = capital_inicial
        btc_oem = 0.0
        patrimonio_hist_oem = []

        for _, row in df_plot.iterrows():
            p_mercado = row['Mercado']
            p_justo = row['OEM']
            delta = (p_justo - p_mercado) / p_justo
            
            # --- Regras Dinâmicas no Backtest ---
            if caixa_oem > 10: 
                if delta > 0.02:
                    porc_compra = min(0.90, delta * (risco / 2))
                    valor_compra = caixa_oem * porc_compra
                elif delta > -0.10:
                    valor_compra = caixa_oem * 0.01
                else:
                    valor_compra = 0
                
                if valor_compra > 0:
                    btc_oem += valor_compra / p_mercado
                    caixa_oem -= valor_compra
                
            if btc_oem > 0:
                if delta <= -0.10:
                    porc_venda = min(0.90, abs(delta) * (risco / 2))
                    qtd_vender = btc_oem * porc_venda
                else:
                    qtd_vender = 0
                    
                if qtd_vender > 0:
                    valor_venda = qtd_vender * p_mercado
                    caixa_oem += valor_venda
                    btc_oem -= qtd_vender
                
            patrimonio_hist_oem.append(caixa_oem + (btc_oem * p_mercado))
            
        df_plot['Patrimonio_OEM'] = patrimonio_hist_oem

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

        fig_bt = go.Figure()
        fig_bt.add_trace(go.Scatter(x=df_plot['Data'], y=df_plot['Patrimonio_BnH'], name='Buy & Hold', line=dict(color='#888888', dash='dash')))
        fig_bt.add_trace(go.Scatter(x=df_plot['Data'], y=df_plot['Patrimonio_OEM'], name='Estratégia OEM', line=dict(color='#00FF00', width=3)))
        fig_bt.update_layout(template="plotly_dark", title="Crescimento de Patrimônio (US$)", yaxis_title="Saldo em Dólar", hovermode="x unified")
        st.plotly_chart(fig_bt, use_container_width=True)

else:
    st.info("🔄 Aguardando conexão estável com as APIs... O servidor tentará carregar novamente em breve.")
