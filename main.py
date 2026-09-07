# -*- coding: utf-8 -*-
import os, re, time, math, sqlite3, itertools, json
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
YF_BATCH_SIZE=int(os.getenv('YF_BATCH_SIZE','10'))
YF_BATCH_PAUSE=float(os.getenv('YF_BATCH_PAUSE','8.0'))
YF_MAX_RETRIES=int(os.getenv('YF_MAX_RETRIES','3'))
YF_BACKOFF_BASE=float(os.getenv('YF_BACKOFF_BASE','60.0'))
YF_CACHE_MAX_AGE_HOURS=float(os.getenv('YF_CACHE_MAX_AGE_HOURS','20'))
YF_CACHE_DIR=os.path.join(DATA_DIR,'yf_cache')
os.makedirs(YF_CACHE_DIR,exist_ok=True)

def fix_text(s):
    if not isinstance(s, str):
        s = str(s)
    # Mesaj daha once UTF-8 -> Latin-1/Windows-1252 olarak bozulduysa
    # en fazla 3 tur onarmayi dene. Dogru Turkce metne dokunmaz.
    markers = ('Ã', 'Ã', 'Ã', 'Ã°', 'Ã', 'Ã¢')
    for _ in range(3):
        if not any(x in s for x in markers):
            break
        repaired = None
        for enc in ('latin1', 'cp1252'):
            try:
                repaired = s.encode(enc).decode('utf-8')
                break
            except Exception:
                pass
        if repaired is None or repaired == s:
            break
        s = repaired
    return s

def tg(msg):
    # Once olasi mojibake'i duzelt. Sonra tum Turkce karakterleri ve emojileri
    # ASCII JSON icindeki \uXXXX kacislarina cevir. Boylece Railway/GitHub/HTTP
    # katmanlarinda karakter kodlamasi degisse bile Telegram metni dogru cozer.
    msg = fix_text(msg)
    if not BOT_TOKEN:
        print('[TELEGRAM YOK]'); print(msg); return False
    ok = False
    for cid in CHAT_IDS:
        try:
            payload = json.dumps({'chat_id': cid, 'text': msg}, ensure_ascii=True).encode('ascii')
            r = requests.post(
                f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage',
                data=payload,
                headers={'Content-Type':'application/json'},
                timeout=20
            )
            print('[TELEGRAM OK]' if r.ok else '[TELEGRAM HATA]', cid, r.text[:200])
            ok = ok or r.ok
        except Exception as e:
            print('[TELEGRAM EXC]', cid, e)
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
    """KAP BIST Åirketleri tablosunun yalnÄ±zca 'Kod' sÃ¼tununu okur.
    Eski sÃ¼rÃ¼m tÃ¼m sayfadaki bÃ¼yÃ¼k harfli kelimeleri sembol sanabildiÄi iÃ§in
    Åehir/Ã¼nvan kelimeleri Yahoo'ya ticker olarak gÃ¶nderilebiliyordu.
    """
    r=requests.get(
        KAP_BIST_URL,
        headers={'User-Agent':'Mozilla/5.0 (compatible; BISTLearningBot/1.1)'},
        timeout=30
    )
    r.raise_for_status()
    soup=BeautifulSoup(r.text,'html.parser')

    codes=set()

    # Ãnce gerÃ§ek tablo satÄ±rlarÄ±nÄ± kullan.
    for tr in soup.find_all('tr'):
        tds=tr.find_all('td')
        if not tds:
            continue
        raw=tds[0].get_text(' ',strip=True).upper()
        # KAP'ta bazÄ± iÅlem gÃ¶rmeyen/Ã¶zel kurum kodlarÄ±nda boÅluk olabiliyor.
        # Yahoo BIST hisseleri iÃ§in tek parÃ§a 3-6 karakterli kodlarÄ± al.
        if re.fullmatch(r'[A-Z0-9]{3,6}',raw):
            codes.add(raw)

    # KAP gÃ¶rÃ¼nÃ¼mÃ¼ tablo etiketi kullanmazsa Åirket link metinlerinden yedekle.
    if len(codes)<250:
        for a in soup.find_all('a'):
            raw=a.get_text(' ',strip=True).upper()
            href=(a.get('href') or '').lower()
            if ('sirket' in href or 'company' in href) and re.fullmatch(r'[A-Z0-9]{3,6}',raw):
                codes.add(raw)

    # Son emniyet: yalnÄ±zca satÄ±r baÅÄ±nda kod + Åirket Ã¼nvanÄ± kalÄ±bÄ±nÄ± yakala.
    if len(codes)<250:
        page=soup.get_text('\n',strip=True)
        for m in re.finditer(r'(?m)^([A-Z0-9]{3,6})\s*$',page):
            codes.add(m.group(1))

    codes=sorted(codes)
    if len(codes)<250:
        raise RuntimeError(f'KAP sembol parse yetersiz: {len(codes)}')
    print(f'[KAP] gerÃ§ek sembol adedi={len(codes)}')
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

