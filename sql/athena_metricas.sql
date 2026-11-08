-- Substitua __GOLD_DATABASE__ pelo database Gold da implantação.
-- Cada view atual seleciona uma única carga; escolha também corte e universo.
SELECT load_id,data_corte,quantidade,saldo_contabil_centavos,
       saldos_preenchidos,saldos_ausentes,saldos_negativos,
       CAST(saldo_contabil_centavos AS DECIMAL(38,2)) /
         NULLIF(saldos_preenchidos,0) / 100 AS ticket_contabil_reais,
       100.0*saldos_preenchidos/NULLIF(quantidade,0) AS completude_saldo_pct,
       100.0*atraso_acima_90/NULLIF(atraso_calculavel,0) AS atraso_acima90_pct
FROM __GOLD_DATABASE__.vw_indicadores_carteira_atual
WHERE universo='CARTEIRA_PUBLICADA' AND data_corte='2026-08-01';

SELECT load_id,data_corte,tipo_pessoa,produto,faixa_atraso,status_qualidade,
       quantidade,saldo_contabil_centavos,saldos_ausentes,saldos_negativos
FROM __GOLD_DATABASE__.vw_resumo_carteira_atual
WHERE data_corte='2026-08-01'
ORDER BY quantidade DESC;

-- O denominador é repetido: não somar total_registros nem registros_afetados.
SELECT codigo,campo,severidade,ocorrencias,registros_afetados,total_registros,
       100.0*registros_afetados/NULLIF(total_registros,0) AS afetados_pct
FROM __GOLD_DATABASE__.vw_qualidade_atual
ORDER BY registros_afetados DESC;
