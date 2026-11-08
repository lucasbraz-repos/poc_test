"""Regras explícitas e conservadoras de limpeza; nenhum dado financeiro é inventado."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date

from . import parsers as p

UFS = set("AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split())
UF_NAMES = dict(zip([
    "ACRE", "ALAGOAS", "AMAPA", "AMAZONAS", "BAHIA", "CEARA", "DISTRITO FEDERAL", "ESPIRITO SANTO",
    "GOIAS", "MARANHAO", "MATO GROSSO", "MATO GROSSO DO SUL", "MINAS GERAIS", "PARA", "PARAIBA",
    "PARANA", "PERNAMBUCO", "PIAUI", "RIO DE JANEIRO", "RIO GRANDE DO NORTE", "RIO GRANDE DO SUL",
    "RONDONIA", "RORAIMA", "SANTA CATARINA", "SAO PAULO", "SERGIPE", "TOCANTINS",
], "AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split()))


def stable_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value):
    return hashlib.sha256(stable_json(value).encode()).hexdigest()


def aging(days):
    if days is None:
        return "SEM_VENCIMENTO"
    for limit, label in [(0, "A_VENCER_OU_NO_DIA"), (90, "001_090"), (180, "091_180"),
                         (360, "181_360"), (720, "361_720"), (1080, "721_1080")]:
        if days <= limit:
            return label
    return "ACIMA_1080"


class Cleaner:
    def __init__(self, raw, *, date_1904=False):
        self.raw = raw
        self.date_1904 = date_1904
        self.data = {}
        self.issues = []

    def issue(self, code, field, detail=None, severity="WARNING"):
        self.issues.append({"codigo": code, "campo": field, "severidade": severity, "detalhe": detail})

    def parse(self, field, column, function, *, required=False, severe=False):
        value = self.raw.get(column)
        try:
            result = function(value)
        except (ValueError, ArithmeticError, OverflowError) as error:
            self.issue("FORMATO_INVALIDO", field, {"coluna": column, "valor": value, "motivo": str(error)},
                       "ERROR" if severe else "WARNING")
            result = None
        self.data[field] = result
        if required and result is None:
            self.issue("CAMPO_OBRIGATORIO_AUSENTE", field, {"coluna": column}, "ERROR" if severe else "WARNING")
        return result

    def reconcile(self, field, columns, function, *, required=False, severe=False):
        candidates = []
        for col in columns:
            try:
                value = function(self.raw.get(col))
            except (ValueError, ArithmeticError) as error:
                self.issue("FORMATO_INVALIDO", field, {"coluna": col, "valor": self.raw.get(col), "motivo": str(error)})
                value = None
            if value is not None:
                candidates.append((col, value))
        distinct = list(dict.fromkeys(value for _, value in candidates))
        if len(distinct) > 1:
            self.issue("COLUNAS_REDUNDANTES_DIVERGENTES", field, {"candidatos": candidates},
                       "ERROR" if severe else "WARNING")
        value = distinct[0] if distinct else None
        if value is not None and p.clean_text(self.raw.get(columns[0])) is None:
            self.issue("RECUPERADO_DE_COLUNA_REDUNDANTE", field, {"coluna": candidates[0][0]}, "INFO")
        if required and value is None:
            self.issue("CAMPO_OBRIGATORIO_AUSENTE", field, {"colunas": columns}, "ERROR" if severe else "WARNING")
        self.data[field] = value
        return value

    def doc(self, value, length=None):
        value = p.digits(value)
        if value is not None and (len(value) not in (11, 14) or length and len(value) != length):
            raise ValueError("Documento com comprimento incompatível; não completar dígitos.")
        return value

    def date(self, field, column, *, required=False, severe=False):
        def parse(value):
            result, flag = p.parse_date(value, date_1904=self.date_1904, hyphen_order="MDY", flag_ambiguous=False)
            if flag:
                self.issue(flag, field, {"valor": value})
            return result
        return self.parse(field, column, parse, required=required, severe=severe)

    def money(self, field, column, *, required=False):
        def parse(value):
            cents, adjusted = p.parse_excel_money(value)
            if adjusted:
                self.issue("RESIDUO_NUMERICO_EXCEL_AJUSTADO", field,
                           {"original": value, "centavos": cents, "tolerancia_reais": "0.00000001"}, "INFO")
            return cents
        value = self.parse(field, column, parse, required=required)
        if value is not None and value < 0:
            self.issue("VALOR_MONETARIO_NEGATIVO", field, {"centavos_preservados": value})
        return value

    def integer(self, field, column):
        value = self.parse(field, column, p.parse_integer)
        if value is not None and value < 0:
            self.issue("CONTAGEM_NEGATIVA", field, {"valor": value})
        return value

    def run(self):
        d = self.data
        self.parse("codigo_identificador", "A", p.normalize_text, required=True, severe=True)
        self.reconcile("cnpj_cedente", ["B", "N"], lambda x: self.doc(x, 14), required=True, severe=True)
        contract = lambda v: re.sub(r"\s+", "", p.normalize_text(v)) if p.clean_text(v) is not None else None
        self.reconcile("numero_contrato", ["AR", "D", "P", "BS"], contract, required=True, severe=True)
        person = self.parse("tipo_pessoa", "Y", p.parse_person_type)
        if person is None:
            candidates = [self.raw.get(c) for c in ["X", "AQ", "BR", "C", "O"] if p.clean_text(self.raw.get(c))]
            lengths = set()
            for candidate in candidates:
                try:
                    lengths.add(len(self.doc(candidate)))
                except ValueError:
                    pass
            if len(lengths) == 1:
                person = "PF" if next(iter(lengths)) == 11 else "PJ"
                d["tipo_pessoa"] = person
                self.issue("TIPO_PESSOA_INFERIDO", "tipo_pessoa", {"criterio": "comprimento_documentos_concordantes"}, "INFO")
        if person is None:
            self.issue("TIPO_PESSOA_INDETERMINADO", "tipo_pessoa", severity="ERROR")
        active = "C" if person == "PF" else "O" if person == "PJ" else None
        self.reconcile("documento_devedor", ["X", "AQ", "BR"] + ([active] if active else []), self.doc,
                       required=True, severe=True)
        doc = d["documento_devedor"]
        if doc and person and len(doc) != (11 if person == "PF" else 14):
            self.issue("DOCUMENTO_INCOMPATIVEL_TIPO", "documento_devedor", severity="ERROR")
        for field in ["documento_devedor", "cnpj_cedente"]:
            d[field + "_valido"] = int(p.document_is_valid(d[field])) if d[field] else None
            if d[field] and not d[field + "_valido"]:
                self.issue("DIGITOS_VERIFICADORES_INVALIDOS", field)
        name_cols = ["E", "Z", "BT"] if person == "PF" else ["Q", "Z", "BT"]
        self.reconcile("nome_devedor", name_cols, p.normalize_text, required=True)
        self.date("data_corte", "CA", required=True, severe=True)
        self.date("data_nascimento", "F")
        self.date("data_abertura", "R")
        self.date("data_contratacao", "AU", required=True)
        self.date("data_vencimento", "AV", required=True)
        self.date("data_ajuizamento", "BY")

        for field, col in {"empresa_empregadora": "G", "sexo": "H", "estado_civil": "I", "profissao": "J",
                           "nome_mae": "K", "situacao_empresa": "S", "socio_1_nome": "U", "socio_2_nome": "W",
                           "rua": "AA", "numero_endereco": "AB", "complemento": "AC", "bairro": "AD",
                           "cidade": "AE", "uf": "AF", "pais": "AH", "telefone_1_tipo": "AI",
                           "telefone_2_tipo": "AL", "produto": "AS", "subproduto": "AT", "origem_credito": "AZ",
                           "tipo_garantia": "BP", "numero_processo": "BU", "comarca": "BV", "tipo_acao": "BW",
                           "ultimo_andamento": "BZ"}.items():
            self.parse(field, col, p.normalize_text)
        sex = p.token(d["sexo"])
        if sex:
            d["sexo"] = {"F": "F", "FEMININO": "F", "M": "M", "MASCULINO": "M"}.get(sex)
            if d["sexo"] is None:
                self.issue("CATEGORIA_INVALIDA", "sexo", {"valor": sex})
        d["uf"] = UF_NAMES.get(p.token(d["uf"]), d["uf"])
        if d["uf"] and d["uf"] not in UFS:
            self.issue("UF_INVALIDA", "uf", {"valor": d["uf"]})
            d["uf"] = None
        if not d["produto"]:
            self.issue("CAMPO_OBRIGATORIO_AUSENTE", "produto")
        self.parse("cep", "AG", p.digits)
        if d["cep"] and len(d["cep"]) != 8:
            self.issue("CEP_INVALIDO", "cep", {"valor": d["cep"]})
            d["cep"] = None
        for field, col in {"obito": "L", "ajuizado": "BA", "protesto": "BB", "negativado": "BC",
                           "serasa_limpa_nome": "BD", "desenrola": "BE", "possui_avalista": "BM",
                           "possui_garantia": "BO", "garantia_registrada": "BQ"}.items():
            self.parse(field, col, p.parse_bool)
        for field, col in {"renda_centavos": "M", "valor_original_centavos": "AW", "valor_contabil_centavos": "AX",
                           "valor_face_centavos": "AY", "valor_parcela_centavos": "BH", "valor_pago_centavos": "BJ",
                           "valor_acao_centavos": "BX"}.items():
            self.money(field, col, required=col in {"AW", "AX", "AY"})
        for field, col in {"dias_atraso_informado": "BF", "parcelas_plano": "BG", "parcelas_pagas": "BI",
                           "parcelas_restantes": "BK", "parcelas_total": "BL"}.items():
            self.integer(field, col)
        for field, col in {"socio_1_documento": "T", "socio_2_documento": "V", "documento_avalista": "BN"}.items():
            value = self.parse(field, col, lambda x: self.doc(x, 11) if col in {"T", "V"} else self.doc(x))
            if value and not p.document_is_valid(value):
                self.issue("DIGITOS_VERIFICADORES_INVALIDOS", field)
        for number, ddd_col, phone_col, email_col in [(1, "AJ", "AK", "AO"), (2, "AM", "AN", "AP")]:
            ddd = self.parse(f"telefone_{number}_ddd", ddd_col, p.normalize_ddd)
            self.parse(f"telefone_{number}", phone_col, lambda v: p.normalize_phone(v, ddd))
            email = self.parse(f"email_{number}", email_col, p.normalize_email)
            if email and not email.split("@", 1)[0].isascii():
                self.issue("EMAIL_REQUER_SMTPUTF8", f"email_{number}", severity="INFO")

        pf_fields = ["data_nascimento", "empresa_empregadora", "sexo", "estado_civil", "profissao", "nome_mae", "obito", "renda_centavos"]
        pj_fields = ["data_abertura", "situacao_empresa", "socio_1_documento", "socio_1_nome", "socio_2_documento", "socio_2_nome"]
        for field in pj_fields if person == "PF" else pf_fields if person == "PJ" else []:
            if d[field] is not None:
                self.issue("CAMPO_INAPLICAVEL_REMOVIDO", field, {"valor": d[field], "tipo": person})
                d[field] = None
        if d["data_corte"] and d["data_vencimento"]:
            delay = (date.fromisoformat(d["data_corte"]) - date.fromisoformat(d["data_vencimento"])).days
            d["dias_atraso_calculado"] = max(delay, 0)
            if delay < 0:
                self.issue("VENCIMENTO_APOS_CORTE", "data_vencimento")
            if d["dias_atraso_informado"] is not None and d["dias_atraso_informado"] != max(delay, 0):
                self.issue("ATRASO_RECALCULADO", "dias_atraso_calculado",
                           {"informado": d["dias_atraso_informado"], "calculado": max(delay, 0)}, "INFO")
        else:
            d["dias_atraso_calculado"] = None
        d["faixa_atraso"] = aging(d["dias_atraso_calculado"])
        if d["data_contratacao"]:
            for field in ["data_abertura", "data_nascimento"]:
                if d[field] and d[field] > d["data_contratacao"]:
                    self.issue("CADASTRO_POSTERIOR_CONTRATACAO", field)
            if d["data_ajuizamento"] and d["data_ajuizamento"] < d["data_contratacao"]:
                self.issue("AJUIZAMENTO_ANTES_CONTRATACAO", "data_ajuizamento")
            if d["data_corte"] and d["data_contratacao"] > d["data_corte"]:
                self.issue("CONTRATACAO_APOS_CORTE", "data_contratacao")
            if d["data_vencimento"] and d["data_vencimento"] < d["data_contratacao"]:
                self.issue("VENCIMENTO_ANTES_CONTRATACAO", "data_vencimento")
        if d["data_corte"]:
            for field in ["data_nascimento", "data_abertura", "data_ajuizamento"]:
                if d[field] and d[field] > d["data_corte"]:
                    self.issue("DATA_APOS_CORTE", field)
        total, plan, paid, remaining = (d[k] for k in ["parcelas_total", "parcelas_plano", "parcelas_pagas", "parcelas_restantes"])
        if total is None and plan is not None and plan >= 0:
            d["parcelas_total"] = total = plan
            self.issue("TOTAL_PARCELAS_RECUPERADO_PLANO", "parcelas_total", severity="INFO")
        if total is not None and plan is not None and total != plan:
            self.issue("TOTAL_PARCELAS_DIVERGENTE", "parcelas_total")
        if remaining is None and plan is not None and paid is not None and 0 <= paid <= plan:
            d["parcelas_restantes"] = remaining = plan - paid
            self.issue("PARCELAS_RESTANTES_DERIVADAS", "parcelas_restantes", severity="INFO")
        if plan is not None and paid is not None and remaining is not None and plan != paid + remaining:
            self.issue("PARCELAS_INCONSISTENTES", "parcelas_restantes")
        if total is not None and paid is not None and paid > total:
            self.issue("PAGAS_SUPERAM_TOTAL", "parcelas_pagas")
        if d["possui_avalista"] == 1 and not d["documento_avalista"]:
            self.issue("AVALISTA_SEM_DOCUMENTO", "documento_avalista")
        if d["possui_avalista"] == 0 and d["documento_avalista"]:
            self.issue("DOCUMENTO_AVALISTA_COM_FLAG_NAO", "documento_avalista")
        if d["possui_garantia"] == 1 and not d["tipo_garantia"]:
            self.issue("GARANTIA_SEM_TIPO", "tipo_garantia")
        if d["possui_garantia"] == 0 and (d["tipo_garantia"] or d["garantia_registrada"] == 1):
            self.issue("GARANTIA_CONTRADIZ_FLAG", "possui_garantia")
        if d["ajuizado"] == 0 and any(d[k] for k in ["numero_processo", "comarca", "tipo_acao", "data_ajuizamento", "valor_acao_centavos"]):
            self.issue("DADOS_JUDICIAIS_COM_AJUIZADO_NAO", "ajuizado")
        if d["ajuizado"] == 1 and not d["numero_processo"]:
            self.issue("AJUIZADO_SEM_PROCESSO", "numero_processo")
        for i in (1, 2):
            if bool(d[f"socio_{i}_documento"]) != bool(d[f"socio_{i}_nome"]):
                self.issue("SOCIO_INCOMPLETO", f"socio_{i}_documento")
        return d, self.issues


def quality_status(issues):
    if any(i["severidade"] == "ERROR" for i in issues):
        return "QUARENTENA"
    if any(i["severidade"] == "WARNING" for i in issues):
        return "REVISAO"
    return "APTO"
