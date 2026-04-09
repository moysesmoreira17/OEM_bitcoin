import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime
from dateutil.relativedelta import relativedelta
import requests
import math
import time
import yfinance as yf

# ==========================================
# 1. CONFIGURAÇÃO BASE
# ==========================================
st.set_page_config(page_title="Terminal OEM v4.0 - Chameleon", layout="wide")

# Chave protegida no Cofre da Nuvem
FRED_API_KEY = st.secrets["FRED_API_KEY"]

DATA_HALVING = datetime(2024, 4, 19)
DATA_GENESIS = datetime(2009, 1, 3)
DATA_PICO_EXCHANGES = datetime(2020, 3, 12)

# ==========================================
# 2. EXTRAÇÃO DE DADOS (APIs)
# ==========================================
@st.cache_data(ttl=3600)
def carregar_dados_completos(meses):
    try:
        hoje = datetime.now()
        inicio = hoje - relativedelta(months=meses)
        inicio_str = inicio.strftime('%Y-%m-%d')
        
        # 1. FRED (Juros e M2)
        url_j = f"https://api.stlouisfed.org/fred/series/observations?series_id=DFII10&api_key={FRED_API_KEY}&file_type=json&observation_start={inicio_str}"
        url_m = f"https://api.stlouisfed.org/fred/series/observations?series_id=WM2NS&api_key={FRED_API_KEY}&file_type=json&observation_start={inicio_str}"
        resp_j = requests.get(url_j).json().get('observations', [])
        resp_m = requests.get(url_m).json().get('observations', [])
        if not resp_j or not resp_m: raise ValueError("Falha na API do FRED.")

        # 2. Yahoo Finance (DXY - Índice do Dólar)
        try:
            dxy_df = yf.Ticker("DX-Y.NYB").history(start=inicio_str)[['Close']].rename(columns={'Close':'DXY'})
            dxy_df.index = dxy_df.index.tz_localize(None).normalize()
        except:
            dxy_df = pd.DataFrame(columns=['DXY'])

        # 3. Binance (Preço Histórico)
        start_ms = int(inicio.timestamp() * 1000)
        end_ms = int(hoje.timestamp() * 1000)
        dados_btc = []
        headers_falsos = {'User-Agent': 'Mozilla/5.0'}
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
        if not dados_btc: raise ValueError("Rate Limit da Binance.")

        # 4. Blockchain (Dificuldade)
        url_d = f"https://api.blockchain.info/charts/difficulty?timespan={meses}months&format=json&sampled=true"
        resp_d = requests.get(url_d).json().get('values', [])

        # 5. Tratamento de Dados
        df_j = pd.DataFrame(resp_j)[['date', 'value']].rename(columns={'value':'Juro'}).dropna()
        df_j['date'] = pd.to_datetime(df_j['date'])
        df_j = df_j.set_index('date')
        df_j['Juro'] = pd.to_numeric(df_j['Juro'], errors='coerce')
        
        df_m = pd.DataFrame(resp_m)[['date', 'value']].rename(columns={'value':'M2'}).dropna()
        df_m['date'] = pd.to_datetime(df_m['date'])
        df_m = df_m.set_index('date')
        df_m['M2'] = pd.to_numeric(df_m['M2'], errors='coerce')
        
        df_btc = pd.DataFrame(dados_btc).set_index('date')
        
        df_diff = pd.DataFrame([{"date": datetime.fromtimestamp(p['x']), "Diff": p['y']/1e12} for p in resp_d])
        df_diff = df_diff.set_index('date')

        # Mesclando todas as fontes
        df = df_btc.join([df_j, df_m, dxy_df, df_diff], how='outer').ffill().dropna()
        
        # Criando o Indicador de Volatilidade (Desvio Padrão de 30 dias anualizado)
        df['Vol'] = df['Preco'].pct_change().rolling(30).std() * math.sqrt(365)
        return df.dropna()
    except Exception as e:
        st.error(f"🛑 Interceptação de Segurança: {e}")
        return None

def buscar_preco_live():
    try: 
        return float(requests.get("https://data-api.binance.vision/api/v3/ticker/price?symbol=BTCUSDT", headers={'User-Agent': 'Mozilla/5.0'}).json()['price'])
    except: return None

def buscar_dxy_live():
    try:
        return float(yf.Ticker("DX-Y.NYB").history(period="1d")['Close'].iloc[-1])
    except: return 100.0