def _extract_yf_frame(data,sym,batch):
    if data is None or len(data)==0:
        return None
    if isinstance(data.columns,pd.MultiIndex):
        # yfinance sÃ¼rÃ¼mÃ¼ne gÃ¶re ticker 0. veya 1. seviyede gelebilir.
        for level in range(data.columns.nlevels):
            vals=set(map(str,data.columns.get_level_values(level)))
            if sym in vals:
                try:
                    df=data.xs(sym,axis=1,level=level).copy().dropna(how='all')
                    if not df.empty:
                        return df
                except Exception:
                    pass
        return None
    if len(batch)==1:
        df=data.copy().dropna(how='all')
        return df if not df.empty else None
    return None


def _cache_path(sym):
    safe=re.sub(r'[^A-Za-z0-9_.-]+','_',sym)
    return os.path.join(YF_CACHE_DIR,safe+'.pkl')

def _cache_load(sym, allow_stale=True):
    path=_cache_path(sym)
    if not os.path.exists(path):
        return None, None
    try:
        age_h=max(0.0,(time.time()-os.path.getmtime(path))/3600.0)
        if (not allow_stale) and age_h>YF_CACHE_MAX_AGE_HOURS:
            return None, age_h
        df=pd.read_pickle(path)
        if df is None or len(df)<25:
            return None, age_h
        return df, age_h
    except Exception as e:
        print('[YF CACHE READ ERROR]',sym,str(e)[:120])
        return None, None

def _cache_save(sym,df):
    if df is None or len(df)<25:
        return False
    path=_cache_path(sym)
    tmp=path+'.tmp'
    try:
        df.to_pickle(tmp)
        os.replace(tmp,path)
        return True
    except Exception as e:
        print('[YF CACHE WRITE ERROR]',sym,str(e)[:120])
        try:
            if os.path.exists(tmp): os.remove(tmp)
        except Exception: pass
        return False

def _cache_is_fresh(age_h):
    return age_h is not None and age_h<=YF_CACHE_MAX_AGE_HOURS

def _is_rate_limit_error(exc):
    t=str(exc).lower()
    return ('ratelimit' in t or 'rate limit' in t or 'too many requests' in t or '429' in t)

def _yf_download_with_retry(symbols,period='6mo',group_by='ticker'):
    last_exc=None
    for attempt in range(1,YF_MAX_RETRIES+1):
        try:
            data=yf.download(
                symbols,period=period,interval='1d',auto_adjust=False,
                group_by=group_by,threads=False,progress=False,timeout=45
            )
            if data is not None and len(data)>0:
                return data
            last_exc=RuntimeError('Yahoo returned empty data')
        except Exception as e:
            last_exc=e
        if attempt < YF_MAX_RETRIES:
            if last_exc is not None and _is_rate_limit_error(last_exc):
                wait=YF_BACKOFF_BASE*(2**(attempt-1))
                print(f'[YF RATE LIMIT] attempt={attempt}/{YF_MAX_RETRIES} sleep={wait:.0f}s')
            else:
                wait=min(15.0*attempt,45.0)
                print(f'[YF RETRY] attempt={attempt}/{YF_MAX_RETRIES} sleep={wait:.0f}s err={str(last_exc)[:100]}')
            time.sleep(wait)
    if last_exc:
        print('[YF GROUP FAILED]',str(last_exc)[:180])
    return None

