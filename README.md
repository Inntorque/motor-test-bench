# Inntorque 测功机本地上位机

本目录只维护 Electron 桌面上位机。程序使用随包提供的本地界面和设备通信 Worker，离线运行，不启动网页服务器或总网关。界面、串口、USB-CAN、电源控制、试验记录及特性图均在本机运行。

## 打开软件

Windows x64 / Ubuntu x64 便携包：解压后使用根目录 **测功机.exe / 测功机**。不需要安装 Git、Node.js、Python 或浏览器；Ubuntu 首次按包内脚本配置桌面和设备访问环境。软件内“帮助 → 检查更新”从同一仓库的正式 Releases 获取签名更新包。

部署、数据迁移及回退说明见 [双端部署文档](docs/portable-deployment.md)。现有 Ubuntu 安装入口仍可使用：`~/.local/opt/inntorque-bench/启动测功机.sh`，原桌面版配置在 `~/.config/InntorqueBench/`，便携启动器使用安装目录内的 `config/`。

在“设备设置”中连接传感器、两路电源和驱动板。开启电机电源后等待驱动板回传并完成连接，再使用右侧控制台；接口连接本身不启动输出。电机支持测功模式和电流环模式。

## 数据与特性图

- 数据自动保存到软件安装目录的 `测功数据/`，左上角“数据目录”可打开。每次启动建立独立记录，包含原始时间线、测量表、特性点及报告；退出前会等待已接收数据保存完成。
- 实时读数持续更新；特性图只在开始力矩加载后采集。正常加载按固定 2 秒周期执行，特性图横轴采用制动输出设定扭矩，每个完成的档位在特性表中只占一行。每次有有效加载点的试验结束后，桌面版会在本次采集目录自动保存一张特性图 SVG。
- 加载中保持电机输出，转速归零后保持当前制动，传感器与编码器连续确认静止 5 秒才记录堵转终点、停止输出并保留特性图。中途恢复转动则重置确认。人工停止或断电后的零速不作为最大扭矩终点。
- “最大扭矩”为本次工况下轴端实测堵转扭矩。达到标定范围或试验设定上限结束，并不等于测到了电机极限。
- 本次整理保留了旧工程的历史测功数据，位于上一级 `历史测功数据/`；当前软件数据入口为上一级 `测功数据`。

## 源码目录

| 目录 | 内容 |
| --- | --- |
| `desktop/` | 桌面窗口、本地文件保存、签名更新服务、Go 启动器 |
| `web/` | 桌面界面源码，由 Electron 加载 |
| `browser/` | 本机通信 Worker、设备协议和测功流程 |
| `protocol/`、`profiles/` | 通信定义、验证数据与设备配置 |
| `scripts/` | 构建、签名打包、协议校验与上传内容检查 |
| `tests/` | 上位机自动化测试和虚拟设备 |
| `site/` | 构建后的本地运行资源 |
| `artifacts/` | 桌面应用打包输出 |

`web` 和 `browser` 是桌面应用内部模块名称，不是独立网站或后台服务。Git 历史保留；旧网关、预览工程、旧说明和历史构建备份已从工作目录移至系统回收站。

## 构建、运行和验证

开发需要 Node.js 22.18 或更高版本及 npm；运行安装好的软件无需另装 Node.js、Python 或浏览器。

```sh
npm ci
npm run desktop:setup
npm run build
npm start
```

生成独立桌面应用：

```sh
npm run desktop:build
```

输出位于 `artifacts/InntorqueBench-linux-x64/`。本机安装使用 `npm run desktop:install`，更新前先关闭旧软件；安装保留测功数据与用户配置。Linux 安装脚本需已有 sudo 授权，用于配置 Chromium 沙箱权限。这是开发用单版本目录；可在线更新的正式分发包使用下方发布流程。

```sh
npm test
npm run test:ui
npm run test:desktop
```

桌面测试使用隔离配置与虚拟设备，不开启真实电机或制动器。`.runtime/` 为可重新生成的测试输出。软件测试不代替真实测功台验收。

## 双端版本与统一接入

- [仓库与发布管理](docs/repository-and-release.md)：源码、发行包、签名密钥分别管理；推送和发布均需明确许可。
- [统一驱动板协议](protocol/README.md)：MBP/1 线协议、能力、时序、单位、会话和停止约定。
- [驱动板接入验收](protocol/ACCEPTANCE.md)：新驱动固件需逐项通过，不能仅凭能连接就宣称兼容。
- [1.2.2 更新说明](docs/releases/1.2.2.md)：当前本地候选版。

规范、应用分别版本化。既有 5014 原生控制继续保留；第三方驱动通过 MBP/1 按能力接入电流环或测功模式。C11 设备 SDK 提供协议栈、板级回调及虚拟接入示例，C99 库提供独立编解码。

本地发布构建额外需要 Go 1.27.4；协议 C SDK 校验需要支持 C99 和 C11 的编译器。以下命令只生成本地文件，不上传：

```sh
npm run protocol:check
go -C desktop/launcher test ./...
npm run review:upload
# INNTORQUE_SIGNING_KEY_FILE 指向仓库外私钥；GO_BINARY 可指定 Go 路径。
BENCH_PLATFORM=linux npm run release:local
BENCH_PLATFORM=win32 npm run release:local
```

输出统一放在 `artifacts/releases/v<版本>/`。客户端只读取经过人工正式发布的 Releases，不执行 git pull、不拉取开发源码、不安装开发依赖。加载采集与发布格式见 [本版说明](docs/releases/1.2.2.md)。
