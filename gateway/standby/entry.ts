export function installStandby() {
  const enter = document.querySelector<HTMLButtonElement>('#enter-standby')!;
  const leave = document.querySelector<HTMLButtonElement>('#leave-standby')!;
  const dialog = document.querySelector<HTMLDialogElement>('#cosmic-standby')!;
  const message = dialog.querySelector<HTMLElement>('[role=status]')!;
  let generation = 0,dispose: (() => void) | undefined;

  function present(open: boolean) {
    document.documentElement.toggleAttribute('data-cosmic-standby',open);
    document.dispatchEvent(new Event('cosmic-standby-change'));
  }
  function release() {
    generation++;dispose?.();dispose = undefined;
    present(false);enter.focus({preventScroll:true});
  }
  function close() {dialog.close();release();}
  enter.addEventListener('click',async () => {
    if (dialog.open) return;
    const current = ++generation;
    message.textContent = '';
    dialog.showModal();present(true);leave.focus({preventScroll:true});
    try {
      const {createUniverse} = await import('./universe.ts');
      if (current !== generation || !dialog.open) return;
      const previous = dialog.querySelector<HTMLCanvasElement>('canvas')!;
      const canvas = previous.cloneNode(false) as HTMLCanvasElement;previous.replaceWith(canvas);
      dispose = createUniverse(canvas);
    } catch (error) {
      if (current === generation && dialog.open) message.textContent = '当前浏览器暂时无法显示粒子场，可返回总网关。';
      console.error('宇宙待机效果未能启动：',error);
    }
  });
  leave.addEventListener('click',close);
  dialog.addEventListener('keydown',event => {if (event.key === 'Escape') event.stopPropagation();});
  dialog.addEventListener('cancel',event => {event.preventDefault();close();});
  dialog.addEventListener('close',() => {
    if (!dialog.open && document.documentElement.hasAttribute('data-cosmic-standby')) release();
  });
}
