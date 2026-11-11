import streamlit as st
import pandas as pd
from dateutil.relativedelta import relativedelta

from oem import carregamento, modelo, telas
from oem.config import ATIVOS
from oem.estrategia import Parametros

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

FRED_API_KEY = st.secrets.get("FRED_API_KEY")

# ==========================================
# 2. INTERFACE: SELEÇÃO DE ATIVO E CONTROLES
# ==========================================
st.sidebar.title("⚙️ Controle OEM")
ativo = ATIVOS[st.sidebar.selectbox("🪙 Ativo Operacional", list(ATIVOS))]
ticker_curto = ativo.ticker

ABAS = {
    "Monitoramento Live": telas.tela_monitoramento,
    "🧪 Backtest Histórico": telas.tela_backtest,
    "🔥 Otimizador Walk-Forward": telas.tela_otimizador,
    "🧠 IA Auto-Tuning (MLP Neural)": telas.tela_previsao,
}
aba_selecionada = st.sidebar.radio("Modo", list(ABAS))
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
z_score_limite = st.sidebar.slider(
    "Limite Crítico do Z-Score de Preço", 2.0, 8.0, float(st.session_state.opt_zscore), step=0.5,
    help="Distância do preço até a média de 365 dias, em desvios-padrão. Não é o MVRV on-chain.",
)

st.sidebar.subheader("🧮 Calibração do Modelo")
if ativo.alpha_fixo is None:
    calibrar_alpha = True
    st.sidebar.caption(f"O ALPHA do {ticker_curto} é sempre calibrado só com dados passados.")
else:
    calibrar_alpha = st.sidebar.checkbox(
        "Calibrar ALPHA só com dados passados",
        value=False,
        help=f"Desligado: usa o ALPHA fixo ({ativo.alpha_fixo}). Ligado: em cada dia, o ALPHA é a mediana de "
        "preço/modelo de todos os dias anteriores, sem olhar o futuro.",
    )

st.sidebar.markdown("---")
if st.sidebar.button("Sair (Logout)", width="stretch"):
    st.session_state.autenticado = False
    st.rerun()
st.sidebar.caption(telas.AVISO_LEGAL)

parametros = Parametros(
    risco=risco,
    sensibilidade=sensibilidade,
    max_compra=max_buy_pct,
    max_venda=max_sell_pct,
    z_limite=z_score_limite,
    janela_momentum=janela_cin,
)

# ==========================================
# 3. DADOS E MODELO
# ==========================================
with st.spinner("🔄 Conectando aos servidores de dados..."):
    base, falhas_obrigatorias, falhas_opcionais = carregamento.carregar_base(ativo, FRED_API_KEY)

if base is None:
    st.error(
        "🛑 Não foi possível calcular o valor justo: fontes obrigatórias falharam. "
        "Nenhum valor substituto foi usado, para não exibir números falsos."
    )
    st.markdown("\n".join(f"- {falha}" for falha in falhas_obrigatorias))
    st.info("As fontes que funcionaram ficam guardadas. As que falharam são tentadas de novo em 2 minutos, ou agora pelo botão abaixo.")
    if st.button("🔄 Tentar novamente"):
        carregamento.limpar_falhas()
        st.rerun()
    st.stop()

if falhas_opcionais:
    st.warning(f"⚠️ Fontes opcionais indisponíveis (algumas correlações e gráficos ficam de fora): {', '.join(falhas_opcionais)}")

df_completo = modelo.calcular_oem(base, ativo, calibrar_alpha)
inicio_janela = pd.Timestamp.now().normalize() - relativedelta(months=meses)
df_janela = modelo.recortar_janela(df_completo, inicio_janela)

if len(df_janela) < 2:
    st.error("🛑 Não há dados suficientes nesta janela (o ALPHA calibrado precisa de pelo menos 180 dias de histórico antes).")
    st.stop()
if df_janela.index[0] > inicio_janela + pd.Timedelta(days=7):
    st.caption(f"ℹ️ Os dados disponíveis começam em {df_janela.index[0]:%d/%m/%Y}.")

contexto = telas.Contexto(
    ativo=ativo,
    df=df_janela,
    df_completo=df_completo,
    meses=meses,
    params=parametros,
    caixa=caixa,
    saldo_cripto=saldo_cripto,
    alpha_calibrado=calibrar_alpha,
)
ABAS[aba_selecionada](contexto)
st.caption(telas.AVISO_LEGAL)
