/* A shaded, deformable translucent membrane, rather than a wireframe sphere. */
(() => {
  const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
  let state='idle',amplitude=0,changed=0,last=0,phase=0,opacity=1;
  window.NoemaMembrane={setState(value){state=['idle','listening','thinking','responding','attention','muted'].includes(value)?value:'idle';changed=performance.now()},setAudioLevel(value){amplitude=Math.max(0,Math.min(1,Number(value)||0))}};
  function draw(ms){
    requestAnimationFrame(draw);
    if(document.hidden||ms-last<(reduced?250:33))return;
    const elapsed=Math.min(80,ms-last);last=ms;
    if(!reduced&&state!=='muted')phase+=elapsed/(state==='thinking'?1800:3500);
    opacity+=((state==='muted'?.28:1)-opacity)*.08;
    const t=reduced?0:phase;
    const pulse=!reduced&&['responding','attention'].includes(state)?Math.max(0,1-(ms-changed)/280):0;
    for(const canvas of document.querySelectorAll('canvas.membrane')){
      if(canvas.closest('.hidden,[hidden]') || !canvas.getClientRects().length)continue;
      const c=canvas.getContext('2d'), w=canvas.width, r=w*.29*(1+Math.sin(t)*.015+(state==='listening'?amplitude*.045:0)+pulse*.035), mid=w/2;
      if(!c)continue;c.globalAlpha=opacity;
      c.clearRect(0,0,w,w);
      const halo=c.createRadialGradient(mid,mid,r*.4,mid,mid,r*1.7);
      halo.addColorStop(0,'#f5f5f500');halo.addColorStop(.55,'#f5f5f510');halo.addColorStop(1,'#f5f5f500');c.fillStyle=halo;c.fillRect(0,0,w,w);
      c.save();c.translate(mid,mid+r*1.17);c.scale(1,.16);
      const reflection=c.createRadialGradient(0,0,0,0,0,r);reflection.addColorStop(0,'#f5f5f522');reflection.addColorStop(1,'#f5f5f500');c.fillStyle=reflection;c.fillRect(-r,-r,r*2,r*2);c.restore();
      for(let layer=0;layer<7;layer++){
        c.beginPath();
        for(let i=0;i<=220;i++){
          const a=i/220*Math.PI*2;
          const ripple=Math.sin(a*3+t+layer*.8)*.042+Math.sin(a*5-t*.7+layer)*.022;
          const rad=r*(1+ripple+Math.sin(layer+t)*.03);
          const x=mid+Math.cos(a)*rad, y=mid+Math.sin(a)*rad*(.94+Math.sin(t*.4+layer)*.04);
          i?c.lineTo(x,y):c.moveTo(x,y);
        }
        c.closePath();
        const surface=c.createRadialGradient(mid-r*.6,mid-r*.7,r*.05,mid,mid,r*1.05);
        surface.addColorStop(0,'#f5f5f509');surface.addColorStop(.38,'#a6a7aa03');surface.addColorStop(.78,'#15151701');surface.addColorStop(.93,'#f5f5f509');surface.addColorStop(1,'#f5f5f51b');
        c.fillStyle=surface;c.fill();
        const rim=c.createLinearGradient(mid-r,mid-r,mid+r,mid+r);rim.addColorStop(0,'#f7f7f7b0');rim.addColorStop(.27,'#a6a7aa18');rim.addColorStop(.5,'#f5f5f570');rim.addColorStop(.75,'#a6a7aa10');rim.addColorStop(1,'#f5f5f585');
        c.strokeStyle=rim;c.lineWidth=w/650;c.shadowColor='#f5f5f5';c.shadowBlur=layer===0?10:3;c.stroke();c.shadowBlur=0;
      }
      // Refractive folds are translucent ribbons, not latitude/longitude lines.
      for(let fold=0;fold<5;fold++){
        c.save();c.translate(mid,mid);c.rotate(fold*1.23+Math.sin(t*.35+fold)*.22);
        const sheen=c.createLinearGradient(-r,0,r,0);sheen.addColorStop(0,'#f5f5f500');sheen.addColorStop(.22,'#f5f5f520');sheen.addColorStop(.5,'#f5f5f50b');sheen.addColorStop(.82,'#f5f5f56a');sheen.addColorStop(1,'#f5f5f500');
        c.beginPath();c.moveTo(-r*.88,-r*.3);c.bezierCurveTo(-r*.15,-r*1.18,r*1.02,-r*.68,r*.86,r*.38);c.bezierCurveTo(r*.98,-r*.55,-r*.08,-r*.97,-r*.88,-r*.3);c.fillStyle=sheen;c.fill();c.strokeStyle=sheen;c.lineWidth=w/900;c.stroke();c.restore();
      }
    }
  }
  requestAnimationFrame(draw);
})();
