import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
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
        inicio_query = inicio - relativedelta(days=40) # Margem maior para garantir cálculos móveis corretos no início
        inicio_str = inicio_query.strftime('%Y-%m-%d')
        
        # 1. FRED
        url_j = f"https://api.stlouisfed.org/fred/series/observations?series_id=DFII10&api_key={FRED_API_KEY}&file_type=json&observation_start={inicio_str}"
        url_m = f"https://api.stlouisfed.org/fred/series/observations?series_id=WM2NS&api_key={FRED_API_KEY}&file_type=json&observation_start={inicio_str}"
        resp_j = requests.get(url_j).json().get('observations', [])
        resp_m = requests.get(url_m).json().get('observations', [])

        # 2. Binance
        start_ms = int(inicio_query.timestamp() * 1000)
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
        url_d = f"https://api.blockchain.info/charts/difficulty?timespan={meses+2}months&format=json&sampled=true"
        resp_d = requests.get(url_d).json().get('values', [])

        # 4. Yahoo Finance (DXY - Índice do Dólar)
        try:
            dxy_raw = yf.Ticker("DX-Y.NYB").history(start=inicio_str)[['Close']]
            dxy_raw.index = dxy_raw.index.tz_localize(None).normalize()
            df_dxy = pd.DataFrame({'DXY': dxy_raw['Close']})
            df_dxy.index.name = 'date'
        except Exception as e:
            st.warning(f"Aviso: Não foi possível carregar o DXY em tempo real. Usando linha base 100.")
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

        # Juntando tudo
        df_final = df_j.set_index('date').join(
                   df_m.set_index('date'), how='outer').join(
                   df_dxy, how='outer').join(
                   df_btc.set_index('date'), how='outer').join(
                   df_diff.set_index('date'), how='outer').ffill().dropna()
        
        # Filtra estritamente para o período solicitado do backtest
        df_final = df_final[df_final.index >= pd.to_datetime(inicio.strftime('%Y-%m-%d'))]
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
        return 100.0 # Fallback

# ==========================================
# 2. INTERFACE E SIDEBAR (LEIS UNIVERSAIS)
# ==========================================
st.sidebar.title("⚙️ Controle OEM")
aba_selecionada = st.sidebar.radio("Modo de Operação", ["Monitoramento Live", "Prova Matemática (Backtest)"])
meses = st.sidebar.slider("Janela Histórica (Meses)", 1, 120, 48, step=1)
risco = st.sidebar.slider("Agressividade Dinâmica Base", 1.0, 5.0, 3.0, step=0.5)

st.sidebar.markdown("---")
st.sidebar.subheader("💼 Seu Portfólio Live")
caixa = st.sidebar.number_input("Saldo em Caixa (USD)", min_value=0.0, value=20.0, step=10.0)
saldo_btc = st.sidebar.number_input("Saldo em Bitcoin (BTC)", min_value=0.0, value=0.0009, step=0.000100, format="%.4f")

# --- LEIS UNIVERSAIS DE EXECUÇÃO ---
st.sidebar.markdown("---")
st.sidebar.subheader("🛡️ Limites de Execução")
max_buy_pct = st.sidebar.slider("Teto de Compra (% Máx do Caixa)", 1, 100, 90) / 100.0
max_sell_pct = st.sidebar.slider("Teto de Venda (% Máx do BTC)", 1, 100, 10) / 100.0

st.sidebar.subheader("⏱️ Cinemática (Radar)")
janela_cin = st.sidebar.slider("Janela Momentum (Dias)", 1, 30, 20)
sensibilidade = st.sidebar.slider("Força do Modulador Macro", 1.0, 10.0, 2.0, step=0.5)

df_hist = carregar_dados_mercado(meses)

