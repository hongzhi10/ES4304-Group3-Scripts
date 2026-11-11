import json,numpy as np,matplotlib
matplotlib.use('Agg');import matplotlib.pyplot as plt
r=json.load(open('scan.json'));inc=sorted(float(k) for k in r)
C={'two':'#2a78d6','one':'#eb6834'};L={'two':'Two satellites (planes 10 h apart)','one':'One satellite'}
INK,MUT,GRID='#0b0b0b','#52514e','#e4e3df'
plt.rcParams.update({'font.family':'Arial','font.size':10,'axes.edgecolor':MUT,'axes.labelcolor':INK,'xtick.color':MUT,'ytick.color':MUT})
fig,ax=plt.subplots(1,2,figsize=(9.2,3.6),dpi=220)
for k in ('two','one'):
    worst=[min(v[0] for v in r[f'{i:.1f}'][k]) for i in inc]
    ax[0].plot(inc,worst,color=C[k],lw=2,marker='o',ms=5,mec='white',mew=1.2,label=L[k],zorder=3)
    med=[np.median([v[1] for v in r[f'{i:.1f}'][k]]) for i in inc]
    lo=[min(v[1] for v in r[f'{i:.1f}'][k]) for i in inc];hi=[max(v[1] for v in r[f'{i:.1f}'][k]) for i in inc]
    off=-0.06 if k=='two' else 0.06
    x=np.array(inc)+off
    ax[1].vlines(x,lo,hi,color=C[k],lw=1.6,zorder=2)
    ax[1].plot(x,med,'o',color=C[k],ms=5,mec='white',mew=1.2,label=L[k],zorder=3)
for a in ax:
    a.axvline(4,color=MUT,lw=1,ls=(0,(4,3)),zorder=1)
    a.grid(axis='y',color=GRID,lw=.8);a.set_axisbelow(True)
    for s in ('top','right'):a.spines[s].set_visible(False)
    a.set_xticks(range(7));a.set_xticklabels([f'{i}°' for i in range(7)]);a.set_xlabel('Orbit inclination')
ax[0].set_ylim(50,103);ax[0].set_ylabel('Nights with a 03:00–06:00 look (%)')
ax[0].set_title('Worst of the six provinces',loc='left',fontsize=10.5,color=INK,fontweight='bold')
ax[1].set_ylim(0,12.5);ax[1].set_ylabel('Night looks per night')
ax[1].set_title('Looks per night: median and range across provinces',loc='left',fontsize=10.5,color=INK,fontweight='bold')
ax[0].text(4.08,52,'Proposed 4°',color=MUT,fontsize=8.5)
ax[0].legend(loc='lower left',frameon=False,fontsize=8.5)
fig.tight_layout();fig.savefig('inclination_scan.png',facecolor='white')
