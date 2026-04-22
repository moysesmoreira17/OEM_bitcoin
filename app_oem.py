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
import numpy as np
import itertools 

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
        inicio_query = inicio - relativedelta(days=40) 
        inicio_str = inicio_query.strftime('%Y-%m-%d')
        
        url_j = f"https://api.stlouisfed.org/fred/series/observations?series_id=DFII10&api_key={FRED_API_KEY}&file_type=json&observation_start={inicio_str}"
        url_m = f"https://api.stlouisfed.org/fred/series/observations?series_id=WM2NS&api_key={FRED_API_KEY}&file_type=json&observation_start={inicio_str}"
        resp_j = requests.get(url_j).json().get('observations', [])
        resp_m = requests.get(url_m).json().get('observations', [])

        start_ms = int(inicio_query.timestamp() * 1000)
        end_ms = int(hoje.timestamp() * 1000)
        dados_btc = []
        headers_falsos = {'User-Agent': 'Mozilla/5.0'}
        
        tentativas = 0
        while start_ms < end_ms and tentativas < 3:
            url_b = f"https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1d&startTime={start_ms}&endTime={end_ms}&limit=1000"
            resposta = requests.get(url_b, headers=headers_falsos)
            if resposta.status_code != 200:
                tentativas += 1; time.sleep(2); continue
            resp_b = resposta.json()
            if not resp_b or isinstance(resp_b, dict): break
            for c in resp_b:
                dados_btc.append({"date": datetime.fromtimestamp(c[0]/1000.0), "Preco": float(c[4])})
            start_ms = resp_b[-1][0] + 86400000 
            time.sleep(0.3) 

        url_d = f"https://api.blockchain.info/charts/difficulty?timespan={meses+2}months&format=json&sampled=true"
        resp_d = requests.get(url_d).json().get('values', [])

        try:
            dxy_raw = yf.Ticker("DX-Y.NYB").history(start=inicio_str)[['Close']]
            dxy_raw.index = dxy_raw.index.tz_localize(None).normalize()
            df_dxy = pd.DataFrame({'DXY': dxy_raw['Close']})
            df_dxy.index.name = 'date'
        except:
            df_dxy = pd.DataFrame(columns=['DXY'])
            df_dxy.index.name = 'date'

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
                   df_dxy, how='outer').join(
                   df_btc.set_index('date'), how='outer').join(
                   df_diff.set_index('date'), how='outer').ffill().dropna()
        
        df_final = df_final[df_final.index >= pd.to_datetime(inicio.strftime('%Y-%m-%d'))]
        return df_final
    except Exception as e:
        st.error(f"🛑 Erro de Coleta: {e}")
        return None

def buscar_preco_live():
    try: 
        headers = {'User-Agent': 'Mozilla/5.0'}
        return float(requests.get("https://data-api.binance.vision/api/v3/ticker/price?symbol=BTCUSDT", headers=headers).json()['price'])
    except: return None

def buscar_dxy_live():
    try: return float(yf.Ticker("DX-Y.NYB").history(period="1d")['Close'].iloc[-1])
    except: return 100.0 

# ==========================================
# 2. INTERFACE E SIDEBAR 
# ==========================================
st.sidebar.title("⚙️ Controle OEM")
aba_selecionada = st.sidebar.radio("Modo", ["Monitoramento Live", "Prova Matemática (Backtest)", "🔥 Otimizador de Grade (5D)"])
meses = st.sidebar.slider("Janela Histórica (Meses)", 1, 120, 48, step=1)
risco = st.sidebar.slider("Agressividade Dinâmica Base", 1.0, 5.0, 3.0, step=0.5)

st.sidebar.markdown("---")
st.sidebar.subheader("💼 Seu Portfólio Live")
caixa = st.sidebar.number_input("Saldo em Caixa (USD)", min_value=0.0, value=20.0, step=10.0)
saldo_btc = st.sidebar.number_input("Saldo em Bitcoin (BTC)", min_value=0.0, value=0.0009, step=0.000100, format="%.4f")

st.sidebar.markdown("---")
st.sidebar.subheader("🛡️ Limites de Execução")
max_buy_pct = st.sidebar.slider("Teto de Compra (% Máx)", 1, 100, 30) / 100.0
max_sell_pct = st.sidebar.slider("Teto de Venda (% Máx)", 1, 100, 10) / 100.0