def download_daily(yf_symbols,period='6mo'):
    # 1) Cache'i once yukle. Taze cache icin Yahoo'ya hic istek atma.
    # 2) Yalniz eksik veya bayat sembolleri yavas gruplar halinde yenile.
    # 3) Yahoo rate-limit verirse bayat cache'i kullanmaya devam et.
    out={}
    stale_cache={}
    need_refresh=[]
    for sym in yf_symbols:
        df,age_h=_cache_load(sym,allow_stale=True)
        if df is not None:
            out[sym]=df
            stale_cache[sym]=df
        if df is None or not _cache_is_fresh(age_h):
            need_refresh.append(sym)

    fresh_count=len(yf_symbols)-len(need_refresh)
    print(f'[YF CACHE] total={len(yf_symbols)} fresh={fresh_count} refresh={len(need_refresh)} cached_any={len(out)}')
    if not need_refresh:
        print(f'[YF] cache_only ok={len(out)}/{len(yf_symbols)}')
        return out

    batches=list(chunks(need_refresh,YF_BATCH_SIZE))
    failed=[]
    for bi,batch in enumerate(batches,1):
        data=_yf_download_with_retry(batch,period=period,group_by='ticker')
        if data is None or len(data)==0:
            failed.extend(batch)
        else:
            for sym in batch:
                df=_extract_yf_frame(data,sym,batch)
                if df is not None and len(df)>=25:
                    out[sym]=df
                    _cache_save(sym,df)
                else:
                    failed.append(sym)
        print(f'[YF] batch={bi}/{len(batches)} cache+new_ok={len(out)} refresh_fail={len(set(failed))}')
        if bi < len(batches):
            time.sleep(YF_BATCH_PAUSE)

    # Yalniz hic cache'i olmayan eksikleri bir kez daha, 5'li gruplarda dene.
    retry=[s for s in dict.fromkeys(failed) if s not in stale_cache and s not in out]
    if retry:
        print(f'[YF] missing_retry={len(retry)}')
        time.sleep(max(YF_BACKOFF_BASE,60.0))
        for batch in chunks(retry,max(2,min(5,YF_BATCH_SIZE))):
            data=_yf_download_with_retry(batch,period=period,group_by='ticker')
            if data is not None and len(data)>0:
                for sym in batch:
                    df=_extract_yf_frame(data,sym,batch)
                    if df is not None and len(df)>=25:
                        out[sym]=df
                        _cache_save(sym,df)
            time.sleep(max(YF_BATCH_PAUSE,8.0))

    # Refresh basarisiz olsa bile eski cache silinmez; out zaten onu tasiyor.
    print(f'[YF] total_data_ok={len(out)}/{len(yf_symbols)} newly_missing={sum(1 for s in yf_symbols if s not in out)}')
    return out

def download_index(period='6mo'):
    # Endeks de ayni cache/backoff mantigini kullanir.
    cached,age_h=_cache_load(INDEX_SYMBOL,allow_stale=True)
    if cached is not None and _cache_is_fresh(age_h):
        print('[YF INDEX] fresh cache')
        return cached
    data=_yf_download_with_retry(INDEX_SYMBOL,period=period,group_by='column')
    if data is not None and len(data)>0:
        if isinstance(data.columns,pd.MultiIndex):
            extracted=None
            for level in range(data.columns.nlevels):
                if INDEX_SYMBOL in set(map(str,data.columns.get_level_values(level))):
                    try:
                        extracted=data.xs(INDEX_SYMBOL,axis=1,level=level)
                        break
                    except Exception:
                        pass
            if extracted is not None:
                data=extracted
        if len(data)>=25:
            _cache_save(INDEX_SYMBOL,data)
            return data
    if cached is not None:
        print('[YF INDEX] refresh failed, stale cache used')
        return cached
    return pd.DataFrame()

