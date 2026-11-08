"""Relatório agregado para cargas AWS; dados individuais permanecem no lake."""
from html import escape


def render_report(summary):
    esc = lambda v: escape(str(v), quote=True)
    counts = summary["counts"]
    cards = "".join(f'<div class="card"><span>{esc(label)}</span><strong>{value:,}</strong></div>'
        for label, value in [("Linhas recebidas", counts["linhas_origem"]), ("Registros únicos", counts["registros_unicos"]),
                             ("Aptos", counts["APTO"]), ("Revisão", counts["REVISAO"]), ("Quarentena", counts["QUARENTENA"])])
    checks = "".join(f'<li>{"OK" if passed else "FALHA"} · {esc(name)}</li>' for name, passed in summary["controls"].items())
    return f'''<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pipeline AWS · carteira</title><style>body{{font:16px/1.6 system-ui;color:#17243c;background:#f1f4f8;margin:0}}main{{max-width:1000px;margin:40px auto;padding:24px}}header{{background:#13284a;color:white;padding:28px;border-radius:16px}}section{{background:white;padding:24px;margin:20px 0;border-radius:12px}}.cards{{display:flex;flex-wrap:wrap;gap:12px;margin:20px 0}}.card{{flex:1;background:white;padding:18px;border-radius:12px}}strong{{display:block;font-size:28px}}span{{font-size:12px}}code{{overflow-wrap:anywhere}}a{{color:#224db2}}</style><main>
<header><small>AWS · S3 + GLUE + ATHENA</small><h1>Carteira processada</h1><p>Bronze → Silver → Gold, com rastreabilidade e controle de publicação.</p></header>
<div class="cards">{cards}</div><section><h2>Consulta no Athena</h2><p>Database: <code>{esc(summary['gold_database'])}</code>. Use <code>vw_carteira_atual</code> para a última carga publicada e <code>vw_precificacao_atual</code> para o subconjunto APTO.</p><p>APTO significa conformidade às regras de qualidade; não determina um preço.</p><a href="https://console.aws.amazon.com/athena/">Abrir console do Athena</a></section>
<section><h2>Validações</h2><ul>{checks}</ul></section><section><h2>Linhagem</h2><p>Load ID: <code>{esc(summary['load_id'])}</code></p><p>Workflow: <code>{esc(summary['workflow_run_id'])}</code></p><p>Arquivo: <code>{esc(summary['source_key'])}</code></p><p>SHA-256: <code>{esc(summary['source_sha256'])}</code></p><p>Publicado em UTC: {esc(summary['published_at'])}</p></section><section><h2>Armazenamento</h2><p>Parquet privado no bucket <code>{esc(summary['bucket'])}</code>, com partições registradas no Glue Data Catalog. As views só incluem cargas publicadas integralmente. O relatório apresenta agregados, sem documentos ou contatos.</p></section></main></html>'''
