import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import requests
import math
import time
import yfinance as yf
import numpy as np
import itertools 

# ==========================================
# 0. PORTA DO COFRE (LOGIN INSTITUCIONAL)
# ==========================================
st.set_page_config(page_title="Terminal Quantitativo OEM (BRL)", layout="wide")

if 'autenticado' not in st.session_state:
    st.session_state.autenticado = False

if not st.session_state.autenticado:
    c1, c2, c3 = st.columns([1, 1, 1])
    with c2:
        st.markdown("<br><br><br>", unsafe_allow_html=True)
        st.title("🔐 Terminal OEM")
        st.markdown("Acesso restrito. Insira suas credenciais institucionais.")
        
        usuario = st.text_input("Usuário")
        senha = st.text_input("Senha", type="password")
        
        if st.button("Autenticar", use_container_width=True):
            if usuario in st.secrets and st.secrets[usuario] == senha:
                st.session_state.autenticado = True
                st.rerun() 
            else:
                st.error("Credenciais inválidas. Acesso negado.")
    st.stop()

# ==========================================
# 1. MEMÓRIA DE SESSÃO DA ESTRATÉGIA
# ==========================================
if 'opt_risco' not in st.session_state: st.session_state.opt_risco = 3.0
if 'opt_janela' not in st.session_state: st.session_state.opt_janela = 14
if 'opt_sens' not in st.session_state: st.session_state.opt_sens = 5.0
if 'opt_buy' not in st.session_state: st.session_state.opt_buy = 90
if 'opt_sell' not in st.session_state: st.session_state.opt_sell = 10
if 'opt_zscore' not in st.session_state: st.session_state.opt_zscore = 4.0

# ==========================================
# 2. CONFIGURAÇÃO E DADOS BASE
# ==========================================
FRED_API_KEY = st.secrets.get("FRED_API_KEY", "CHAVE_AUSENTE")

DATA_HALVING_GLOBAL = datetime(2024, 4, 19) 
DATA_PICO_EXCHANGES = datetime(2020, 3, 12)

# ==========================================
# INTERFACE: SELEÇÃO DE ATIVO E ABAS
# ==========================================
st.sidebar.title("⚙️ Controle OEM")
ativo_selecionado = st.sidebar.selectbox("🪙 Ativo Operacional", ["Bitcoin (BTC)", "Ethereum (ETH)"])

ticker_curto = "BTC" if ativo_selecionado == "Bitcoin (BTC)" else "ETH"

if ativo_selecionado == "Bitcoin (BTC)":
    SIMBOLO_BINANCE = "BTCUSDT"
    DATA_GENESIS = datetime(2009, 1, 3)
    ALPHA_ATIVO = 3.4
    BETA_ATIVO = 0.18
    DELTA_ATIVO = 0.5
else:
    SIMBOLO_BINANCE = "ETHUSDT"
    DATA_GENESIS = datetime(2015, 7, 30)
    ALPHA_ATIVO = 0.15   
    BETA_ATIVO = 0.28    
    DELTA_ATIVO = 0.5

