import os, re, time, math, sqlite3, itertools
from datetime import datetime, timedelta, timezone
import requests, pandas as pd, numpy as np, yfinance as yf
from bs4 import BeautifulSoup

LOCAL_TZ=timezone(timedelta(hours=3))
BOT_TOKEN=(os.getenv('BOT_TOKEN','') or os.getenv('TELEGRAM_BOT_TOKEN','')).strip()
CHAT_IDS=[int(x.strip()) for x in os.getenv('CHAT_IDS','2097448038').split(',') if x.strip()]
DATA_DIR=os.getenv('DATA_DIR','.').strip() or '.'
os.makedirs(DATA_DIR,exist_ok=True)
DB_PATH=os.path.join(DATA_DIR,'bist_tavan_learning.db')
KAP_BIST_URL='https://www.kap.org.tr/tr/bist-sirketler'
INDEX_SYMBOL='XU100.IS'
TAVAN_HIT_PCT=9.50
TAVAN_CLOSE_PCT=9.25
LOOP_SECONDS=900
RUN_AFTER_HOUR=18
RUN_AFTER_MINUTE=20
MIN_COMBO_N=12

def fix_text(s):
    if not isinstance(s,str): s=str(s)
    if any(x in s for x in ('Ã','Ä','Å','ð','Â','â')):
        try:s=s.encode('latin1').decode('utf-8')
        except:pass
    return s

