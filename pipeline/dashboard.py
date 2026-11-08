"""Apresentação agregada compartilhada: valores exatos e universos explícitos."""

from __future__ import annotations

import json
from decimal import ROUND_HALF_UP, Decimal, localcontext
from html import escape

from .metrics import safe_ratio

UNIVERSE_LABELS = {"CARTEIRA_PUBLICADA": "Carteira publicada", "APTOS": "Registros aptos"}


def number(value):
    return f"{int(value or 0):,}".replace(",", ".")


def money(value):
    if value is None:
        return "Não disponível"
    with localcontext() as context:
        context.prec = 50
        amount = (Decimal(value) / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        whole, cents = f"{abs(amount):.2f}".split(".")
    return f"{'−' if amount < 0 else ''}R$ {number(whole)},{cents}"


def percent(numerator, denominator):
    ratio = safe_ratio(numerator, denominator)
    return "Não disponível" if ratio is None else f"{ratio * 100:.2f}%".replace(".", ",")


def text(value):
    return escape(str(value if value is not None else "Não informado"), quote=True)


def average_cents(total, filled):
    if total is None or not filled:
        return None
    with localcontext() as context:
        context.prec = 50
        return Decimal(total) / Decimal(filled)


def financial_table(row):
    rows = []
    for label, amount, prefix in [
        ("Dívida original", "valor_original_centavos", "original"),
        ("Saldo contábil", "saldo_contabil_centavos", "saldos"),
        ("Valor de face", "valor_face_centavos", "face"),
    ]:
        filled, missing, negative = (
            row.get(prefix + "_" + suffix, 0) for suffix in ["preenchidos", "ausentes", "negativos"]
        )
        rows.append(
            f"<tr><td>{label}</td><td>{money(row.get(amount))}</td><td>{number(filled)} / {number(row.get('quantidade'))}<small>{percent(filled, row.get('quantidade'))} preenchidos</small></td><td>{number(missing)}</td><td>{number(negative)}</td><td>{money(average_cents(row.get(amount), filled))}</td></tr>"
        )
    return (
        '<div class="scroll"><table><thead><tr><th>Medida</th><th>Soma disponível</th><th>Cobertura</th><th>Sem valor utilizável</th><th>Negativos</th><th>Média dos preenchidos</th></tr></thead><tbody>'
        + "".join(rows)
        + "</tbody></table></div>"
    )


def bars(rows, dimension):
    counts = {}
    for row in rows:
        name = row.get(dimension) or "Não informado"
        counts[name] = counts.get(name, 0) + int(row["quantidade"])
    maximum = max(counts.values(), default=1) or 1
    result = "".join(
        f'<li><span>{text(name)}</span><b>{number(count)}</b><i style="width:{100 * count / maximum:.2f}%"></i></li>'
        for name, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    )
    return (
        '<ul class="bars">' + result + "</ul>"
        if result
        else "<p>Sem registros neste universo e corte.</p>"
    )


def indicator_panel(row, portfolio):
    selected = [
        r
        for r in portfolio
        if r.get("data_corte") == row.get("data_corte")
        and (row["universo"] != "APTOS" or r["status_qualidade"] == "APTO")
    ]
    cards = [
        ("Registros", number(row["quantidade"])),
        ("Devedores documentais distintos", number(row["devedores_distintos"])),
        ("Cedentes documentais distintos", number(row["cedentes_distintos"])),
        (
            "Atraso acima de 90 dias",
            f"{number(row['atraso_acima_90'])} · {percent(row['atraso_acima_90'], row['atraso_calculavel'])}",
        ),
    ]
    result = (
        '<div class="cards">'
        + "".join(
            f"<div><small>{label}</small><strong>{value}</strong></div>" for label, value in cards
        )
        + "</div>"
    )
    result += financial_table(row)
    result += (
        '<div class="split"><div><h3>Registros por produto</h3>'
        + bars(selected, "produto")
        + "</div><div><h3>Registros por faixa de atraso</h3>"
        + bars(selected, "faixa_atraso")
        + "</div></div>"
    )
    result += "<h3>Disponibilidade e indicadores declarados</h3><table><thead><tr><th>Indicador</th><th>Registros</th><th>Denominador / observação</th></tr></thead><tbody>"
    for label, field, denominator, detail in [
        (
            "Com e-mail utilizável",
            "com_email",
            "quantidade",
            "Validação sintática; não comprova entrega",
        ),
        (
            "Com telefone utilizável",
            "com_telefone",
            "quantidade",
            "Validação sintática; não comprova contato",
        ),
        (
            "Atraso calculável",
            "atraso_calculavel",
            "quantidade",
            "Base para o percentual acima de 90 dias",
        ),
        (
            "Vencimento após corte",
            "vencimento_apos_corte",
            "quantidade",
            "Data informada posterior ao corte",
        ),
        (
            "Garantia: sim",
            "garantia_sim",
            "quantidade",
            "Indicador declarado; não verifica eficácia",
        ),
        (
            "Garantia: sem informação",
            "garantia_sem_informacao",
            "quantidade",
            "Sem valor 0/1 utilizável",
        ),
        (
            "Ajuizado: sim",
            "ajuizado_sim",
            "quantidade",
            "Indicador declarado; não é contagem de processos",
        ),
        (
            "Ajuizado: sem informação",
            "ajuizado_sem_informacao",
            "quantidade",
            "Sem valor 0/1 utilizável",
        ),
    ]:
        result += f"<tr><td>{label}</td><td>{number(row[field])} · {percent(row[field], row[denominator])}</td><td>{detail}<small>Base: {number(row[denominator])} registros</small></td></tr>"
    return result + "</tbody></table>"


def render_dashboard(summary, *, local_downloads=False):
    """HTML offline com corte/universo selecionáveis; nenhum registro individual embutido."""
    counts = summary.get("counts", {})
    analytics = summary.get("analytics", {})
    indicators = analytics.get("indicadores", [])
    portfolio = analytics.get("resumo", summary.get("portfolio", []))
    choices, panels = [], {}
    for index, row in enumerate(
        sorted(indicators, key=lambda r: (r.get("data_corte") or "", r["universo"]), reverse=True)
    ):
        if row["universo"] not in UNIVERSE_LABELS:
            continue
        key = str(index)
        choices.append(
            f'<option value="{key}">{text(row.get("data_corte") or "Sem corte utilizável")} · {UNIVERSE_LABELS[row["universo"]]}</option>'
        )
        panels[key] = indicator_panel(row, portfolio)
    first = next(iter(panels), None)
    cards = [
        ("Linhas recebidas", counts.get("linhas_origem", 0)),
        ("Registros únicos", counts.get("registros_unicos", 0)),
        ("Duplicatas removidas", counts.get("duplicatas_removidas", 0)),
        ("Registros Gold", counts.get("linhas_gold", 0)),
    ]
    volumes = (
        '<div class="cards">'
        + "".join(
            f"<div><small>{label}</small><strong>{number(value)}</strong></div>"
            for label, value in cards
        )
        + "</div>"
    )
    quality = "<table><thead><tr><th>Estado</th><th>Registros únicos</th><th>Participação na base tratada</th></tr></thead><tbody>"
    for status in ["APTO", "REVISAO", "QUARENTENA"]:
        quality += f"<tr><td>{status}</td><td>{number(counts.get(status))}</td><td>{percent(counts.get(status, 0), counts.get('registros_unicos', 0))}</td></tr>"
    quality += "</tbody></table><h3>Regras com mais registros afetados</h3><table><thead><tr><th>Regra</th><th>Nível</th><th>Registros afetados</th><th>Ocorrências</th></tr></thead><tbody>"
    for row in sorted(summary.get("quality_counts", []), key=lambda r: -int(r.get("registros", 0)))[
        :12
    ]:
        quality += f"<tr><td>{text(row['codigo'])}</td><td>{text(row['severidade'])}</td><td>{number(row.get('registros'))}<small>{percent(row.get('registros', 0), counts.get('registros_unicos', 0))} da base tratada</small></td><td>{number(row.get('ocorrencias'))}</td></tr>"
    quality += "</tbody></table><p>Um registro pode violar várias regras. Ocorrências e registros afetados não devem ser somados como pessoas.</p>"
    checks = "".join(
        f"<li>{'OK' if passed else 'FALHA'} · {text(name)}</li>"
        for name, passed in summary.get("controls", {}).items()
    )
    downloads = ""
    if local_downloads:
        names = [
            "base_tratada.csv",
            "base_precificacao.csv",
            "carteira_agregada.csv",
            "indicadores_carteira.csv",
            "qualidade_agregada.csv",
            "quarentena.csv",
            "carteira.sqlite",
            "dicionario_dados.csv",
            "dicionario_metricas.csv",
        ]
        downloads = (
            "<section><h2>Arquivos locais</h2><p>Os arquivos individuais contêm dados pessoais; os marts são agregados.</p>"
            + "".join(f'<a class="chip" href="{name}" download>{name}</a>' for name in names)
            + "</section>"
        )
    elif summary.get("gold_database"):
        downloads = (
            "<section><h2>Dados para consumo</h2><p>Database: <code>"
            + text(summary["gold_database"])
            + '</code>. Consulte <code>vw_resumo_carteira_atual</code>, <code>vw_indicadores_carteira_atual</code> e <code>vw_qualidade_atual</code>. O detalhe reduzido está em <code>vw_carteira_mascarada_atual</code>; o identificado, em <code>vw_carteira_atual</code>, para analistas autorizados.</p><a href="https://console.aws.amazon.com/athena/">Abrir Athena</a></section>'
        )
    truncation = (
        '<p class="note">O relatório apresenta uma amostra dos agregados; os datasets completos estão no lake.</p>'
        if any(summary.get("analytics_truncated", {}).values())
        else ""
    )
    payload = (
        json.dumps(panels, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Carteira · indicadores e qualidade</title>
<style>*{{box-sizing:border-box}}body{{margin:0;background:#f1f4f8;color:#17243c;font:15px/1.6 system-ui}}main{{max-width:1180px;margin:auto;padding:30px 22px}}header{{background:#13284a;color:white;border-radius:16px;padding:28px}}section{{background:white;margin:20px 0;padding:24px;border-radius:12px}}h1,h2,h3{{line-height:1.25}}h2{{font-size:21px}}h3{{font-size:16px;margin-top:26px}}.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:20px 0}}.cards>div{{background:white;border:1px solid #dce3ee;border-radius:10px;padding:16px}}strong{{display:block;font-size:25px}}small{{display:block;color:#65748a;font-size:11px}}header small{{color:#ced9eb}}table{{border-collapse:collapse;width:100%;font-size:12px}}th,td{{text-align:left;padding:10px;border-bottom:1px solid #e2e8f0;vertical-align:top}}th{{background:#f7f9fc}}.scroll{{overflow:auto}}select{{max-width:100%;font:inherit;padding:10px;border:1px solid #a6b8d7;border-radius:8px}}.split{{display:grid;grid-template-columns:1fr 1fr;gap:24px}}.bars{{list-style:none;padding:0}}.bars li{{display:flex;position:relative;flex-wrap:wrap;justify-content:space-between;padding:6px 0 12px;font-size:12px}}.bars i{{display:block;position:absolute;bottom:4px;left:0;height:4px;background:#547bc5;border-radius:4px}}code{{overflow-wrap:anywhere;font-size:12px}}.chip{{display:inline-block;padding:8px;margin:5px;background:#edf2fa;border-radius:6px}}a{{color:#224db2}}.note{{background:#fff5db;padding:12px}}@media(max-width:700px){{.cards{{grid-template-columns:repeat(2,1fr)}}.split{{grid-template-columns:1fr}}section{{padding:16px}}}}@media print{{select{{display:none}}main{{padding:0}}section{{break-inside:avoid}}}}</style></head><body><main>
<header><small>CARTEIRA · EXPOSIÇÃO E QUALIDADE</small><h1>Dados para análise, com universo definido</h1><p>Fotografia da fonte, valores disponíveis e decisões de tratamento rastreáveis.</p></header>{volumes}
<section><h2>Indicadores da carteira</h2><p>Escolha um corte e um universo. Os aptos estão contidos na carteira publicada; os universos não devem ser somados.</p><label for="scope">Corte e universo </label><select id="scope">{"".join(choices)}</select>{truncation}<div id="indicators">{panels.get(first, "<p>Indicadores indisponíveis nesta carga.</p>")}</div><p>Valores em reais na apresentação, armazenados em centavos. As somas preservam negativos; ausências não são zeros. Médias usam somente os valores preenchidos.</p></section>
<section><h2>Qualidade da base tratada</h2><p>Denominador: todos os registros únicos desta carga, incluindo quarentena e todos os cortes.</p>{quality}</section>
<section><h2>Controles de publicação</h2><ul>{checks}</ul></section>{downloads}
<section><h2>O que a fonte permite analisar</h2><p>Exposição por produto, perfil e atraso; qualidade e disponibilidade cadastral; indicadores jurídicos e de garantia declarados. APTO significa conformidade às regras locais de qualidade.</p><p>A fonte não contém eventos de recebimento datados, custos de cobrança ou histórico suficiente para estimar recuperação e preço. Saldos e valor pago acumulado não formam uma série de arrecadação.</p></section>
<section><h2>Linhagem e tempo</h2><p>Cortes: {text(", ".join(sorted({str(r.get("data_corte") or "sem corte") for r in indicators})) or "Não informado")}</p><p>Publicação / execução UTC: {text(summary.get("published_at", summary.get("executed_at_utc")))}</p><p>“Atual” indica a última publicação, não necessariamente o maior corte. Os saldos não são aditivos entre cortes ou reprocessamentos.</p><p>Origem: {text(summary.get("source_filename", summary.get("source_key")))}</p><p>Identificador: <code>{text(summary.get("load_id", summary.get("run_id")))}</code></p><p>SHA-256: <code>{text(summary.get("source_sha256"))}</code></p></section>
</main><script type="application/json" id="panels">{payload}</script><script type="text/javascript">const panels=JSON.parse(document.getElementById('panels').textContent);document.getElementById('scope').addEventListener('change',event=>{{document.getElementById('indicators').innerHTML=panels[event.target.value]||'';}});</script></body></html>"""
