"""Exporta a apresentação HTML e o resumo Markdown da carga."""

from pathlib import Path

from .dashboard import money, number, render_dashboard


def write_reports(output: Path, summary: dict) -> None:
    """Apresenta agregados sem embutir registros individuais no relatório."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "relatorio.html").write_text(
        render_dashboard(summary, local_downloads=True), encoding="utf-8"
    )
    counts = summary["counts"]
    lines = [
        "# Relatório da pipeline",
        "",
        f"Origem: {summary.get('source_filename', 'Não informada')}.",
        f"Execução: {summary.get('run_id', 'Não informada')}.",
        f"Publicação/execução UTC: {summary.get('executed_at_utc', 'Não informada')}.",
        "",
        "| Medida | Registros |",
        "|---|---:|",
    ]
    for label, name in [
        ("Linhas de origem", "linhas_origem"),
        ("Registros únicos", "registros_unicos"),
        ("Duplicatas removidas", "duplicatas_removidas"),
        ("Carteira Gold", "linhas_gold"),
        ("APTO", "APTO"),
        ("REVISAO", "REVISAO"),
        ("QUARENTENA", "QUARENTENA"),
    ]:
        lines.append(f"| {label} | {number(counts.get(name))} |")
    lines += [
        "",
        "## Indicadores por universo e corte",
        "",
        "Os universos estão contidos uns nos outros. Não somar universos, cortes ou reprocessamentos.",
        "Somas preservam sinais e consideram somente valores preenchidos. Ausência não é zero.",
        "",
        "| Universo | Corte | Registros | Saldo contábil | Saldos preenchidos | Saldos ausentes |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in summary.get("analytics", {}).get("indicadores", []):
        lines.append(
            f"| {row['universo']} | {row['data_corte'] or 'Sem corte'} | {number(row['quantidade'])} | {money(row['saldo_contabil_centavos'])} | {number(row['saldos_preenchidos'])} | {number(row['saldos_ausentes'])} |"
        )
    lines += [
        "",
        "## Qualidade",
        "",
        "Uma ocorrência não equivale a uma pessoa. Um registro pode aparecer em várias regras.",
        "",
        "| Regra | Nível | Registros afetados | Ocorrências |",
        "|---|---|---:|---:|",
    ]
    for row in summary.get("quality_counts", []):
        lines.append(
            f"| {row['codigo']} | {row['severidade']} | {number(row['registros'])} | {number(row['ocorrencias'])} |"
        )
    lines += ["", "## Controles", ""]
    lines.extend(
        f"- {'OK' if passed else 'FALHA'}: {name}."
        for name, passed in summary.get("controls", {}).items()
    )
    lines += [
        "",
        "## Consumo",
        "",
        "Consulte relatorio.html para filtrar o corte/universo. Os marts, os dicionários e as bases são arquivos da mesma carga.",
        "APTO indica conformidade às regras de qualidade. A fonte não permite calcular preço ou recuperação sem dados adicionais.",
        "As bases individuais e o SQLite contêm dados pessoais e exigem acesso controlado.",
        "",
    ]
    (output / "relatorio.md").write_text("\n".join(lines), encoding="utf-8")