def rsi(series,n=14):
    d=series.diff(); g=d.clip(lower=0); l=-d.clip(upper=0)
    ag=g.ewm(alpha=1/n,adjust=False,min_periods=n).mean(); al=l.ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    rs=ag/al.replace(0,np.nan); return 100-(100/(1+rs))

def atr_pct(df,n=14):
    prev=df['Close'].shift(1)
    tr=pd.concat([(df['High']-df['Low']).abs(),(df['High']-prev).abs(),(df['Low']-prev).abs()],axis=1).max(axis=1)
    return tr.rolling(n).mean()/df['Close']*100

FEATURE_COLS=['ret1','ret3','ret5','ret10','vol_ratio5','vol_ratio20','range5','range20','compression5','rsi14','atr14_pct','dist_high20','dist_high50','green3','green5','index_ret1','index_ret5','rel1','rel5','prev_tavan20']

FEATURE_LABELS={
    "ret1":"1 gÃ¼nlÃ¼k momentum",
    "ret3":"3 gÃ¼nlÃ¼k momentum",
    "ret5":"5 gÃ¼nlÃ¼k momentum",
    "ret10":"10 gÃ¼nlÃ¼k momentum",
    "vol_ratio5":"Hacim / 5 gÃ¼nlÃ¼k ortalama",
    "vol_ratio20":"Hacim / 20 gÃ¼nlÃ¼k ortalama",
    "range5":"5 gÃ¼nlÃ¼k fiyat aralÄ±ÄÄ±",
    "range20":"20 gÃ¼nlÃ¼k fiyat aralÄ±ÄÄ±",
    "compression5":"5 gÃ¼nlÃ¼k sÄ±kÄ±Åma oranÄ±",
    "rsi14":"RSI (14)",
    "atr14_pct":"ATR (14) yÃ¼zdesi",
    "dist_high20":"20 gÃ¼nlÃ¼k zirveye uzaklÄ±k",
    "dist_high50":"50 gÃ¼nlÃ¼k zirveye uzaklÄ±k",
    "green3":"Son 3 gÃ¼nde yeÅil gÃ¼n sayÄ±sÄ±",
    "green5":"Son 5 gÃ¼nde yeÅil gÃ¼n sayÄ±sÄ±",
    "index_ret1":"BIST 100 1 gÃ¼nlÃ¼k deÄiÅim",
    "index_ret5":"BIST 100 5 gÃ¼nlÃ¼k deÄiÅim",
    "rel1":"Hissenin BIST 100'e gÃ¶re 1 gÃ¼nlÃ¼k gÃ¼cÃ¼",
    "rel5":"Hissenin BIST 100'e gÃ¶re 5 gÃ¼nlÃ¼k gÃ¼cÃ¼",
    "prev_tavan20":"Son 20 gÃ¼nde tavan sayÄ±sÄ±",
}

def feature_label(col):
    return FEATURE_LABELS.get(col,col)

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
        for name,lo,hi in [('dÃ¼ÅÃ¼k',None,q1),('orta-alt',q1,q2),('orta-Ã¼st',q2,q3),('yÃ¼ksek',q3,None)]:
            ss=[int(h or 0) for v,h in vals if (lo is None or float(v)>=lo) and (hi is None or float(v)<hi)]
            if len(ss)<30:continue
            rate=sum(ss)/len(ss); findings.append({'feature':col,'lo':lo,'hi':hi,'n':len(ss),'rate':rate,'lift':rate/base if base else 0})
    findings.sort(key=lambda x:(x['lift'],x['rate'],x['n']),reverse=True)
    return total,hits,findings[:12]

def where_from(f):
    col,lo,hi=f['feature'],f['lo'],f['hi']
    etiket=feature_label(col)
    if lo is None:return f'{col}<{hi}',f'{etiket} < {hi:.2f}'
    if hi is None:return f'{col}>={lo}',f'{etiket} â¥ {lo:.2f}'
    return f'{col}>={lo} and {col}<{hi}',f'{etiket} {lo:.2f}-{hi:.2f}'

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



