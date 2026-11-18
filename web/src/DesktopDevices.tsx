import React, {useEffect, useRef} from 'react';

export default function DesktopDevices({open, onClose, children}: {
  open: boolean; onClose: () => void; children: React.ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const interfaces = useRef<HTMLDivElement>(null);
  const visibleContent = useRef(children);
  if (open) visibleContent.current = children;
  useEffect(() => {
    const bar = document.getElementById('inntorque-browser-access');
    if (!bar) return;
    interfaces.current.append(bar);
    return () => { document.body.prepend(bar); };
  }, []);
  useEffect(() => {
    if (open) dialog.current.showModal();
    else dialog.current.close();
  }, [open]);
  return <dialog ref={dialog} className="desktop-devices" aria-labelledby="desktop-devices-title"
    onCancel={onClose} onClick={event => {if (event.target === event.currentTarget) onClose();}}>
    <header className="desktop-device-heading">
      <div><h2 id="desktop-devices-title">设备设置</h2><p>接口连接、电源输出与传感器参数</p></div>
      <button className="button" onClick={onClose} aria-label="关闭设备设置">完成</button>
    </header>
    <div className="desktop-device-body">
      <div className="desktop-interfaces" ref={interfaces} />
      {visibleContent.current}
    </div>
  </dialog>;
}
