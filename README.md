# 电机与减速箱加载试验台网页上位机

仓库：[Inntorque/motor-test-bench](https://github.com/Inntorque/motor-test-bench)。机械链为 **电机 → 减速箱 → 动态扭矩传感器 → 磁粉制动器**。两台程控电源分别给驱动板和制动线圈供电，传感器测量减速箱输出端扭矩。网页通过本机通信桥访问真实设备，不生成模拟连接或试验读数。

## 当前功能

- **独立设备通道**：电机电源默认 RU 系列 Modbus RTU，可切换 SSJ；制动电源使用 SSJ。支持串口 RS485 及 RTU 透明 TCP 网关，分别保存已验证的连接配置。
- **两阶段力矩测试**：①空载启动，制动保持关闭；②操作员独立确认后逐级加力矩。默认最大输出模式动态调整 Iq，也可选择固定 10 A 对照。最大输出要求配套固件 **0x0A3C 或更新版本**，旧服务或旧固件不会被当作支持新模式。
- **统一停止与状态确认**：控制所有者、请求代际、心跳、设备保护和关闭读回共同管理输出；编码器与传感器联合确认停转，传感器单独报零不会结束加载。
- **曲线与完整记录**：保留真实加载轨迹和关闭输出前的实测终点，支持六轴特性图、编码器/传感器转速对照、CSV/JSONL/SVG 导出。最大输出测试自动关联完整记录，已有手动记录不会被擅自结束。
- **跨平台运行**：Ubuntu / Windows 共用页面与 Python 服务，Windows 提供独立运行环境和局域网访问部署流程。仓库内的 `vendor/xige_foc` 包含配套主机服务，不包含 MCU 固件或现场数据。

电机电源开启前只读核对面板已有 **24 V / 20 A** 预设，不代写电机电源设定。制动侧手动设置范围为 **0–2.3 A / 24 V**，联动力矩测试沿用 **小于 2 A（1.999 A）** 的运行边界。设备直流电流与 Iq 是不同测量通道；设置值、实际值及最大输出诊断分别显示。

连接、刷新和恢复通信不会自动开启输出。设备 OVP/OCP 设置保持原值；电压饱和、正常跟踪波动和新增诊断质量标记不增加停机条件。测试结果是否达到物理极限仍需结合采样质量、跟踪误差、供电实测和独立标定判断。

## 使用说明

- [电机电流与最大输出接口](docs/motor-current.md)
- [两阶段力矩加载、停转判定与诊断记录](docs/coupled-current.md)
- [RU 电源协议、预设及限幅](docs/ru-power-supply.md)
- [首页完整参数记录](docs/workbench-recordings.md)
- [自动测试](docs/automatic-tests.md)与[被动专项测量](docs/specialized-tests.md)

## 启动

Windows / Ubuntu 共用同一套浏览器页面和本机服务。日常使用只运行根目录对应系统的“启动工作台”脚本，或执行 `npm start`，然后在浏览器打开 `http://127.0.0.1:5173/`。入口自动管理网页、双电源/传感器通信桥及电机 CAN 服务，无需分别开终端。

正式运行使用 `dist/` 静态页面，不运行 Vite 开发监听器。Python 网页服务统一转发 `/api` 到本机通信桥；所有服务只监听本机。浏览器不直接占用串口或 CAN，设备操作及完整记录仍在原后端执行。启动服务不会开启设备输出。

首次构建安装 Node.js 22.12+、Python 3.10+：

```sh
npm ci
npm run build
```

Ubuntu 安装本机通信依赖：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-motor.txt
bash 启动工作台-Ubuntu.sh
```

Windows 安装本机通信依赖：

```bat
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-motor.txt
启动工作台-Windows.bat
```

部署时携带构建好的 `dist/`、本工程 Python 文件、`scripts/` 及 `vendor/xige_foc` 主机通信模块；两种系统的 `.venv` 和驱动必须分别安装，不能相互复制。有 `dist/` 时，一键脚本日常启动只需要 Python 运行环境；修改网页后重新 `npm run build`。仍可用 `npm run hardware` 和 `npm run dev` 分别启动开发服务。启动入口会复用已运行且身份匹配的本机服务，不强行替换现场服务。

电源面板模式需设为 485 / Modbus RTU，寄存器地址与 `SSJ_Control` 工作版本相同。设定值和额定范围由操作者按具体电源面板及铭牌核对。断开通讯不会关闭电源输出；停止供电需在连接状态下点击 OFF 并确认实际状态。

传感器面板选择独立 USB-RS485 串口，默认 19200 bps、地址 1、8N2；因手册还把默认值写为 8N1，连接前可按传感器参数页改选停止位。桥接程序只读核对 Modbus 模式、停止位、小数位、扭矩与转速，不会远程清零或改写配置。计算轴功率用 `|T×n|/9550` kW，界面及 CSV 均标明为计算量。现场须以 OLED/已知负载核对实际型号、量程与缩放。

## 构建与验证

```sh
npm run build
npm test
python3 -m unittest discover -s tests -p 'test_*.py'
npm run test:maximum-ui
```

最大输出页面测试在临时静态服务中运行，全部 API 使用替身，不访问硬件桥。其他页面冒烟用例需要独立的离线测试服务。可先安装 Playwright Chromium：

```sh
npx playwright install chromium
npm run test:ui
npm run test:requirements
```

测试不会连接真实设备或发送输出命令；Python 协议用例使用假设备验证双电源隔离、传感器有符号读数与缩放、CRC 失败保持断连，以及传感器通道只发读取命令。构建产物 `dist/` 是静态网页；发布到 GitHub Pages 本身不会包含本机硬件桥。

## 设备与设计资料

- [原始手册](EVG-D200使用手册印刷V3.6版本中性.pdf)
- [设备与通信笔记](docs/device-and-protocol.md)：传感器协议、寄存器和实机验证项。
- [试验台设备与接入方案](docs/test-bench.md)：50/100 N·m 设备关系、CAN/RS485 通道与待确认事项。
- [CAN 接口约定](docs/can-interface-contract.md)：LC_CANABLE_V2.1 适配器层和驱动板协议边界。
- [需求基线与状态](docs/requirements-review.md)：已确认设备关系和未完成的实机验收项目。

每台设备的串口、地址、网关透明模式、额定值及线圈允许范围均须现场核对。离线用例和部署流程检查不能替代具体设备的读写、时序及输出验收。

## Ubuntu / Windows 一键启动与关闭

工程根目录提供 `启动工作台-Ubuntu.sh`、`关闭工作台-Ubuntu.sh`、`启动工作台-Windows.bat`、`关闭工作台-Windows.bat`。

依赖安装与构建见上面的“启动”。Windows 需要适配器驱动，并在页面选择实际 COM 串口；Ubuntu 需要当前用户具备串口访问权限，页面选择实际 `/dev/ttyUSB*` 或 `/dev/ttyACM*`。串口选择仍在现有界面完成。

电机服务复用已经调通的 `xige_foc` 主机通信模块。`vendor/xige_foc` 已包含所需主机源码，不包含固件、调试工具和现场日志；也可继续使用原独立参考工程。优先读取环境变量 `MOTORBENCH_XIGE_ROOT`，其次读取工程根目录 `workbench.local.json` 的 `xigeRoot`；未配置时优先查找相邻的 `xige_foc` / `xige_foc/xige_foc` 及用户桌面同名目录，最后使用 `vendor/xige_foc`，不再写死用户名和系统路径。

可复制 `workbench.example.json` 为 `workbench.local.json`，配置参考工程路径（相对本工程根目录，或绝对路径）和 CAN 适配器：

```json
{
  "xigeRoot": "../xige_foc",
  "motor": { "interface": "zqwl", "baud": 500000, "node": 0 }
}
```

`channel` 可选：省略时沿用参考工程自动发现；有多个 CAN 适配器时指定，例如 Windows 的 `"channel": "COM4"` 或 Ubuntu 的 `"channel": "/dev/ttyACM0"`。环境变量 `MOTORBENCH_CAN_INTERFACE/CHANNEL/BAUD/NODE` 可覆盖各项。不会猜测两台电源的接线归属。Windows 路径在 JSON 中可用 `C:/设备工程/xige_foc`。若参考工程已有 `.venv`，使用其 Python，否则使用工作台 Python；所用环境须安装 `pyserial` 和 `python-can`。找不到参考工程会明确提示，电源和传感器仍可使用。

启动后打开 `http://127.0.0.1:5173/`。按钮“一键连接全部接口”按页面保存的参数逐个验证两台电源、传感器、电机，不自动开启输出。缺少串口配置、设备无应答等会逐项显示，不猜测串口归属。

日志及本次脚本启动的进程信息保存在 `.runtime/`。关闭脚本终止本脚本启动的进程，以及经目录核对的本工程现有 Ubuntu 开发服务（ai-job-torque-workbench-web/bridge）；其他复用的外部服务保留。电机运行时先请求停止并验证；电源仍输出时提示先在工作台 OFF 后再关闭。断开或退出软件不等于切断硬件供电。关闭前会结束正在进行的首页记录并等待后台文件收尾。Windows 启动使用脱离终端的子进程，关闭启动窗口不会随之关闭服务。Windows 实机串口/CAN 驱动和实际设备仍需在 Windows 上验证。

## 专项测试

左侧“专项测试”提供零加载基线、稳态转速/转矩波动、NTC 温度记录，支持独立配方、稳定等待、曲线预览、统计结果、历史查看及 CSV/JSON 导出。原始数据在后台持续保存，不受原普通记录 1500 点限制。任务只测量，不改变设备输出。详细条件见 [专项测试说明](docs/specialized-tests.md)。

## 性能优化（2026-10-19）

页面、功能及控制参数保持原样。电源与电机控制面板只在自身数据/输入变化时更新，避免传感器刷新带动所有表单重绘；曲线一次遍历保留相同像素极值与量程结果，历史追加只复制保留范围。有效原始采集帧、后台完整记录、30 次/秒屏幕同步更新、250 ms 电机心跳及 500 ms 制动步进心跳均保持原策略。离线传感器检查间隔降低到 250 ms，健康查询限制单请求在途并设置超时。

电源后台读数与用户操作分开管理：按钮操作显示忙碌并等待已有读取完成，不再因后台读数占用而丢弃点击。电机/制动状态轮询在控制请求期间继续维持心跳，但旧轮询应答不能覆盖控制结果。正式运行使用静态资源缓存，接口继续禁止缓存。

本机 6 万点数据基准：曲线分桶约 2.45 → 1.45 ms，缓存追加约 0.67 → 0.36 ms；该数据仅代表这些计算步骤，不代表整页帧率或整机 CPU 降幅。全页面初始服务端渲染结果与优化前一致。当前现场开发服务保持运行，新启动入口下次启动时使用正式静态网页服务。

## Windows 一键部署包与局域网访问

从 [Releases](https://github.com/Inntorque/motor-test-bench/releases) 下载 `TorqueWorkbench-Windows-x64.zip`，完整解压后双击 `INSTALL-Windows.bat`。包内提供经过 SHA-256 校验的 Python 3.13.16 x64 独立环境、固定版本通信依赖、编译好的网页和电机主机通信模块，目标电脑不需要安装 Node.js、Python 或 Git。安装前校验包内文件，默认安装到 `%LOCALAPPDATA%\Inntorque\TorqueWorkbench`。

安装器生成桌面快捷方式、随机访问口令，启用局域网网页入口，并请求管理员配置专用网络 TCP 5173 防火墙规则。完成后自动打开本机工作台。其他电脑打开安装电脑 `LAN-ACCESS.txt` 中的局域网地址，输入口令使用；电源/传感器/CAN 必须接在安装电脑上，记录也写在安装电脑的 `recordings/`。主界面及原控制方式不变。网络 IP 变化后重新启动，地址说明会更新。

后端 8766 和 CAN 服务 8892 仍仅监听本机；局域网入口验证访问口令、签名会话和请求来源后转发现有 API。只适用于信任的专用局域网，不向公网转发端口。USB/CAN 驱动与具体硬件相关，未被 Windows 自动识别时仍需厂家驱动；包内默认支持当前 ZQWL 串口 CAN。

下载新包并再次运行安装器即可更新，保留 `recordings/`、本机配置与访问口令。运行中拒绝覆盖，请先结束试验、关闭输出和工作台服务。旧版 `xigeRoot` 自定义路径保留。详细说明见 [Windows 使用说明](docs/windows-package.txt)。

维护者推送 `windows-v*` 标签后，GitHub Actions 在 Windows 环境构建包，并使用包内 Python 实际检查安装、重复安装保留数据、三个服务启动/停止和局域网认证；通过后自动发布 Release 附件及 SHA256SUMS。源码日常更新到 main 只自动更新 Pages，Windows 部署包通过版本标签发布。云端没有真实 USB/CAN，软件验证不能代替实物接线验收。

## 电机 / 制动联动力矩测试

曲线旁先点击①空载准备，再独立点击②力矩加载。最大输出请求由配套固件在当前约束内分配电流参考；固定 10 A 模式保留作对照。达到目标力矩后按设定时间保持，波动不重置计时，未到达时继续闭环调整。直到联合停转确认、已有保护、制动能力边界或手动停止。

最大输出会自动关联完整记录。质量标记、限制来源与实际 Iq/Id 分开保存，不能把“目标已下发”称为“已达到硬件极限”。详细参数、通信接口和记录完整性见 [两阶段测试流程](docs/coupled-current.md)。
