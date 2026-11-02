# 总网关像素动态图标

三个头像保留像素风格，默认完全静止。仅在鼠标悬停到对应 APP 时，使用 Three.js 转动其内部转子或输出轴，外壳和相机始终固定；电机图案放大 8%，底色框保持原尺寸、位置。测功机保留散热筋、底座和左侧转轴；SG-6010HB 保留圆形转子、法兰和右侧转轴；7010 保留薄型环状壳体与中心通孔。底色、名称、LOGO 和 APP 入口不变。

建模参考 [img2threejs](https://github.com/img2threejs/img2threejs) 的图像分层观察、重复结构和程序化几何方法（参考版本 `3e7099e1b7fe93e187e06db4bdbe1e1906c21a28`，Apache-2.0）。该项目是重建技能和工具流程，并非一键转换图片的运行时库；浏览器动态由 Three.js `0.187.0` 实现。本项目使用自编模型与动画，不宣称完成上游完整动画、物理资产验收。参考图为已有的小尺寸插画，背面结构为风格化推定，不能作为对应产品的精确 CAD。

## 像素效果与资源使用

每个头像都以真实 **240×240 绘制缓冲区**输出，不使用低分辨率画面放大冒充 240×240。使用分段色阶、轮廓描边、有序抖色、关闭抗锯齿和最近邻显示，保留像素边缘。

三个头像共用一个 240×240 WebGL 渲染器，分别拷贝到三个 240×240 Canvas。默认绘制一帧后停止；只在鼠标悬停到某个 APP 时连续绘制该头像，鼠标移开立即停止，并保留转子当前位置。测功机只转输出轴和键槽，SG-6010HB 转内侧转子及输出轴，7010 转内部环形转子；各自外壳、安装底座和外圈螺钉保持不动。演示转速为每秒 4 圈，与真实电机控制无关。连续绘制上限为 24 帧/秒，实际帧率受设备和浏览器调度影响。页面隐藏时停止定时器和动画帧；头像离开视口后不再绘制。系统开启“减少动态效果”时只绘制静止画面。没有持续的阴影贴图、环境反射贴图或后期特效计算。

原来的 240×240 像素 SVG 保留在 `gateway/icons/`，在动态模块加载前显示，或在 WebGL 初始化失败时继续显示。新头像模块和首页资源使用构建内容版本号，避免浏览器沿用之前的静态金属效果。首页只负责图标动画，不改变任何 APP 的测试或通信流程。

测功机与 SG-6010HB 的输出轴使用加宽深色键槽和银色轴向标记，SG 转子增加一处金色标记，便于辨认快速转动；这些是头像演示细节，不表示真实产品的标记或尺寸。鼠标移开后图案恢复原尺寸，转动停止。

## 仓库内重新构建

```sh
npm ci
npm run build
python scripts/build_gateway.py
python scripts/build_gateway_package.py
python scripts/build_gateway_package.py --windows
```

`npm run build` 保留原测功机的构建，并通过 `postbuild` 单独打包总网关图标模块；也可单独运行 `npm run build:gateway-icons`。构建会清理旧 `site/`，之后重新生成两个下载包。日常测试电脑使用已打包网页，不需要安装 Node.js 或 Three.js。

主要文件：

- `gateway/graphics/motor-models.js`：机械外形、像素材质。
- `gateway/graphics/icon-scene.js`：正交相机、光照和轮廓描边。
- `gateway/icon-motion.js`：共享渲染器、绘制节拍、可见性暂停。
- `scripts/build_gateway_icons.mjs`：独立打包到 `.runtime/gateway-icon-bundle/`，随后由 Pages 构建复制到 `site/`。

## 隔离验证

```sh
npx playwright install chromium
npm run test:gateway-icons
```

已安装 Chrome 时可以通过 `CHROME_PATH` 指定浏览器，例如 Ubuntu：

```sh
CHROME_PATH=/usr/bin/google-chrome npm run test:gateway-icons
```

检查三种图案独立、240×240 缓冲区、单一 WebGL 上下文、像素样式、默认静止、仅悬停项转动、外壳和视角像素保持不变、移开停止、帧率上限、隐藏页面及视口外暂停、减少动态效果和窄屏显示。截图保存在 `.runtime/img2threejs-icons/pixel-motion/`。验证使用隔离静态服务，不连接本机通信桥或真实设备。
