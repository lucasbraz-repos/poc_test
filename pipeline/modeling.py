"""Publicação transacional em SQLite e modelo dimensional da carteira."""
from __future__ import annotations

import json
import sqlite3
from datetime import date

from .cleaning import fingerprint, stable_json
from .schema import FACT_FIELDS, INTEGER_FIELDS


def column_type(name):
    return "INTEGER" if name.endswith("_centavos") or name in INTEGER_FIELDS else "TEXT"


def dict_rows(connection, query, params=()):
    cursor = connection.execute(query, params)
    columns = [c[0] for c in cursor.description]
    return [dict(zip(columns, row)) for row in cursor]


def build_database(path, records, lineage, profile, metadata):
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    fields = list(records[0]["data"]) if records else []
    if not fields:
        raise ValueError("Nenhum registro de dados encontrado.")
    columns_sql = ",".join(f'"{field}" {column_type(field)}' for field in fields)
    with connection:
        connection.execute(f"""CREATE TABLE registros_tratados (
            registro_id TEXT PRIMARY KEY,{columns_sql},
            status_qualidade TEXT NOT NULL CHECK(status_qualidade IN ('APTO','REVISAO','QUARENTENA')),
            linhas_origem INTEGER NOT NULL,quantidade_alertas INTEGER NOT NULL)""")
        connection.executescript("""
            CREATE TABLE carga (run_id TEXT PRIMARY KEY, metadados_json TEXT NOT NULL);
            CREATE TABLE linhagem_origem (
                arquivo_sha256 TEXT NOT NULL, aba TEXT NOT NULL, linha_excel INTEGER NOT NULL,
                registro_id TEXT NOT NULL REFERENCES registros_tratados(registro_id), hash_linha_original TEXT NOT NULL,
                PRIMARY KEY(arquivo_sha256,aba,linha_excel));
            CREATE TABLE ocorrencias_qualidade (
                ocorrencia_id INTEGER PRIMARY KEY, registro_id TEXT NOT NULL REFERENCES registros_tratados(registro_id),
                codigo TEXT NOT NULL, campo TEXT NOT NULL,severidade TEXT NOT NULL CHECK(severidade IN ('INFO','WARNING','ERROR')),
                detalhe_json TEXT);
            CREATE INDEX ix_ocorrencia_registro ON ocorrencias_qualidade(registro_id);
            CREATE INDEX ix_ocorrencia_codigo ON ocorrencias_qualidade(codigo);
            CREATE TABLE perfil_colunas (coluna TEXT PRIMARY KEY, nome TEXT NOT NULL,total INTEGER NOT NULL,
                ausentes INTEGER NOT NULL,distintos INTEGER NOT NULL);
        """)
        connection.execute("INSERT INTO carga VALUES (?,?)", (metadata["run_id"], stable_json(metadata)))
        placeholders = ",".join("?" for _ in range(len(fields) + 4))
        connection.executemany(f"INSERT INTO registros_tratados VALUES ({placeholders})", [
            [r["id"], *[r["data"][f] for f in fields], r["status"], len(r["rows"]),
             sum(i["severidade"] != "INFO" for i in r["issues"])] for r in records])
        connection.executemany("INSERT INTO linhagem_origem VALUES (?,?,?,?,?)", lineage)
        connection.executemany("INSERT INTO ocorrencias_qualidade(registro_id,codigo,campo,severidade,detalhe_json) VALUES (?,?,?,?,?)",
                               [(r["id"], i["codigo"], i["campo"], i["severidade"], stable_json(i["detalhe"]))
                                for r in records for i in r["issues"]])
        connection.executemany("INSERT INTO perfil_colunas VALUES (?,?,?,?,?)", [
            [col, item["nome"], item["total"], item["ausentes"], item["distintos"]]
            for col, item in profile["columns"].items()])
        create_dimensions(connection, records)
        create_facts(connection, records)
    return connection


