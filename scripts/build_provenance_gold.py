"""scripts/build_provenance_gold.py — P 정답표(해설 인용 100건) 재생성 스크립트.
라벨은 작업보조 LLM가 문맥을 읽고 수작업으로 붙였다(L 사전). 표본 추출은 고정 seed로 재현된다. 사람 확인(reviewer) 칸은 비워 둔다."""
import json,re,random,collections,csv,sys
sys.path.insert(0,'.')
# (gold_law, article, paragraph, item, resolvability, note)
L = {
1:("EXT:공동관리규정(종전)","2","","3","INHERIT","종전 「국가연구개발사업의 관리 등에 관한 규정」"),
2:("013774","16","1","","QUOTE_SELF","혁신법 조문 인용 안의 내부 참조"),
3:("013774","3","","1","SECTION","적용범위 절 문맥 필요"),
4:("013774","2","","2","INHERIT","지침 안 '법'=혁신법 정의"),
5:("013774","3","","","EXPLICIT","전각 괄호 ｢혁신법｣"),
6:("013774","9","","","INHERIT","같은 문장 ｢혁신법｣ 이어받음"),
7:("75386","7","","1","INHERIT","'고시'=연구개발비 사용 기준"),
8:("013774","18","","","INHERIT","'혁신법 제9조에서'"),
9:("013933","65","2","","INHERIT","「…시행령」(이하 '영')"),
10:("013774","3","","","SECTION","짧은 답 — 절 문맥 필요"),
11:("013933","9","1","2","QUOTE_SELF","시행령 제12조 인용 안 내부 참조"),
12:("75386","40","4","","INHERIT","같은 문장 사용 기준"),
13:("013774","4","","","SECTION","다음 Q의 '제4조 관련' 머리글 혼입"),
14:("75386","25의2","","","INHERIT","같은 문장 사용 기준"),
15:("013774","15","1","","QUOTE_SELF","'이 법' 포함 혁신법 조문 인용"),
16:("FORM:013980-서식2","6","2","","OTHER","시행규칙 서식(협약서) 내부 조항 — 법령 조문 아님"),
17:("013933","부칙4","","","INHERIT","시행령 부칙 — 코퍼스 미보유"),
18:("013774","4","","","INHERIT","'동 법'=혁신법"),
19:("EXT:고등교육법","17","","","INHERIT","직전 「고등교육법」"),
20:("013774","부칙3","","","INHERIT","혁신법 부칙 — 코퍼스 미보유"),
21:("013774","부칙1","","","INHERIT","혁신법 부칙 — 코퍼스 미보유"),
22:("EXT:산업기술혁신 촉진법","42","","","WORLD","법령명 없음 — 조 제목(전문생산기술연구소)으로만 식별"),
23:("EXT:국가연구개발사업 연구노트 지침","5","","","QUOTE_SELF","지침 원문 인용"),
24:("013933","39","2","1","INHERIT","같은 문장 혁신법 시행령"),
25:("013774","21","1","","QUOTE_SELF","혁신법 조문 인용 안 내부 참조"),
26:("EXT:국가연구개발사업 연구노트 지침","3","","","QUOTE_SELF","지침 원문 인용"),
27:("EXT:공동관리규정(종전)","2","","3","INHERIT","종전 공동관리규정"),
28:("013774","32","","","QUOTE_SELF","혁신법 조문 인용 안 내부 참조"),
29:("75386","40","7","","INHERIT","같은 문장 사용 기준"),
30:("UNRESOLVABLE","","","","UNRESOLVABLE","원문 깨짐(…제2조 제1호에제7조…)"),
31:("EXT:국가연구개발사업 연구노트 지침","2","","","QUOTE_SELF","지침 원문 인용"),
32:("75386","39","5","","INHERIT","'동 고시'=사용 기준"),
33:("013774","4","","","INHERIT","같은 문장 ｢혁신법｣"),
34:("013774","21","2","","QUOTE_SELF","혁신법 조문 인용 안 내부 참조"),
35:("013933","9","","","EXPLICIT","동법 시행령(혁신법)"),
36:("013933","2","","","EXPLICIT",""),
37:("013933","35","5","","EXPLICIT","출처는 정확히 시행령. 단 현행 내용은 34조5항(DRIFT)"),
38:("013933","9","1","","EXPLICIT",""),
39:("013933","9","1","","EXPLICIT",""),
40:("013933","14","","","EXPLICIT","동법 시행령"),
41:("013933","2","","","EXPLICIT",""),
42:("013933","32","","","EXPLICIT",""),
43:("013933","13","","","EXPLICIT",""),
44:("013933","38","","","EXPLICIT",""),
45:("013933","27","","","EXPLICIT",""),
46:("013933","9","","","EXPLICIT",""),
47:("013933","34","4","","EXPLICIT",""),
48:("013933","49","1","","EXPLICIT",""),
49:("013933","64","2","","EXPLICIT","현행은 64조3항(DRIFT)"),
50:("013933","13","2","","EXPLICIT",""),
51:("013933","31","3","","EXPLICIT","현행은 31조4항(DRIFT)"),
52:("013933","19","2","2","EXPLICIT",""),
53:("013933","18","4","","EXPLICIT",""),
54:("013933","9","1","","EXPLICIT",""),
55:("013933","13","","","EXPLICIT",""),
56:("013933","6","","","EXPLICIT",""),
57:("013774","9","","","EXPLICIT",""),
58:("013774","11","2","","EXPLICIT",""),
59:("013774","15","","","EXPLICIT",""),
60:("013933","2","2","1","EXPERT","매뉴얼 표기는 '혁신법 제2조 제2항 제1호'이나 혁신법 제2조엔 항이 없음 — 의도는 시행령 제2조제2항제1호(매뉴얼 자체 오기)"),
61:("013774","11","","","EXPLICIT",""),
62:("013774","35","1","","EXPLICIT",""),
63:("013774","16","","","EXPLICIT",""),
64:("013774","15","1","5","EXPLICIT",""),
65:("013774","2","","","EXPLICIT",""),
66:("013774","35","","","EXPLICIT",""),
67:("013774","10","1","","EXPLICIT",""),
68:("013774","3","","","EXPLICIT",""),
69:("013774","3","","","EXPLICIT",""),
70:("013774","2","","2","EXPLICIT",""),
71:("013774","11","2","","EXPLICIT",""),
72:("013774","14","","","EXPLICIT",""),
73:("75386","43","2","","EXPLICIT",""),
74:("75386","25","12","","EXPLICIT",""),
75:("75386","26","5","","EXPLICIT",""),
76:("75386","40","10","","EXPLICIT",""),
77:("75386","25","4","","EXPLICIT",""),
78:("75386","73","","","EXPLICIT",""),
79:("75386","25의2","","","EXPLICIT",""),
80:("75386","40","10","","EXPLICIT",""),
81:("75386","40","5","","EXPLICIT",""),
82:("75386","37","2","","EXPLICIT",""),
83:("75386","25의2","6","","EXPLICIT",""),
84:("75386","21","4","","EXPLICIT",""),
85:("EXT:고등교육법","17","","","INHERIT","'동법'=직전 「고등교육법」(혁신법 아님!)"),
86:("EXT:고등교육법","14의2","","","INHERIT","'동법'=직전 「고등교육법」(혁신법 아님!)"),
87:("EXT:과학기술분야 정부출연연구기관 등의 설립·운영 및 육성에 관한 법률","5의2","","","EXPLICIT",""),
88:("EXT:특정연구기관 육성법","3","","","EXPLICIT",""),
89:("EXT:국가연구개발 시설·장비의 관리 등에 관한 표준지침","2","","","EXPLICIT","향후 코퍼스 추가 예정 법령"),
90:("EXT:중견기업 성장촉진 및 경쟁력 강화에 관한 특별법","2","","1","EXPLICIT",""),
91:("013933","2","","","EXPLICIT","「혁신법 시행령」"),
92:("EXT:고등교육법","14의2","","","EXPLICIT",""),
93:("EXT:부가가치세법 시행규칙","32","","","EXPLICIT",""),
94:("EXT:국회법","85의3","","","EXPLICIT","시행령 조문 인용 안 외부 법"),
95:("013774","9","4","","EXPLICIT_SHORT","시행령 인용 안 '법'=혁신법"),
96:("013774","9","4","","EXPLICIT_SHORT","시행령 인용 안 '법'=혁신법"),
97:("013774","2","","2","EXPLICIT_SHORT","'법'=혁신법"),
98:("013774","16","","","EXPLICIT_SHORT","지침 인용 안 '법'=혁신법"),
99:("013774","11","1","","EXPLICIT_SHORT","시행령 인용 안 '법'=혁신법"),
100:("013774","2","","","EXPLICIT_SHORT",""),
}
d=json.load(open('data/guide/BON.json',encoding='utf-8'))
pat=re.compile(r'(?P<q>「[^」]{2,40}」\s*|(?:국가연구개발혁신법|혁신법|같은\s*법|동법|이\s*법)?\s*(?:시행령|시행규칙)\s*|(?:국가연구개발사업\s*)?(?:연구개발비\s*)?사용\s*기준\s*|국가연구개발혁신법\s*|혁신법\s*|같은\s*법\s*|동법\s*|(?<![가-힣])법\s*|(?<![가-힣])영\s*)?제\s*(?P<a>\d+)\s*조(?:의\s*(?P<b>\d+))?(?:\s*제?\s*(?P<p>\d+)\s*항)?(?:\s*제?\s*(?P<h>\d+)\s*호)?')
recs=[]
for g in d:
    t=re.sub(r'\s+',' ',g.get('answer') or '')
    for m in pat.finditer(t):
        q=(m.group('q') or '').strip()
        if '「' in q: cat='bracket'
        elif re.search('시행령|시행규칙',q): cat='decree_rule'
        elif '기준' in q: cat='usage_std'
        elif re.search('같은|동법|이 ?법',q): cat='same_law'
        elif q in ('법','영'): cat='short'
        elif q: cat='law_named'
        else: cat='bare'
        recs.append(dict(gid=g['guide_id'],page=g['page_start'],start=m.start(),end=m.end(),raw=t[m.start():m.end()].strip(),cat=cat,
            ctx=t[max(0,m.start()-100):m.start()]+'⟦'+t[m.start():m.end()]+'⟧'+t[m.end():m.end()+30]))
