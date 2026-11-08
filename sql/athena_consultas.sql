-- Selecione o database Gold criado pelo Terraform antes de executar.
-- A visão atual evita somar versões diferentes da mesma carteira.
SELECT status_qualidade, COUNT(*) AS registros,
       SUM(valor_contabil_centavos) AS saldo_contabil_centavos
FROM vw_carteira_atual
GROUP BY status_qualidade;

SELECT data_corte, produto, faixa_atraso, COUNT(*) AS contratos,
       SUM(valor_face_centavos) AS valor_face_centavos
FROM vw_carteira_atual
GROUP BY data_corte, produto, faixa_atraso;

SELECT produto, COUNT(*) AS contratos,
       SUM(valor_contabil_centavos) AS saldo_centavos
FROM vw_precificacao_atual
GROUP BY produto;