# ==========================================
# 3. MOTOR CAMALEÃO (PROCESSAMENTO)
# ==========================================
def processar_modelo(df):
    resultados = []
    vol_media = df['Vol'].mean()

    for d, r in df.iterrows():
        # --- Lógica de Alternância de Regime ---
        if r['DXY'] > 104.5 or r['Juro'] > 4.5:
            a_regime, d_regime = 2.8, 0.75  # Defensivo (Crise)
            nome_regime = "CRISE MACRO"
        elif r['DXY'] < 101.5 and r['Juro'] < 3.0:
            a_regime, d_regime = 4.0, 0.35  # Agressivo (Expansão)
            nome_regime = "EXPANSÃO DE LIQUIDEZ"
        else:
            a_regime, d_regime = 3.4, 0.50  # Padrão (Neutro)
            nome_regime = "MERCADO NEUTRO"

        # --- Equação OEM ---
        anos_g = (d - DATA_GENESIS).days / 365.25
        m2_g = (r['M2']/1000)*4.8
        penet = 0.05 / (1 + math.exp(-0.4 * (anos_g - 10)))
        liq_e = m2_g * penet * 100 
        m_halv = (d - DATA_HALVING).days / 30.44
        f_amort = 1 + math.log10(max(1, anos_g/4))
        f_ciclo = 1 + (0.18/f_amort * math.cos((2*math.pi*m_halv)/48))
        f_esc = 1 + (0.02 * max(0, (d - DATA_PICO_EXCHANGES).days/365.25)) 
        den = max(0.1, r['Juro'] + d_regime)
        fator_dxy = 100.0 / max(50.0, r['DXY'])
        
        p_oem = a_regime * (liq_e/den) * f_ciclo * r['Diff'] * f_esc * fator_dxy
        
        # --- Fator de Volatilidade (Risk Parity) ---
        fator_vol = vol_media / max(0.01, r['Vol'])
        
        resultados.append({
            "Data": d, "OEM": p_oem, "Mercado": r['Preco'], 
            "Regime": nome_regime, "FatorVol": fator_vol, "Vol": r['Vol']
        })
    return pd.DataFrame(resultados)

# ==========================================
# 4. INTERFACE DO USUÁRIO
# ==========================================
st.sidebar.title("🧬 OEM Chameleon v4.0")
aba_selecionada = st.sidebar.radio("Modo", ["Monitoramento Live", "Prova Matemática (Backtest)"])
meses = st.sidebar.slider("Janela de Análise (Meses)", 12, 120, 48, step=1)
risco_user = st.sidebar.slider("Agressividade Dinâmica", 1.0, 5.0, 3.0, step=0.5)

st.sidebar.markdown("---")
st.sidebar.subheader("💼 Seu Portfólio")
caixa = st.sidebar.number_input("Saldo em Caixa (USD)", value=10000.0, step=100.0)
saldo_btc = st.sidebar.number_input("Saldo em Bitcoin (BTC)", value=0.1000, step=0.0100, format="%.4f")

df_raw = carregar_dados_completos(meses)

