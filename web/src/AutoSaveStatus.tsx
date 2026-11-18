import React, {useEffect, useState} from 'react';

export default function AutoSaveStatus({onOpen}: {onOpen: () => void}) {
  const [directory,setDirectory] = useState('');
  const [error,setError] = useState('');
  const [ready,setReady] = useState(false);
  useEffect(() => {
    let active = true, pending = false;
    window.inntorqueDesktop.dataDirectory().then(path=>{if(active)setDirectory(path);}).catch(error=>{if(active)setError(error.message);});
    const refresh = async () => {
      if (pending) return;
      pending = true;
      try {
        const response = await fetch('/api/desktop/autosave');
        const status = await response.json();
        if (!response.ok) throw new Error(status.error || '无法读取自动保存状态');
        if (active) {setReady(Boolean(status?.running));setError(status?.error || '');}
      } catch (error) {if(active)setError(error.message);}
      finally {pending = false;}
    };
    void refresh(); const timer = setInterval(refresh,1000);
    return () => {active=false;clearInterval(timer);};
  },[]);
  const statusText=error?'自动保存失败':ready?'自动保存已开启':'正在准备自动保存';
  return <div className={`auto-save-status ${error?'save-failed':ready?'save-ready':''}`} aria-label="自动保存状态">
    <button className="button desktop-data-folder" onClick={onOpen} aria-label="打开数据目录" title={`${statusText}\n${directory}`}>
      <span className="auto-save-indicator" role="img" aria-label={statusText}/>
      数据目录
    </button>
    {error&&<div className="auto-save-error" role="alert">自动保存失败：{error}</div>}
  </div>;
}