def tavan_vs_yukselip_kalan(c,lookback_days=60,near_low=5.0):
    """Tavan yapanlarÄ±, ertesi gÃ¼n en az %5 yÃ¼kselip tavan eÅiÄine ulaÅamayanlardan ayÄ±rÄ±r."""
    md=c.execute('select max(day) from daily_features').fetchone()[0]
    if not md:
        return 0,0,[]
    since=(datetime.fromisoformat(md)-timedelta(days=lookback_days)).date().isoformat()
    tavan_n=c.execute('select count(*) from daily_features where day>=? and next_tavan_hit=1',(since,)).fetchone()[0] or 0
    near_n=c.execute('select count(*) from daily_features where day>=? and next_tavan_hit=0 and next_high_pct>=? and next_high_pct<?',(since,near_low,TAVAN_HIT_PCT)).fetchone()[0] or 0
    out=[]
    if tavan_n<30 or near_n<30:
        return tavan_n,near_n,out
    for col in FEATURE_COLS:
        tv=c.execute(f'select {col} from daily_features where day>=? and next_tavan_hit=1 and {col} is not null',(since,)).fetchall()
        nr=c.execute(f'select {col} from daily_features where day>=? and next_tavan_hit=0 and next_high_pct>=? and next_high_pct<? and {col} is not null',(since,near_low,TAVAN_HIT_PCT)).fetchall()
        a=np.array([float(x[0]) for x in tv if x[0] is not None and math.isfinite(float(x[0]))],dtype=float)
        b=np.array([float(x[0]) for x in nr if x[0] is not None and math.isfinite(float(x[0]))],dtype=float)
        if len(a)<30 or len(b)<30:
            continue
        ma=float(np.mean(a)); mb=float(np.mean(b))
        va=float(np.var(a,ddof=1)) if len(a)>1 else 0.0
        vb=float(np.var(b,ddof=1)) if len(b)>1 else 0.0
        pooled=math.sqrt(max(((len(a)-1)*va+(len(b)-1)*vb)/(len(a)+len(b)-2),0.0)) if len(a)+len(b)>2 else 0.0
        effect=(ma-mb)/pooled if pooled>1e-12 else 0.0
        out.append({'feature':col,'tavan_mean':ma,'near_mean':mb,'effect':effect,'n_tavan':len(a),'n_near':len(b)})
    out.sort(key=lambda x:abs(x['effect']),reverse=True)
    return tavan_n,near_n,out[:10]


