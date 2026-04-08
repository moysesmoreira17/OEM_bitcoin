import requests
import math
import pandas as pd
from datetime import datetime
from dateutil.relativedelta import relativedelta
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import time

# ==========================================
# CONFIGURAÇÕES E CHAVES
# ==========================================
FRED_API_KEY = 'e13422aa86d75b68c58b6ced02d8fb31'
DATA_HALVING = datetime(2024, 4, 19)
DATA_GENESIS = datetime(2009, 1, 3)     # Criação da Rede
DATA_PICO_EXCHANGES = datetime(2020, 3, 12) # Início do dreno de liquidez
CAMINHO_EXCEL = r"C:\Users\lmeng\Desktop\Automatização\Historico_OEM_V2.xlsx"

# Constantes Recalibradas (OEM v2.0)
ALPHA = 2.1   # Escala recalibrada devido aos novos fatores institucionais
BETA = 0.18   # Volatilidade base máxima
DELTA = 0.5   # Amortecedor de juros negativos

# ==========================================
# MOTORES DE BUSCA 
# ==========================================
def buscar_fred(series_id, data_inicio, data_fim):
    url = f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}&api_key={FRED_API_KEY}&file_type=json&observation_start={data_inicio}&observation_end={data_fim}"
    resp = requests.get(url).json()
    return resp.get('observations', [])

def buscar_preco_real_btc(data_inicio, data_fim):
    print("[-] Baixando histórico real de preços (Binance)...")
    start_ms = int(data_inicio.timestamp() * 1000)
    end_ms = int(data_fim.timestamp() * 1000)
    
    dados = []
    limite = 1000 
    
    while start_ms < end_ms:
        url = f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&startTime={start_ms}&endTime={end_ms}&limit={limite}"
        resp = requests.get(url).json()
        
        if not resp or isinstance(resp, dict): break
            
        for candle in resp:
            data_obj = datetime.fromtimestamp(candle[0] / 1000.0)
            dados.append({"date": data_obj, "Preco_Mercado": float(candle[4])})
            
        start_ms = resp[-1][0] + 86400000 
        time.sleep(0.1) 
        
    df_btc = pd.DataFrame(dados)
    df_btc.set_index('date', inplace=True)
    df_btc.index = df_btc.index.normalize()
    return df_btc

def buscar_dificuldade_historica(meses):
    print("[-] Baixando histórico da Dificuldade (Blockchain.com)...")
    url = f"https://api.blockchain.info/charts/difficulty?timespan={meses}months&format=json&sampled=true"
    resp = requests.get(url).json()
    
    dados = []
    for ponto in resp['values']:
        data_obj = datetime.fromtimestamp(ponto['x'])
        dificuldade_t = float(ponto['y']) / 1_000_000_000_000
        dados.append({"date": data_obj, "Dificuldade_T": dificuldade_t})
        
    df_diff = pd.DataFrame(dados)
    df_diff.set_index('date', inplace=True)
    df_diff.index = df_diff.index.normalize()
    return df_diff

