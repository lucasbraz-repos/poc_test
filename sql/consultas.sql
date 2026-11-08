-- Consultas de consumo para o banco SQLite produzido pela pipeline.
-- Valores monetários terminados em _centavos são INTEGER.
-- :data_corte é um parâmetro ISO, por exemplo '2026-08-01'.
-- Sempre escolha o corte: uma fato snapshot não é aditiva no tempo.
-- Cada diretório de carga contém um snapshot SQLite independente.
-- Ao comparar cargas, escolha a versão: cargas repetidas não são nova exposição.

-- 1. Cortes e estados disponíveis.
SELECT data_corte,
       status_qualidade,
       COUNT(*) AS contratos
FROM fato_carteira
GROUP BY data_corte, status_qualidade
ORDER BY data_corte, status_qualidade;

-- 2. Exposição por produto, tipo e qualidade em um corte.
-- COUNT(valor_...) distingue saldo ausente de saldo igual a zero.
-- A coluna em reais é só apresentação; a soma canônica permanece em centavos.
SELECT tipo_pessoa,
       produto,
       status_qualidade,
       COUNT(*) AS contratos,
       COUNT(valor_contabil_centavos) AS contratos_com_saldo,
       SUM(valor_contabil_centavos) AS saldo_contabil_centavos,
       ROUND(SUM(valor_contabil_centavos) / 100.0, 2) AS saldo_contabil_reais
FROM fato_carteira
WHERE data_corte = :data_corte
GROUP BY tipo_pessoa, produto, status_qualidade
ORDER BY tipo_pessoa, produto, status_qualidade;

-- 3. Distribuição por faixa de atraso calculada.
SELECT faixa_atraso,
       status_qualidade,
       COUNT(*) AS contratos,
       SUM(valor_face_centavos) AS valor_face_centavos
FROM fato_carteira
WHERE data_corte = :data_corte
GROUP BY faixa_atraso, status_qualidade
ORDER BY faixa_atraso, status_qualidade;

-- 4. Elegíveis às regras locais: não representa preço ou aprovação financeira.
SELECT data_corte,
       tipo_pessoa,
       produto,
       COUNT(*) AS contratos,
       SUM(valor_contabil_centavos) AS saldo_contabil_centavos
FROM vw_precificacao
WHERE data_corte = :data_corte
GROUP BY data_corte, tipo_pessoa, produto
ORDER BY tipo_pessoa, produto;

-- 5. Completude e sinais negativos; não converter ausências em zero.
SELECT COUNT(*) AS contratos,
       SUM(CASE WHEN valor_original_centavos IS NULL THEN 1 ELSE 0 END) AS sem_valor_original,
       SUM(CASE WHEN valor_contabil_centavos IS NULL THEN 1 ELSE 0 END) AS sem_valor_contabil,
       SUM(CASE WHEN valor_face_centavos IS NULL THEN 1 ELSE 0 END) AS sem_valor_face,
       SUM(CASE WHEN valor_contabil_centavos < 0 THEN 1 ELSE 0 END) AS saldo_contabil_negativo,
       SUM(CASE WHEN dias_atraso_calculado IS NULL THEN 1 ELSE 0 END) AS sem_atraso_calculado
FROM fato_carteira
WHERE data_corte = :data_corte;

-- 6. Exposição por cedente sem exibir o documento de cada devedor.
SELECT cedente_id,
       status_qualidade,
       COUNT(*) AS contratos,
       COUNT(DISTINCT devedor_id) AS identidades_distintas,
       SUM(valor_contabil_centavos) AS saldo_contabil_centavos
FROM fato_carteira
WHERE data_corte = :data_corte
GROUP BY cedente_id, status_qualidade
ORDER BY saldo_contabil_centavos DESC;

-- 7. Auditoria da unicidade do grão publicado: o resultado esperado é vazio.
SELECT cedente_id,
       numero_contrato,
       data_corte,
       COUNT(*) AS quantidade
FROM fato_carteira
GROUP BY cedente_id, numero_contrato, data_corte
HAVING COUNT(*) > 1;

-- 8. Identidades com validação documental local reprovada no corte.
-- Não expõe os documentos; esta validação não consulta cadastro oficial.
SELECT f.tipo_pessoa,
       f.status_qualidade,
       COUNT(*) AS contratos,
       COUNT(DISTINCT f.devedor_id) AS identidades
FROM fato_carteira AS f
JOIN dim_devedor AS d ON d.devedor_id = f.devedor_id
WHERE f.data_corte = :data_corte
  AND d.documento_valido = 0
GROUP BY f.tipo_pessoa, f.status_qualidade
ORDER BY f.tipo_pessoa, f.status_qualidade;
