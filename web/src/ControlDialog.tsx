import React, {useId, useRef, useState} from 'react';
import {createPortal} from 'react-dom';

export default function ControlDialog({label, children}: {label:string; children:React.ReactNode | (()=>React.ReactNode)}) {
  const dialog = useRef<HTMLDialogElement>(null), titleId = useId();
  const [open,setOpen]=useState(false);
  return <>
    <button className="button small control-dialog-trigger" onClick={()=>{setOpen(true);dialog.current.showModal();}}>{label}</button>
    {createPortal(<dialog ref={dialog} className="control-dialog" aria-labelledby={titleId} onClose={()=>setOpen(false)}
      onClick={event=>{if(event.target===event.currentTarget)dialog.current.close();}}>
      <header><h2 id={titleId}>{label}</h2><button className="button" onClick={()=>dialog.current.close()}>关闭</button></header>
      <div className="control-dialog-body">{typeof children==='function' ? open && children() : children}</div>
    </dialog>,document.body)}
  </>;
}