@st.cache_data(ttl=3600)
def carregar_dados_mercado(meses, simbolo):
    erros_diag = [] 
    
    hoje = datetime.now()
    inicio = hoje - relativedelta(months=meses)
    inicio_query = inicio - relativedelta(days=400) 
    inicio_str = inicio_query.strftime('%Y-%m-%d')
    
    headers_seguros = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json'
    }

    def buscar_com_teimosia(url, nome_sensor):
        for tentativa in range(3):
            try:
                r = requests.get(url, headers=headers_seguros, timeout=12)
                if r.status_code == 200:
                    return r.json().get('observations', [])
            except: pass
            time.sleep(1.5)
        erros_diag.append(f"{nome_sensor} (Timeout/Falha)")
        return []

    url_j = f"https://api.stlouisfed.org/fred/series/observations?series_id=DFII10&api_key={FRED_API_KEY}&file_type=json&observation_start={inicio_str}"
    resp_j = buscar_com_teimosia(url_j, "FRED Juros")

    url_m = f"https://api.stlouisfed.org/fred/series/observations?series_id=WM2NS&api_key={FRED_API_KEY}&file_type=json&observation_start={inicio_str}"
    resp_m = buscar_com_teimosia(url_m, "FRED M2")

    start_ms = int(inicio_query.timestamp() * 1000)
    end_ms = int(hoje.timestamp() * 1000)
    dados_cripto = []
    tentativas = 0
    while start_ms < end_ms and tentativas < 3:
        url_b = f"https://data-api.binance.vision/api/v3/klines?symbol={simbolo}&interval=1d&startTime={start_ms}&endTime={end_ms}&limit=1000"
        try:
            resposta = requests.get(url_b, headers=headers_seguros, timeout=10)
            if resposta.status_code != 200:
                tentativas += 1; time.sleep(2); continue
            resp_b = resposta.json()
            if not resp_b or isinstance(resp_b, dict): break
            for c in resp_b:
                dados_cripto.append({"date": datetime.fromtimestamp(c[0]/1000.0), "Preco": float(c[4])})
            start_ms = resp_b[-1][0] + 86400000 
            time.sleep(0.3) 
        except Exception as e:
            tentativas += 1; time.sleep(2)
            if tentativas == 3: erros_diag.append("Binance (Timeout)")

    url_d = f"https://api.blockchain.info/charts/difficulty?timespan={meses+14}months&format=json&sampled=true"
    try:
        r_d = requests.get(url_d, headers=headers_seguros, timeout=10)
        resp_d = r_d.json().get('values', []) if r_d.status_code == 200 else []
    except Exception as e:
        resp_d = []
        erros_diag.append("Blockchain (Timeout)")

    def puxar_yf(ticker, nome_coluna):
        tentativas = 0
        while tentativas < 3:
            try:
                df_raw = yf.Ticker(ticker).history(start=inicio_str)[['Close']]
                if not df_raw.empty:
                    df_raw.index = df_raw.index.tz_localize(None).normalize()
                    df_retorno = pd.DataFrame({nome_coluna: df_raw['Close']})
                    df_retorno.index.name = 'date'
                    return df_retorno
            except: pass
            tentativas += 1
            time.sleep(1.5) 
        erros_diag.append(f"YF {ticker} (Falha)")
        df_vazio = pd.DataFrame(columns=[nome_coluna])
        df_vazio.index.name = 'date'
        return df_vazio

    df_dxy = puxar_yf("DX-Y.NYB", 'DXY')
    df_brl = puxar_yf("BRL=X", 'BRL')
    df_ndx = puxar_yf("^NDX", 'NDX')
    df_cny = puxar_yf("CNY=X", 'USD_CNY')

    df_j = pd.DataFrame(resp_j)[['date', 'value']].rename(columns={'value':'Juro'}).dropna() if resp_j else pd.DataFrame(columns=['date', 'Juro'])
    if not df_j.empty: df_j['date'], df_j['Juro'] = pd.to_datetime(df_j['date']), pd.to_numeric(df_j['Juro'], errors='coerce')

    df_m = pd.DataFrame(resp_m)[['date', 'value']].rename(columns={'value':'M2'}).dropna() if resp_m else pd.DataFrame(columns=['date', 'M2'])
    if not df_m.empty: df_m['date'], df_m['M2'] = pd.to_datetime(df_m['date']), pd.to_numeric(df_m['M2'], errors='coerce')

    df_cripto = pd.DataFrame(dados_cripto) if dados_cripto else pd.DataFrame(columns=['date', 'Preco'])
    if not df_cripto.empty: df_cripto['date'] = pd.to_datetime(df_cripto['date'])

    df_diff = pd.DataFrame([{"date": datetime.fromtimestamp(p['x']), "Diff": p['y']/1e12} for p in resp_d]) if resp_d else pd.DataFrame(columns=['date', 'Diff'])
    if not df_diff.empty: df_diff['date'] = pd.to_datetime(df_diff['date'])

    try:
        df_final = df_j.set_index('date').join(
                   df_m.set_index('date'), how='outer').join(
                   df_dxy, how='outer').join(
                   df_brl, how='outer').join(
                   df_ndx, how='outer').join(
                   df_cny, how='outer').join(
                   df_cripto.set_index('date'), how='outer').join(
                   df_diff.set_index('date'), how='outer')

        valores_padrao = {
            'Juro': 5.0, 'M2': 20000.0, 'DXY': 100.0, 'BRL': 5.0, 
            'NDX': 15000.0, 'USD_CNY': 7.2, 'Diff': 80.0
        }
        
        df_final = df_final.ffill()
        for col, val in valores_padrao.items():
            if col in df_final.columns:
                df_final[col] = df_final[col].fillna(val)
                
        df_final = df_final.dropna(subset=['Preco'])

        if df_final.empty:
            st.error(f"🛑 Falha Crítica: O servidor não retornou dados para {ativo_selecionado}.")
            if erros_diag: st.error(f"Detalhes: {', '.join(erros_diag)}")
            return None

        df_final['Mercado_USD'] = df_final['Preco']
        df_final['Baseline_365d'] = df_final['Mercado_USD'].rolling(window=365, min_periods=30).mean()
        df_final['Std_365d'] = df_final['Mercado_USD'].rolling(window=365, min_periods=30).std()
        df_final['Z_Score'] = ((df_final['Mercado_USD'] - df_final['Baseline_365d']) / df_final['Std_365d']).fillna(0)

        df_final = df_final[df_final.index >= pd.to_datetime((hoje - relativedelta(months=meses)).strftime('%Y-%m-%d'))]
        
        if erros_diag:
            st.warning(f"⚠️ Operando com Escudo de Segurança. Alguns sensores falharam: {', '.join(erros_diag)}")
            
        return df_final

    except Exception as e:
        st.error(f"🛑 Erro Interno de Processamento: {e}")
        return None

def buscar_preco_live(simbolo):
    try: 
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(f"https://data-api.binance.vision/api/v3/ticker/price?symbol={simbolo}", headers=headers, timeout=5)
        return float(r.json()['price']) if r.status_code == 200 else None
    except: return None

def buscar_dxy_live():
    try: return float(yf.Ticker("DX-Y.NYB").history(period="1d")['Close'].iloc[-1])
    except: return 100.0 

def buscar_brl_live():
    try: return float(yf.Ticker("BRL=X").history(period="1d")['Close'].iloc[-1])
    except: return 5.0 

def buscar_ndx_live():
    try: return float(yf.Ticker("^NDX").history(period="1d")['Close'].iloc[-1])
    except: return 15000.0

def buscar_cny_live():
    try: return float(yf.Ticker("CNY=X").history(period="1d")['Close'].iloc[-1])
    except: return 7.2

aba_selecionada = st.sidebar.radio("Modo", [
    "Monitoramento Live", 
    "Prova Matemática (Backtest)", 
    "🔥 Otimizador Global (Consenso)",
    "🧠 IA Auto-Tuning (MLP Neural)"
])
meses = st.sidebar.slider("Janela Histórica (Meses)", 1, 120, 48, step=1)

risco = st.sidebar.slider("Agressividade Dinâmica Base", 1.0, 5.0, float(st.session_state.opt_risco), step=0.5)

st.sidebar.markdown("---")
st.sidebar.subheader(f"💼 Seu Portfólio Live ({ticker_curto})")
caixa = st.sidebar.number_input("Saldo em Caixa (BRL)", min_value=0.0, value=100.0, step=50.0)
saldo_cripto = st.sidebar.number_input(f"Saldo em {ticker_curto}", min_value=0.0, value=0.0, step=0.01, format="%.4f")

st.sidebar.markdown("---")
st.sidebar.subheader("🛡️ Limites de Execução")
max_buy_pct = st.sidebar.slider("Teto de Compra (% Máx)", 1, 100, int(st.session_state.opt_buy)) / 100.0
max_sell_pct = st.sidebar.slider("Teto de Venda (% Máx)", 1, 100, int(st.session_state.opt_sell)) / 100.0