def seri_tavan_ogrenme(c,lookback_days=60):
    """
    Ä°lk tavan baÅlamadan ÃNCEKÄ° gÃ¼nÃ¼n Ã¶zelliklerinden, ilk tavandan sonra
    2. ve 3. iÅlem gÃ¼nÃ¼nde de tavan gÃ¶rÃ¼lÃ¼p gÃ¶rÃ¼lmeyeceÄini Ã¶Ärenir.

    Veri sÄ±zÄ±ntÄ±sÄ±nÄ± Ã¶nlemek iÃ§in Ã¶zellikler yalnÄ±z seri baÅlamadan Ã¶nceki
    satÄ±rdan alÄ±nÄ±r. `next_tavan_hit` ertesi iÅlem gÃ¼nÃ¼nÃ¼n tavan etiketidir.
    """
    md=c.execute('select max(day) from daily_features').fetchone()[0]
    if not md:
        return {'first_n':0,'serial2_n':0,'serial3_n':0,'single_n':0,'sep2':[],'sep3':[]}

    max_day=datetime.fromisoformat(md).date()
    since=(max_day-timedelta(days=lookback_days)).isoformat()
    # Ä°lk tavanÄ±n bir Ã¶nceki gÃ¼nÃ¼nÃ¼ ve sonraki 2 iÅlem gÃ¼nÃ¼nÃ¼ doÄru kurabilmek
    # iÃ§in rapor penceresinden biraz daha eski satÄ±rlarÄ± da oku.
    pad_since=(max_day-timedelta(days=lookback_days+20)).isoformat()
    cols=['code','day','next_tavan_hit',*FEATURE_COLS]
    q=f"select {','.join(cols)} from daily_features where day>=? order by code,day"
    df=pd.read_sql_query(q,c,params=(pad_since,))
    if df.empty:
        return {'first_n':0,'serial2_n':0,'serial3_n':0,'single_n':0,'sep2':[],'sep3':[]}

    df['next_tavan_hit']=pd.to_numeric(df['next_tavan_hit'],errors='coerce').fillna(0).astype(int)
    g=df.groupby('code',sort=False)['next_tavan_hit']
    # Bir satÄ±rÄ±n prev_hit'i, o gÃ¼nÃ¼n kendisinin tavan olup olmadÄ±ÄÄ±nÄ± temsil eder.
    df['prev_hit']=g.shift(1).fillna(0).astype(int)
    df['hit_after_1']=g.shift(-1).fillna(0).astype(int)
    df['hit_after_2']=g.shift(-2).fillna(0).astype(int)

    # day satÄ±rÄ±nÄ±n ertesi gÃ¼nÃ¼ ilk tavan olsun; day'in kendisi tavan olmasÄ±n.
    starts=df[(df['day']>=since) & (df['next_tavan_hit']==1) & (df['prev_hit']!=1)].copy()
    if starts.empty:
        return {'first_n':0,'serial2_n':0,'serial3_n':0,'single_n':0,'sep2':[],'sep3':[]}

    starts['serial2']=(starts['hit_after_1']==1)
    starts['serial3']=(starts['hit_after_1']==1) & (starts['hit_after_2']==1)
    starts['single']=(starts['hit_after_1']!=1)

    first_n=int(len(starts))
    serial2_n=int(starts['serial2'].sum())
    serial3_n=int(starts['serial3'].sum())
    single_n=int(starts['single'].sum())

    def ayir(mask_a,mask_b,min_a=20,min_b=20):
        a_df=starts[mask_a]
        b_df=starts[mask_b]
        if len(a_df)<min_a or len(b_df)<min_b:
            return []
        out=[]
        for col in FEATURE_COLS:
            a=pd.to_numeric(a_df[col],errors='coerce').replace([np.inf,-np.inf],np.nan).dropna().to_numpy(dtype=float)
            b=pd.to_numeric(b_df[col],errors='coerce').replace([np.inf,-np.inf],np.nan).dropna().to_numpy(dtype=float)
            if len(a)<min_a or len(b)<min_b:
                continue
            ma=float(np.mean(a)); mb=float(np.mean(b))
            va=float(np.var(a,ddof=1)) if len(a)>1 else 0.0
            vb=float(np.var(b,ddof=1)) if len(b)>1 else 0.0
            denom=len(a)+len(b)-2
            pooled=math.sqrt(max(((len(a)-1)*va+(len(b)-1)*vb)/denom,0.0)) if denom>0 else 0.0
            effect=(ma-mb)/pooled if pooled>1e-12 else 0.0
            out.append({'feature':col,'a_mean':ma,'b_mean':mb,'effect':effect,'n_a':len(a),'n_b':len(b)})
        out.sort(key=lambda x:abs(x['effect']),reverse=True)
        return out[:10]

    # 2+ seri yapanlarÄ±, ilk tavandan sonra duranlardan ayÄ±r.
    sep2=ayir(starts['serial2'],starts['single'],20,20)
    # 3+ seri yapanlarÄ±, 1-2 tavanda kalanlardan ayÄ±r. 3+ Ã¶rnekleri daha az olabilir.
    sep3=ayir(starts['serial3'],~starts['serial3'],12,20)
    return {
        'first_n':first_n,'serial2_n':serial2_n,'serial3_n':serial3_n,'single_n':single_n,
        'sep2':sep2,'sep3':sep3
    }

