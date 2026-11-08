exec(open('analysis/profile_investigation.py').read().split("out={'rows'")[0])
from collections import Counter,defaultdict
month_pt={'janeiro':1,'fevereiro':2,'marco':3,'abril':4,'maio':5,'junho':6,'julho':7,'agosto':8,'setembro':9,'outubro':10,'novembro':11,'dezembro':12}
month_en={s:i+1 for i,s in enumerate(['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec'])}
def date(v,col):
 if not v:return None
 t=fold(v)
 try:
  if re.fullmatch(r'\d{4,5}(\.\d+)?',t):return datetime.date(1899,12,30)+datetime.timedelta(days=int(float(t)))
  if re.fullmatch(r'\d{4}-\d{2}-\d{2}',t):return datetime.date.fromisoformat(t)
  if re.search(r' de ',t):
   d,m,y=t.split(' de ');return datetime.date(int(y),month_pt[m],int(d))
  if re.fullmatch(r'\d{1,2}-[a-z]{3}-\d{4}',t):
   d,m,y=t.split('-');return datetime.date(int(y),month_en[m],int(d))
  if re.fullmatch(r'\d{1,2}-\d{1,2}-\d{4}',t):
   m,d,y=map(int,t.split('-'));return datetime.date(y,m,d)
  if re.fullmatch(r'\d{1,2}[/.]\d{1,2}[/.]\d{2,4}',t):
   d,m,y=map(int,re.split(r'[/.]',t));y=y+1900 if col=='F' and y>=30 and y<100 else y+2000 if y<100 else y;return datetime.date(y,m,d)
 except Exception as e:print('FAIL DATE',v,col,e)
 return None
def integer(v):
 if not v:return None
 return int(decimal.Decimal(v.strip().replace('.','').replace(',','.')))
one={r['A']:r for r in clean}.values()
checks=Counter();examples=defaultdict(list)
for r in one:
 d={c:date(r[c],c) for c in ['F','R','AU','AV','BY','CA']}; n={c:integer(r[c]) for c in ['BF','BG','BI','BK','BL']}
 def flag(k):
  checks[k]+=1
  if len(examples[k])<5:examples[k].append(r['A'])
 if d['AV'] and d['CA']:
  if n['BF'] is not None:
   checks['atraso_comparavel']+=1
   if n['BF']!=(d['CA']-d['AV']).days:flag('atraso_divergente')
  if d['AV']>d['CA']:flag('vencimento_apos_corte')
 if d['AU'] and d['AV'] and d['AU']>d['AV']:flag('contratacao_apos_vencimento')
 if d['AU'] and d['CA'] and d['AU']>d['CA']:flag('contratacao_apos_corte')
 if d['BY'] and d['CA'] and d['BY']>d['CA']:flag('ajuizamento_apos_corte')
 if d['BY'] and d['AU'] and d['BY']<d['AU']:flag('ajuizamento_antes_contratacao')
 if all(n[c] is not None for c in ['BG','BI','BK']):
  checks['parcelas_comparavel']+=1
  if n['BG']!=n['BI']+n['BK']:flag('parcelas_divergente')
 if n['BG'] is not None and n['BL'] is not None:
  checks['total_parcelas_comparavel']+=1
  if n['BG']!=n['BL']:flag('total_parcelas_divergente')
 if n['BI'] is not None and n['BG'] is not None and n['BI']>n['BG']:flag('pagas_maior_plano')
 if d['F'] and d['CA']:
  age=(d['CA']-d['F']).days/365.2425
  if age<18:flag('idade_menor_18')
  if age>100:flag('idade_maior_100')
 if d['F'] and d['AU'] and d['F']>d['AU']:flag('nascimento_apos_contratacao')
 if d['R'] and d['AU'] and d['R']>d['AU']:flag('abertura_apos_contratacao')
 for c in ['M','AW','AX','AY','BH','BJ','BX']:
  v=money(r[c])
  if v is not None and v<0:flag('negativo_'+c)
 s=fold(r['BM']);has=r['BN'] is not None
 if s in {'n','nao','no','false','falso','0'} and has:flag('avalista_negado_documento_presente')
 if s in {'s','sim','yes','true','verdadeiro','1'} and not has:flag('avalista_confirmado_documento_ausente')
 s=fold(r['BO']);has=bool(r['BP'] or r['BQ'])
 if s in {'n','nao','no','false','falso','0'} and has:flag('garantia_negada_dados_presentes')
 if s in {'s','sim','yes','true','verdadeiro','1'} and not has:flag('garantia_confirmada_dados_ausentes')
 s=fold(r['BA']);has=bool(r['BU'])
 if s in {'n','nao','no','false','falso','0'} and has:flag('ajuizado_negado_processo_presente')
 if s in {'s','sim','yes','true','verdadeiro','1'} and not has:flag('ajuizado_confirmado_processo_ausente')
for c in ['F','R','AU','AV','BY','CA']:
 vals=[r[c] for r in one if r[c] and re.fullmatch(r'\d{1,2}/\d{1,2}/\d{2}',r[c])];years=Counter(int(v.split('/')[-1]) for v in vals)
 print('TWO_DIGIT_YEARS',c,dict(sorted(years.items())))
print('COUNTS',json.dumps(checks,ensure_ascii=False,indent=2))
print('EXAMPLES',json.dumps(examples,ensure_ascii=False,indent=2))
Path('analysis/relationships_source.json').write_text(json.dumps({'counts':checks,'examples':examples},ensure_ascii=False,indent=2))
