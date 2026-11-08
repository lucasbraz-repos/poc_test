"""Conversões explícitas: nenhum valor inválido é silenciosamente imputado.

Valores monetários saem em centavos inteiros. Datas com ano de dois dígitos usam
uma janela fixa (00–29: 2000–2029; 30–99: 1930–1999). O dia 60 do calendário
Excel de 1900 é rejeitado: 29/02/1900 nunca existiu.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

NULL_TOKENS = frozenset({"", "N/A", "NA", "NULL", "NONE", "NAN", "S/D", "SEM DADO", "-"})
VALID_DDDS = frozenset(
    "11 12 13 14 15 16 17 18 19 21 22 24 27 28 31 32 33 34 35 37 38 "
    "41 42 43 44 45 46 47 48 49 51 53 54 55 61 62 63 64 65 66 67 68 69 "
    "71 73 74 75 77 79 81 82 83 84 85 86 87 88 89 91 92 93 94 95 96 97 98 99".split()
)


def clean_text(value: object) -> str | None:
    if value is None:
        return None
    text = " ".join(unicodedata.normalize("NFC", str(value)).split())
    return None if text.upper() in NULL_TOKENS else text


def token(value: object) -> str | None:
    text = clean_text(value)
    if text is None:
        return None
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    ).upper()


def normalize_text(value: object) -> str | None:
    text = clean_text(value)
    return text.upper() if text is not None else None


def digits(value: object) -> str | None:
    if isinstance(value, bool):
        raise ValueError("booleano não é identificador")
    if isinstance(value, (float, Decimal)):
        number = Decimal(str(value))
        if not number.is_finite() or number != number.to_integral_value():
            raise ValueError("identificador numérico não inteiro")
        value = str(int(number))
    text = clean_text(value)
    if text is None:
        return None
    if not re.fullmatch(r"[0-9\s.\-/()+]+", text):
        raise ValueError("identificador contém caracteres não permitidos")
    result = re.sub(r"[^0-9]", "", text)
    if not result:
        raise ValueError("identificador sem dígitos")
    return result


def parse_money(value: object) -> int | None:
    if isinstance(value, bool):
        raise ValueError("booleano não é valor monetário")
    text = clean_text(value)
    if text is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        number_text = str(value)
    else:
        text = re.sub(r"\s", "", text)
        # A moeda pode preceder ou suceder o sinal, mas aparece somente uma vez.
        if text.upper().count("R$") > 1:
            raise ValueError("símbolo monetário repetido")
        text = re.sub(r"(?i)R\$", "", text)
        negative = False
        if text.startswith("(") and text.endswith(")"):
            negative = True
            text = text[1:-1]
            if text.startswith(("-", "+")):
                raise ValueError("sinal monetário duplicado")
        elif "(" in text or ")" in text:
            raise ValueError("parênteses monetários inválidos")
        if text.startswith(("-", "+")):
            negative = text[0] == "-"
            text = text[1:]
        if not re.fullmatch(r"[0-9.,]+", text):
            raise ValueError("valor monetário inválido")
        if "." in text and "," in text:
            decimal_separator = "." if text.rfind(".") > text.rfind(",") else ","
            thousands_separator = "," if decimal_separator == "." else "."
            if text.count(decimal_separator) != 1:
                raise ValueError("separadores monetários inconsistentes")
            integer_part, fractional_part = text.rsplit(decimal_separator, 1)
            if not re.fullmatch(
                r"[0-9]{1,3}(?:" + re.escape(thousands_separator) + r"[0-9]{3})+", integer_part
            ):
                raise ValueError("agrupamento de milhares inválido")
            if not re.fullmatch(r"[0-9]{1,2}", fractional_part):
                raise ValueError("valor monetário exige no máximo dois decimais")
            number_text = integer_part.replace(thousands_separator, "") + "." + fractional_part
        elif "." in text or "," in text:
            separator = "." if "." in text else ","
            if text.count(separator) > 1:
                if not re.fullmatch(
                    r"[0-9]{1,3}(?:" + re.escape(separator) + r"[0-9]{3}){2,}", text
                ):
                    raise ValueError("agrupamento monetário inválido")
                number_text = text.replace(separator, "")
            else:
                integer_part, fractional_part = text.split(separator)
                if not re.fullmatch(r"[0-9]*", integer_part) or not re.fullmatch(
                    r"[0-9]{1,2}", fractional_part
                ):
                    raise ValueError("separador monetário ambíguo ou decimais inválidos")
                number_text = (integer_part or "0") + "." + fractional_part
        else:
            number_text = text
        if negative:
            number_text = "-" + number_text
    try:
        number = Decimal(number_text)
        cents = number * 100
        if not number.is_finite() or cents != cents.to_integral_value():
            raise ValueError("valor monetário não representa centavos exatos")
        return int(cents)
    except (InvalidOperation, OverflowError) as exc:
        raise ValueError("valor monetário inválido") from exc


def parse_integer(value: object) -> int | None:
    if isinstance(value, bool):
        raise ValueError("booleano não é quantidade")
    text = clean_text(value)
    if text is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            number = Decimal(str(value))
            if not number.is_finite() or number != number.to_integral_value():
                raise ValueError("quantidade fracionária")
            return int(number)
        except InvalidOperation as exc:
            raise ValueError("quantidade inválida") from exc
    if re.fullmatch(r"[+-]?[0-9]+", text):
        return int(text)
    for separator in (".", ","):
        if re.fullmatch(r"[+-]?[0-9]{1,3}(?:" + re.escape(separator) + r"[0-9]{3})+", text):
            return int(text.replace(separator, ""))
    if re.fullmatch(r"[+-]?[0-9]+[.,]0{1,2}", text):
        return int(Decimal(text.replace(",", ".")))
    raise ValueError("quantidade inválida ou fracionária")


def parse_excel_money(value: object) -> tuple[int | None, bool]:
    """Recupera somente resíduos muito pequenos da representação XML do Excel.

    Primeiro aplica o parser estrito. O fallback exige decimal com ponto e ao
    menos seis casas, com distância ao centavo de no máximo 1E-8 real. Retorna
    a flag para o chamador registrar a correção no histórico de qualidade.
    Casas decimais reais, como ``1.234`` ou ``1.234567``, continuam inválidas.
    """
    try:
        return parse_money(value), False
    except ValueError as strict_error:
        text = clean_text(value)
        if text is None or not re.fullmatch(r"[+-]?[0-9]+\.[0-9]{6,}", text):
            raise
        try:
            number = Decimal(text)
            rounded = number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if abs(number - rounded) > Decimal("1E-8"):
                raise strict_error
            return int(rounded * 100), True
        except InvalidOperation as exc:
            raise ValueError("resíduo monetário fora da precisão suportada") from exc


_MONTHS = {
    "JANEIRO": 1,
    "FEVEREIRO": 2,
    "MARCO": 3,
    "ABRIL": 4,
    "MAIO": 5,
    "JUNHO": 6,
    "JULHO": 7,
    "AGOSTO": 8,
    "SETEMBRO": 9,
    "OUTUBRO": 10,
    "NOVEMBRO": 11,
    "DEZEMBRO": 12,
    "JAN": 1,
    "FEB": 2,
    "FEV": 2,
    "MAR": 3,
    "APR": 4,
    "ABR": 4,
    "MAY": 5,
    "MAI": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "AGO": 8,
    "SEP": 9,
    "SET": 9,
    "OCT": 10,
    "OUT": 10,
    "NOV": 11,
    "DEC": 12,
    "DEZ": 12,
}


def _year(text: str) -> int:
    if len(text) not in (2, 4):
        raise ValueError("ano deve conter dois ou quatro dígitos")
    year = int(text)
    return (2000 + year if year <= 29 else 1900 + year) if len(text) == 2 else year


def parse_date(
    value: object,
    *,
    date_1904: bool = False,
    hyphen_order: str = "DMY",
    flag_ambiguous: bool = True,
) -> tuple[str | None, str | None]:
    """Lê a data; a configuração de layout precede qualquer inferência.

    ISO é sempre YMD. Hífens numéricos seguem ``hyphen_order``. Barras e
    pontos seguem DMY; um hífen com mês > 12 pode ser reconhecido como MDY
    inequívoco no modo DMY. ``flag_ambiguous=False`` registra que o chamador
    já definiu a convenção no contrato da fonte.
    """
    if hyphen_order not in {"DMY", "MDY"}:
        raise ValueError("hyphen_order deve ser DMY ou MDY")
    if isinstance(value, bool):
        raise ValueError("booleano não é data")
    if isinstance(value, datetime):
        return value.date().isoformat(), None
    if isinstance(value, date):
        return value.isoformat(), None
    text = clean_text(value)
    if text is None:
        return None, None
    if re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?", text):
        serial = Decimal(text)
        if serial != serial.to_integral_value() or serial < 0 or serial > 2958465:
            raise ValueError("serial Excel inválido ou contém horário")
        serial_int = int(serial)
        if date_1904:
            epoch = date(1904, 1, 1)
        else:
            if serial_int == 60:
                raise ValueError("29/02/1900 não existe (serial Excel 60)")
            epoch = date(1899, 12, 31)
            if serial_int > 60:
                serial_int -= 1
        try:
            return (epoch + timedelta(days=serial_int)).isoformat(), None
        except OverflowError as exc:
            raise ValueError("serial Excel fora do calendário") from exc
    iso = re.fullmatch(r"([0-9]{4})-([0-9]{1,2})-([0-9]{1,2})", text)
    if iso:
        return date(*map(int, iso.groups())).isoformat(), None
    expanded = re.fullmatch(r"([0-9]{1,2})\s+DE\s+([A-Z]+)\s+DE\s+([0-9]{2}|[0-9]{4})", token(text))
    named = expanded or re.fullmatch(
        r"([0-9]{1,2})[- /]([A-Z]+)[- /]([0-9]{2}|[0-9]{4})", token(text)
    )
    if named:
        day_text, month_text, year_text = named.groups()
        if month_text not in _MONTHS:
            raise ValueError("nome do mês desconhecido")
        return date(_year(year_text), _MONTHS[month_text], int(day_text)).isoformat(), None
    numeric = re.fullmatch(r"([0-9]{1,2})([/.-])([0-9]{1,2})\2([0-9]{2}|[0-9]{4})", text)
    if not numeric:
        raise ValueError("formato de data desconhecido")
    first_text, separator, second_text, year_text = numeric.groups()
    first, second = int(first_text), int(second_text)
    flag = None
    if separator == "-" and hyphen_order == "MDY":
        month, day = first, second
    else:
        day, month = first, second
        if separator == "-" and first <= 12 < second:
            month, day = first, second
        elif (
            separator in {"/", "-"}
            and 1 <= first <= 12
            and 1 <= second <= 12
            and first != second
            and flag_ambiguous
        ):
            flag = "DATA_AMBIGUA_DIA_PRIMEIRO"
    return date(_year(year_text), month, day).isoformat(), flag


def parse_bool(value: object) -> int | None:
    if isinstance(value, bool):
        return int(value)
    text = token(value)
    if text is None:
        return None
    if text in {"SIM", "S", "YES", "Y", "TRUE", "VERDADEIRO", "1"}:
        return 1
    if text in {"NAO", "N", "NO", "FALSE", "FALSO", "0"}:
        return 0
    raise ValueError("booleano desconhecido")


def parse_person_type(value: object) -> str | None:
    text = token(value)
    if text is None:
        return None
    if text in {"PF", "F", "PESSOA FISICA"}:
        return "PF"
    if text in {"PJ", "J", "PESSOA JURIDICA"}:
        return "PJ"
    raise ValueError("tipo de pessoa desconhecido")


def document_is_valid(value: object) -> bool:
    try:
        document = digits(value)
    except ValueError:
        return False
    if document is None or len(document) not in {11, 14} or len(set(document)) == 1:
        return False
    numbers = [int(c) for c in document]
    if len(document) == 11:
        for length in (9, 10):
            remainder = (
                sum(n * w for n, w in zip(numbers[:length], range(length + 1, 1, -1))) * 10 % 11
            )
            if numbers[length] != (0 if remainder == 10 else remainder):
                return False
    else:
        for length, weights in (
            (12, (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)),
            (13, (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)),
        ):
            remainder = sum(n * w for n, w in zip(numbers[:length], weights)) % 11
            if numbers[length] != (0 if remainder < 2 else 11 - remainder):
                return False
    return True


def normalize_email(value: object) -> str | None:
    """Valida sintaxe EAI e preserva acentos do local-part (SMTPUTF8).

    O lowercase do endereço completo é a convenção adotada nesta fonte;
    não se pretende estabelecer equivalência universal entre caixas postais.
    """
    text = clean_text(value)
    if text is None:
        return None
    if text.count("@") != 1:
        raise ValueError("e-mail inválido")
    local, domain = text.rsplit("@", 1)
    if (
        not local
        or len(local) > 64
        or local.startswith(".")
        or local.endswith(".")
        or ".." in local
    ):
        raise ValueError("parte local do e-mail inválida")
    ascii_symbols = frozenset(".!#$%&'*+/=?^_`{|}~-")
    if any(
        not (c in ascii_symbols or unicodedata.category(c)[0] in {"L", "M", "N"}) for c in local
    ):
        raise ValueError("parte local do e-mail inválida")
    try:
        domain = domain.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("domínio do e-mail inválido") from exc
    labels = domain.split(".")
    if len(labels) < 2 or len(domain) > 253 or len(labels[-1]) < 2 or labels[-1].isdigit():
        raise ValueError("domínio do e-mail inválido")
    if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
        raise ValueError("domínio do e-mail inválido")
    result = local.lower() + "@" + domain
    if len(result) > 254:
        raise ValueError("e-mail longo demais")
    return result


def normalize_ddd(value: object) -> str | None:
    result = digits(value)
    if result is None:
        return None
    if len(result) == 4 and result.startswith("55"):
        result = result[2:]
    if result not in VALID_DDDS:
        raise ValueError("DDD brasileiro inválido")
    return result


def normalize_phone(value: object, ddd: object = None) -> str | None:
    text = clean_text(value)
    if text is None:
        return None
    number = digits(value)
    given_ddd = normalize_ddd(ddd)
    if text.startswith("+") and not number.startswith("55"):
        raise ValueError("código de país incompatível com Brasil")
    if len(number) in {12, 13}:
        if not number.startswith("55"):
            raise ValueError("código de país incompatível com Brasil")
        number = number[2:]
    if len(number) in {10, 11}:
        actual_ddd, local = number[:2], number[2:]
        normalize_ddd(actual_ddd)
        if given_ddd is not None and actual_ddd != given_ddd:
            raise ValueError("DDD diverge do telefone completo")
    elif len(number) in {8, 9}:
        if given_ddd is None:
            raise ValueError("telefone local sem DDD")
        actual_ddd, local = given_ddd, number
    else:
        raise ValueError("comprimento de telefone inválido")
    if not (re.fullmatch(r"[2-9][0-9]{7}", local) or re.fullmatch(r"9[0-9]{8}", local)):
        raise ValueError("número local brasileiro inválido")
    return "+55" + actual_ddd + local
