# Inntorque 测试总网关与本机服务

## 网页入口

目标地址保持 `https://inntorque.github.io/motor-test-bench/`，首页改为总网关，包含测功机、SG-6010HB、7010 上位机。LOGO 宽 264 px；三个图标保留像素风格，使用 Three.js 代码模型进行 240×240 动态绘制，尺寸为初版的 1.1 倍。首页不添加控制面板、侧栏或状态卡片。图标生成和维护见 [APP 图标说明](gateway-icons.md)。

GitHub Pages 只分发网页和下载包，不能代替测试电脑上的 USB-CAN / RS485 服务。每台测试电脑先安装并启动一次本机服务，再从总网关选择 APP。APP 的页面在这台电脑的浏览器内打开，控制、心跳、SSE、导出、记录仍走原本的本机同源接口；原始数据保存在测试电脑，测试过程不依赖云端持续连接。

Chrome / Edge 首次可能询问本站的本地网络或回环网络访问权限，应允许本站访问本机服务。不需要关闭浏览器安全选项。参考 [Chrome 本地网络访问说明](https://developer.chrome.com/docs/modern-web-guidance/guides/security/local-network-access)。

## 测试电脑安装

Windows 10/11 x64 下载 `downloads/Inntorque-Local-Services-Windows-x64.zip`，完整解压，双击 `启动测试总网关-Windows.bat`。包内包含独立 Python 和 ZQWL / SLCAN 通信依赖，不需要 Node.js、Git 或额外安装 Python。USB-CAN、串口驱动按实际适配器安装；gs_usb / WinUSB 依赖与驱动需另行核对，不能将适配器打开当成电机已应答。

Ubuntu 下载 `downloads/Inntorque-Local-Services-Source.zip`，完整解压，运行 `启动测试总网关-Ubuntu.sh`。首次创建本目录 `.venv` 并安装固定版本通信依赖，需要系统 Python 3.10+、venv 和联网；后续直接使用本目录环境。关闭使用对应的 `关闭测试总网关` 脚本，会先走原上位机停机确认，不强杀未知进程。

本机服务只监听 `127.0.0.1:5181`，本身不连接设备。选择 APP 后才启动其控制服务：测功机 5173、SG-6010HB 8892、7010 8888。只运行一个 APP，避免同一 USB-CAN 被多个工程争用。切换或更新前先正常关闭当前 APP；也可在 `http://127.0.0.1:5181/` 点击确认关闭。重复打开同一版本复用原服务；发现其他目录或其他程序占用端口时拒绝接管。

测功机沿用 `workbench.local.json`，其“一键连接全部接口”、测试配置、SDK 和 MBP/1 协议不变。SG 与 7010 可复制 `gateway.local.example.json` 为 `gateway.local.json` 后填写实际串口、接口、波特率及节点；不填则沿用原工程自动发现。示例 COM4 / COM5 不是测试电脑的默认实物接线。

两个独立电机 APP 保持原来的控制协议；必须匹配其原本支持的驱动板固件。特别是 SG 原网页仍使用原私有接口，不能据网页打开推断它能控制仅接受 MBP/1 的新固件。测功机的 MBP/1 SDK、固件 A/B、保护阈值、电流环和电源参数没有因总网关改变。

本机网页包不含 MCU 工具链、Bootloader、固件镜像和完整固件构建工程；原页面的编译/OTA 维护入口仍需对应完整工程，不能用本包完成固件编译或据此宣称 OTA 验收。此轮只验收上位机页面、服务与离线通信链路，不操作真实硬件。

## 分别维护 APP

`gateway/apps.json` 是入口清单，管理 ID、名称、图标配色、端口和宿主接口版本。图标为 `gateway/icons/<id>.svg`。移除清单项后重新构建，总网关不再展示该 APP；原控制源码和测试数据不随入口删除。新增 APP 还需提供对应网页、控制服务和图标，不能只添加一个空链接。

测功机网页源码继续在 `web/`。两个电机网页在 `gateway/ui/<id>/index.html`；其控制脚本在 `gateway/hosts/<id>/scripts/`。网页与控制脚本从原工程只读导入，HTML 提取前后通过 AST 比较，控制逻辑不变。`source-manifest.json` 记录原文件 SHA-256。更新原工程版本后显式重新导入：

```sh
python scripts/import_gateway_hosts.py --sg-root /实际/SG工程 --rm-root /实际/7010工程
```

导入器不写原工程，不复制原工程运行日志、保存参数、依赖环境或固件。每个 APP 独立生成内容版本与 ZIP 校验值；只有网页改变、控制脚本未变时，本机服务重新打开该 APP 即按网上清单下载并校验新版网页。已运行 APP 保留当前版本，更新不会打断测试；每次打开都会重新校验本机缓存。

控制脚本变更时，本机运行包需要一起更新。网页清单包含控制程序指纹和 `hostApi`，不匹配时明确要求更新本机运行包，不尝试兼容未知控制接口。更新运行包前正常关闭服务，然后覆盖程序文件；保留 `recordings/`、`runtime/gateway/apps/`、`workbench.local.json`、`gateway.local.json`，这些目录分别保存测试记录、独立电机记录/档案与本机设置。

## 本地构建与验证

```sh
npm ci
npm run build
python scripts/build_gateway.py
python scripts/build_gateway_package.py
python scripts/build_gateway_package.py --windows
```

`dist/` 继续是原测功机页面，原桌面工作台入口不变。`site/` 为最终 Pages 内容，含总网关、三个启动页、独立网页 ZIP 和本机服务下载包。构建会清理自己识别的旧 `site/`，避免删除 APP 后残留旧启动页；拒绝覆盖无法识别的目录。`site/` 和下载产物不提交，由 Actions 从源码生成。

校验不连接真实设备。`--offline` 仅用于隔离验证，会阻止两个独立电机控制服务打开 CAN；不能作为现场运行配置。实际 Windows USB/CAN、Chrome 首次权限、固件匹配与带电运行仍须在测试电脑验收。

## 一次推送的部署顺序

Pages 来源使用 Actions。`motor-test-bench` 保留为开发分支，已校验版本合入同一仓库的 `main`；发布工作流只监听 `main`，依次执行构建/软件检查、Windows 运行包构建、Pages 内容装配与部署。仍更新原地址 `https://inntorque.github.io/motor-test-bench/`，首页为总网关，原工作台从“测功机”APP 进入。

`github-pages` 环境沿用已有的 `main` 部署许可，不需要放行开发分支或新建环境。开发分支推送不触发正式 Pages 部署；正式发布由 `main` 的构建结果生成，不能把工作目录的缓存产物直接当成线上版本。参见 [GitHub Pages 自定义工作流](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)。

校验通过后同步开发分支和 `main`，等待 `main` 的 Actions 与 Pages 部署成功，再在测试电脑下载对应运行包、授予本机网络权限并实际连接验证。校验通过降低部署失败概率，不保证外部网络、GitHub 服务、驱动或实物通信必然成功。需要回退时，重新部署上一版 Pages 工作流产物并使用匹配的本机运行包；不覆盖或删除测试记录。
