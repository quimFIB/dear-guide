
const fs=require('fs');
function extract(path,out){
  const h=fs.readFileSync(path,'utf8');
  const s=h.indexOf('const NW=168'), e=h.indexOf('function longestPath');
  fs.writeFileSync(out, h.slice(s,e)+'\nmodule.exports={layerLayout,NW,NH};');
}
extract('/tmp/dg-bench/layout/old_app.html','/tmp/dg-bench/layout/oldL.js');
extract('dgraph/static/app.html','/tmp/dg-bench/layout/newL.js');
const OLD=require('/tmp/dg-bench/layout/oldL.js');
const NEW=require('/tmp/dg-bench/layout/newL.js');
const NW=OLD.NW, NH=OLD.NH, SEP=String.fromCharCode(0);
function load(p){const raw=JSON.parse(fs.readFileSync(p,'utf8'));
  const ids=raw.vertices.map(v=>v.id),known=new Set(ids),kids={},par={};
  for(const ed of raw.edges){if(ed.active===false||!known.has(ed.from))continue;
    for(const t of(ed.to||[])){if(!known.has(t))continue;
      (kids[ed.from]=kids[ed.from]||[]).push(t);(par[t]=par[t]||[]).push(ed.from);}}
  return{ids,kids,par};}
function ranks(ids,par){const memo={},visit=(id,seen)=>{
  if(id in memo)return memo[id];if(seen.has(id))return 0;seen.add(id);
  const ps=par[id]||[],r=ps.length?1+Math.max(...ps.map(p=>visit(p,seen))):0;
  seen.delete(id);return memo[id]=r;};ids.forEach(i=>visit(i,new Set()));return memo;}
function hits(x1,y1,x2,y2,bx,by,bw,bh){const n=32;
  for(let i=1;i<n;i++){const t=i/n,x=x1+(x2-x1)*t,y=y1+(y2-y1)*t;
    if(x>bx&&x<bx+bw&&y>by&&y<by+bh)return true;}return false;}
function score(ids,kids,xy,bends){
  let through=0,total=0;
  for(const u of ids) for(const v of (kids[u]||[])){
    total++;
    const wp=(bends&&bends[u+SEP+v])||[];
    const pts=[{x:xy[u].x+NW/2,y:xy[u].y+NH},...wp,{x:xy[v].x+NW/2,y:xy[v].y}];
    let hit=false;
    for(let i=0;i<pts.length-1&&!hit;i++)
      for(const w of ids){ if(w===u||w===v) continue; const c=xy[w];
        if(hits(pts[i].x,pts[i].y,pts[i+1].x,pts[i+1].y,c.x,c.y,NW,NH)){hit=true;break;} }
    if(hit)through++;
  }
  return {through,total};
}
for(const name of process.argv.slice(2)){
  const {ids,kids,par}=load('/tmp/dg-bench/'+name+'/decisions.json');
  const rank=ranks(ids,par);
  const t0=Date.now();
  const o=OLD.layerLayout(ids,id=>rank[id],id=>par[id]||[],id=>kids[id]||[]);
  const tO=Date.now()-t0;
  const t1=Date.now();
  const n=NEW.layerLayout(ids,id=>rank[id],id=>par[id]||[],id=>kids[id]||[]);
  const tN=Date.now()-t1;
  const so=score(ids,kids,o.xy,null), sn=score(ids,kids,n.xy,n.bends);
  console.log(name.padEnd(11)+' '+String(ids.length).padStart(5)+' nodes, '
    +String(so.total).padStart(6)+' edges | through a box: '
    +String(so.through).padStart(5)+' -> '+String(sn.through).padStart(5)
    +'  ('+(100*(1-sn.through/so.through)).toFixed(1)+'% fewer) | '
    +tO+'ms -> '+tN+'ms | canvas '+Math.round(o.extent.w)+' -> '+Math.round(n.extent.w)+'px');
}
