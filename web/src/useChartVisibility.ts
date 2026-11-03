import {useEffect,useRef,useState} from 'react';

export function useChartVisibility() {
  const ref=useRef(null);
  const [visible,setVisible]=useState(!document.hidden);
  useEffect(()=>{
    let intersecting=true;
    const update=()=>setVisible(intersecting&&!document.hidden);
    const observer=new IntersectionObserver(([entry])=>{
      intersecting=entry.isIntersecting;update();
    },{rootMargin:'100px'});
    observer.observe(ref.current);
    document.addEventListener('visibilitychange',update);
    return()=>{observer.disconnect();document.removeEventListener('visibilitychange',update);};
  },[]);
  return [ref,visible] as const;
}