def tg(msg):
    msg=fix_text(msg)
    if not BOT_TOKEN:
        print('[TELEGRAM YOK]'); print(msg); return False
    ok=False
    for cid in CHAT_IDS:
        try:
            r=requests.post(f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage',data={'chat_id':cid,'text':msg},timeout=20)
            print('[TELEGRAM OK]' if r.ok else '[TELEGRAM HATA]',cid,r.text[:200]); ok=ok or r.ok
        except Exception as e: print('[TELEGRAM EXC]',cid,e)
    return ok

def db():
    c=sqlite3.connect(DB_PATH,timeout=60)
    c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA synchronous=NORMAL')
    return c

def meta_get(c,k):
    r=c.execute('select value from meta where key=?',(k,)).fetchone()
    return r[0] if r else None

def meta_set(c,k,v):
    c.execute('insert into meta(key,value) values(?,?) on conflict(key) do update set value=excluded.value',(k,str(v)))

def setup():
    c=db()
    c.execute('create table if not exists meta(key text primary key,value text)')
    c.execute('create table if not exists symbols(code text primary key,yf_symbol text not null,first_seen text,last_seen text)')
    c.execute('''create table if not exists daily_features(
      code text not null,day text not null,close real,high real,low real,open real,volume real,
      ret1 real,ret3 real,ret5 real,ret10 real,vol_ratio5 real,vol_ratio20 real,
      range5 real,range20 real,compression5 real,rsi14 real,atr14_pct real,
      dist_high20 real,dist_high50 real,green3 integer,green5 integer,
      index_ret1 real,index_ret5 real,rel1 real,rel5 real,prev_tavan20 integer,
      next_high_pct real,next_close_pct real,next_tavan_hit integer,next_tavan_close integer,
      primary key(code,day))''')
    c.execute('create index if not exists ix_day on daily_features(day)')
    c.execute('create index if not exists ix_hit on daily_features(next_tavan_hit,day)')
    if meta_get(c,'last_run_day') is None: meta_set(c,'last_run_day','')
    if meta_get(c,'start_day') is None: meta_set(c,'start_day',datetime.now(LOCAL_TZ).date().isoformat())
    c.commit(); c.close()

def kap_bist_kodlari():
    r=requests.get(KAP_BIST_URL,headers={'User-Agent':'Mozilla/5.0'},timeout=30); r.raise_for_status()
    text=BeautifulSoup(r.text,'html.parser').get_text(' ',strip=True)
    bad={'BIST','KAP','ALL','PAY','PAZAR','TÜM','ŞİRKET','ŞİRKETLERİ','BORSA','YILDIZ','ANA','ALT','PİYASA','İŞLEM'}
    cand=set(re.findall(r'\b[A-ZÇĞİÖŞÜ0-9]{2,6}\b',text))
    codes=sorted(x for x in cand if x not in bad and re.fullmatch(r'[A-Z0-9]+',x))
    if len(codes)<300: raise RuntimeError(f'KAP sembol parse yetersiz: {len(codes)}')
    return codes

def symbol_list(c):
    try:
        codes=kap_bist_kodlari(); today=datetime.now(LOCAL_TZ).date().isoformat()
        for code in codes:
            c.execute('''insert into symbols(code,yf_symbol,first_seen,last_seen) values(?,?,?,?)
                         on conflict(code) do update set last_seen=excluded.last_seen''',(code,f'{code}.IS',today,today))
        c.commit(); return codes
    except Exception as e:
        print('[KAP LISTE HATA]',e)
        rows=c.execute('select code from symbols order by code').fetchall()
        if rows:return [r[0] for r in rows]
        env=[x.strip().upper() for x in os.getenv('BIST_SYMBOLS','').split(',') if x.strip()]
        if env:return env
        raise

def chunks(seq,n):
    for i in range(0,len(seq),n): yield seq[i:i+n]

def download_daily(yf_symbols,period='90d'):
    out={}
    for batch in chunks(yf_symbols,80):
        try:
            data=yf.download(batch,period=period,interval='1d',auto_adjust=False,group_by='ticker',threads=True,progress=False)
            if data is None or len(data)==0: continue
            if isinstance(data.columns,pd.MultiIndex):
                lvl=data.columns.get_level_values(0)
                for sym in batch:
                    if sym in lvl:
                        df=data[sym].copy().dropna(how='all')
                        if not df.empty: out[sym]=df
            elif len(batch)==1:
                df=data.copy().dropna(how='all')
                if not df.empty: out[batch[0]]=df
        except Exception as e: print('[YF BATCH HATA]',batch[:3],e)
        time.sleep(1)
    return out

def rsi(series,n=14):
    d=series.diff(); g=d.clip(lower=0); l=-d.clip(upper=0)
    ag=g.ewm(alpha=1/n,adjust=False,min_periods=n).mean(); al=l.ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    rs=ag/al.replace(0,np.nan); return 100-(100/(1+rs))

def atr_pct(df,n=14):
    prev=df['Close'].shift(1)
    tr=pd.concat([(df['High']-df['Low']).abs(),(df['High']-prev).abs(),(df['Low']-prev).abs()],axis=1).max(axis=1)
    return tr.rolling(n).mean()/df['Close']*100

FEATURE_COLS=['ret1','ret3','ret5','ret10','vol_ratio5','vol_ratio20','range5','range20','compression5','rsi14','atr14_pct','dist_high20','dist_high50','green3','green5','index_ret1','index_ret5','rel1','rel5','prev_tavan20']

def features_for_symbol(code,df,index_df):
    if df is None or len(df)<25:return []
    df=df.copy(); df.columns=[str(x) for x in df.columns]
    need=['Open','High','Low','Close','Volume']
    if not all(x in df.columns for x in need):return []
    for col in need:df[col]=pd.to_numeric(df[col],errors='coerce')
    df=df.dropna(subset=['Close'])
    if len(df)<25:return []
    close=df['Close']; vol=df['Volume'].replace(0,np.nan)
    ret1=close.pct_change(1)*100; ret3=close.pct_change(3)*100; ret5=close.pct_change(5)*100; ret10=close.pct_change(10)*100
    vr5=vol/vol.shift(1).rolling(5).mean(); vr20=vol/vol.shift(1).rolling(20).mean()
    hi5=df['High'].rolling(5).max(); lo5=df['Low'].rolling(5).min(); hi20=df['High'].rolling(20).max(); lo20=df['Low'].rolling(20).min(); hi50=df['High'].rolling(50).max()
    range5=(hi5/lo5-1)*100; range20=(hi20/lo20-1)*100; comp=range5/range20.replace(0,np.nan)
    rsi14=rsi(close,14); atr14=atr_pct(df,14); dh20=(close/hi20-1)*100; dh50=(close/hi50-1)*100
    green=(close>close.shift(1)).astype(int); g3=green.rolling(3).sum(); g5=green.rolling(5).sum()
    day_high_pct=(df['High']/close.shift(1)-1)*100; prev_tavan=(day_high_pct>=TAVAN_HIT_PCT).astype(int).shift(1).rolling(20).sum()
    idx1=pd.Series(index=df.index,dtype=float); idx5=pd.Series(index=df.index,dtype=float)
    if index_df is not None and not index_df.empty and 'Close' in index_df.columns:
        ic=pd.to_numeric(index_df['Close'],errors='coerce'); idx1=ic.pct_change(1).reindex(df.index)*100; idx5=ic.pct_change(5).reindex(df.index)*100
    def v(s,i):
        x=s.iloc[i]
        return None if pd.isna(x) or not math.isfinite(float(x)) else float(x)
    rows=[]
    for i in range(20,len(df)-1):
        c0=float(close.iloc[i]); c1=float(close.iloc[i+1]); h1=float(df['High'].iloc[i+1])
        nh=(h1/c0-1)*100 if c0 else None; nc=(c1/c0-1)*100 if c0 else None
        ir1=v(idx1,i); ir5=v(idx5,i); rr1=v(ret1,i); rr5=v(ret5,i)
        rows.append({'code':code,'day':pd.Timestamp(df.index[i]).date().isoformat(),'close':c0,'high':float(df['High'].iloc[i]),'low':float(df['Low'].iloc[i]),'open':float(df['Open'].iloc[i]),'volume':float(df['Volume'].iloc[i] or 0),
                     'ret1':rr1,'ret3':v(ret3,i),'ret5':rr5,'ret10':v(ret10,i),'vol_ratio5':v(vr5,i),'vol_ratio20':v(vr20,i),'range5':v(range5,i),'range20':v(range20,i),'compression5':v(comp,i),
                     'rsi14':v(rsi14,i),'atr14_pct':v(atr14,i),'dist_high20':v(dh20,i),'dist_high50':v(dh50,i),'green3':int(g3.iloc[i]) if not pd.isna(g3.iloc[i]) else None,'green5':int(g5.iloc[i]) if not pd.isna(g5.iloc[i]) else None,
                     'index_ret1':ir1,'index_ret5':ir5,'rel1':None if rr1 is None or ir1 is None else rr1-ir1,'rel5':None if rr5 is None or ir5 is None else rr5-ir5,'prev_tavan20':int(prev_tavan.iloc[i]) if not pd.isna(prev_tavan.iloc[i]) else 0,
                     'next_high_pct':nh,'next_close_pct':nc,'next_tavan_hit':1 if nh>=TAVAN_HIT_PCT else 0,'next_tavan_close':1 if nc>=TAVAN_CLOSE_PCT else 0})
    return rows

def save_rows(c,rows):
    cols=['code','day','close','high','low','open','volume',*FEATURE_COLS,'next_high_pct','next_close_pct','next_tavan_hit','next_tavan_close']
    q=','.join('?' for _ in cols); colstr=','.join(cols); n=0
    for r in rows:
        c.execute(f'insert or replace into daily_features({colstr}) values({q})',[r.get(x) for x in cols]); n+=1
    return n

def feature_lifts(c,lookback_days=60):
    md=c.execute('select max(day) from daily_features').fetchone()[0]
    if not md:return 0,0,[]
    since=(datetime.fromisoformat(md)-timedelta(days=lookback_days)).date().isoformat()
    total,hits=c.execute('select count(*),sum(next_tavan_hit) from daily_features where day>=?',(since,)).fetchone(); total=total or 0; hits=hits or 0; base=hits/total if total else 0
    findings=[]
    for col in FEATURE_COLS:
        vals=c.execute(f'select {col},next_tavan_hit from daily_features where day>=? and {col} is not null',(since,)).fetchall()
        if len(vals)<100:continue
        arr=np.array([float(v) for v,_ in vals]); q1,q2,q3=np.quantile(arr,[.25,.5,.75])
        for name,lo,hi in [('düşük',None,q1),('orta-alt',q1,q2),('orta-üst',q2,q3),('yüksek',q3,None)]:
            ss=[int(h or 0) for v,h in vals if (lo is None or float(v)>=lo) and (hi is None or float(v)<hi)]
            if len(ss)<30:continue
            rate=sum(ss)/len(ss); findings.append({'feature':col,'lo':lo,'hi':hi,'n':len(ss),'rate':rate,'lift':rate/base if base else 0})
    findings.sort(key=lambda x:(x['lift'],x['rate'],x['n']),reverse=True)
    return total,hits,findings[:12]

def where_from(f):
    col,lo,hi=f['feature'],f['lo'],f['hi']
    if lo is None:return f'{col}<{hi}',f'{col}<{hi:.2f}'
    if hi is None:return f'{col}>={lo}',f'{col}>={lo:.2f}'
    return f'{col}>={lo} and {col}<{hi}',f'{col} {lo:.2f}-{hi:.2f}'

def combo_lifts(c,lookback_days=60):
    total,hits,top=feature_lifts(c,lookback_days)
    if not total:return []
    md=c.execute('select max(day) from daily_features').fetchone()[0]; since=(datetime.fromisoformat(md)-timedelta(days=lookback_days)).date().isoformat(); base=hits/total if total else 0
    selected=[]; seen=set()
    for f in top:
        if f['feature'] in seen:continue
        seen.add(f['feature']); selected.append(f)
        if len(selected)>=8:break
    out=[]
    for k in (2,3):
        for items in itertools.combinations(selected,k):
            wh=[]; names=[]
            for f in items:
                w,n=where_from(f); wh.append(w); names.append(n)
            n,h=c.execute(f"select count(*),sum(next_tavan_hit) from daily_features where day>=? and {' and '.join(wh)}",(since,)).fetchone(); n=n or 0; h=h or 0
            if n<MIN_COMBO_N:continue
            rate=h/n; out.append({'name':' + '.join(names),'n':n,'rate':rate,'lift':rate/base if base else 0})
    out.sort(key=lambda x:(x['lift'],x['rate'],x['n']),reverse=True); return out[:8]

def report(c):
    total,hits,findings=feature_lifts(c,60); combos=combo_lifts(c,60); base=hits/total if total else 0
    lines=['📈 BIST TAVAN ÖĞRENME RAPORU','',f'Son 60 günde tavan-görme taban oranı: %{base*100:.2f} ({hits}/{total})']
    if findings:
        lines+=['','🧠 Tavan öncesinde öne çıkan tekil özellikler:']
        for f in findings[:5]:
            rng=f"<{f['hi']:.2f}" if f['lo'] is None else (f">={f['lo']:.2f}" if f['hi'] is None else f"{f['lo']:.2f}-{f['hi']:.2f}")
            lines.append(f"• {f['feature']} {rng} → tavan %{f['rate']*100:.2f}, bazın {f['lift']:.2f}x (n={f['n']})")
    if combos:
        lines+=['','🧩 En güçlü tavan-öncesi kombinasyonlar:']
        for x in combos[:5]: lines.append(f"• {x['name']} → tavan %{x['rate']*100:.2f}, bazın {x['lift']:.2f}x (n={x['n']})")
    lines+=['','Not: İlk aşamada AL sinyali yok; amaç tavan yapanların yapmayanlardan gerçek farkını öğrenmek.']
    return '\n'.join(lines)

def run_once():
    c=db(); codes=symbol_list(c); yfs=[f'{x}.IS' for x in codes]; print('[BIST] sembol',len(codes))
    idx=yf.download(INDEX_SYMBOL,period='90d',interval='1d',auto_adjust=False,progress=False)
    if isinstance(idx.columns,pd.MultiIndex):
        try: idx=idx.xs(INDEX_SYMBOL,axis=1,level=1)
        except: pass
    frames=download_daily(yfs,'90d'); saved=good=0
    for code in codes:
        df=frames.get(f'{code}.IS')
        if df is None or df.empty:continue
        try:saved+=save_rows(c,features_for_symbol(code,df,idx)); good+=1
        except Exception as e:print('[FEATURE HATA]',code,e)
    meta_set(c,'last_run_day',datetime.now(LOCAL_TZ).date().isoformat()); c.commit()
    print(f'[BIST ÖĞRENİYOR] kod={len(codes)} veri_ok={good} rows={saved}')
    tg(report(c)); c.close()

def should_run(c):
    now=datetime.now(LOCAL_TZ)
    if now.weekday()>=5:return False
    if meta_get(c,'last_run_day')==now.date().isoformat():return False
    return now.hour>RUN_AFTER_HOUR or (now.hour==RUN_AFTER_HOUR and now.minute>=RUN_AFTER_MINUTE)

def main():
    setup(); print('BIST TAVAN ÖĞRENEN BOT V1',DB_PATH)
    tg('🧠 BIST TAVAN ÖĞRENEN BOT BAŞLADI\nSadece BIST100 değil, KAP içindeki BIST şirketlerinin tamamını izleyecek.\nHedef: Her gün tavan görenleri bulup, tavan olmadan önce diğer hisselerden hangi özelliklerle ayrıldıklarını öğrenmek.\nİlk aşamada AL/SAT mesajı yok.')
    while True:
        c=db()
        try:run=should_run(c)
        finally:c.close()
        if run:
            try:run_once()
            except Exception as e:print('[GENEL HATA]',e)
        time.sleep(LOOP_SECONDS)

if __name__=='__main__': main()
