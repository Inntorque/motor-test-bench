# Inntorque 测试总网关与浏览器直连

## 网页入口与使用

打开 [Inntorque 测试总网关](https://inntorque.github.io/motor-test-bench/)，选择测功机、SG-6010HB 或 7010 上位机。三个 APP 的完整页面、控制执行器和设备通信代码均从 GitHub Pages 加载，在浏览器内运行。测试电脑不安装 Python、Node.js 或运行包，不启动本机 HTTP 服务，不请求 5181、5173、8892、8888 等回环端口。旧桌面启动脚本不再是公网网页的运行条件。

使用桌面版 Chrome 或 Edge，通过 HTTPS 打开。点击顶部“授权串口”，在浏览器弹窗选择 USB-CAN 串口或 USB-RS485；多台设备需分别授权。gs_usb 类适配器使用“授权 USB-CAN”。授权只取得访问许可，不打开设备、不发送配置、不启动输出。浏览器要求用户点击授权，网页无法跳过这一步。参见 [Web Serial 官方说明](https://developer.chrome.com/docs/capabilities/serial)和 [WebUSB 官方说明](https://developer.chrome.com/docs/capabilities/usb)。

授权后，在现有连接配置中选择对应接口，再连接验证。CAN 适配器及节点在顶部选择，CAN 波特率沿用 500 kbit/s；测功机默认 MBP 节点 1，两个独立电机 APP 默认原协议节点 0。不要将同型号的两个电源串口互换；浏览器不会根据相同 VID/PID 猜测供电对象，刷新后需要重新选择授权接口。

测功机的一键连接、两阶段加载、最大输出、专项测试、电机/驱动板名称和裸机/减速箱配置保持原界面和控制含义。万瑞达电机电源连接只读，开启前核对面板 24 V / 20 A 预设；制动电源保留原 SSJ 缩放和限幅。传感器保留 Modbus RTU 与 HEX6/HEX8 只读采集。浏览器不能访问任意原始 TCP 端口，本版本使用 USB-RS485，不提供旧 RTU 透明 TCP 网关选项。

## 适配器与权限

支持 ZQWL USB 串口 CAN（串口 3 Mbit/s）、CANable2 的 SLCAN 串口固件（VID:PID `16d0:117e`、串口 115200 / 8N1）和 gs_usb USB-CAN，均发送原有 Classic CAN 帧。CANable2 与 ZQWL 均点击“授权串口”，CANable2 识别后自动走 SLCAN，不使用 gs_usb 的 USB 筛选。CAN 总线仍为 500 kbit/s；串口数字不代表 CAN 波特率。CANable2 原固件没有配置 ACK，软件读取适配器版本后配置通道，再以板端真实应答判断设备连接，不能将串口打开当成板端连接成功。命令依据 [CANable2 原固件说明](https://github.com/normaldotcom/canable2-fw#supported-commands)。浏览器直连并不免除操作系统设备驱动、串口权限或 USB 接口权限：Ubuntu 用户仍需具备串口/USB 访问权限；Windows 的原生 gs_usb 接口需可由 WebUSB/WinUSB 访问。系统已占用接口、其他 APP 已打开设备或接口驱动不匹配时，浏览器会返回实际打开错误，不接管其他程序。

三个 APP 不自动打开设备。切换 APP 前，先停止、确认设备输出关闭并断开当前连接。两个独立电机 APP 的“断开设备”会先发送原 STOP、读取最新状态确认，再释放 USB-CAN；无需关闭任何本机服务。某些旧固件只支持独立电机 APP 的原私有协议，不能把网页打开当成 MBP/1 固件兼容。测功机继续依据 MBP/1 能力声明接入。

## 记录与页面生命周期

测试配置、对象名称和新记录存入当前浏览器、当前站点的 IndexedDB。测功机保留 CSV、JSONL 和专项报告导出；独立电机 APP 自动保存运行状态、原始 CAN 帧、参数/目标操作，并在顶部选择记录导出 CSV/JSONL。SG 最大输出停止后读取原固件冻结轨迹，可单独导出 CSV；JSONL 同时包含已读取轨迹及完整性状态。有限的冻结轨迹仍不代表全程 2 kHz 原始流。

不同电脑、不同浏览器和无痕窗口的数据相互独立。清除网站数据会删除这些浏览器记录，重要结果应导出保存。原 Python 版本的磁盘记录不会删除，但浏览器不能无授权读取旧 `recordings/`、原串口路径或本机 JSON 配置，也不会自动导入它们。

正常停止沿用原有命令取消代际、关闭读回、最新状态确认及电源断电兜底。开启输出前写入状态日志；重新加载后若上次关闭未确认，会要求重新连接原设备并停止核对，不伪造关闭成功。设备声明的保护、最大输出分配与 SDK 心跳没有修改，诊断质量标志不额外变成停机条件。

纯网页的执行生命周期与标签页一致。测试时保持页面打开，不刷新、不切换 APP、不让电脑休眠；正常退出前先点击停止并确认。浏览器崩溃、强制关闭或系统休眠时，网页不能保证继续发送控制或完成 RS485 电源 OFF；电机驱动固件已有的失联停机机制仍由板端执行，不能据此承诺程控电源自动断电。网页更新不会重载正在运行的标签页，下一次正常打开才加载新版。

## 固件和维护边界

`protocol/` 的 MBP/1 定义、`motor_sdk/`、板端固件及原模板工程不因本次迁移改变。浏览器执行器在 `browser/`，把原主机端通信及测试流程移植到 JavaScript；它不向电机固件加入新的协议、阈值或弱磁参数。

原独立电机页面的“一键编译并 CAN-OTA”依赖完整本机工程和 MCU 编译工具链，纯网页不执行该工具链。本版本将该入口标为独立维护并禁用，未实现网页编译或固件上传。需要维护固件时，先结束测试，再使用现有独立维护工程；不将设备控制成功误报成固件升级验收。

`gateway/apps.json` 管理入口 ID、名称和配色。测功机网页在 `web/`，两个独立电机网页快照在 `gateway/ui/<id>/`；原工程只读导入来源与 SHA-256 留在 `source-manifest.json`。`gateway/hosts/` 与 `workbench/` 中的 Python 源码保留作旧工具和回归参照，不作为公网 APP 的运行服务。构建时仅提取原描述符，不打开 CAN/串口。维护 APP 时分别修改对应页面与 `browser/` 执行器，SDK 与冻结协议边界保持不变。

## 构建、校验和部署

以下命令只由维护/CI 电脑执行，测试电脑不需要：

```sh
python -m pip install -r requirements-motor.txt
npm ci
npm run build
python scripts/build_gateway.py
npm test
npm run test:browser
npm run test:gateway-icons
python -m unittest discover -s tests -p 'test_*.py'
```

`dist/` 是测功机基础网页构建；`site/` 才是公网交付内容，包含总网关、三个完整 APP 和浏览器执行器。构建时注入独立运行入口与内容版本，清理已识别的旧输出目录，不包含本机服务安装包。旧网页 ZIP 作为原界面回归参照保留，不是运行依赖。`.runtime/`、`site/`、设备配置及现场数据均不提交。

`motor-test-bench` 为开发分支，已校验版本同步到同一仓库 `main`；Pages 工作流只监听 `main`，运行构建、协议/控制/记录测试和隔离浏览器测试，再发布原网址。移除原 Windows 本机服务打包前置条件，避免网页发布继续依赖已取消的运行包。已有 Windows 历史工具发布流程不由 Pages 触发。

软件测试使用虚拟设备，覆盖原 SDK 金向量、分片、完整采集、最大输出、停止、记录和无本机服务打开三个 APP。不能据此宣称 Ubuntu/Windows 实物 USB-CAN、RS485、电机固件和全程负载测试已经验收。正式发布后须在测试电脑授权实际设备并验证。
