"""Relatórios locais agregados, sem publicar informações dos devedores."""

from __future__ import annotations

from collections import defaultdict
from html import escape
from pathlib import Path


def _number(value: object) -> str:
    return f"{int(value or 0):,}".replace(",", ".")


def _money(value: object) -> str:
    if value is None:
        return "Não disponível"
    amount = int(value)
    whole, cents = divmod(abs(amount), 100)
    return f"{'−' if amount < 0 else ''}R$ {_number(whole)},{cents:02d}"


def _h(value: object) -> str:
    return escape(str(value if value is not None else "Não informado"), quote=True)


def _m(value: object) -> str:
    return str(value if value is not None else "Não informado").replace("|", "\\|").replace("\n", " ")


def _label(value: object) -> str:
    return str(value).replace("_", " ").capitalize()


def write_reports(output: Path, summary: dict) -> None:
    """Grava relatorio.html e relatorio.md usando apenas os agregados da carga."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    counts = summary.get("counts", {})
    quality = sorted(summary.get("quality_counts", []), key=lambda r: (-r.get("registros", 0), str(r.get("codigo", ""))))
    dates = sorted(summary.get("cutoff_dates", {}).items())
    financial = summary.get("financial", {})
    controls = summary.get("controls", {})
    portfolio = summary.get("portfolio", [])
    products = defaultdict(lambda: {"quantidade": 0, "saldo": None})
    for row in portfolio:
        product = row.get("produto") or "Não informado"
        products[product]["quantidade"] += int(row.get("quantidade") or 0)
        if row.get("saldo_contabil_centavos") is not None:
            products[product]["saldo"] = (products[product]["saldo"] or 0) + int(row["saldo_contabil_centavos"])
    ranked_products = sorted(products.items(), key=lambda x: (-x[1]["quantidade"], x[0]))
    links = [
        ("base_tratada.csv", "Base tratada", "Registros únicos e estado de qualidade"),
        ("base_precificacao.csv", "Base apta às regras", "Subconjunto APTO; não é uma indicação de preço"),
        ("quarentena.csv", "Quarentena", "Casos impeditivos para saneamento"),
        ("carteira.sqlite", "Banco SQLite", "Dimensões, fato e visões de consulta"),
        ("qualidade.json", "Métricas de qualidade", "Resumo estruturado da execução"),
        ("dicionario_dados.csv", "Dicionário de dados", "Definição das colunas tratadas"),
        ("linhagem.csv", "Linhagem", "Relação entre registros e linhas de origem"),
    ]
    metrics = [
        ("Linhas recebidas", counts.get("linhas_origem", 0), "Antes da deduplicação"),
        ("Registros únicos", counts.get("registros_unicos", 0), "Após tratar repetições"),
        ("Repetições removidas", counts.get("duplicatas_removidas", 0), f"{_number(counts.get('duplicatas_exatas'))} exatas · {_number(counts.get('duplicatas_normalizacao'))} após normalização"),
        ("Carteira publicada", counts.get("linhas_gold", 0), "Registros fora da quarentena"),
    ]
    states = [
        ("APTO", "Aptos", "Passaram pelas regras locais; disponíveis para análise."),
        ("REVISAO", "Em revisão", "Preservados com ocorrências que precisam ser avaliadas."),
        ("QUARENTENA", "Em quarentena", "Excluídos da fato publicada; exigem saneamento."),
    ]
    metrics_html = "".join(f'<article class="metric"><span>{_h(label)}</span><strong>{_number(value)}</strong><small>{_h(detail)}</small></article>' for label, value, detail in metrics)
    states_html = "".join(f'<article class="state {code.lower()}"><span>{_h(label)}</span><strong>{_number(counts.get(code))}</strong><p>{_h(detail)}</p></article>' for code, label, detail in states)
    cuts_html = "".join(f'<span class="chip">{_h(cut)} <b>{_number(amount)}</b></span>' for cut, amount in dates) or '<span class="muted">Nenhuma data de corte válida.</span>'
    quality_rows = "".join(
        f'<tr><td><code>{_h(row.get("codigo"))}</code></td><td>{_h(row.get("severidade"))}</td><td class="num">{_number(row.get("registros"))}</td><td class="num">{_number(row.get("ocorrencias"))}</td></tr>'
        for row in quality[:12]
    ) or '<tr><td colspan="4">Nenhuma ocorrência registrada.</td></tr>'
    product_max = max((r["quantidade"] for _, r in ranked_products), default=1) or 1
    product_rows = "".join(
        f'<tr><td>{_h(name)}<span class="bar" aria-hidden="true"><i style="width:{100 * row["quantidade"] / product_max:.2f}%"></i></span></td><td class="num">{_number(row["quantidade"])}</td><td class="num">{_money(row["saldo"])}</td></tr>'
        for name, row in ranked_products
    ) or '<tr><td colspan="3">Nenhum registro publicado.</td></tr>'
    financial_rows = []
    for group, title in (("todos", "Registros únicos"), ("gold", "Carteira publicada"), ("aptos", "Aptos às regras")):
        values = financial.get(group, {})
        amounts = []
        for column in ("valor_original_centavos", "valor_contabil_centavos", "valor_face_centavos"):
            amount = values.get(column)
            missing = values.get(column + "_ausentes", 0)
            amounts.append(f'<td class="num">{_money(amount)}<small>{_number(missing)} ausentes</small></td>')
        financial_rows.append(f'<tr><td>{_h(title)}</td>{"".join(amounts)}</tr>')
    checks_html = "".join(f'<li class="check {"pass" if passed else "fail"}"><b>{"OK" if passed else "FALHA"}</b><span>{_h(_label(name))}</span></li>' for name, passed in controls.items()) or '<li>Nenhum controle informado.</li>'
    files_html = "".join(f'<a class="file" href="{_h(filename)}" download><strong>{_h(title)} <span aria-hidden="true">↗</span></strong><small>{_h(detail)}</small><code>{_h(filename)}</code></a>' for filename, title, detail in links)
    profile_columns = summary.get("source_profile", {}).get("columns", {})
    incomplete = sorted(profile_columns.items(), key=lambda item: (-int(item[1].get("ausentes", 0)), item[0]))[:10]
    profile_rows = "".join(f'<tr><td>{_h(letter)}</td><td>{_h(row.get("nome"))}</td><td class="num">{_number(row.get("ausentes"))}</td><td class="num">{_number(row.get("distintos"))}</td></tr>' for letter, row in incomplete) or '<tr><td colspan="4">Perfil indisponível.</td></tr>'
    html = f'''<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<title>Carteira de crédito — relatório da pipeline</title>
<style>
:root{{--bg:#f1f4f8;--ink:#17243c;--muted:#65748a;--line:#e2e8f0;--blue:#224db2;--green:#16714d;--amber:#96620c;--red:#a63b43}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}main{{max-width:1220px;margin:0 auto;padding:40px 28px 64px}}header{{background:#13284a;color:#fff;border-radius:20px;padding:34px 36px;margin-bottom:24px}}.eyebrow{{font-size:11px;letter-spacing:.14em;text-transform:uppercase;font-weight:700;opacity:.75}}h1{{font-size:34px;line-height:1.15;letter-spacing:-.03em;margin:12px 0}}header p{{max-width:790px;opacity:.8;margin-bottom:0}}h2{{font-size:20px;margin:0 0 12px;letter-spacing:-.02em}}h3{{font-size:15px;margin:0 0 9px}}p{{margin:0 0 12px}}.muted,small{{color:var(--muted)}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin:18px 0}}.metric,.panel,.state,.file{{background:#fff;border:1px solid var(--line);border-radius:14px}}.metric{{padding:22px}}.metric span,.state span{{font-size:12px;font-weight:600;color:var(--muted)}}.metric strong{{display:block;font-size:32px;letter-spacing:-.03em;margin:7px 0}}small{{display:block;font-size:11px}}.panel{{padding:24px;margin:20px 0}}.states{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}}.state{{padding:22px;border-top:4px solid var(--green)}}.state strong{{display:block;font-size:28px;margin:6px 0}}.state p{{font-size:12px;color:var(--muted);margin:0}}.revisao{{border-top-color:var(--amber)}}.quarentena{{border-top-color:var(--red)}}.note{{border-left:3px solid #6383c3;padding:12px 16px;background:#f3f6fc;font-size:13px;margin-top:16px}}.split{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}.split .panel{{margin:0}}.table-wrap{{overflow-x:auto}}table{{width:100%;border-collapse:collapse;font-size:12px}}th{{color:var(--muted);font-weight:600;text-align:left;background:#f7f9fc}}th,td{{padding:11px 10px;border-bottom:1px solid var(--line);vertical-align:top}}tbody tr:last-child td{{border:0}}.num{{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}}code{{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:11px;overflow-wrap:anywhere}}.chip{{display:inline-block;padding:6px 10px;margin:3px 5px 3px 0;background:#edf2fa;border-radius:7px;font-size:12px}}.chip b{{margin-left:8px}}.bar{{display:block;height:4px;background:#edf1f7;margin-top:8px;width:100%;border-radius:5px}}.bar i{{display:block;height:100%;background:#557cc8;border-radius:5px}}.checks{{list-style:none;padding:0;margin:0;display:grid;grid-template-columns:repeat(2,1fr);gap:10px}}.check{{display:flex;align-items:center;gap:10px;font-size:12px}}.check b{{font-size:10px;min-width:44px;text-align:center;border-radius:5px;padding:4px;background:#e7f4ed;color:var(--green)}}.fail b{{background:#fdecec;color:var(--red)}}.files{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}a.file{{display:block;text-decoration:none;color:var(--ink);padding:17px;transition:box-shadow .15s}}a.file:hover,a.file:focus{{box-shadow:0 4px 16px #1e366b16;outline:2px solid #809ed8}}.file strong{{display:flex;justify-content:space-between;font-size:13px}}.file small{{margin:5px 0 12px}}.file code{{color:var(--blue)}}.meta{{display:grid;grid-template-columns:160px 1fr;gap:6px 14px;font-size:12px;margin:0}}.meta dt{{color:var(--muted)}}.meta dd{{margin:0;overflow-wrap:anywhere}}footer{{font-size:11px;color:var(--muted);padding:10px 2px}}ul.explain{{padding-left:20px;margin:0;font-size:13px}}li{{margin-bottom:7px}}@media(max-width:850px){{.grid{{grid-template-columns:repeat(2,1fr)}}.split{{grid-template-columns:1fr}}.files{{grid-template-columns:repeat(2,1fr)}}}}@media(max-width:560px){{main{{padding:18px 14px 36px}}header{{padding:26px 22px}}h1{{font-size:27px}}.states,.files,.checks{{grid-template-columns:1fr}}.panel{{padding:18px}}.metric{{padding:16px}}.metric strong{{font-size:26px}}.meta{{grid-template-columns:1fr;gap:2px}}.meta dd{{margin-bottom:8px}}}}@media print{{body{{background:#fff}}main{{max-width:none;padding:0}}.panel,.metric,.state{{break-inside:avoid}}header{{background:#fff;color:var(--ink);border:1px solid var(--line)}}.file{{color:var(--ink)}}}}
</style>
</head>
<body><main>
<header><div class="eyebrow">Engenharia de dados · teste técnico</div><h1>Uma carteira consistente,<br>com decisões rastreáveis.</h1><p>Resultado da ingestão, padronização e validação da base. A publicação mantém as limitações de qualidade visíveis e preserva os casos que exigem revisão.</p></header>
<section aria-label="Indicadores da carga" class="grid">{metrics_html}</section>
<section class="panel"><h2>Qualidade e elegibilidade</h2><div class="states">{states_html}</div><div class="note"><strong>APTO significa pronto para análise sob as regras do teste.</strong> Não representa preço aprovado, probabilidade de recuperação nem confirmação cadastral. Uma ocorrência pode afetar vários campos e um registro pode ter várias ocorrências.</div></section>
<section class="panel"><h2>Datas de corte</h2><p class="muted">A carteira representa uma fotografia em cada corte. Não some saldos de cortes diferentes como se fossem dívidas distintas.</p>{cuts_html}</section>
<section class="panel"><h2>Exposição e reconciliação financeira</h2><p class="muted">Somas dos valores disponíveis, preservando sinais negativos. Ausências não são substituídas por zero; os valores do banco são centavos inteiros.</p><div class="table-wrap"><table><thead><tr><th>Universo</th><th class="num">Dívida original</th><th class="num">Saldo contábil</th><th class="num">Valor de face</th></tr></thead><tbody>{''.join(financial_rows)}</tbody></table></div></section>
<div class="split"><section class="panel"><h2>Principais ocorrências</h2><p class="muted">Até 12 regras, ordenadas por registros afetados. Ocorrências não são contagens de pessoas distintas.</p><div class="table-wrap"><table><thead><tr><th>Regra</th><th>Nível</th><th class="num">Registros</th><th class="num">Ocorrências</th></tr></thead><tbody>{quality_rows}</tbody></table></div></section>
<section class="panel"><h2>Produtos da carteira publicada</h2><p class="muted">Inclui aptos e registros em revisão; a quarentena não entra nesta distribuição. Barras representam quantidade de contratos.</p><div class="table-wrap"><table><thead><tr><th>Produto</th><th class="num">Contratos</th><th class="num">Saldo contábil</th></tr></thead><tbody>{product_rows}</tbody></table></div></section></div>
<section class="panel"><h2>Verificações de publicação</h2><ul class="checks">{checks_html}</ul></section>
<section class="panel"><h2>Completude na origem</h2><p class="muted">As 10 colunas com mais ausências. Há campos que se aplicam somente a PF ou PJ; esta visão bruta não é uma taxa de erro de negócio.</p><div class="table-wrap"><table><thead><tr><th>Coluna</th><th>Nome original</th><th class="num">Ausentes</th><th class="num">Valores distintos</th></tr></thead><tbody>{profile_rows}</tbody></table></div></section>
<section class="panel"><h2>Decisões que afetam a leitura</h2><ul class="explain"><li>Datas com hífen numérico seguem o contrato MDY desta fonte; barras e pontos seguem DMY e datas ISO seguem YMD. O pivô para anos com dois dígitos é 30.</li><li>O atraso é recalculado no corte sem apagar o valor informado. As divergências são rastreáveis.</li><li>Negativos monetários mantêm o sinal e geram aviso. Não há substituição por valor absoluto nem imputação automática de saldos.</li><li>Grupos incompatíveis de cedente, contrato e corte ficam em quarentena. Incluir o devedor na chave é uma alternativa que depende de homologação.</li><li>Os blocos específicos PF/PJ são verificados separadamente. Documento com dígito verificador válido não comprova existência nem titularidade.</li></ul></section>
<section class="panel"><h2>Arquivos para consumo</h2><p class="muted">Os links apontam para arquivos locais gerados junto a este relatório. As bases individuais e a linhagem podem conter dados pessoais e exigem acesso controlado.</p><div class="files">{files_html}</div></section>
<section class="panel"><h2>Próximo passo para precificar</h2><p>São necessários recebimentos históricos por contrato e data, custos e estratégias de cobrança, garantias e situação jurídica verificadas, além da definição do resultado a prever. A fotografia atual permite segmentar exposição e avaliar qualidade; não fundamenta sozinha um preço de aquisição.</p></section>
<section class="panel"><h2>Rastreabilidade da execução</h2><dl class="meta"><dt>Arquivo de origem</dt><dd>{_h(summary.get('source_filename'))}</dd><dt>Aba</dt><dd>{_h(summary.get('sheet'))}</dd><dt>SHA-256</dt><dd><code>{_h(summary.get('source_sha256'))}</code></dd><dt>Execução</dt><dd><code>{_h(summary.get('run_id'))}</code></dd><dt>Versão das regras</dt><dd>{_h(summary.get('rule_version'))}</dd><dt>Horário em UTC</dt><dd>{_h(summary.get('executed_at_utc'))}</dd></dl></section>
<footer>Relatório agregado gerado a partir dos resultados da pipeline. Funciona offline e não carrega recursos externos.</footer>
</main></body></html>'''
    (output / "relatorio.html").write_text(html, encoding="utf-8")

    lines = [
        "# Relatório da pipeline de dados", "",
        f"Origem: **{_m(summary.get('source_filename'))}** · aba **{_m(summary.get('sheet'))}**.", "",
        f"Execução: `{_m(summary.get('run_id'))}` · regras `{_m(summary.get('rule_version'))}` · UTC: {_m(summary.get('executed_at_utc'))}.", "",
        "## Resultado da carga", "",
        "| Indicador | Quantidade |", "|---|---:|",
    ]
    lines.extend(f"| {_m(label)} | {_number(value)} |" for label, value, _ in metrics)
    lines.extend(["", f"Repetições removidas: {_number(counts.get('duplicatas_exatas'))} exatas e {_number(counts.get('duplicatas_normalizacao'))} após normalização.", "", "| Estado | Registros | Significado |", "|---|---:|---|"])
    lines.extend(f"| {code} | {_number(counts.get(code))} | {_m(detail)} |" for code, _, detail in states)
    lines.extend(["", "APTO significa pronto para análise sob as regras do teste; não representa preço aprovado, aprovação de crédito nem confirmação cadastral.", "", "## Datas de corte", ""])
    lines.extend([f"- `{_m(cut)}`: {_number(amount)} registros." for cut, amount in dates] or ["Nenhum corte válido informado."])
    lines.extend(["", "## Valores e ausências", "", "Somas de valores disponíveis, com sinais preservados. Nulos não são zeros. Valores armazenados no banco em centavos inteiros.", "", "| Universo | Dívida original | Saldo contábil | Valor de face |", "|---|---:|---:|---:|"])
    for group, title in (("todos", "Registros únicos"), ("gold", "Carteira publicada"), ("aptos", "Aptos às regras")):
        values = financial.get(group, {})
        cells = [f"{_money(values.get(field))} ({_number(values.get(field + '_ausentes'))} ausentes)" for field in ("valor_original_centavos", "valor_contabil_centavos", "valor_face_centavos")]
        lines.append(f"| {title} | {' | '.join(cells)} |")
    lines.extend(["", "## Principais ocorrências", "", "Até 12 regras por registros afetados. Uma linha pode produzir várias ocorrências.", "", "| Código | Severidade | Registros | Ocorrências |", "|---|---|---:|---:|"])
    lines.extend(f"| {_m(r.get('codigo'))} | {_m(r.get('severidade'))} | {_number(r.get('registros'))} | {_number(r.get('ocorrencias'))} |" for r in quality[:12])
    if not quality:
        lines.append("| Sem ocorrências | — | 0 | 0 |")
    lines.extend(["", "## Produtos publicados", "", "Inclui APTO e REVISAO; exclui QUARENTENA.", "", "| Produto | Contratos | Saldo contábil |", "|---|---:|---:|"])
    lines.extend(f"| {_m(name)} | {_number(row['quantidade'])} | {_money(row['saldo'])} |" for name, row in ranked_products)
    lines.extend(["", "## Verificações", ""])
    lines.extend([f"- {'OK' if passed else 'FALHA'}: {_m(_label(name))}." for name, passed in controls.items()] or ["Nenhum controle informado."])
    lines.extend([
        "", "## Decisões de tratamento", "",
        "- Datas com hífen numérico: MDY por contrato desta fonte; barras/pontos: DMY; ISO: YMD. Pivô fixo para ano de dois dígitos: 30.",
        "- O atraso calculado no corte é separado do informado na fonte; divergências são rastreáveis.",
        "- Negativos mantêm o sinal e geram aviso; saldos ausentes não são imputados.",
        "- Repetições equivalentes são reduzidas; conflitos de cedente/contrato/corte ficam em quarentena sem escolha arbitrária de versão.",
        "- A chave conservadora exige homologação do cedente. Incluir o devedor na chave mudaria a interpretação dos grupos conflitantes.",
        "- PF e PJ têm regras de aplicabilidade distintas; validação documental local não comprova titularidade.",
        "", "## Arquivos", "",
    ])
    lines.extend(f"- [{title}]({filename}): {detail}." for filename, title, detail in links)
    lines.extend([
        "", "As bases individuais e a linhagem podem conter dados pessoais; compartilhe com acesso controlado e use agregados quando a identificação não for necessária.",
        "", "## Para construir uma precificação", "",
        "Obter recebimentos históricos por contrato e data, custos e estratégias de cobrança, garantias e situação jurídica verificadas e definir o resultado a prever. Avaliar modelos respeitando o tempo e evitando dados posteriores ao corte. Esta base, isoladamente, não fundamenta probabilidade de recuperação ou preço de aquisição.",
        "", "## Linhagem da carga", "",
        f"SHA-256 da origem: `{_m(summary.get('source_sha256'))}`.", "",
    ])
    (output / "relatorio.md").write_text("\n".join(lines), encoding="utf-8")
