import numpy as np, json
R,RM,MU,J2,WE,DAY=6378.137,6371.0,398600.4418,1.08262668e-3,7.2921159e-5,86400
D2R=np.pi/180;DAYS=94;DT=4.0
sites=[('Riau',0.4,102.3),('Jambi',-1.7,103.5),('Ogan Komering Ilir',-3.3,105.6),('Kubu Raya',-0.4,109.4),('Kahayan–Sebangau',-2.4,113.9),('Barito Kuala',-2.9,114.6)]
def mk(h,inc,ltan,sw):
    a=R+h;n=(MU/a**3)**.5;i=inc*D2R;q=J2*(R/a)**2;c=np.cos(i)
    return dict(sw=sw,ci=c,si=np.sin(i),dOm=-1.5*q*n*c,du=n*(1+.75*q*(3*c*c-1))+.75*n*q*(5*c*c-1),Om0=15*(ltan-12)*D2R,u0=0.0)
t=np.arange(0,DAYS*DAY,DT)
def passes(s,lat,lon):
    Om=s['Om0']+s['dOm']*t;u=s['u0']+s['du']*t;cu,su=np.cos(u),np.sin(u);cO,sO=np.cos(Om),np.sin(Om)
    x=cu*cO-su*s['ci']*sO;y=cu*sO+su*s['ci']*cO;z=su*s['si'];th=np.pi+WE*t;ct,st=np.cos(th),np.sin(th)
    X=x*ct+y*st;Y=y*ct-x*st;a,b=lat*D2R,lon*D2R
    c=X*np.cos(a)*np.cos(b)+Y*np.cos(a)*np.sin(b)+z*np.sin(a)
    k=np.where((c[1:-1]>c[:-2])&(c[1:-1]>=c[2:])&(c[1:-1]>np.cos(s['sw']/2/RM)))[0]+1
    return t[k]
def stats(sats):
    o=[]
    for n,la,lo in sites:
        tt=np.sort(np.concatenate([passes(s,la,lo) for s in sats]));lst=(tt/3600+lo/15)%24
        pre=tt[(lst>=3)&(lst<6)]/DAY;k={int(v) for v in np.floor(pre+lo/360+5/24) if 1<=v<=93}
        o.append((100*len(k)/93,len(tt[(lst>=19)|(lst<5)])/DAYS))
    return o
res={}
for inc in np.arange(0,6.01,0.5):
    res[f'{inc:.1f}']={'two':stats([mk(650,inc,12,935),mk(650,inc,22,935)]),'one':stats([mk(650,inc,12,935)])}
    print(inc,[round(v[0]) for v in res[f'{inc:.1f}']['two']],[round(v[0]) for v in res[f'{inc:.1f}']['one']])
json.dump(res,open('scan.json','w'))