st.sidebar.subheader("⏱️ Cinemática (Radar)")
janela_cin = st.sidebar.slider("Janela Momentum (Dias)", 1, 30, 7)
sensibilidade = st.sidebar.slider("Força do Modulador", 1.0, 10.0, 5.0, step=0.5)

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
    # ABA 2: BACKTEST 
    # ==========================================
    elif aba_selecionada == "Prova Matemática (Backtest)":
        st.title("🧪 Mesa de Teste de Estresse (Backtest)")
        
        c_fin1, c_fin2, c_fin3, c_fin4 = st.columns(4)
        with c_fin1: start_usd = st.number_input("Caixa Inicial (USD)", min_value=0.0, value=1000.0, step=100.0)
        with c_fin2: start_btc = st.number_input("Saldo Inicial (BTC)", min_value=0.0, value=0.0000, step=0.01, format="%.4f")
        with c_fin3: aporte_mensal = st.number_input("Aporte Mensal (Salário)", min_value=0.0, value=250.0, step=50.0)
        with c_fin4: taxa_corretora = st.number_input("Taxa da Corretora (%)", min_value=0.0, value=0.10, step=0.05) / 100.0
            
        preco_compra_bnh = df_plot.iloc[0]['Mercado']
        qtd_btc_bnh = start_btc + ((start_usd * (1 - taxa_corretora)) / preco_compra_bnh) if preco_compra_bnh > 0 else start_btc
        total_investido_bnh = start_usd + (start_btc * preco_compra_bnh)
        
        caixa_oem = start_usd
        btc_oem = start_btc
        total_investido_oem = start_usd + (start_btc * preco_compra_bnh)
        
        patrimonio_hist_oem, hist_caixa, hist_valor_btc, patrimonio_hist_bnh = [], [], [], []
        compras_x, compras_y, vendas_x, vendas_y = [], [], [], []
        mes_anterior = df_plot.iloc[0]['Data'].month

        for _, row in df_plot.iterrows():
            p_mercado, p_justo, data_atual = row['Mercado'], row['OEM'], row['Data']
            derivada_btc = row['dBTC_dt']
            delta = (p_justo - p_mercado) / p_justo
            
            if data_atual.month != mes_anterior:
                caixa_oem += aporte_mensal; total_investido_oem += aporte_mensal
                qtd_btc_bnh += (aporte_mensal * (1 - taxa_corretora)) / p_mercado; total_investido_bnh += aporte_mensal
                mes_anterior = data_atual.month
            
            if delta > 0.02: 
                compras_x.append(data_atual); compras_y.append(p_mercado)
            elif delta <= -0.10: 
                vendas_x.append(data_atual); vendas_y.append(p_mercado)
                
            if caixa_oem > 5: 
                if delta > 0.02: 
                    mod_c = max(0.2, min(1 - (derivada_btc * sensibilidade), 2.0))
                    v_compra = caixa_oem * min(max_buy_pct, (delta * (risco / 2)) * mod_c)
                elif delta > -0.10: v_compra = caixa_oem * 0.01 
                else: v_compra = 0
                
                if v_compra > 0:
                    btc_oem += (v_compra * (1 - taxa_corretora)) / p_mercado; caixa_oem -= v_compra
                
            if btc_oem > 0:
                if delta <= -0.10: 
                    mod_v = max(0.2, min(1 + (derivada_btc * sensibilidade), 2.0))
                    q_vender = btc_oem * min(max_sell_pct, (abs(delta) * (risco / 2)) * mod_v)
                else: q_vender = 0
                
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

        if total_investido_oem > 0:
            lucro_bnh = ((df_plot['Pat_BnH'].iloc[-1] - total_investido_bnh) / total_investido_bnh) * 100
            lucro_oem = ((df_plot['Pat_OEM'].iloc[-1] - total_investido_oem) / total_investido_oem) * 100
            dd_bnh = ((df_plot['Pat_BnH'] / df_plot['Pat_BnH'].cummax()) - 1).fillna(0).min() * 100
            dd_oem = ((df_plot['Pat_OEM'] / df_plot['Pat_OEM'].cummax()) - 1).fillna(0).min() * 100
        else:
            lucro_bnh, lucro_oem, dd_bnh, dd_oem = 0.0, 0.0, 0.0, 0.0

        st.markdown("---")
        c1, c2, c3 = st.columns(3)
        with c1:
            st.subheader("Benchmark (DCA)")
            st.metric("Retorno Líquido", f"{lucro_bnh:.1f}%")
            st.metric("Risco (Drawdown Máx)", f"{dd_bnh:.1f}%", delta_color="inverse")
            st.metric("Sharpe | Sortino", f"{sharpe_bnh:.2f} | {sortino_bnh:.2f}") 
        with c2:
            st.subheader("Teoria OEM (Ativo)")
            st.metric("Retorno Líquido", f"{lucro_oem:.1f}%")
            st.metric("Risco (Drawdown Máx)", f"{dd_oem:.1f}%", delta_color="inverse")
            st.metric("Sharpe | Sortino", f"{sharpe_oem:.2f} | {sortino_oem:.2f}") 
        with c3:
            st.subheader("Carteira Final OEM")
            st.metric("Caixa Restante", f"US$ {caixa_oem:,.2f}")
            st.metric("Saldo em BTC", f"{btc_oem:.5f} BTC")
            st.metric("Total Injetado", f"US$ {total_investido_oem:,.2f}")

        st.markdown("---")
        fig_bt = go.Figure()
        fig_bt.add_trace(go.Scatter(x=df_plot['Data'], y=df_plot['Pat_BnH'], name='Benchmark (DCA)', line=dict(color='#888888', dash='dash')))
        fig_bt.add_trace(go.Scatter(x=df_plot['Data'], y=df_plot['Pat_OEM'], name='Estratégia OEM', line=dict(color='#00FF00', width=3)))
        fig_bt.update_layout(template="plotly_dark", title="Crescimento de Patrimônio Líquido", hovermode="x unified", height=400)
        st.plotly_chart(fig_bt, use_container_width=True)

    # ==========================================
    # ABA 3: OTIMIZADOR DE GRADE (ALTA RESOLUÇÃO)
    # ==========================================
    elif aba_selecionada == "🔥 Otimizador de Grade (5D)":
        st.title("🔥 Otimizador de Matriz 5D (Alta Resolução)")
        st.markdown("O algoritmo varrerá a interação simultânea entre **Janela, Agressividade (Risco), Modulador, Compras e Vendas**. *(Nota: Os sliders da barra lateral são ignorados nesta aba, pois o robô testa todos os cenários automaticamente).*")
        
        c_fin1, c_fin2, c_fin3, c_fin4 = st.columns(4)
        with c_fin1: start_usd = st.number_input("Caixa Inicial (USD)", min_value=0.0, value=1000.0, step=100.0)
        with c_fin2: start_btc = st.number_input("Saldo Inicial (BTC)", min_value=0.0, value=0.0000, step=0.01, format="%.4f")
        with c_fin3: aporte_mensal = st.number_input("Aporte Mensal", min_value=0.0, value=250.0, step=50.0)
        with c_fin4: taxa_corretora = st.number_input("Taxa Corretora (%)", min_value=0.0, value=0.10, step=0.05) / 100.0

        if st.button("🚀 Processar Matriz de Alta Resolução", use_container_width=True):
            with st.spinner("Computando 900 backtests vetoriais. Por favor, aguarde..."):
                
                # Matriz 5D de Alta Resolução (Granularidade Destravada)
                janelas_teste = [3, 7, 14, 21]
                riscos_teste = [1.0, 2.0, 3.0, 4.0, 5.0]           # Agressividade passo a passo
                sensibilidades_teste = [1.0, 3.0, 5.0, 7.0, 9.0]   # Modulador passo a passo
                compras_teste = [0.3, 0.6, 0.9]
                vendas_teste = [0.1, 0.3, 0.6] 
                
                combinacoes = list(itertools.product(janelas_teste, riscos_teste, sensibilidades_teste, compras_teste, vendas_teste))
                
                # Extraindo dados para numpy
                mercado_arr = df_plot['Mercado'].values
                oem_arr = df_plot['OEM'].values
                meses_arr = df_plot['Data'].dt.month.values
                n_dias = len(mercado_arr)
                
                resultados = []

                for jan_t, ris_t, sens_t, max_b, max_s in combinacoes:
                    der_arr = pd.Series(mercado_arr).pct_change(periods=jan_t).fillna(0).values
                    cx = start_usd
                    bt = start_btc
                    tot_inv = start_usd + (start_btc * mercado_arr[0])
                    mes_ant = meses_arr[0]
                    pat = np.zeros(n_dias)
                    
                    for i in range(n_dias):
                        m_curr, o_curr, mth, der = mercado_arr[i], oem_arr[i], meses_arr[i], der_arr[i]
                        
                        if mth != mes_ant:
                            cx += aporte_mensal; tot_inv += aporte_mensal; mes_ant = mth
                            
                        dlt = (o_curr - m_curr) / o_curr
                        
                        if cx > 5:
                            if dlt > 0.02:
                                mc = max(0.2, min(1 - (der * sens_t), 2.0))
                                vc = cx * min(max_b, (dlt * (ris_t/2)) * mc)
                                if vc > 0: bt += (vc * (1 - taxa_corretora)) / m_curr; cx -= vc
                            elif dlt > -0.10:
                                vc = cx * 0.01
                                bt += (vc * (1 - taxa_corretora)) / m_curr; cx -= vc
                                
                        if bt > 0 and dlt <= -0.10:
                            mv = max(0.2, min(1 + (der * sens_t), 2.0))
                            qv = bt * min(max_s, (abs(dlt) * (ris_t/2)) * mv)
                            if qv > 0: cx += (qv * m_curr) * (1 - taxa_corretora); bt -= qv
                                
                        pat[i] = cx + (bt * m_curr)
                        
                    rets = pd.Series(pat).pct_change().dropna()
                    ret_neg = rets[rets < 0]
                    sortino_val = (rets.mean() / ret_neg.std()) * np.sqrt(365) if len(ret_neg)>0 and ret_neg.std()>0 else 0
                    roi_val = ((pat[-1] - tot_inv) / tot_inv) * 100 if tot_inv > 0 else 0
                    
                    resultados.append({
                        "Janela (Dias)": jan_t,
                        "Agressividade Base": ris_t,
                        "Força do Modulador": sens_t,
                        "Teto Compra (%)": f"{max_b*100:.0f}%",
                        "Teto Venda (%)": f"{max_s*100:.0f}%",
                        "Índice Sortino": round(sortino_val, 2),
                        "Retorno (%)": round(roi_val, 1)
                    })
                    
                df_res = pd.DataFrame(resultados)
                df_res = df_res.sort_values(by="Índice Sortino", ascending=False).reset_index(drop=True)
                
                st.success(f"✅ Processamento Concluído! O computador rodou {len(combinacoes)} cenários simultâneos com sucesso.")
                
                st.markdown("### 🏆 Top 5 Melhores Configurações Absolutas")
                st.dataframe(df_res.head(5), use_container_width=True)
                
                st.markdown("---")
                st.markdown("### 🗺️ Matrizes Térmicas e Pontos Ótimos (Sweet Spots)")
                
                c_h1, c_h2, c_h3 = st.columns(3)
                
                # Função para extrair a coordenada campeã
                def get_best_point(pivot_df):
                    c_max = pivot_df.max().idxmax()
                    r_max = pivot_df[c_max].idxmax()
                    v_max = pivot_df.loc[r_max, c_max]
                    return r_max, c_max, v_max

                with c_h1:
                    pivot_1 = df_res.pivot_table(index='Força do Modulador', columns='Agressividade Base', values='Índice Sortino', aggfunc='max')
                    r_bst, c_bst, v_bst = get_best_point(pivot_1)
                    
                    fig_h1 = go.Figure(data=go.Heatmap(z=pivot_1.values, x=[f"Risco {c}" for c in pivot_1.columns], y=[f"Modulador {i}" for i in pivot_1.index], colorscale='Viridis', text=np.round(pivot_1.values, 2), texttemplate="%{text}"))
                    fig_h1.update_layout(template="plotly_dark", title="Motor vs Freio ABS", height=400)
                    st.plotly_chart(fig_h1, use_container_width=True)
                    st.success(f"**📍 Ponto de Ouro:**\nAgressividade **{c_bst}** com Modulador **{r_bst}**\n*(Sortino: {v_bst:.2f})*")
                    
                with c_h2:
                    pivot_2 = df_res.pivot_table(index='Força do Modulador', columns='Janela (Dias)', values='Índice Sortino', aggfunc='max')
                    r_bst, c_bst, v_bst = get_best_point(pivot_2)
                    
                    fig_h2 = go.Figure(data=go.Heatmap(z=pivot_2.values, x=[f"{c} Dias" for c in pivot_2.columns], y=[f"Modulador {i}" for i in pivot_2.index], colorscale='Plasma', text=np.round(pivot_2.values, 2), texttemplate="%{text}"))
                    fig_h2.update_layout(template="plotly_dark", title="Calibragem de Tempo", height=400)
                    st.plotly_chart(fig_h2, use_container_width=True)
                    st.info(f"**📍 Ponto de Ouro:**\nJanela **{c_bst} Dias** com Modulador **{r_bst}**\n*(Sortino: {v_bst:.2f})*")

                with c_h3:
                    pivot_3 = df_res.pivot_table(index='Teto Venda (%)', columns='Teto Compra (%)', values='Índice Sortino', aggfunc='max')
                    r_bst, c_bst, v_bst = get_best_point(pivot_3)
                    
                    fig_h3 = go.Figure(data=go.Heatmap(z=pivot_3.values, x=pivot_3.columns, y=pivot_3.index, colorscale='Magma', text=np.round(pivot_3.values, 2), texttemplate="%{text}"))
                    fig_h3.update_layout(template="plotly_dark", title="Calibragem de Bolso", height=400)
                    st.plotly_chart(fig_h3, use_container_width=True)
                    st.warning(f"**📍 Ponto de Ouro:**\nCompra **{c_bst}** e Venda **{r_bst}**\n*(Sortino: {v_bst:.2f})*")

else:
    st.info("🔄 Conectando aos servidores de dados...")
