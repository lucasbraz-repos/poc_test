"""Leitor streaming de XLSX: preserva valores e posições, sem dependências externas."""
from __future__ import annotations

import posixpath
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


class Workbook:
    def __init__(self, path: Path, sheet: str | None = None):
        self.path = Path(path)
        with ZipFile(self.path) as archive:
            root = ET.fromstring(archive.read("xl/workbook.xml"))
            props = root.find("m:workbookPr", NS)
            self.date_1904 = props is not None and props.get("date1904") in {"1", "true"}
            sheets = root.findall("m:sheets/m:sheet", NS)
            if sheet is None and len(sheets) != 1:
                raise ValueError("Informe --sheet quando o arquivo tiver mais de uma aba.")
            selected = next((s for s in sheets if sheet is None or s.get("name") == sheet), None)
            if selected is None:
                raise ValueError(f"Aba não encontrada: {sheet}")
            self.sheet = selected.get("name")
            relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            rel = next(r for r in relationships if r.get("Id") == selected.get(f"{{{REL}}}id"))
            target = rel.get("Target", "")
            self.sheet_path = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
            self.shared = []
            if "xl/sharedStrings.xml" in archive.namelist():
                with archive.open("xl/sharedStrings.xml") as stream:
                    for _, element in ET.iterparse(stream, events=("end",)):
                        if element.tag == f"{{{NS['m']}}}si":
                            self.shared.append("".join(t.text or "" for t in element.findall(".//m:t", NS)))
                            element.clear()
        first = next(self.rows(), None)
        if first is None or first[0] != 1:
            raise ValueError("O contrato da fonte exige cabeçalhos na linha 1.")
        self.headers = first[1]

    def rows(self):
        """Yield (linha_excel, {letra_coluna: valor_original}, colunas_com_formula)."""
        with ZipFile(self.path) as archive, archive.open(self.sheet_path) as stream:
            for _, element in ET.iterparse(stream, events=("end",)):
                if element.tag != f"{{{NS['m']}}}row":
                    continue
                values, formulas = {}, []
                for cell in element.findall("m:c", NS):
                    reference = cell.get("r", "")
                    match = re.match(r"[A-Z]+", reference)
                    if not match:
                        raise ValueError(f"Célula sem referência de coluna: {reference!r}")
                    column = match.group()
                    node = cell.find("m:v", NS)
                    value = node.text if node is not None else None
                    kind = cell.get("t")
                    if kind == "s" and value is not None:
                        value = self.shared[int(value)]
                    elif kind == "inlineStr":
                        value = "".join(t.text or "" for t in cell.findall(".//m:t", NS))
                    elif kind == "e":
                        value = f"#EXCEL_ERROR:{value}"
                    if cell.find("m:f", NS) is not None:
                        formulas.append(column)
                    values[column] = value
                if any(v is not None and str(v).strip() for v in values.values()):
                    yield int(element.get("r", "0")), values, formulas
                element.clear()
