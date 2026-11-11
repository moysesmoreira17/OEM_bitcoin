# Terminal Quantitativo OEM

Painel em Streamlit que estima um **valor justo** para Bitcoin e Ethereum a partir de
variáveis macro (liquidez M2, juro real, DXY) e on-chain (dificuldade de mineração), e
transforma a distância entre preço e valor justo em sinais de compra e venda.

> ⚠️ Ferramenta de estudo. Backtests e projeções usam dados passados e não garantem
> resultados futuros. Nada aqui é recomendação de investimento.

## Como rodar

```bash
pip install -r requirements.txt
streamlit run app_oem.py
```

O app lê os segredos do Streamlit (`.streamlit/secrets.toml` localmente, ou o painel
*Secrets* no Streamlit Cloud). Além das credenciais de login, ele precisa de
`FRED_API_KEY` (chave gratuita em <https://fred.stlouisfed.org/docs/api/api_key.html>).
Nunca versione esse arquivo.

### Linha de comando

Gera a tabela histórica sem abrir o app (mesmo código do painel):

```bash
FRED_API_KEY=sua_chave python motor_oem.py --meses 48 --saida Historico_OEM.xlsx --grafico oem.html
```

Opções: `--ativo BTC|ETH`, `--semanal` (só sextas-feiras), `--calibrar-alpha`.

## Estrutura

| Arquivo | O que faz |
|---|---|
| `app_oem.py` | Login, barra lateral e carregamento; chama as telas |
| `oem/config.py` | Parâmetros por ativo e defasagens de publicação |
| `oem/dados.py` | Busca e alinhamento das fontes (Binance, FRED, Yahoo, Blockchain.com) |
| `oem/carregamento.py` | Cache do Streamlit em volta das fontes |
| `oem/modelo.py` | Fórmula do valor justo (vetorizada), calibração do ALPHA, Z-Score |
| `oem/estrategia.py` | **Regra de decisão única** + simulação do backtest |
| `oem/metricas.py` | Retorno TWR, Sharpe, Sortino, drawdown, correlações |
| `oem/otimizador.py` | Otimização walk-forward (treino × teste) |
| `oem/previsao.py` | Projeção com rede neural avaliada sem olhar o futuro |
| `oem/telas.py` | As quatro abas do painel |
| `motor_oem.py` | Versão de linha de comando |
| `tests/` | Testes automatizados (`pytest`) |

## Escolhas de metodologia

- **Uma regra só.** O painel live, o backtest e o otimizador chamam a mesma função
  `decidir_acao`, então o que é testado é exatamente o que roda ao vivo.
- **Sem dados do futuro no backtest.** O M2 entra com 49 dias de defasagem e o juro
  real com 1 dia, que é quando esses dados ficam públicos (ajustável em `oem/config.py`).
- **Aportes não são lucro.** Sharpe, Sortino e drawdown usam o retorno ponderado no
  tempo (TWR), que desconta o dinheiro novo de cada mês.
- **Otimizador walk-forward.** Os parâmetros são escolhidos no início da janela
  (treino) e medidos no final (teste), que o otimizador não viu.
- **ALPHA.** O BTC usa o ALPHA fixo (3,4) por padrão. Na barra lateral dá para trocar
  por um ALPHA calibrado em cada dia só com os dias anteriores. O ETH sempre usa o
  calibrado.
- **Ethereum.** Desde 2022 o ETH é Proof-of-Stake: o modelo dele não usa a dificuldade
  de mineração nem o ciclo de halving do Bitcoin, só o núcleo macro.
- **Sem valores inventados.** Se uma fonte obrigatória falhar, o painel avisa e não
  calcula nada. Fontes opcionais (Nasdaq, Yuan) só tiram o gráfico ou a correlação.
- **Z-Score de preço.** É a distância do preço até a média de 365 dias em
  desvios-padrão. Não é o MVRV on-chain.
- **Correlações** são calculadas sobre retornos semanais, não sobre níveis de preço.
- **Rede neural.** Prevê o retorno (não o preço), é re-treinada ao longo do tempo,
  escolhe memória/horizonte num período de validação e reporta o resultado num
  período de teste posterior, sempre comparando com referências ingênuas.

## Testes

```bash
pip install -r requirements-dev.txt
pytest
```

Os testes não acessam a internet: usam dados sintéticos e rodam o app inteiro com o
`AppTest` do Streamlit.