def report(c):
    total,hits,findings=feature_lifts(c,60); combos=combo_lifts(c,60); base=hits/total if total else 0
    lines=['ð BIST TAVAN ÃÄRENME RAPORU','',f'Son 60 gÃ¼nde tavan gÃ¶rme oranÄ±: %{base*100:.2f} ({hits}/{total})']
    if findings:
        lines+=['','ð§  Tavan Ã¶ncesinde Ã¶ne Ã§Ä±kan tekil Ã¶zellikler:']
        for f in findings[:5]:
            rng=f"<{f['hi']:.2f}" if f['lo'] is None else (f">={f['lo']:.2f}" if f['hi'] is None else f"{f['lo']:.2f}-{f['hi']:.2f}")
            lines.append(f"â¢ {feature_label(f['feature'])} {rng} â tavan %{f['rate']*100:.2f}, bazÄ±n {f['lift']:.2f}x (n={f['n']})")
    if combos:
        lines+=['','ð§© En gÃ¼Ã§lÃ¼ tavan-Ã¶ncesi kombinasyonlar:']
        for x in combos[:5]: lines.append(f"â¢ {x['name']} â tavan %{x['rate']*100:.2f}, bazÄ±n {x['lift']:.2f}x (n={x['n']})")

    tavan_n,near_n,sep=tavan_vs_yukselip_kalan(c,60,5.0)
    if sep:
        lines+=['',f'ð¬ Tavan yapanÄ± yalnÄ±z %5â{TAVAN_HIT_PCT:.1f} yÃ¼kselip kalanlardan ayÄ±ranlar (tavan n={tavan_n}, diÄer n={near_n}):']
        for x in sep[:5]:
            yon='daha yÃ¼ksek' if x['effect']>0 else 'daha dÃ¼ÅÃ¼k'
            lines.append(f"â¢ {feature_label(x['feature'])}: tavan ort. {x['tavan_mean']:.2f} | %5â{TAVAN_HIT_PCT:.1f} kalan ort. {x['near_mean']:.2f} â tavanda {yon} (ayrÄ±m {abs(x['effect']):.2f}Ï)")
    elif tavan_n or near_n:
        lines+=['',f'ð¬ Tavan / %5â{TAVAN_HIT_PCT:.1f} karÅÄ±laÅtÄ±rmasÄ± iÃ§in Ã¶rnek henÃ¼z yetersiz (tavan n={tavan_n}, diÄer n={near_n}).']

    seri=seri_tavan_ogrenme(c,60)
    if seri['first_n']:
        r2=seri['serial2_n']/seri['first_n']*100
        r3=seri['serial3_n']/seri['first_n']*100
        lines+=['','ð¥ SERÄ° TAVAN ÃÄRENMESÄ°',
                f"Ä°lk tavan adaylarÄ±nÄ±n sayÄ±sÄ±: {seri['first_n']}",
                f"Ä°lk tavandan sonra 2. iÅlem gÃ¼nÃ¼nde de tavan: %{r2:.2f} ({seri['serial2_n']}/{seri['first_n']})",
                f"3+ iÅlem gÃ¼nÃ¼ seri tavan: %{r3:.2f} ({seri['serial3_n']}/{seri['first_n']})"]
        if seri['sep2']:
            lines+=['','ð§¬ 2+ tavan yapanÄ± tek tavanda kalandan ayÄ±ran ilk-tavan Ã¶ncesi Ã¶zellikler:']
            for x in seri['sep2'][:5]:
                yon='daha yÃ¼ksek' if x['effect']>0 else 'daha dÃ¼ÅÃ¼k'
                lines.append(f"â¢ {feature_label(x['feature'])}: seri ort. {x['a_mean']:.2f} | tek tavan ort. {x['b_mean']:.2f} â seride {yon} (ayrÄ±m {abs(x['effect']):.2f}Ï)")
        else:
            lines+=['',f"ð§¬ 2+ / tek tavan ayrÄ±mÄ± iÃ§in Ã¶rnek henÃ¼z yetersiz (2+ n={seri['serial2_n']}, tek n={seri['single_n']})."]
        if seri['sep3']:
            lines+=['','ð 3+ seri tavanÄ± 1â2 tavanda kalandan ayÄ±ran ilk-tavan Ã¶ncesi Ã¶zellikler:']
            for x in seri['sep3'][:5]:
                yon='daha yÃ¼ksek' if x['effect']>0 else 'daha dÃ¼ÅÃ¼k'
                lines.append(f"â¢ {feature_label(x['feature'])}: 3+ seri ort. {x['a_mean']:.2f} | diÄer ort. {x['b_mean']:.2f} â 3+ seride {yon} (ayrÄ±m {abs(x['effect']):.2f}Ï)")
        elif seri['serial3_n']:
            lines+=['',f"ð 3+ seri tavan ayrÄ±mÄ± iÃ§in Ã¶rnek henÃ¼z yetersiz (3+ n={seri['serial3_n']})."]
    else:
        lines+=['','ð¥ Seri tavan Ã¶Ärenmesi iÃ§in henÃ¼z yeterli ilk-tavan Ã¶rneÄi yok.']

    lines+=['','Not: Ä°lk aÅamada AL sinyali yok; amaÃ§ tavan yapanlarÄ±n, yarÄ±da kalanlarÄ±n ve seri tavan yapanlarÄ±n gerÃ§ek farkÄ±nÄ± Ã¶Ärenmek.']
    return '\n'.join(lines)

