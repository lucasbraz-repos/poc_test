from pathlib import Path
import argparse
import zipfile,xml.etree.ElementTree as E,re,collections,json,unicodedata,datetime,decimal
NS={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
def fold(s):return ''.join(c for c in unicodedata.normalize('NFKD',s or '') if not unicodedata.combining(c)).lower().strip()
def col(n):
 s=''
 while n:n,r=divmod(n-1,26);s=chr(65+r)+s
 return s
def norm(s):
 v=' '.join((s or '').split())
 return None if fold(v) in {'','na','n/a','null','none','nan','s/d','-','n.d.','sem informacao','não informado','nao informado'} else v
source_parser=argparse.ArgumentParser(description='Investigação exploratória da planilha, com saída local em analysis/.')
source_parser.add_argument('arquivo',nargs='?',type=Path,default=Path('BaseTeste.xlsx'))
p=source_parser.parse_args().arquivo
with zipfile.ZipFile(p) as z:
 shared=[''.join(t.text or '' for t in si.findall('.//m:t',NS)) for si in E.fromstring(z.read('xl/sharedStrings.xml'))]
 root=E.fromstring(z.read('xl/worksheets/sheet1.xml'))
 rows=[]
 for row in root.findall('.//m:row',NS):
  r={}
  for c in row:
   v=c.find('m:v',NS);s=v.text if v is not None else ''
   if c.attrib.get('t')=='s':s=shared[int(s)]
   if c.attrib.get('t')=='inlineStr':s=''.join(t.text or '' for t in c.findall('.//m:t',NS))
   r[re.sub(r'\d','',c.attrib['r'])]=s
  rows.append(r)
headers=rows.pop(0)
cols=[col(i) for i in range(1,80)]
rows=[{c:r.get(c,'') for c in cols} for r in rows]
clean=[{c:norm(v) for c,v in r.items()} for r in rows]
digits=lambda s:re.sub(r'\D','',s or '')
def cpf(s):
 if len(s)!=11 or len(set(s))==1:return False
 for k in (9,10):
  d=(sum(int(s[i])*(k+1-i) for i in range(k))*10)%11
  if d==10:d=0
  if d!=int(s[k]):return False
 return True
def cnpj(s):
 if len(s)!=14 or len(set(s))==1:return False
 for k,w in [(12,[5,4,3,2,9,8,7,6,5,4,3,2]),(13,[6,5,4,3,2,9,8,7,6,5,4,3,2])]:
  rem=sum(int(s[i])*w[i] for i in range(k))%11;d=0 if rem<2 else 11-rem
  if d!=int(s[k]):return False
 return True
def money(s):
 if not s:return None
 t=re.sub(r'[^0-9.,()\-]','',s)
 neg=t.startswith('(') and t.endswith(')');t=t.strip('()')
 if '.' in t and ',' in t:
  dec='.' if t.rfind('.')>t.rfind(',') else ',';oth=',' if dec=='.' else '.';t=t.replace(oth,'').replace(dec,'.')
 elif ',' in t:t=t.replace(',','.')
 try:return decimal.Decimal(t)*(-1 if neg else 1)
 except:return None
out={'rows':len(rows),'cols':headers,'columns':{},'checks':{}}
for c in cols:
 vals=[r[c] for r in clean];ct=collections.Counter(v for v in vals if v is not None)
 out['columns'][c]={'header':headers[c],'null':sum(v is None for v in vals),'distinct':len(ct),'top':ct.most_common(12)}
checks=out['checks']
checks['exact_duplicate_excess']=len(rows)-len({tuple(r.items()) for r in rows})
checks['normalized_whitespace_duplicate_excess']=len(clean)-len({tuple(r.items()) for r in clean})
for c in ['A','D','P','AR','BS']:
 ct=collections.Counter(fold(r[c]) for r in clean if r[c]);checks['key_'+c]={'distinct':len(ct),'dup_excess':sum(v-1 for v in ct.values()),'duplicate_groups':sum(v>1 for v in ct.values()),'max_multiplicity':max(ct.values()),'null':sum(r[c] is None for r in clean)}
types=collections.Counter(fold(r['Y']) for r in clean);checks['types']=types
checks['mixed_PF_PJ_blocks']={'PF_with_pj_doc_or_reason':sum(fold(r['Y']) in {'f','pf','pessoa fisica'} and (r['O'] or r['Q']) is not None for r in clean),'PJ_with_pf_doc_or_name':sum(fold(r['Y']) in {'j','pj','pessoa juridica'} and (r['C'] or r['E']) is not None for r in clean)}
for c in ['B','C','N','O','X','AQ','BR','T','V','BN']:
 vals=[digits(r[c]) for r in clean if r[c]];checks['document_'+c]={'lengths':collections.Counter(map(len,vals)),'valid_cpf':sum(cpf(v) for v in vals),'valid_cnpj':sum(cnpj(v) for v in vals)}
for group in [('B','N'),('D','P','AR','BS'),('X','AQ','BR')]:
 mismatches=[]
 for i,r in enumerate(clean,2):
  vals=[(digits(r[c]) if group[0] in {'B','X'} else fold(r[c])) for c in group if r[c]]
  if len(set(vals))>1:mismatches.append(i)
 checks['divergence_'+'_'.join(group)]={'count':len(mismatches),'sample_rows':mismatches[:20]}
for c in ['M','AW','AX','AY','BH','BJ','BX']:
 vals=[money(r[c]) for r in clean if r[c]];good=[v for v in vals if v is not None]
 checks['money_'+c]={'parse_failures':sum(v is None for v in vals),'negative':sum(v<0 for v in good),'zero':sum(v==0 for v in good),'min':str(min(good)),'max':str(max(good)),'sum':str(sum(good))}
for c in ['F','R','AU','AV','BY','CA']:
 vals=[r[c] for r in clean if r[c]];forms=collections.Counter()
 amb=0
 for v in vals:
  if re.fullmatch(r'\d{4,5}(\.\d+)?',v):forms['excel_serial']+=1
  elif re.search(r' de ',v):forms['pt_extenso']+=1
  elif re.fullmatch(r'\d{4}-\d{2}-\d{2}',v):forms['iso']+=1
  elif re.search(r'[A-Za-z]',v):forms['mes_textual']+=1
  elif re.fullmatch(r'\d{1,2}/\d{1,2}/\d{2}',v):forms['barra_2dig']+=1
  elif re.fullmatch(r'\d{1,2}/\d{1,2}/\d{4}',v):forms['barra_4dig']+=1
  elif re.fullmatch(r'\d{1,2}\.\d{1,2}\.\d{4}',v):forms['ponto_4dig']+=1
  elif re.fullmatch(r'\d{1,2}-\d{1,2}-\d{4}',v):
   a,b,y=map(int,v.split('-'));forms['hifen_4dig']+=1
   if a<=12 and b<=12 and a!=b:amb+=1
  else:forms['unknown:'+v]+=1
 checks['date_'+c]={'formats':forms,'ambiguous_hyphen':amb}
for c in ['L','BA','BB','BC','BD','BE','BM','BO','BQ']:
 checks['bool_'+c]=collections.Counter(fold(r[c]) for r in clean)
checks['PF_specific_nonnull_on_PJ']={c:sum(fold(r['Y']) in {'j','pj','pessoa juridica'} and r[c] is not None for r in clean) for c in ['C','E','F','G','H','I','J','K','L','M']}
checks['PJ_specific_nonnull_on_PF']={c:sum(fold(r['Y']) in {'f','pf','pessoa fisica'} and r[c] is not None for r in clean) for c in ['O','Q','R','S','T','U','V','W']}
checks['unknown_type']=sum(fold(r['Y']) not in {'j','pj','pessoa juridica','f','pf','pessoa fisica'} for r in clean)
Path('analysis/profile_source.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
print(json.dumps({'rows':out['rows'],'checks':checks},ensure_ascii=False,indent=2))