if df_hist is not None:
    dados_oem = []
    for d, r in df_hist.iterrows():
        anos_g = (d - DATA_GENESIS).days / 365.25
        
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
        
        p_oem = ALPHA * (liq_e/den) * f_ciclo * r['Diff'] * f_esc * fator_dxy
        
        dados_oem.append({"Data": d, "OEM": p_oem, "Mercado": r['Preco'], "DXY": dxy_atual})
    
    df_plot = pd.DataFrame(dados_oem)
    df_plot['1_DXY'] = 1 / df_plot['DXY']
    
    # Cinemática Móvel (usando a janela universal)
    df_plot['dBTC_dt'] = df_plot['Mercado'].pct_change(periods=janela_cin).fillna(0)

    # ==========================================
    # ABA 1: MONITORAMENTO LIVE
    # ==========================================
    if aba_selecionada == "Monitoramento Live":
        st.title("📡 Terminal OEM - Tempo Real")
        
        preco_agora = buscar_preco_live()
        dxy_agora = buscar_dxy_live()
        inverso_dxy_agora = 1 / dxy_agora if dxy_agora else 0
        
        if preco_agora: df_plot.iloc[-1, df_plot.columns.get_loc('Mercado')] = preco_agora
        if dxy_agora: df_plot.iloc[-1, df_plot.columns.get_loc('DXY')] = dxy_agora
        df_plot.iloc[-1, df_plot.columns.get_loc('1_DXY')] = inverso_dxy_agora

        u = df_plot.iloc[-1]
        
        fator_dxy_live = 100.0 / max(50.0, dxy_agora)
        oem_corrigido = u['OEM'] * (fator_dxy_live / (100.0 / max(50.0, u['DXY']))) if u['DXY'] != dxy_agora else u['OEM']
        
        delta = (oem_corrigido - u['Mercado']) / oem_corrigido
        derivada_live = u['dBTC_dt']
        
        acao_cor = "white"
        
        if delta > 0.02:
            modulador_compra = max(0.2, min(1 - (derivada_live * sensibilidade), 2.0))
            forca_compra = (delta * (risco / 2)) * modulador_compra
            porcentagem = min(max_buy_pct, forca_compra) 
            
            status = "🟢 COMPRA ELÁSTICA"
            recomendacao = f"Compre US$ {caixa * porcentagem:,.2f} ({porcentagem*100:.1f}% do Caixa)"
            acao_cor = "#00FF00"
            
        elif delta < -0.10:
            modulador_venda = max(0.2, min(1 + (derivada_live * sensibilidade), 2.0))
            forca_venda = (abs(delta) * (risco / 2)) * modulador_venda
            porcentagem = min(max_sell_pct, forca_venda) 
            
            status = "🔴 VENDA PARCIAL"
            qtd_venda = saldo_btc * porcentagem
            recomendacao = f"Venda {qtd_venda:.4f} BTC (Receba ~US$ {qtd_venda * u['Mercado']:,.2f})"
            acao_cor = "#FF0000"
        else:
            status = "🔵 DCA PASSIVO"
            recomendacao = f"Compre apenas US$ {caixa * 0.01:,.2f} (1% do Caixa)"
            acao_cor = "#00BFFF"

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Preço Justo (OEM)", f"US$ {oem_corrigido:,.0f}")
        c2.metric("Preço Mercado", f"US$ {u['Mercado']:,.0f}", f"{delta*100:.2f}% (Delta OEM)")
        c3.metric("Força Inversa (1/DXY)", f"{inverso_dxy_agora:.5f}", f"Cinemática {janela_cin}d: {derivada_live*100:.1f}%", delta_color="off")
        
        with c4:
            st.markdown(f"<h4 style='text-align: center; color: {acao_cor}; margin-bottom: 0px;'>{status}</h4>", unsafe_allow_html=True)
            st.markdown(f"<p style='text-align: center; font-size: 14px;'><b>Ação:</b> {recomendacao}</p>", unsafe_allow_html=True)

        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(go.Scatter(x=df_plot['Data'], y=df_plot['OEM'], name='Valor Justo (OEM)', line=dict(color='#F7931A', width=3)), secondary_y=False)
        fig.add_trace(go.Scatter(x=df_plot['Data'], y=df_plot['Mercado'], name='Preço Mercado', line=dict(color='white', width=1.5, dash='dash')), secondary_y=False)
        fig.add_trace(go.Scatter(x=df_plot['Data'], y=df_plot['1_DXY'], name='1/DXY', line=dict(color='#00BFFF', width=1.5, dash='dot'), opacity=0.6), secondary_y=True)
        
        fig.update_layout(template="plotly_dark", height=600, margin=dict(l=0, r=0, t=30, b=0), hovermode="x unified")
        fig.update_yaxes(title_text="Preço (USD)", secondary_y=False)
        fig.update_yaxes(title_text="Índice 1/DXY", secondary_y=True, showgrid=False) 
        
        st.plotly_chart(fig, use_container_width=True)

    # ==========================================
    # ABA 2: BACKTEST INSTITUCIONAL (MESA DE ESTRESSE)
    # ==========================================
    elif aba_selecionada == "Prova Matemática (Backtest)":
        st.title("🧪 Mesa de Teste de Estresse (Backtest)")
        st.markdown("Simule cenários reais customizando a sua exposição, aportes contínuos e taxas.")
        
        # --- PAINEL FINANCEIRO DE ESTRESSE ---
        st.markdown("### 🎛️ Parâmetros do Portfólio")
        c_fin1, c_fin2, c_fin3, c_fin4 = st.columns(4)
        
        with c_fin1:
            start_usd = st.number_input("Caixa Inicial (USD)", min_value=0.0, value=1000.0, step=100.0)
        with c_fin2:
            start_btc = st.number_input("Saldo Inicial (BTC)", min_value=0.0, value=0.0000, step=0.01, format="%.4f")
        with c_fin3:
            aporte_mensal = st.number_input("Aporte Mensal (Salário)", min_value=0.0, value=250.0, step=50.0)
        with c_fin4:
            taxa_corretora = st.number_input("Taxa da Corretora (%)", min_value=0.0, value=0.10, step=0.05) / 100.0
            
        preco_compra_bnh = df_plot.iloc[0]['Mercado']
        
        # Inicialização Benchmark (DCA Cego)
        qtd_btc_bnh = start_btc + ((start_usd * (1 - taxa_corretora)) / preco_compra_bnh) if preco_compra_bnh > 0 else start_btc
        total_investido_bnh = start_usd + (start_btc * preco_compra_bnh)
        
        # Inicialização OEM Dinâmica
        caixa_oem = start_usd
        btc_oem = start_btc
        total_investido_oem = start_usd + (start_btc * preco_compra_bnh)
        
        patrimonio_hist_oem = []
        hist_caixa = []
        hist_valor_btc = []
        patrimonio_hist_bnh = []
        
        compras_x, compras_y = [], []
        vendas_x, vendas_y = [], []
        
        mes_anterior = df_plot.iloc[0]['Data'].month

        for _, row in df_plot.iterrows():
            p_mercado = row['Mercado']
            p_justo = row['OEM']
            data_atual = row['Data']
            derivada_btc = row['dBTC_dt']
            delta = (p_justo - p_mercado) / p_justo
            
            # 1. INJEÇÃO DO APORTE MENSAL
            if data_atual.month != mes_anterior:
                caixa_oem += aporte_mensal
                total_investido_oem += aporte_mensal
                
                qtd_btc_bnh += (aporte_mensal * (1 - taxa_corretora)) / p_mercado
                total_investido_bnh += aporte_mensal
                
                mes_anterior = data_atual.month
            
            # 2. BÚSSOLA OEM (Gráficos Visuais)
            if delta > 0.02: 
                compras_x.append(data_atual)
                compras_y.append(p_mercado)
            elif delta <= -0.10: 
                vendas_x.append(data_atual)
                vendas_y.append(p_mercado)
                
            # 3. EXECUÇÃO DE COMPRA OEM
            if caixa_oem > 5: 
                if delta > 0.02: 
                    modulador_compra = max(0.2, min(1 - (derivada_btc * sensibilidade), 2.0))
                    forca_compra = (delta * (risco / 2)) * modulador_compra
                    valor_compra = caixa_oem * min(max_buy_pct, forca_compra)
                elif delta > -0.10: 
                    valor_compra = caixa_oem * 0.01 
                else: 
                    valor_compra = 0
                
                if valor_compra > 0:
                    btc_oem += (valor_compra * (1 - taxa_corretora)) / p_mercado
                    caixa_oem -= valor_compra
                
            # 4. EXECUÇÃO DE VENDA OEM
            if btc_oem > 0:
                if delta <= -0.10: 
                    modulador_venda = max(0.2, min(1 + (derivada_btc * sensibilidade), 2.0))
                    forca_venda = (abs(delta) * (risco / 2)) * modulador_venda
                    qtd_vender = btc_oem * min(max_sell_pct, forca_venda)
                else: 
                    qtd_vender = 0
                    
                if qtd_vender > 0:
                    caixa_oem += (qtd_vender * p_mercado) * (1 - taxa_corretora)
                    btc_oem -= qtd_vender
                
            # 5. REGISTROS DIÁRIOS
            hist_caixa.append(caixa_oem)
            hist_valor_btc.append(btc_oem * p_mercado)
            patrimonio_hist_oem.append(caixa_oem + (btc_oem * p_mercado))
            patrimonio_hist_bnh.append(qtd_btc_bnh * p_mercado)
            
        df_plot['Patrimonio_OEM'] = patrimonio_hist_oem
        df_plot['Patrimonio_BnH_DCA'] = patrimonio_hist_bnh
        df_plot['Caixa_Hist'] = hist_caixa
        df_plot['BTC_USD_Hist'] = hist_valor_btc
        
        # --- CÁLCULOS DE PERFORMANCE ---
        if total_investido_oem > 0:
            lucro_bnh = ((df_plot['Patrimonio_BnH_DCA'].iloc[-1] - total_investido_bnh) / total_investido_bnh) * 100
            lucro_oem = ((df_plot['Patrimonio_OEM'].iloc[-1] - total_investido_oem) / total_investido_oem) * 100
            dd_bnh = ((df_plot['Patrimonio_BnH_DCA'] / df_plot['Patrimonio_BnH_DCA'].cummax()) - 1).fillna(0).min() * 100
            dd_oem = ((df_plot['Patrimonio_OEM'] / df_plot['Patrimonio_OEM'].cummax()) - 1).fillna(0).min() * 100
        else:
            lucro_bnh, lucro_oem, dd_bnh, dd_oem = 0.0, 0.0, 0.0, 0.0

        st.markdown("---")
        st.markdown(f"<p style='text-align: center; color: #888; font-size: 14px;'><i>Total de dinheiro injetado (Aportes Reais) no período: <b>US$ {total_investido_oem:,.2f}</b></i></p>", unsafe_allow_html=True)
        
        c1, c2, c3 = st.columns(3)
        with c1:
            st.subheader("Benchmark (DCA Cego)")
            st.metric("Retorno Líquido", f"{lucro_bnh:.1f}%")
            st.metric("Risco (Drawdown Máx)", f"{dd_bnh:.1f}%", delta_color="inverse")
        with c2:
            st.subheader("Teoria OEM (Ativo)")
            st.metric("Retorno Líquido", f"{lucro_oem:.1f}%")
            st.metric("Risco (Drawdown Máx)", f"{dd_oem:.1f}%", delta_color="inverse")
        with c3:
            st.subheader("Sua Carteira Final OEM")
            st.metric("Caixa Restante (USD)", f"US$ {caixa_oem:,.2f}")
            st.metric("Saldo Final em BTC", f"{btc_oem:.5f} BTC")

        st.markdown("---")
        
        st.subheader("🎯 Bússola Estrutural (Sinais da Teoria)")
        fig_sinais = go.Figure()
        fig_sinais.add_trace(go.Scatter(x=df_plot['Data'], y=df_plot['Mercado'], name='Preço BTC', line=dict(color='white', width=1.5)))
        fig_sinais.add_trace(go.Scatter(x=df_plot['Data'], y=df_plot['OEM'], name='Valor Justo (OEM)', line=dict(color='#F7931A', width=2)))
        fig_sinais.add_trace(go.Scatter(x=compras_x, y=compras_y, mode='markers', name='Sinal Compra (Desconto)', marker=dict(symbol='triangle-up', size=10, color='#00FF00', line=dict(width=1, color='black'))))
        fig_sinais.add_trace(go.Scatter(x=vendas_x, y=vendas_y, mode='markers', name='Sinal Venda (Ágio)', marker=dict(symbol='triangle-down', size=10, color='#FF0000', line=dict(width=1, color='black'))))
        fig_sinais.update_layout(template="plotly_dark", yaxis_title="Preço (USD)", hovermode="x unified", height=400)
        st.plotly_chart(fig_sinais, use_container_width=True)

        st.subheader("⚖️ Dinâmica de Composição do Portfólio OEM")
        fig_comp = go.Figure()
        fig_comp.add_trace(go.Scatter(x=df_plot['Data'], y=df_plot['BTC_USD_Hist'], name='Valor em Bitcoin (USD)', stackgroup='one', fillcolor='rgba(247, 147, 26, 0.7)', line=dict(width=0)))
        fig_comp.add_trace(go.Scatter(x=df_plot['Data'], y=df_plot['Caixa_Hist'], name='Reserva de Caixa (USD)', stackgroup='one', fillcolor='rgba(0, 255, 127, 0.7)', line=dict(width=0)))
        fig_comp.update_layout(template="plotly_dark", yaxis_title="Saldo Total (US$)", hovermode="x unified", height=400)
        st.plotly_chart(fig_comp, use_container_width=True)

        st.subheader("📈 Crescimento de Patrimônio Líquido")
        fig_bt = go.Figure()
        fig_bt.add_trace(go.Scatter(x=df_plot['Data'], y=df_plot['Patrimonio_BnH_DCA'], name='Benchmark (DCA Cego)', line=dict(color='#888888', dash='dash')))
        fig_bt.add_trace(go.Scatter(x=df_plot['Data'], y=df_plot['Patrimonio_OEM'], name='Estratégia OEM', line=dict(color='#00FF00', width=3)))
        fig_bt.update_layout(template="plotly_dark", yaxis_title="Patrimônio Total (US$)", hovermode="x unified", height=300)
        st.plotly_chart(fig_bt, use_container_width=True)

else:
    st.info("🔄 Aguardando conexão com a Binance e com o FRED. Caso demore muito, verifique sua conexão ou recarregue a página.")