def run_once():
    c=db()
    codes=symbol_list(c)
    yfs=[f'{x}.IS' for x in codes]
    print('[BIST] sembol',len(codes))

    idx=download_index('6mo')
    frames=download_daily(yfs,'6mo')
    saved=good=0
    for code in codes:
        df=frames.get(f'{code}.IS')
        if df is None or df.empty:
            continue
        try:
            rows=features_for_symbol(code,df,idx)
            if rows:
                saved+=save_rows(c,rows)
                good+=1
        except Exception as e:
            print('[FEATURE HATA]',code,e)

    c.commit()
    total=c.execute('select count(*) from daily_features').fetchone()[0] or 0
    print(f'[BIST ÃÄRENÄ°YOR] kod={len(codes)} veri_ok={good} rows_yazildi={saved} db_toplam={total}')

    # Veri gerÃ§ekten oluÅmadan "0/0" Ã¶Ärenme raporu gÃ¶nderme.
    if total==0:
        tg(
            'â ï¸ BIST ÃÄRENME VERÄ°SÄ° OLUÅMADI\n'
            f'KAP kodu: {len(codes)} | Yahoo veri OK: {good} | SatÄ±r: {saved}\n'
            '0/0 raporu gÃ¶nderilmedi. Railway logunda [YF] ve [FEATURE HATA] satÄ±rlarÄ±nÄ± kontrol et.'
        )
        c.close()
        return

    meta_set(c,'last_run_day',datetime.now(LOCAL_TZ).date().isoformat())
    c.commit()
    tg(report(c))
    c.close()

def should_run(c):
    now=datetime.now(LOCAL_TZ)
    if now.weekday()>=5:return False
    if meta_get(c,'last_run_day')==now.date().isoformat():return False
    return now.hour>RUN_AFTER_HOUR or (now.hour==RUN_AFTER_HOUR and now.minute>=RUN_AFTER_MINUTE)

def main():
    setup(); print('BIST TAVAN OGRENEN BOT V1.3 CACHE + RESUME',DB_PATH)
    tg('ð§  BIST TAVAN ÃÄRENEN BOT BAÅLADI\nSadece BIST100 deÄil, KAP iÃ§indeki BIST Åirketlerinin tamamÄ±nÄ± izleyecek.\nHedef: Her gÃ¼n tavan gÃ¶renleri bulup, tavan olmadan Ã¶nce diÄer hisselerden hangi Ã¶zelliklerle ayrÄ±ldÄ±klarÄ±nÄ± Ã¶Ärenmek.\nÄ°lk aÅamada AL/SAT mesajÄ± yok.')
    while True:
        c=db()
        try:run=should_run(c)
        finally:c.close()
        if run:
            try:run_once()
            except Exception as e:print('[GENEL HATA]',e)
        time.sleep(LOOP_SECONDS)

if __name__=='__main__': main()