if df_raw is not None:
    df_final = processar_modelo(df_raw)

    # ABA 1: LIVE
    if aba_selecionada == "Monitoramento Live":
        # Injeção de dados ao vivo no último minuto
        preco_agora = buscar_preco_live()
        if preco_agora: df_final.iloc[-1, df_final.columns.get_loc('Mercado')] = preco_agora
        
        u = df_final.iloc[-1]
        delta = (u['OEM'] - u['Mercado']) / u['OEM']

        # --- Lógica de Gestão com Volatilidade ---
        if delta > 0.02:
            perc_base = min(0.90, delta * (risco_user / 2))
            perc_final = perc_base * min(1.0, u['FatorVol']) # Trava de segurança Anti-Faca
            status, cor = "🟢 COMPRA INTELIGENTE", "#00FF00"
            txt = f"Compre US$ {caixa * perc_final:,.2f} ({perc_final*100:.1f}% do Caixa)"
        elif delta < -0.10:
            perc_base = min(0.90, abs(delta) * (risco_user / 2))
            perc_final = perc_base * min(1.0, u['FatorVol'])
            status, cor = "🔴 VENDA INTELIGENTE", "#FF0000"
            txt = f"Venda {saldo_btc * perc_final:.4f} BTC (Receba ~US$ {saldo_btc * perc_final * u['Mercado']:,.2f})"
        else:
            status, cor = "🔵 DCA / MANUTENÇÃO", "#00BFFF"
            perc_dca = 0.01 * min(1.0, u['FatorVol'])
            txt = f"Compre apenas US$ {caixa * perc_dca:,.2f} (~1% do Caixa)"

        st.title(f"📡 Status de Regime: {u['Regime']}")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Preço Justo (OEM)", f"US$ {u['OEM']:,.0f}")
        c2.metric("Preço Mercado", f"US$ {u['Mercado']:,.0f}", f"{delta*100:.2f}% (Delta)")
        c3.metric("Volatilidade Anual", f"{u['Vol']*100:.1f}%", "Fator de Risco" if u['FatorVol'] < 1 else "Estável", delta_color="inverse")
        
        with c4:
            st.markdown(f"<div style='text-align:center; background:{cor}22; padding:10px; border-radius:10px; border:1px solid {cor}'><b>{status}</b><br>{txt}</div>", unsafe_allow_html=True)

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=df_final['Data'], y=df_final['OEM'], name='Valor Justo (OEM)', line=dict(color='#F7931A', width=3)))
        fig.add_trace(go.Scatter(x=df_final['Data'], y=df_final['Mercado'], name='Preço Mercado', line=dict(color='white', width=1.5, dash='dash')))
        fig.update_layout(template="plotly_dark", height=600, margin=dict(l=0, r=0, t=30, b=0))
        st.plotly_chart(fig, use_container_width=True)

    # ABA 2: BACKTEST COMPLETO
    elif aba_selecionada == "Prova Matemática (Backtest)":
        st.title("🧪 Backtest Institucional (Com Volatilidade)")
        st.markdown(f"Simulando investimento de **US$ 10.000** ao longo de **{meses} meses** com inteligência Camaleão.")
        
        capital_inicial = 10000.0
        preco_compra_bnh = df_final.iloc[0]['Mercado']
        qtd_btc_bnh = capital_inicial / preco_compra_bnh
        df_final['Patrimonio_BnH'] = df_final['Mercado'] * qtd_btc_bnh
        
        caixa_oem = capital_inicial
        btc_oem = 0.0
        patrimonio_hist_oem = []

        for _, row in df_final.iterrows():
            p_mercado = row['Mercado']
            p_justo = row['OEM']
            delta = (p_justo - p_mercado) / p_justo
            fator_vol = min(1.0, row['FatorVol'])
            
            # --- Regras Dinâmicas V4.0 ---
            if caixa_oem > 10: 
                if delta > 0.02:
                    perc_base = min(0.90, delta * (risco_user / 2))
                    valor_compra = caixa_oem * (perc_base * fator_vol)
                elif delta > -0.10:
                    valor_compra = caixa_oem * (0.01 * fator_vol) # DCA
                else:
                    valor_compra = 0
                
                if valor_compra > 0:
                    btc_oem += valor_compra / p_mercado
                    caixa_oem -= valor_compra
                
            if btc_oem > 0:
                if delta <= -0.10:
                    perc_base = min(0.90, abs(delta) * (risco_user / 2))
                    qtd_vender = btc_oem * (perc_base * fator_vol)
                else:
                    qtd_vender = 0
                    
                if qtd_vender > 0:
                    caixa_oem += qtd_vender * p_mercado
                    btc_oem -= qtd_vender
                
            patrimonio_hist_oem.append(caixa_oem + (btc_oem * p_mercado))
            
        df_final['Patrimonio_OEM'] = patrimonio_hist_oem

        lucro_bnh = ((df_final['Patrimonio_BnH'].iloc[-1] - capital_inicial) / capital_inicial) * 100
        lucro_oem = ((df_final['Patrimonio_OEM'].iloc[-1] - capital_inicial) / capital_inicial) * 100
        dd_bnh = ((df_final['Patrimonio_BnH'] / df_final['Patrimonio_BnH'].cummax()) - 1).min() * 100
        dd_oem = ((df_final['Patrimonio_OEM'] / df_final['Patrimonio_OEM'].cummax()) - 1).min() * 100

        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Estratégia Buy & Hold")
            st.metric("Retorno Final", f"{lucro_bnh:.1f}%")
            st.metric("Drawdown Máximo (Risco)", f"{dd_bnh:.1f}%", delta_color="inverse")
        with c2:
            st.subheader("Estratégia V4.0 (Chameleon)")
            st.metric("Retorno Final", f"{lucro_oem:.1f}%")
            st.metric("Drawdown Máximo (Risco)", f"{dd_oem:.1f}%", delta_color="inverse")

        fig_bt = go.Figure()
        fig_bt.add_trace(go.Scatter(x=df_final['Data'], y=df_final['Patrimonio_BnH'], name='Buy & Hold', line=dict(color='#888888', dash='dash')))
        fig_bt.add_trace(go.Scatter(x=df_final['Data'], y=df_final['Patrimonio_OEM'], name='Estratégia OEM', line=dict(color='#00FF00', width=3)))
        fig_bt.update_layout(template="plotly_dark", title="Crescimento de Patrimônio (US$)", yaxis_title="Saldo em Dólar", hovermode="x unified")
        st.plotly_chart(fig_bt, use_container_width=True)

else:
    st.info("🔄 Carregando dados ou aguardando restabelecimento das APIs...")
