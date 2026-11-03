const navigation = document.querySelector<HTMLElement>('.apps')!;
try {
  const response = await fetch('./catalog.json', {cache: 'no-store'});
  if (!response.ok) throw new Error('APP 清单加载失败');
  const catalog = await response.json();
  const icons: {id: string; element: HTMLElement}[] = [];
  for (const app of catalog.apps) {
    const link = document.createElement('a');
    link.className = 'app';
    link.href = `./apps/${app.id}/?v=${encodeURIComponent(app.browserVersion || app.version)}`;
    link.setAttribute('aria-label', `打开${app.name}`);
    const icon = document.createElement('span');
    icon.className = `app-icon ${app.theme}`;
    const image = document.createElement('img');
    image.src = `./icons/${app.id}.svg?v=__ICON_VERSION__`;
    image.alt = '';
    image.width = image.height = 240;
    icon.append(image);
    const name = document.createElement('span');
    name.className = 'app-name';
    name.textContent = app.name;
    link.append(icon, name);
    navigation.append(link);
    if (app.iconMotion !== false) icons.push({id: app.id, element: icon});
  }
  import('./icon-motion.js?v=__ICON_VERSION__').then(module => module.animateIcons(icons)).catch(error => {
    console.warn('动态图标未启用，保留像素图片：', error);
  });
} catch (error) {
  navigation.textContent = error.message;
}

export {};
