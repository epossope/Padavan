const http=require('http');
const fs=require('fs');
const path=require('path');
const root=path.resolve(__dirname,'..','miniapp');
const types={'.js':'application/javascript','.css':'text/css','.html':'text/html','.json':'application/json','.ttf':'font/ttf'};
http.createServer((request,response)=>{
  const url=new URL(request.url,'http://127.0.0.1');
  let file='';
  if(url.pathname==='/app'||url.pathname==='/app/')file=path.join(root,'index.html');
  else if(url.pathname.startsWith('/app/assets/'))file=path.resolve(root,decodeURIComponent(url.pathname.slice('/app/assets/'.length)));
  if(!file||(!file.startsWith(root+path.sep)&&file!==path.join(root,'index.html'))||!fs.existsSync(file)){response.writeHead(404);return response.end()}
  response.writeHead(200,{'Content-Type':types[path.extname(file)]||'application/octet-stream','Cache-Control':'no-store'});
  fs.createReadStream(file).pipe(response);
}).listen(8095,'127.0.0.1',()=>console.log('smoke server ready'));