random.seed(20261007)
quota={'bare':34,'decree_rule':22,'law_named':16,'usage_std':12,'same_law':2,'bracket':8,'short':6}
by=collections.defaultdict(list)
for r in recs: by[r['cat']].append(r)
S=[]
for c,n in quota.items(): S+=random.sample(by[c],min(n,len(by[c])))
assert len(S)==100
CORP={'013774','013933','013980','75386','010090'}
import os
os.makedirs('data/eval',exist_ok=True)
with open('data/eval/provenance_gold.csv','w',encoding='utf-8-sig',newline='') as f:
    w=csv.writer(f,lineterminator='\r\n')
    w.writerow(['pg_id','guide_id','page','answer_span_start','answer_span_end','raw_citation','surface_type','context',
                'gold_law','gold_article','gold_paragraph','gold_item','gold_in_corpus','resolvability','split','note','labeler','reviewer'])
    for i,r in enumerate(S,1):
        law,art,para,item,res,note=L[i]
        inc='Y' if (law in CORP and not art.startswith('부칙')) else 'N'
        split='dev' if i%5 in (1,2) else 'test'
        w.writerow([f'PG-{i:03d}',r['gid'],r['page'],r['start'],r['end'],r['raw'],r['cat'],r['ctx'],law,art,para,item,inc,res,split,note,'작업보조 LLM',''])
print('written 100')
from collections import Counter
rows=list(csv.DictReader(open('data/eval/provenance_gold.csv',encoding='utf-8-sig')))
print(Counter(r['resolvability'] for r in rows)); print(Counter(r['gold_in_corpus'] for r in rows)); print(Counter(r['split'] for r in rows))
print(Counter(r['gold_law'] if not r['gold_law'].startswith('EXT') else 'EXT' for r in rows))
