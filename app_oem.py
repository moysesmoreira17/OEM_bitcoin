import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime
from dateutil.relativedelta import relativedelta
import requests
import math
import time
import yfinance as yf

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
        
        # 1. FRED
        url_j = f"https://api.stlouisfed.org/fred/series/observations?series_id=DFII10&api_key={FRED_API_KEY}&file_type=json&observation_start={inicio_str}"
        url_m = f"https://api.stlouisfed.org/fred/series/observations?series_id=WM2NS&api_key={FRED_API_KEY}&file_type=json&observation_start={inicio_str}"
        resp_j = requests.get(url_j).json().get('observations', [])
        resp_m = requests.get(url_m).json().get('observations', [])

        # 2. Binance
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

        # 3. Blockchain (Dificuldade)
        url_d = f"https://api.blockchain.info/charts/difficulty?timespan={meses}months&format=json&sampled=true"
        resp_d = requests.get(url_d).json().get('values', [])

        # 4. Yahoo Finance (DXY - Índice do Dólar em Tempo Real)
        try:
            dxy_raw = yf.Ticker("DX-Y.NYB").history(start=inicio_str)[['Close']]
            dxy_raw.index = dxy_raw.index.tz_localize(None).normalize()
            df_dxy = pd.DataFrame({'DXY': dxy_raw['Close']})
            df_dxy.index.name = 'date'
        except Exception as e:
            st.warning(f"Aviso: Não foi possível carregar o DXY em tempo real. Usando linha base 100. Erro: {e}")
            df_dxy = pd.DataFrame(columns=['DXY'])
            df_dxy.index.name = 'date'

        # 5. Tratamento
        df_j = pd.DataFrame(resp_j)[['date', 'value']].rename(columns={'value':'Juro'}).dropna()
        df_j['date'], df_j['Juro'] = pd.to_datetime(df_j['date']), pd.to_numeric(df_j['Juro'], errors='coerce')
        
        df_m = pd.DataFrame(resp_m)[['date', 'value']].rename(columns={'value':'M2'}).dropna()
        df_m['date'], df_m['M2'] = pd.to_datetime(df_m['date']), pd.to_numeric(df_m['M2'], errors='coerce')
        
        df_btc = pd.DataFrame(dados_btc)
        df_btc['date'] = pd.to_datetime(df_btc['date'])
        
        df_diff = pd.DataFrame([{"date": datetime.fromtimestamp(p['x']), "Diff": p['y']/1e12} for p in resp_d])
        df_diff['date'] = pd.to_datetime(df_diff['date'])

        # Juntando tudo (agora com o DXY)
        df_final = df_j.set_index('date').join(
                   df_m.set_index('date'), how='outer').join(
                   df_dxy, how='outer').join(
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

def buscar_dxy_live():
    try:
        return float(yf.Ticker("DX-Y.NYB").history(period="1d")['Close'].iloc[-1])
    except:
        return 100.0 # Fallback caso a API falhe

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
        
        # O Fator DXY atua como a lupa em tempo real sobre a liquidez atrasada
        dxy_atual = r['DXY'] if not pd.isna(r['DXY']) else 100.0
        fator_dxy = 100.0 / max(50.0, dxy_atual) 
        
        m2_g = (r['M2']/1000)*4.8
        penet = 0.05 / (1 + math.exp(-0.4 * (anos_g - 10)))
        liq_e = m2_g * penet * 100 
        m_halv = (d - DATA_HALVING).days / 30.44
        f_amort = 1 + math.log10(max(1, anos_g/4))
        f_ciclo = 1 + ((BETA/f_amort) * math.cos((2*math.pi*m_halv)/48))
        f_esc = 1 + (0.02 * max(0, (d - DATA_PICO_EXCHANGES).days/365.25)) 
        den = max(0.1, r['Juro'] + DELTA)
        
        # A nova equação com o Sensor Real Time
        p_oem = ALPHA * (liq_e/den) * f_ciclo * r['Diff'] * f_esc * fator_dxy
        
        dados_oem.append({"Data": d, "OEM": p_oem, "Mercado": r['Preco'], "DXY": dxy_atual})
    
    df_plot = pd.DataFrame(dados_oem)

    # ==========================================
    # ABA 1: LIVE
    # ==========================================
    if aba_selecionada == "Monitoramento Live":
        st.title("📡 Terminal OEM - Tempo Real")
        
        preco_agora = buscar_preco_live()
        dxy_agora = buscar_dxy_live()
        
        if preco_agora: df_plot.iloc[-1, df_plot.columns.get_loc('Mercado')] = preco_agora
        df_plot.iloc[-1, df_plot.columns.get_loc('DXY')] = dxy_agora

        u = df_plot.iloc[-1]
        
        # Recálculo do OEM da última linha com o DXY dos últimos segundos
        fator_dxy_live = 100.0 / max(50.0, dxy_agora)
        oem_corrigido = u['OEM'] * (fator_dxy_live / (100.0 / max(50.0, u['DXY']))) if u['DXY'] != dxy_agora else u['OEM']
        
        delta = (oem_corrigido - u['Mercado']) / oem_corrigido
        
        acao_cor = "white"
        if delta > 0.02:
            porcentagem = min(0.90, delta * (risco / 2))
            status = "🟢 COMPRA ELASTICA"
            recomendacao = f"Compre US$ {caixa * porcentagem:,.2f} ({porcentagem*100:.1f}% do Caixa)"
            acao_cor = "#00FF00"
        elif delta < -0.10:
            porcentagem = min(0.90, abs(delta) * (risco / 2))
            status = "🔴 VENDA ELASTICA"
            qtd_venda = saldo_btc * porcentagem
            recomendacao = f"Venda {qtd_venda:.4f} BTC (Receba ~US$ {qtd_venda * u['Mercado']:,.2f})"
            acao_cor = "#FF0000"
        else:
            status = "🔵 DCA (MANUTENÇÃO)"
            recomendacao = f"Compre apenas US$ {caixa * 0.01:,.2f} (1% do Caixa)"
            acao_cor = "#00BFFF"

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Preço Justo (OEM)", f"US$ {oem_corrigido:,.0f}")
        c2.metric("Preço Mercado", f"US$ {u['Mercado']:,.0f}", f"{delta*100:.2f}% (Delta)")
        
        # Novo Termômetro DXY
        dxy_cor = "normal" if dxy_agora < 104 else "inverse" # Vermelho se dólar estiver muito forte (ruim pro BTC)
        c3.metric("Liquidez Global (DXY)", f"{dxy_agora:.2f} pts", delta_color=dxy_cor)
        
        with c4:
            st.markdown(f"<h4 style='text-align: center; color: {acao_cor}; margin-bottom: 0px;'>{status}</h4>", unsafe_allow_html=True)
            st.markdown(f"<p style='text-align: center; font-size: 14px;'><b>Ação:</b> {recomendacao}</p>", unsafe_allow_html=True)

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=df_plot['Data'], y=df_plot['OEM'], name='Valor Justo (OEM)', line=dict(color='#F7931A', width=3)))
        fig.add_trace(go.Scatter(x=df_plot['Data'], y=df_plot['Mercado'], name='Preço Mercado', line=dict(color='white', width=1.5, dash='dash')))
        fig.update_layout(template="plotly_dark", height=600, margin=dict(l=0, r=0, t=30, b=0))
        st.plotly_chart(fig, use_container_width=True)

    # ==========================================
    # ABA 2: BACKTEST
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
            
            if caixa_oem > 10: 
                if delta > 0.02: valor_compra = caixa_oem * min(0.90, delta * (risco / 2))
                elif delta > -0.10: valor_compra = caixa_oem * 0.01
                else: valor_compra = 0
                
                if valor_compra > 0:
                    btc_oem += valor_compra / p_mercado
                    caixa_oem -= valor_compra
                
            if btc_oem > 0:
                if delta <= -0.10: qtd_vender = btc_oem * min(0.90, abs(delta) * (risco / 2))
                else: qtd_vender = 0
                    
                if qtd_vender > 0:
                    caixa_oem += qtd_vender * p_mercado
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
