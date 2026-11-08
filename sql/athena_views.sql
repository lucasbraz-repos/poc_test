CREATE OR REPLACE VIEW __GOLD_DATABASE__.vw_carteira AS
SELECT f.*, d.documento AS documento_devedor, d.nome AS nome_devedor,
       d.documento_valido, c.cnpj AS cnpj_cedente
FROM __GOLD_DATABASE__.fato_carteira f
JOIN __GOLD_DATABASE__.dim_devedor d ON f.load_id=d.load_id AND f.devedor_id=d.devedor_id
JOIN __GOLD_DATABASE__.dim_cedente c ON f.load_id=c.load_id AND f.cedente_id=c.cedente_id
JOIN __CONTROL_DATABASE__.cargas_publicadas p ON f.load_id=p.load_id;

CREATE OR REPLACE VIEW __GOLD_DATABASE__.vw_precificacao AS
SELECT * FROM __GOLD_DATABASE__.vw_carteira WHERE status_qualidade='APTO';

CREATE OR REPLACE VIEW __GOLD_DATABASE__.vw_carteira_atual AS
SELECT f.* FROM __GOLD_DATABASE__.vw_carteira f
WHERE f.load_id=(SELECT load_id FROM __CONTROL_DATABASE__.cargas_publicadas ORDER BY published_at DESC,load_id DESC LIMIT 1);

CREATE OR REPLACE VIEW __GOLD_DATABASE__.vw_precificacao_atual AS
SELECT * FROM __GOLD_DATABASE__.vw_carteira_atual WHERE status_qualidade='APTO';

CREATE OR REPLACE VIEW __GOLD_DATABASE__.vw_carteira_mascarada AS
SELECT registro_id,load_id,data_corte,tipo_pessoa,produto,subproduto,uf,faixa_atraso,
       dias_atraso_calculado,valor_original_centavos,valor_contabil_centavos,valor_face_centavos,status_qualidade
FROM __GOLD_DATABASE__.vw_carteira;

CREATE OR REPLACE VIEW __GOLD_DATABASE__.vw_carteira_mascarada_atual AS
SELECT registro_id,load_id,data_corte,tipo_pessoa,produto,subproduto,uf,faixa_atraso,
       dias_atraso_calculado,valor_original_centavos,valor_contabil_centavos,valor_face_centavos,status_qualidade
FROM __GOLD_DATABASE__.vw_carteira_atual;

CREATE OR REPLACE VIEW __GOLD_DATABASE__.vw_resumo_carteira_atual AS
SELECT m.* FROM __GOLD_DATABASE__.mart_resumo_carteira m
WHERE m.load_id=(SELECT load_id FROM __CONTROL_DATABASE__.cargas_publicadas ORDER BY published_at DESC,load_id DESC LIMIT 1);

CREATE OR REPLACE VIEW __GOLD_DATABASE__.vw_indicadores_carteira_atual AS
SELECT m.* FROM __GOLD_DATABASE__.mart_indicadores_carteira m
WHERE m.load_id=(SELECT load_id FROM __CONTROL_DATABASE__.cargas_publicadas ORDER BY published_at DESC,load_id DESC LIMIT 1);

CREATE OR REPLACE VIEW __GOLD_DATABASE__.vw_qualidade_atual AS
SELECT m.* FROM __GOLD_DATABASE__.mart_qualidade m
WHERE m.load_id=(SELECT load_id FROM __CONTROL_DATABASE__.cargas_publicadas ORDER BY published_at DESC,load_id DESC LIMIT 1);

CREATE OR REPLACE VIEW __SILVER_DATABASE__.vw_quarentena AS
SELECT q.* FROM __SILVER_DATABASE__.quarentena q
JOIN __CONTROL_DATABASE__.cargas_publicadas p ON q.load_id=p.load_id;
