import pandas as pd

import motor_oem
from oem import dados
from tests.conftest import series_sinteticas


def test_cli_gera_planilha_e_grafico(tmp_path, monkeypatch):
    hoje = pd.Timestamp.now().normalize()
    series = series_sinteticas(inicio=hoje - pd.Timedelta(days=899), dias=900)
    monkeypatch.setattr(dados, "buscar_fonte", lambda coluna, simbolo, inicio, chave: series[coluna])
    saida, grafico = tmp_path / "oem.csv", tmp_path / "oem.html"

    assert motor_oem.main(["--meses", "12", "--semanal", "--saida", str(saida), "--grafico", str(grafico)]) == 0

    tabela = pd.read_csv(saida)
    assert 50 <= len(tabela) <= 54  # ~52 sextas-feiras
    assert set(tabela["Regime"]) <= {"COMPRA", "VENDA", "DCA", "SATURACAO"}
    assert grafico.stat().st_size > 0


def test_cli_falha_sem_inventar_dados(tmp_path, monkeypatch, capsys):
    def fora_do_ar(coluna, simbolo, inicio, chave):
        raise dados.ErroFonteDados(f"{coluna}: fora do ar")

    monkeypatch.setattr(dados, "buscar_fonte", fora_do_ar)
    assert motor_oem.main(["--saida", str(tmp_path / "x.csv")]) == 1
    assert "fora do ar" in capsys.readouterr().err
    assert not (tmp_path / "x.csv").exists()