def create_dimensions(db, records):
    db.executescript("""
        CREATE TABLE dim_cedente (cedente_id TEXT PRIMARY KEY, cnpj TEXT NOT NULL UNIQUE,documento_valido INTEGER NOT NULL);
        CREATE TABLE dim_devedor (devedor_id TEXT PRIMARY KEY,documento TEXT NOT NULL,tipo_pessoa TEXT NOT NULL,
            nome TEXT,documento_valido INTEGER NOT NULL,UNIQUE(documento,tipo_pessoa));
        CREATE TABLE dim_produto (produto_id TEXT PRIMARY KEY,produto TEXT,subproduto TEXT);
        CREATE TABLE dim_data (data TEXT PRIMARY KEY,ano INTEGER NOT NULL,mes INTEGER NOT NULL,dia INTEGER NOT NULL,trimestre INTEGER NOT NULL);
    """)
    cedentes, debtors, products, dates = {}, {}, {}, set()
    debtor_names = {}
    for record in records:
        if record["status"] == "QUARENTENA":
            continue
        d = record["data"]
        cedente_id = fingerprint(["cedente", d["cnpj_cedente"]])
        debtor_id = fingerprint(["devedor", d["tipo_pessoa"], d["documento_devedor"]])
        product_id = fingerprint(["produto", d["produto"], d["subproduto"]])
        record["dimension_ids"] = [cedente_id, debtor_id, product_id]
        cedentes[cedente_id] = (cedente_id, d["cnpj_cedente"], d["cnpj_cedente_valido"] or 0)
        # Não escolher arbitrariamente um nome divergente para a mesma identidade.
        names = debtor_names.setdefault(debtor_id, set())
        if d["nome_devedor"]:
            names.add(d["nome_devedor"])
        debtors[debtor_id] = (debtor_id, d["documento_devedor"], d["tipo_pessoa"],
                              next(iter(names)) if len(names) == 1 else None, d["documento_devedor_valido"] or 0)
        products[product_id] = (product_id, d["produto"], d["subproduto"])
        dates.update(d[f] for f in d if f.startswith("data_") and d[f])
    db.executemany("INSERT INTO dim_cedente VALUES (?,?,?)", sorted(cedentes.values()))
    db.executemany("INSERT INTO dim_devedor VALUES (?,?,?,?,?)", sorted(debtors.values()))
    db.executemany("INSERT INTO dim_produto VALUES (?,?,?)", sorted(products.values()))
    db.executemany("INSERT INTO dim_data VALUES (?,?,?,?,?)", [(v, (dt := date.fromisoformat(v)).year, dt.month, dt.day, (dt.month - 1) // 3 + 1) for v in sorted(dates)])


def create_facts(db, records):
    fields_sql = ",".join(f'"{field}" {column_type(field)}' for field in FACT_FIELDS)
    db.execute(f"""CREATE TABLE fato_carteira (
        registro_id TEXT PRIMARY KEY REFERENCES registros_tratados(registro_id),
        cedente_id TEXT NOT NULL REFERENCES dim_cedente(cedente_id),
        devedor_id TEXT NOT NULL REFERENCES dim_devedor(devedor_id),
        produto_id TEXT NOT NULL REFERENCES dim_produto(produto_id),{fields_sql},
        status_qualidade TEXT NOT NULL CHECK(status_qualidade IN ('APTO','REVISAO')),
        UNIQUE(cedente_id,numero_contrato,data_corte),
        FOREIGN KEY(data_corte) REFERENCES dim_data(data))""")
    db.executemany("INSERT INTO fato_carteira VALUES (" + ",".join("?" for _ in range(len(FACT_FIELDS) + 5)) + ")", [
        [r["id"], *r["dimension_ids"], *[r["data"][f] for f in FACT_FIELDS], r["status"]]
        for r in records if r["status"] != "QUARENTENA"])
    db.executescript("""
        CREATE INDEX ix_fato_corte ON fato_carteira(data_corte);
        CREATE INDEX ix_fato_status ON fato_carteira(status_qualidade);
        CREATE VIEW vw_carteira AS SELECT f.*,d.documento AS documento_devedor,d.nome AS nome_devedor,
            d.documento_valido,c.cnpj AS cnpj_cedente
            FROM fato_carteira f JOIN dim_devedor d USING(devedor_id) JOIN dim_cedente c USING(cedente_id);
        CREATE VIEW vw_precificacao AS SELECT * FROM vw_carteira WHERE status_qualidade='APTO';
        CREATE VIEW vw_carteira_mascarada AS SELECT registro_id,data_corte,tipo_pessoa,produto,subproduto,uf,
            faixa_atraso,dias_atraso_calculado,valor_original_centavos,valor_contabil_centavos,valor_face_centavos,
            status_qualidade FROM fato_carteira;
        CREATE VIEW mart_resumo_carteira AS SELECT data_corte,tipo_pessoa,produto,faixa_atraso,status_qualidade,
            COUNT(*) AS quantidade,SUM(valor_contabil_centavos) AS saldo_contabil_centavos,
            SUM(valor_contabil_centavos IS NULL) AS saldos_ausentes,
            SUM(valor_contabil_centavos<0) AS saldos_negativos
            FROM fato_carteira GROUP BY data_corte,tipo_pessoa,produto,faixa_atraso,status_qualidade;
        CREATE VIEW vw_quarentena AS SELECT * FROM registros_tratados WHERE status_qualidade='QUARENTENA';
    """)