# ==========================================
# CÁLCULO MESTRE - OEM V2.0 (NÍVEL INSTITUCIONAL)
# ==========================================
def executar_analise_avancada():
    print("="*50)
    print(" MOTOR OEM v2.0 - MODELO INSTITUCIONAL ".center(50))
    print("="*50)
    
    try:
        meses_retroativos = int(input("[?] Digite a quantidade de meses para análise (ex: 24, 48): "))
    except ValueError:
        print("[ERRO] Digite apenas números.")
        return

    hoje = datetime.now()
    data_inicio = hoje - relativedelta(months=meses_retroativos)
    data_inicio_str = data_inicio.strftime('%Y-%m-%d')
    data_fim_str = hoje.strftime('%Y-%m-%d')
    
    print(f"\n[*] Viajando no tempo de {data_inicio_str} até {data_fim_str}...\n")
    
    dados_juros = buscar_fred('DFII10', data_inicio_str, data_fim_str)
    dados_m2 = buscar_fred('WM2NS', data_inicio_str, data_fim_str)
    df_btc = buscar_preco_real_btc(data_inicio, hoje)
    df_diff = buscar_dificuldade_historica(meses_retroativos)
    
    # Tratamento Pandas
    df_juros = pd.DataFrame(dados_juros)[['date', 'value']].dropna()
    df_juros['date'] = pd.to_datetime(df_juros['date'])
    df_juros['value'] = pd.to_numeric(df_juros['value'], errors='coerce')
    df_juros.rename(columns={'value': 'Juro_Real'}, inplace=True)
    df_juros.set_index('date', inplace=True)
    
    df_m2 = pd.DataFrame(dados_m2)[['date', 'value']].dropna()
    df_m2['date'] = pd.to_datetime(df_m2['date'])
    df_m2['value'] = pd.to_numeric(df_m2['value'], errors='coerce')
    df_m2.rename(columns={'value': 'M2_EUA'}, inplace=True)
    df_m2.set_index('date', inplace=True)
    
    df_merge = df_juros.join([df_m2, df_btc, df_diff], how='outer')
    df_merge.ffill(inplace=True) 
    df_merge.dropna(inplace=True) 
    
    df_sextas = df_merge[df_merge.index.weekday == 4].copy()
    print(f"[-] Compilando matriz on-chain para {len(df_sextas)} semanas...\n")
    
    dados_finais = []
    
    for data, linha in df_sextas.iterrows():
        juro_real = linha['Juro_Real']
        m2_global = (linha['M2_EUA'] / 1000) * 4.8
        preco_mercado = linha['Preco_Mercado']
        gamma_tech = linha['Dificuldade_T'] 
        
        anos_desde_genesis = (data - DATA_GENESIS).days / 365.25
        
        # 1. ATUALIZAÇÃO: Gargalo de Stablecoins (Curva Logística de Penetração)
        # Modela que no máximo 5% do M2 global migrará para Cripto no longo prazo
        penetracao_cripto = 0.05 / (1 + math.exp(-0.4 * (anos_desde_genesis - 10)))
        liquidez_efetiva = m2_global * penetracao_cripto * 100 
        
        # 2. ATUALIZAÇÃO: Decaimento Logarítmico do Halving
        meses_passados_halving = (data - DATA_HALVING).days / 30.44
        ciclos_rede = anos_desde_genesis / 4
        fator_amortecimento = 1 + math.log10(ciclos_rede if ciclos_rede > 1 else 1)
        
        angulo = (2 * math.pi * meses_passados_halving) / 48
        fator_ciclo = 1 + ((BETA / fator_amortecimento) * math.cos(angulo))
        
        # 3. ATUALIZAÇÃO: Choque de Oferta (Escoamento On-Chain)
        anos_desde_2020 = max(0, (data - DATA_PICO_EXCHANGES).days / 365.25)
        # O saldo cai, forçando a escassez e aumentando o prêmio em ~5% a.a.
        fator_escassez = 1 + (0.05 * anos_desde_2020) 
        
        # FÓRMULA MESTRA OEM v2.0
        denominador = (juro_real + DELTA)
        if denominador <= 0.1: denominador = 0.1
            
        motor_macro = (liquidez_efetiva / denominador)
        
        # P = Alpha * Macro * CicloAmortecido * Dificuldade * ChoqueDeOferta
        preco_oem = ALPHA * motor_macro * fator_ciclo * gamma_tech * fator_escassez
        
        margem = preco_oem * 0.05 
        if preco_mercado < (preco_oem - margem):
            sinal = 'COMPRA'
        elif preco_mercado > (preco_oem + margem):
            sinal = 'VENDA'
        else:
            sinal = 'NEUTRO'
        
        dados_finais.append({
            "Data": data.strftime('%d/%m/%Y'),
            "Preco_OEM": round(preco_oem, 2),
            "Preco_Mercado": round(preco_mercado, 2),
            "Sinal": sinal,
            "Penetracao_Stablecoin_%": round(penetracao_cripto * 100, 2),
            "Amortecimento_Halving": round(fator_amortecimento, 2),
            "Prêmio_Escassez": round(fator_escassez, 2)
        })

    df_final = pd.DataFrame(dados_finais)
    df_final.to_excel(CAMINHO_EXCEL, index=False)
    print(f"[SUCESSO] Relatório Institucional salvo em: {CAMINHO_EXCEL}")
    
    # GERAR GRÁFICO
    plt.style.use('dark_background')
    plt.figure(figsize=(14, 7))
    
    plt.plot(df_final['Data'], df_final['Preco_OEM'], label='OEM v2.0 (Valor Justo)', color='#F7931A', linewidth=2.5)
    plt.plot(df_final['Data'], df_final['Preco_Mercado'], label='Preço Mercado', color='white', linestyle='--', alpha=0.6)
    
    for i in range(len(df_final)):
        if df_final.loc[i, 'Sinal'] == 'COMPRA':
            plt.scatter(df_final.loc[i, 'Data'], df_final.loc[i, 'Preco_Mercado'], color='#00FF00', marker='^', s=100, zorder=5)
        elif df_final.loc[i, 'Sinal'] == 'VENDA':
            plt.scatter(df_final.loc[i, 'Data'], df_final.loc[i, 'Preco_Mercado'], color='#FF0000', marker='v', s=100, zorder=5)

    plt.title('Modelo OEM Institucional - Adoção, Damping e Escassez On-Chain', fontsize=16, pad=20, fontweight='bold')
    plt.ylabel('Preço (USD)', fontsize=12)
    plt.legend(loc='upper left', fontsize=11)
    plt.grid(color='#333333', linestyle=':', linewidth=0.8)
    
    passo = max(1, len(df_final) // 15) 
    plt.xticks(df_final['Data'][::passo], rotation=45)
    plt.gca().yaxis.set_major_formatter(ticker.StrMethodFormatter('${x:,.0f}'))
    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    executar_analise_avancada()