st.sidebar.subheader("⏱️ Radares de Saturação")
janela_cin = st.sidebar.slider("Janela Momentum (Dias)", 1, 30, int(st.session_state.opt_janela))
sensibilidade = st.sidebar.slider("Força do Modulador", 1.0, 10.0, float(st.session_state.opt_sens), step=0.5)
z_score_limite = st.sidebar.slider("Limite Crítico MVRV (Z-Score)", 2.0, 8.0, float(st.session_state.opt_zscore), step=0.5)

st.sidebar.markdown("---")
if st.sidebar.button("Sair (Logout)", use_container_width=True):
    st.session_state.autenticado = False
    st.rerun()

df_hist = carregar_dados_mercado(meses, SIMBOLO_BINANCE)

if df_hist is not None and not df_hist.empty:
    dados_oem = []
    for d, r in df_hist.iterrows():
        anos_g = max(0.1, (d - DATA_GENESIS).days / 365.25)
        dxy_atual = r['DXY'] if not pd.isna(r['DXY']) else 100.0
        ndx_atual = r['NDX'] if 'NDX' in r and not pd.isna(r['NDX']) else 15000.0
        cny_atual = r['USD_CNY'] if 'USD_CNY' in r and not pd.isna(r['USD_CNY']) else 7.2
        fator_dxy = 100.0 / max(50.0, dxy_atual) 
        
        juro_atual = r['Juro'] if 'Juro' in r and not pd.isna(r['Juro']) else 5.0
        m2_atual = r['M2'] if 'M2' in r and not pd.isna(r['M2']) else 20000.0
        diff_atual = r['Diff'] if 'Diff' in r and not pd.isna(r['Diff']) else 80.0
        
        m2_g = (m2_atual/1000)*4.8
        penet = 0.05 / (1 + math.exp(-0.4 * (anos_g - 10)))
        liq_e = m2_g * penet * 100 
        
        m_halv = (d - DATA_HALVING_GLOBAL).days / 30.44
        f_amort = 1 + math.log10(max(1, anos_g/4))
        f_ciclo = 1 + ((BETA_ATIVO/f_amort) * math.cos((2*math.pi*m_halv)/48))
        f_esc = 1 + (0.02 * max(0, (d - DATA_PICO_EXCHANGES).days/365.25)) 
        den = max(0.1, juro_atual + DELTA_ATIVO)
        
        p_oem_usd = ALPHA_ATIVO * (liq_e/den) * f_ciclo * diff_atual * f_esc * fator_dxy
        brl_rate = r['BRL'] if 'BRL' in r and not pd.isna(r['BRL']) else 5.0
        p_oem_brl = p_oem_usd * brl_rate
        mercado_brl = r['Preco'] * brl_rate
        
        dados_oem.append({
            "Data": d, "OEM": p_oem_brl, "OEM_USD": p_oem_usd, "Mercado": mercado_brl, 
            "DXY": dxy_atual, "BRL": brl_rate, "NDX": ndx_atual, "USD_CNY": cny_atual, "Z_Score": r['Z_Score']
        })
    
    df_plot = pd.DataFrame(dados_oem)
    df_plot['1_DXY'] = 1 / df_plot['DXY']
    df_plot['CNY_USD'] = 1 / df_plot['USD_CNY']
    df_plot['dBTC_dt'] = df_plot['Mercado'].pct_change(periods=janela_cin).fillna(0)

    # ==========================================
    # ABA 1: MONITORAMENTO LIVE
    # ==========================================
    if aba_selecionada == "Monitoramento Live":
        st.title(f"📡 Terminal OEM - {ativo_selecionado}")
        preco_usd_agora = buscar_preco_live(SIMBOLO_BINANCE)
        dxy_agora = buscar_dxy_live()
        brl_agora = buscar_brl_live()
        ndx_agora = buscar_ndx_live()
        cny_agora = buscar_cny_live()
        
        preco_brl_agora = preco_usd_agora * brl_agora if preco_usd_agora and brl_agora else None
        
        if preco_brl_agora: df_plot.iloc[-1, df_plot.columns.get_loc('Mercado')] = preco_brl_agora
        if dxy_agora: df_plot.iloc[-1, df_plot.columns.get_loc('DXY')] = dxy_agora
        if brl_agora: df_plot.iloc[-1, df_plot.columns.get_loc('BRL')] = brl_agora
        if ndx_agora: df_plot.iloc[-1, df_plot.columns.get_loc('NDX')] = ndx_agora
        if cny_agora: 
            df_plot.iloc[-1, df_plot.columns.get_loc('USD_CNY')] = cny_agora
            df_plot.iloc[-1, df_plot.columns.get_loc('CNY_USD')] = 1 / cny_agora
        df_plot.iloc[-1, df_plot.columns.get_loc('1_DXY')] = 1 / dxy_agora if dxy_agora else 0

        u = df_plot.iloc[-1]
        
        fator_dxy_live = 100.0 / max(50.0, dxy_agora)
        oem_corrigido_usd = u['OEM_USD'] * (fator_dxy_live / (100.0 / max(50.0, u['DXY']))) if u['DXY'] != dxy_agora else u['OEM_USD']
        oem_corrigido_brl = oem_corrigido_usd * brl_agora
        
        delta = (oem_corrigido_brl - u['Mercado']) / oem_corrigido_brl
        derivada_live = u['dBTC_dt']
        z_score_live = u['Z_Score']
        
        acao_cor = "white"
        
        if z_score_live >= z_score_limite:
            status = "🚨 SATURAÇÃO MVRV (FUGA FORÇADA)"
            porcentagem = max_sell_pct 
            qtd_venda = saldo_cripto * porcentagem
            recomendacao = f"Bolha Sistêmica: Venda {qtd_venda:.4f} {ticker_curto} Imediatamente (~R$ {qtd_venda * u['Mercado']:,.2f})"
            acao_cor = "#FF00FF"
        elif delta > 0.02:
            modulador_compra = max(0.2, min(1 - (derivada_live * sensibilidade), 2.0))
            forca_compra = (delta * (risco / 2)) * modulador_compra
            porcentagem = min(max_buy_pct, forca_compra) 
            status = "🟢 COMPRA"
            recomendacao = f"Compre R$ {caixa * porcentagem:,.2f} ({porcentagem*100:.1f}% do Caixa)"
            acao_cor = "#00FF00"
        elif delta < -0.10:
            modulador_venda = max(0.2, min(1 + (derivada_live * sensibilidade), 2.0))
            forca_venda = (abs(delta) * (risco / 2)) * modulador_venda
            porcentagem = min(max_sell_pct, forca_venda) 
            status = "🔴 VENDA PARCIAL"
            qtd_venda = saldo_cripto * porcentagem
            recomendacao = f"Venda {qtd_venda:.4f} {ticker_curto} (Receba ~R$ {qtd_venda * u['Mercado']:,.2f})"
            acao_cor = "#FF0000"
        else:
            status = "🔵 DCA PASSIVO"
            recomendacao = f"Compre apenas R$ {caixa * 0.01:,.2f} (1% do Caixa)"
            acao_cor = "#00BFFF"

        c1, c2, c3, c4 = st.columns(4)
        c1.metric(f"Preço Justo OEM (BRL)", f"R$ {oem_corrigido_brl:,.0f}")
        c2.metric(f"Mercado {ticker_curto} (BRL)", f"R$ {u['Mercado']:,.0f}", f"{delta*100:.2f}% (Delta OEM)")
        c3.metric("Risco Z-Score", f"{z_score_live:.2f}", f"Limite em {z_score_limite:.1f}", delta_color="inverse")
        
        with c4:
            st.markdown(f"<h4 style='text-align: center; color: {acao_cor}; margin-bottom: 0px;'>{status}</h4>", unsafe_allow_html=True)
            st.markdown(f"<p style='text-align: center; font-size: 14px;'><b>Ação:</b> {recomendacao}</p>", unsafe_allow_html=True)

        st.markdown("---")
        
        st.markdown(f"#### 🔗 Correlações Estruturais da Matriz ({meses} meses)")
        corr_oem = df_plot['Mercado'].corr(df_plot['OEM'])
        corr_ndx = df_plot['Mercado'].corr(df_plot['NDX'])
        corr_dxy = df_plot['Mercado'].corr(df_plot['1_DXY'])
        corr_cny = df_plot['Mercado'].corr(df_plot['CNY_USD'])
        
        corr_col1, corr_col2, corr_col3, corr_col4 = st.columns(4)
        corr_col1.metric(f"{ticker_curto} vs OEM", f"{corr_oem:.2f}")
        corr_col2.metric(f"{ticker_curto} vs Nasdaq", f"{corr_ndx:.2f}")
        corr_col3.metric(f"{ticker_curto} vs 1/DXY", f"{corr_dxy:.2f}")
        corr_col4.metric(f"{ticker_curto} vs Yuan", f"{corr_cny:.2f}")

        fig = make_subplots(
            rows=3, cols=1, 
            shared_xaxes=True, 
            vertical_spacing=0.06,
            row_heights=[0.5, 0.25, 0.25], 
            specs=[[{"secondary_y": True}], [{"secondary_y": True}], [{"secondary_y": True}]]
        )

        fig.add_trace(go.Scatter(x=df_plot['Data'], y=df_plot['OEM'], name='Valor Justo (R$)', line=dict(color='#F7931A', width=3)), row=1, col=1, secondary_y=False)
        fig.add_trace(go.Scatter(x=df_plot['Data'], y=df_plot['Mercado'], name=f'Mercado {ticker_curto}', line=dict(color='white', width=1.5, dash='dash')), row=1, col=1, secondary_y=False)
        fig.add_trace(go.Scatter(x=df_plot['Data'], y=df_plot['NDX'], name='Nasdaq 100', line=dict(color='#00FFFF', width=2)), row=1, col=1, secondary_y=True)

        fig.add_trace(go.Scatter(x=df_plot['Data'], y=df_plot['Z_Score'], fill='tozeroy', name='Z-Score', line=dict(color='#FF00FF')), row=2, col=1, secondary_y=False)
        fig.add_hline(y=z_score_limite, line_dash="dash", line_color="red", annotation_text="Limite Crítico", row=2, col=1, secondary_y=False)
        fig.add_hline(y=0, line_dash="solid", line_color="rgba(255, 255, 255, 0.3)", row=2, col=1, secondary_y=False)
        fig.add_trace(go.Scatter(x=df_plot['Data'], y=df_plot['1_DXY'], name='1/DXY (Liquidez)', line=dict(color='#00BFFF', width=1, dash='dot'), opacity=0.4), row=2, col=1, secondary_y=True)

        fig.add_trace(go.Scatter(x=df_plot['Data'], y=df_plot['BRL'], name='USD/BRL', line=dict(color='#00FF00', width=2)), row=3, col=1, secondary_y=False)
        fig.add_trace(go.Scatter(x=df_plot['Data'], y=df_plot['Mercado'], name=f'Preço {ticker_curto} (BRL)', line=dict(color='white', width=1.5, dash='dot'), opacity=0.6), row=3, col=1, secondary_y=True)

        fig.update_layout(template="plotly_dark", height=850, margin=dict(l=0, r=0, t=10, b=0), hovermode="x unified")
        fig.update_yaxes(title_text=f"Preço {ticker_curto}", row=1, col=1, secondary_y=False)
        fig.update_yaxes(title_text="Nasdaq 100", row=1, col=1, secondary_y=True, showgrid=False)
        fig.update_yaxes(title_text="Z-Score", row=2, col=1, secondary_y=False)
        fig.update_yaxes(title_text="1/DXY", row=2, col=1, secondary_y=True, showgrid=False)
        fig.update_yaxes(title_text="Câmbio (R$)", row=3, col=1, secondary_y=False)
        fig.update_yaxes(title_text=f"Preço {ticker_curto} (R$)", row=3, col=1, secondary_y=True, showgrid=False)
        
        st.plotly_chart(fig, use_container_width=True)

    # ==========================================
    # ABA 2: BACKTEST
    # ==========================================
    elif aba_selecionada == "Prova Matemática (Backtest)":
        st.title(f"🧪 Mesa de Teste de Estresse ({ativo_selecionado})")
        
        c_fin1, c_fin2, c_fin3, c_fin4 = st.columns(4)
        with c_fin1: start_brl = st.number_input("Valor Investido (BRL)", min_value=0.0, value=5000.0, step=500.0)
        with c_fin2: start_btc = st.number_input(f"Saldo Inicial ({ticker_curto})", min_value=0.0, value=0.0000, step=0.01, format="%.4f")
        with c_fin3: aporte_mensal = st.number_input("Aporte Mensal (BRL)", min_value=0.0, value=1000.0, step=100.0)
        with c_fin4: taxa_corretora = st.number_input("Taxa da Corretora (%)", min_value=0.0, value=0.10, step=0.05) / 100.0
            
        preco_compra_bnh = df_plot.iloc[0]['Mercado']
        qtd_btc_bnh = start_btc + ((start_brl * (1 - taxa_corretora)) / preco_compra_bnh) if preco_compra_bnh > 0 else start_btc
        total_investido_bnh = start_brl + (start_btc * preco_compra_bnh)
        
        caixa_oem = start_brl
        btc_oem = start_btc
        total_investido_oem = start_brl + (start_btc * preco_compra_bnh)
        
        patrimonio_hist_oem, hist_caixa, hist_valor_btc, patrimonio_hist_bnh = [], [], [], []
        mes_anterior = df_plot.iloc[0]['Data'].month

        for _, row in df_plot.iterrows():
            p_mercado, p_justo, data_atual = row['Mercado'], row['OEM'], row['Data']
            derivada_btc = row['dBTC_dt']
            delta = (p_justo - p_mercado) / p_justo
            z_curr = row['Z_Score']
            
            if data_atual.month != mes_anterior:
                caixa_oem += aporte_mensal; total_investido_oem += aporte_mensal
                qtd_btc_bnh += (aporte_mensal * (1 - taxa_corretora)) / p_mercado; total_investido_bnh += aporte_mensal
                mes_anterior = data_atual.month
            
            if z_curr >= z_score_limite and btc_oem > 0:
                q_vender = btc_oem * max_sell_pct 
                caixa_oem += (q_vender * p_mercado) * (1 - taxa_corretora)
                btc_oem -= q_vender
            else:
                if caixa_oem > 30: 
                    if delta > 0.02: 
                        mod_c = max(0.2, min(1 - (derivada_btc * sensibilidade), 2.0))
                        v_compra = caixa_oem * min(max_buy_pct, (delta * (risco / 2)) * mod_c)
                    elif delta > -0.10: v_compra = caixa_oem * 0.01 
                    else: v_compra = 0
                    
                    if v_compra > 0:
                        btc_oem += (v_compra * (1 - taxa_corretora)) / p_mercado; caixa_oem -= v_compra
                    
                if btc_oem > 0 and delta <= -0.10:
                    mod_v = max(0.2, min(1 + (derivada_btc * sensibilidade), 2.0))
                    q_vender = btc_oem * min(max_sell_pct, (abs(delta) * (risco / 2)) * mod_v)
                    if q_vender > 0:
                        caixa_oem += (q_vender * p_mercado) * (1 - taxa_corretora); btc_oem -= q_vender
                
            hist_caixa.append(caixa_oem)
            hist_valor_btc.append(btc_oem * p_mercado)
            patrimonio_hist_oem.append(caixa_oem + (btc_oem * p_mercado))
            patrimonio_hist_bnh.append(qtd_btc_bnh * p_mercado)
            
        df_plot['Pat_OEM'] = patrimonio_hist_oem
        df_plot['Pat_BnH'] = patrimonio_hist_bnh
        
        retornos_oem = df_plot['Pat_OEM'].pct_change().dropna()
        retornos_bnh = df_plot['Pat_BnH'].pct_change().dropna()

        def calc_sharpe_sortino(retornos):
            if len(retornos) == 0 or retornos.std() == 0: return 0.0, 0.0
            sharpe = (retornos.mean() / retornos.std()) * np.sqrt(365)
            ret_neg = retornos[retornos < 0]
            sortino = (retornos.mean() / ret_neg.std()) * np.sqrt(365) if len(ret_neg) > 0 and ret_neg.std() > 0 else sharpe
            return sharpe, sortino

        sharpe_bnh, sortino_bnh = calc_sharpe_sortino(retornos_bnh)
        sharpe_oem, sortino_oem = calc_sharpe_sortino(retornos_oem)

        lucro_bnh = ((df_plot['Pat_BnH'].iloc[-1] - total_investido_bnh) / total_investido_bnh) * 100 if total_investido_bnh > 0 else 0
        lucro_oem = ((df_plot['Pat_OEM'].iloc[-1] - total_investido_oem) / total_investido_oem) * 100 if total_investido_oem > 0 else 0
        dd_bnh = ((df_plot['Pat_BnH'] / df_plot['Pat_BnH'].cummax()) - 1).fillna(0).min() * 100
        dd_oem = ((df_plot['Pat_OEM'] / df_plot['Pat_OEM'].cummax()) - 1).fillna(0).min() * 100

        st.markdown("---")
        c1, c2, c3 = st.columns(3)
        with c1:
            st.subheader("Benchmark (DCA)")
            st.metric("Retorno Líquido", f"{lucro_bnh:.1f}%")
            st.metric("Risco (Drawdown Máx)", f"{dd_bnh:.1f}%", delta_color="inverse")
            st.metric("Sharpe | Sortino", f"{sharpe_bnh:.2f} | {sortino_bnh:.2f}") 
        with c2:
            st.subheader("Teoria OEM (Ativo + Z-Score)")
            st.metric("Retorno Líquido", f"{lucro_oem:.1f}%")
            st.metric("Risco (Drawdown Máx)", f"{dd_oem:.1f}%", delta_color="inverse")
            st.metric("Sharpe | Sortino", f"{sharpe_oem:.2f} | {sortino_oem:.2f}") 
        with c3:
            st.subheader("Carteira Final OEM")
            st.metric("Caixa Restante", f"R$ {caixa_oem:,.2f}")
            st.metric(f"Saldo em {ticker_curto}", f"{btc_oem:.5f} {ticker_curto}")
            st.metric("Total Injetado", f"R$ {total_investido_oem:,.2f}")

        st.markdown("---")
        
        fig_bt = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08, row_heights=[0.7, 0.3])
        fig_bt.add_trace(go.Scatter(x=df_plot['Data'], y=df_plot['Pat_BnH'], name='Benchmark (DCA)', line=dict(color='#888888', dash='dash')), row=1, col=1)
        fig_bt.add_trace(go.Scatter(x=df_plot['Data'], y=df_plot['Pat_OEM'], name='Estratégia OEM', line=dict(color='#00FF00', width=3)), row=1, col=1)
        fig_bt.add_trace(go.Scatter(x=df_plot['Data'], y=df_plot['Z_Score'], fill='tozeroy', name='Z-Score', line=dict(color='#FF00FF')), row=2, col=1)
        fig_bt.add_hline(y=z_score_limite, line_dash="dash", line_color="red", annotation_text="Gatilho de Saturação", row=2, col=1)
        
        fig_bt.update_layout(template="plotly_dark", title="Crescimento de Patrimônio Líquido vs Saturação de Rede", hovermode="x unified", height=650)
        st.plotly_chart(fig_bt, use_container_width=True)

    # ==========================================
    # ABA 3: OTIMIZADOR GLOBAL (CONSENSO)
    # ==========================================
    elif aba_selecionada == "🔥 Otimizador Global (Consenso)":
        st.title(f"🔥 Matriz Global de Consenso ({ticker_curto})")
        st.markdown("O sistema buscará a combinação que performa melhor **através de todas as janelas de tempo simultaneamente**, garantindo estabilidade e eliminando a dependência do ruído.")
        
        c_fin1, c_fin2, c_fin3, c_fin4 = st.columns(4)
        with c_fin1: start_brl = st.number_input("Valor Investido Inicial (BRL)", min_value=0.0, value=5000.0, step=500.0)
        with c_fin2: start_btc = st.number_input(f"Saldo Inicial ({ticker_curto})", min_value=0.0, value=0.0000, step=0.01, format="%.4f")
        with c_fin3: aporte_mensal = st.number_input("Aporte Mensal (BRL)", min_value=0.0, value=1000.0, step=100.0)
        with c_fin4: taxa_corretora = st.number_input("Taxa Corretora (%)", min_value=0.0, value=0.10, step=0.05) / 100.0

        if st.button("🚀 Processar Matriz Global", use_container_width=True):
            with st.spinner("Computando Consenso Global através de todas as janelas temporais. Aguarde..."):
                janelas_teste = [7, 14, 21, 30]
                riscos_teste = [1.0, 2.0, 3.0, 4.0, 5.0]           
                sensibilidades_teste = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]   
                compras_teste = [0.3, 0.6, 0.9]
                vendas_teste = [0.1, 0.3, 0.6] 
                
                combinacoes = list(itertools.product(riscos_teste, sensibilidades_teste, compras_teste, vendas_teste))
                
                mercado_arr = df_plot['Mercado'].values
                oem_arr = df_plot['OEM'].values
                meses_arr = df_plot['Data'].dt.month.values
                z_arr = df_plot['Z_Score'].values
                n_dias = len(mercado_arr)
                
                resultados_consenso = []

                for ris_t, sens_t, max_b, max_s in combinacoes:
                    sortinos_da_config = []
                    
                    for jan_t in janelas_teste:
                        der_arr = pd.Series(mercado_arr).pct_change(periods=jan_t).fillna(0).values
                        cx, bt, mes_ant = start_brl, start_btc, meses_arr[0]
                        tot_inv = start_brl + (start_btc * mercado_arr[0])
                        pat = np.zeros(n_dias)
                        
                        for i in range(n_dias):
                            m_curr, o_curr, mth, der, z_curr = mercado_arr[i], oem_arr[i], meses_arr[i], der_arr[i], z_arr[i]
                            if mth != mes_ant: cx += aporte_mensal; tot_inv += aporte_mensal; mes_ant = mth
                            dlt = (o_curr - m_curr) / o_curr
                            
                            if z_curr >= z_score_limite and bt > 0:
                                qv = bt * max_s
                                cx += (qv * m_curr) * (1 - taxa_corretora); bt -= qv
                            else:
                                if cx > 30:
                                    if dlt > 0.02:
                                        mc = max(0.2, min(1 - (der * sens_t), 2.0))
                                        vc = cx * min(max_b, (dlt * (ris_t/2)) * mc)
                                        if vc > 0: bt += (vc * (1 - taxa_corretora)) / m_curr; cx -= vc
                                    elif dlt > -0.10:
                                        vc = cx * 0.01; bt += (vc * (1 - taxa_corretora)) / m_curr; cx -= vc
                                        
                                if bt > 0 and dlt <= -0.10:
                                    mv = max(0.2, min(1 + (der * sens_t), 2.0))
                                    qv = bt * min(max_s, (abs(dlt) * (ris_t/2)) * mv)
                                    if qv > 0: cx += (qv * m_curr) * (1 - taxa_corretora); bt -= qv
                                    
                            pat[i] = cx + (bt * m_curr)
                            
                        rets = pd.Series(pat).pct_change().dropna()
                        ret_neg = rets[rets < 0]
                        sortino_val = (rets.mean() / ret_neg.std()) * np.sqrt(365) if len(ret_neg)>0 and ret_neg.std()>0 else 0
                        sortinos_da_config.append(sortino_val)
                    
                    media_sortino_global = np.mean(sortinos_da_config)
                    melhor_janela_indice = np.argmax(sortinos_da_config)
                    melhor_janela_vencedora = janelas_teste[melhor_janela_indice]
                    
                    resultados_consenso.append({
                        "Janela Resiliente (Dias)": int(melhor_janela_vencedora),
                        "Agressividade Base": float(ris_t), 
                        "Força do Modulador": float(sens_t),
                        "Teto Compra (%)": float(max_b), 
                        "Teto Venda (%)": float(max_s),  
                        "Score Consenso (Média Sortino)": round(media_sortino_global, 2)
                    })
                    
                df_res = pd.DataFrame(resultados_consenso).sort_values(by="Score Consenso (Média Sortino)", ascending=False).reset_index(drop=True)
                st.session_state.df_res_otimizado = df_res 

        if 'df_res_otimizado' in st.session_state:
            df_res = st.session_state.df_res_otimizado.copy()
            df_display = df_res.copy()
            
            def formata_porcentagem(valor):
                if isinstance(valor, str) and "%" in valor: return valor 
                return f"{int(valor * 100)}%" 
            
            if "Teto Compra (%)" in df_display.columns:
                df_display["Teto Compra (%)"] = df_display["Teto Compra (%)"].apply(formata_porcentagem)
            if "Teto Venda (%)" in df_display.columns:
                df_display["Teto Venda (%)"] = df_display["Teto Venda (%)"].apply(formata_porcentagem)
            
            st.markdown("### 🏆 Top 5 Configurações Globais (Pau para Toda Obra)")
            st.dataframe(df_display.head(5), use_container_width=True)
            
            st.markdown("---")
            if st.button("🎯 Aplicar Configuração Robusta ao Painel Live", type="primary", use_container_width=True):
                st.session_state.opt_janela = int(df_res.iloc[0]["Janela Resiliente (Dias)"])
                st.session_state.opt_risco = float(df_res.iloc[0]["Agressividade Base"])
                st.session_state.opt_sens = float(df_res.iloc[0]["Força do Modulador"])
                val_buy = df_res.iloc[0]["Teto Compra (%)"]
                val_sell = df_res.iloc[0]["Teto Venda (%)"]
                st.session_state.opt_buy = int(val_buy.replace('%','')) if isinstance(val_buy, str) else int(val_buy * 100)
                st.session_state.opt_sell = int(val_sell.replace('%','')) if isinstance(val_sell, str) else int(val_sell * 100)
                st.rerun() 
            
            c_h1, c_h2, c_h3 = st.columns(3)
            def get_best_point(pivot_df):
                c_max = pivot_df.max().idxmax()
                r_max = pivot_df[c_max].idxmax()
                v_max = pivot_df.loc[r_max, c_max]
                return r_max, c_max, v_max

            with c_h1:
                try:
                    pivot_1 = df_res.pivot_table(index='Força do Modulador', columns='Agressividade Base', values='Score Consenso (Média Sortino)', aggfunc='max')
                    fig_h1 = go.Figure(data=go.Heatmap(z=pivot_1.values, x=[f"Risco {c}" for c in pivot_1.columns], y=[f"Modulador {i}" for i in pivot_1.index], colorscale='Viridis', text=np.round(pivot_1.values, 2), texttemplate="%{text}"))
                    fig_h1.update_layout(template="plotly_dark", title="Estabilidade: Motor vs Freio", height=500)
                    st.plotly_chart(fig_h1, use_container_width=True)
                except Exception: pass

            with c_h2:
                try:
                    pivot_2 = df_res.pivot_table(index='Força do Modulador', columns='Teto Compra (%)', values='Score Consenso (Média Sortino)', aggfunc='max')
                    x_labels = [f"{int(c.replace('%',''))}%" if isinstance(c, str) else f"{int(c*100)}%" for c in pivot_2.columns]
                    fig_h2 = go.Figure(data=go.Heatmap(z=pivot_2.values, x=x_labels, y=[f"Modulador {i}" for i in pivot_2.index], colorscale='Plasma', text=np.round(pivot_2.values, 2), texttemplate="%{text}"))
                    fig_h2.update_layout(template="plotly_dark", title="Absorção de Quedas", height=500)
                    st.plotly_chart(fig_h2, use_container_width=True)
                except Exception: pass

            with c_h3:
                try:
                    pivot_3 = df_res.pivot_table(index='Teto Venda (%)', columns='Teto Compra (%)', values='Score Consenso (Média Sortino)', aggfunc='max')
                    x_labels = [f"{int(c.replace('%',''))}%" if isinstance(c, str) else f"{int(c*100)}%" for c in pivot_3.columns]
                    y_labels = [f"{int(i.replace('%',''))}%" if isinstance(i, str) else f"{int(i*100)}%" for i in pivot_3.index]
                    fig_h3 = go.Figure(data=go.Heatmap(z=pivot_3.values, x=x_labels, y=y_labels, colorscale='Magma', text=np.round(pivot_3.values, 2), texttemplate="%{text}"))
                    fig_h3.update_layout(template="plotly_dark", title="Calibragem de Bolso", height=500)
                    st.plotly_chart(fig_h3, use_container_width=True)
                except Exception: pass

    # ==========================================
    # ABA 4: INTELIGÊNCIA ARTIFICIAL (MLP LEVE)
    # ==========================================
    elif aba_selecionada == "🧠 IA Auto-Tuning (MLP Neural)":
        st.title(f"🧠 Projeção Neural com Auto-Tuning ({ticker_curto})")
        st.markdown("Rede Neural Densa com **Otimização Dinâmica**. O algoritmo testa diversas janelas projetivas no passado recente (*Out-of-Sample*) para descobrir qual horizonte de tempo possui a maior precisão direcional estatística. Ele então **trava** a projeção nos parâmetros ótimos para o futuro.")

        if st.button("🚀 Iniciar Otimização e Projeção Neural", use_container_width=True):
            with st.spinner(f"Rodando backtest neural e descobrindo a janela mais precisa para {ticker_curto}. Isso leva apenas alguns segundos..."):
                try:
                    from sklearn.preprocessing import MinMaxScaler
                    from sklearn.neural_network import MLPRegressor
                    
                    features = ['Mercado', 'Z_Score', '1_DXY', 'NDX']
                    
                    # CORREÇÃO CRÍTICA DO ÍNDICE DE DATA
                    df_lstm = df_plot.set_index('Data')[features].copy().dropna()
                    
                    scaler = MinMaxScaler(feature_range=(0, 1))
                    dados_escalados = scaler.fit_transform(df_lstm.values)
                    
                    # Validação Out-of-Sample: Reserva os últimos 40 dias conhecidos
                    dias_teste = 40
                    treino_dados = dados_escalados[:-dias_teste]
                    teste_dados = dados_escalados[-dias_teste:]
                    
                    opcoes_projecao = [3, 5, 7, 10, 14, 21]
                    opcoes_memoria = [7, 14, 21, 30]
                    
                    melhor_dias = 7
                    melhor_memoria = 14
                    maior_precisao_direcional = -1
                    menor_erro = float('inf')
                    
                    # --- FASE 1: AUTO-TUNING (BUSCA PELA MELHOR PRECISÃO) ---
                    for mem in opcoes_memoria:
                        X_train, y_train = [], []
                        for i in range(mem, len(treino_dados)):
                            X_train.append(treino_dados[i - mem:i].flatten())
                            y_train.append(treino_dados[i, 0])
                        X_train, y_train = np.array(X_train), np.array(y_train)
                        
                        modelo_teste = MLPRegressor(hidden_layer_sizes=(16,), activation='relu', max_iter=200, random_state=42)
                        modelo_teste.fit(X_train, y_train)
                        
                        for proj in opcoes_projecao:
                            janela_atual = treino_dados[-mem:]
                            previsoes = []
                            for _ in range(proj):
                                entrada = janela_atual.flatten().reshape(1, -1)
                                pred = modelo_teste.predict(entrada)[0]
                                previsoes.append(pred)
                                
                                novo_passo = np.copy(janela_atual[-1, :])
                                novo_passo[0] = pred
                                janela_atual = np.vstack([janela_atual[1:], novo_passo])
                            
                            real = teste_dados[:proj, 0]
                            
                            # Avaliação de Direção (Subiu ou Desceu em relação ao dia 0?)
                            tendencia_real = real[-1] - treino_dados[-1, 0]
                            tendencia_prevista = previsoes[-1] - treino_dados[-1, 0]
                            acertou_direcao = 1 if (tendencia_real * tendencia_prevista) > 0 else 0
                            
                            # Desempate pelo Erro Médio da Curva
                            erro_rmse = np.sqrt(np.mean((real - previsoes)**2))
                            
                            if acertou_direcao > maior_precisao_direcional or (acertou_direcao == maior_precisao_direcional and erro_rmse < menor_erro):
                                maior_precisao_direcional = acertou_direcao
                                menor_erro = erro_rmse
                                melhor_dias = proj
                                melhor_memoria = mem
                    
                    st.success(f"🎯 **Auto-Tuning concluído!** O motor encontrou a precisão direcional máxima cravando a memória em **{melhor_memoria} dias** e a projeção para **{melhor_dias} dias**.")

                    # --- FASE 2: TREINAMENTO FINAL COM TODOS OS DADOS ---
                    X_final, y_final = [], []
                    for i in range(melhor_memoria, len(dados_escalados)):
                        X_final.append(dados_escalados[i - melhor_memoria:i].flatten())
                        y_final.append(dados_escalados[i, 0])
                    
                    X_final, y_final = np.array(X_final), np.array(y_final)
                    
                    modelo_final = MLPRegressor(hidden_layer_sizes=(32, 16), activation='relu', max_iter=500, random_state=42)
                    modelo_final.fit(X_final, y_final)
                    
                    ultimos_dados = dados_escalados[-melhor_memoria:]
                    previsoes_escaladas = []
                    
                    for _ in range(melhor_dias):
                        entrada_achatada = ultimos_dados.flatten().reshape(1, -1)
                        prox_preco_esc = modelo_final.predict(entrada_achatada)[0]
                        previsoes_escaladas.append(prox_preco_esc)
                        
                        novo_passo = np.copy(ultimos_dados[-1, :])
                        novo_passo[0] = prox_preco_esc 
                        ultimos_dados = np.vstack([ultimos_dados[1:], novo_passo])
                    
                    matriz_dummy = np.zeros((melhor_dias, len(features)))
                    matriz_dummy[:, 0] = previsoes_escaladas
                    precos_projetados = scaler.inverse_transform(matriz_dummy)[:, 0]
                    
                    # Usa o index resolvido para somar o timedelta tranquilamente
                    datas_futuras = [df_lstm.index[-1] + timedelta(days=i) for i in range(1, melhor_dias + 1)]
                    
                    fig_ai = go.Figure()
                    corte = -90
                    fig_ai.add_trace(go.Scatter(x=df_lstm.index[corte:], y=df_lstm['Mercado'].iloc[corte:], name='Histórico Real', line=dict(color='white', width=2)))
                    fig_ai.add_trace(go.Scatter(
                        x=[df_lstm.index[-1]] + datas_futuras, 
                        y=[df_lstm['Mercado'].iloc[-1]] + list(precos_projetados), 
                        name=f'Projeção Otimizada ({melhor_dias}d)', 
                        line=dict(color='#00FA9A', width=3, dash='dash')
                    ))
                    
                    fig_ai.update_layout(
                        template="plotly_dark", 
                        title=f"Visão do Cérebro Neural para os Próximos {melhor_dias} dias", 
                        hovermode="x unified",
                        height=500
                    )
                    st.plotly_chart(fig_ai, use_container_width=True)
                    
                except ImportError:
                    st.error("⚠️ A biblioteca Scikit-Learn não está instalada. Adicione 'scikit-learn' ao seu requirements.txt.")
                except Exception as e:
                    st.error(f"Ocorreu um erro matemático durante o treinamento da rede: {e}")

else:
    st.info("🔄 Conectando aos servidores de dados...")
