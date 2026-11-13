const cyan='#71ead2';
const modes={
  current:{
    title:'三相电流波形', unit:'A', digits:3, seconds:8, minSpan:.2,
    series:[['A 相',cyan],['B 相','#f5bb70'],['C 相','#f087a7']],
    note:'A / C 相为板端采样，B 相由板端重构。图中显示 CAN 采样点。',
    sample:(data,phase)=>phase&&[phase.age,phase.values],
  },
  speed:{
    title:'速度波形', unit:'rpm', digits:1, seconds:20, minSpan:10,
    series:[['机械转速',cyan]],
    sample:(data,phase,age)=>data.encoder_ok&&[age,[data.rpm]],
  },
  position:{
    title:'位置波形', unit:'°', digits:2, seconds:20, minSpan:2,
    series:[['机械位置',cyan]],
    sample:(data,phase,age)=>data.encoder_ok&&data.calibrated&&[age,[data.position_deg]],
  },
};

function axisRange(points, mode){
  let low=Infinity, high=-Infinity;
  for(const {values} of points){
    for(const value of values){
      low=Math.min(low,value);
      high=Math.max(high,value);
    }
  }
  if(!points.length)low=high=0;
  if(mode===modes.current){
    const extent=Math.max(Math.abs(low),Math.abs(high));
    low=-extent;
    high=extent;
  }
  const center=(low+high)/2;
  const span=Math.max(mode.minSpan,(high-low)*1.18);
  const tick=span/4;
  const power=10**Math.floor(Math.log10(tick));
  const step=[1,2,5,10].map(n=>n*power).find(n=>n>=tick);
  return [Math.floor((center-span/2)/step)*step,Math.ceil((center+span/2)/step)*step,step];
}

export function createWaveform(panel){
  const title=panel.querySelector('h2');
  const status=panel.querySelector('#waveform-status');
  const legend=panel.querySelector('#waveform-values');
  const note=panel.querySelector('#waveform-note');
  const canvas=panel.querySelector('canvas');
  const ctx=canvas.getContext('2d');
  let mode, points=[], outputs=[], cursor=null, lastDraw=0, frame=0;

  function select(name){
    const next=modes[name];
    if(next===mode)return;
    mode=next;
    points=[];
    cursor=null;
    panel.hidden=!mode;
    if(!mode){
      cancelAnimationFrame(frame);
      frame=0;
      return;
    }
    title.textContent=mode.title;
    status.textContent='等待采样';
    note.textContent=mode.note??'数据中断处留空，移动指针可查看采样值。';
    const items=mode.series.map(([label,color])=>{
      const item=document.createElement('span');
      const value=document.createElement('output');
      item.style.color=color;
      value.textContent='—';
      item.append(label+' ',value,' '+mode.unit);
      return item;
    });
    legend.replaceChildren(...items);
    outputs=items.map(item=>item.querySelector('output'));
    if(!frame)frame=requestAnimationFrame(animate);
  }

  function record(data,age,phase){
    if(!mode||!data||data.fault)return;
    const sample=mode.sample(data,phase,age);
    if(!sample)return;
    const [sampleAge,values]=sample;
    if(!Number.isFinite(sampleAge)||sampleAge>=.2||!values?.every(Number.isFinite))return;
    const time=performance.now()-sampleAge*1000;
    if(!points.length||time-points.at(-1).time>15)points.push({time,values});
  }

  function animate(now){
    frame=requestAnimationFrame(animate);
    if(now-lastDraw<30)return;
    lastDraw=now;
    const duration=mode.seconds*1000;
    while(points.length&&points[0].time<now-duration)points.shift();
    const width=canvas.clientWidth,height=canvas.clientHeight;
    if(width<120||!height)return;
    const dpr=window.devicePixelRatio||1;
    const pixelWidth=Math.round(width*dpr),pixelHeight=Math.round(height*dpr);
    if(canvas.width!==pixelWidth||canvas.height!==pixelHeight){
      canvas.width=pixelWidth;
      canvas.height=pixelHeight;
    }
    ctx.setTransform(dpr,0,0,dpr,0,0);
    ctx.clearRect(0,0,width,height);
    const left=62,right=width-17,top=18,bottom=height-29;
    const [low,high,step]=axisRange(points,mode);
    const x=time=>right+(time-now)*(right-left)/duration;
    const y=value=>bottom-(value-low)*(bottom-top)/(high-low);
    ctx.font='11px monospace';
    ctx.fillStyle='#92a1a6';
    ctx.lineWidth=1;
    ctx.textBaseline='middle';
    ctx.textAlign='right';
    for(let i=0;i<=Math.round((high-low)/step);i++){
      const value=low+i*step, py=y(value);
      ctx.strokeStyle=Math.abs(value)<step/100?'#536575':'#2d3a47';
      line(left,py,right,py);
      ctx.fillText(Number(value.toFixed(4)).toString(),left-8,py);
    }
    ctx.strokeStyle='#2d3a47';
    for(let i=0;i<=4;i++){
      const px=left+(right-left)*i/4;
      line(px,top,px,bottom);
      ctx.textAlign=i===4?'right':i===0?'left':'center';
      ctx.fillText(i===4?'现在':`−${mode.seconds*(4-i)/4} s`,px,bottom+16);
    }
    const newest=points.at(-1);
    const live=newest&&now-newest.time<350;
    let marked=live?newest:null;
    if(cursor!==null&&points.length&&cursor>=left&&cursor<=right){
      const wanted=now+(cursor-right)*duration/(right-left);
      if(wanted>=points[0].time&&wanted<=newest.time){
        marked=points.reduce((nearest,p)=>Math.abs(p.time-wanted)<Math.abs(nearest.time-wanted)?p:nearest);
      }
    }
    status.textContent=marked&&marked!==newest
      ? `${((now-marked.time)/1000).toFixed(2)} 秒前 · 自动量程`
      : live?'实时 · 自动量程':'等待采样 · 自动量程';
    if(marked){
      ctx.strokeStyle='#6f8594';
      line(x(marked.time),top,x(marked.time),bottom);
    }
    for(const [index,[,color]] of mode.series.entries()){
      ctx.strokeStyle=color;
      ctx.lineWidth=1.6;
      ctx.beginPath();
      let previous=null;
      for(const point of points){
        const value=point.values[index];
        const wrap=previous&&mode===modes.position&&Math.abs(value-previous.values[index])>180;
        if(previous&&point.time-previous.time<350&&!wrap)ctx.lineTo(x(point.time),y(value));
        else ctx.moveTo(x(point.time),y(value));
        previous=point;
      }
      ctx.stroke();
      outputs[index].textContent=marked?marked.values[index].toFixed(mode.digits):'—';
      if(marked){
        ctx.fillStyle=color;
        ctx.beginPath();
        ctx.arc(x(marked.time),y(marked.values[index]),3.5,0,Math.PI*2);
        ctx.fill();
      }
    }
  }

  function line(x1,y1,x2,y2){
    ctx.beginPath();
    ctx.moveTo(x1,y1);
    ctx.lineTo(x2,y2);
    ctx.stroke();
  }
  canvas.addEventListener('pointermove',event=>{cursor=event.offsetX;});
  canvas.addEventListener('pointerleave',()=>{cursor=null;});
  return {select,record};
}
