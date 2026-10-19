# 首页完整参数记录

## 与首页按钮同步的后台诊断记录

点击首页“开始记录”时同步开启后台诊断，点击“停止记录”时同步关闭。通信桥启动和普通设备控制不自动开启记录。诊断保存在 `recordings/background/<启动时间_会话ID>/`；首页报告的 `backgroundDirectory` 与后台清单的 `workbenchRecording.id` 关联两份记录。`recordings/background/latest.json` 指向最近会话（不代表仍在记录，应检查 `running`）；每个会话有 `manifest.json` 和按 15 分钟分卷的 `events-*.jsonl`，历史文件不自动删除。

每行包括事件时间 `recordedAt`、单调时钟 `elapsedS`、事件类型 `kind` 和完整 `payload`：

- `operation_request/operation_response`：页面提交的参数、请求 ID、应答、HTTP 状态及耗时。
- `motor_command_request/motor_command_response/motor_command_error`：电机每次实际写入请求及成功或失败结果，包含自动电流步进和停止命令。
- `brake_step_start/brake_step/brake_step_end`、`brake_command_request/brake_command_response/brake_command_error`：制动步进配置、每级目标和次数、结束原因、每次电源写入及读回结果。
- `motor_read_response/motor_read_error`：工作台读取电机服务的完整反馈、参数及读取异常；不保存会话令牌。
- `snapshot`：四个通道的现有设定、测量、状态、配置及原始接收时间。
- `sensor_frames`：每个有效扭矩帧，按 `[序号, 接收Unix秒, 原始扭矩, 原始转速]` 批量保存，同时带小数位配置；扭矩除以 `10^torqueDecimals`，转速除以 `10^speedDecimals`，轴功率 W 为 `abs(扭矩×转速)/9550×1000`。
- `calibration_parameter_recheck`、`sensor_stream`、`event_gap`、`recording_error`：异常参数重查、传感器重置/缺帧、队列溢出和记录异常。首页完整记录中断时，后台诊断同步结束，保留已有文件；记录结束不控制设备输出。

记录器只读取已有缓存，不额外发送设备命令，不续期电机页面心跳。磁盘写入在独立线程执行，每秒刷新、每 5 秒同步；异常退出最多可能损失最后未同步的数据，不能把未正常结束的会话视为完整。分析时以设备原始接收时间对齐，并检查缺帧事件和会话结束事件。代码 SHA256 和计数保存在清单内，方便后续对照不同版本。

以下为首页完整参数记录，两份记录由同一组开始/停止按钮管理。

### 制动电流步进

首页制动电源卡片的“电源输出控制”提供手动定值和电流步进模式。填写步长（0.001–2.3 A）、间隔（0.2–10 s）及持续时间（1–255 s，须大于间隔），由操作者点击启动。启动要求制动输出已关闭，按 24 V / 0 A 开启后，每隔指定时间递增，达到当前限幅（最高 2.3 A）保持；到期关闭输出。停止按钮、通信失败、输出意外关闭、页面心跳超过 3 秒都会结束步进；关闭结果以实际寄存器读回为准。

后台接口为 GET `/api/brake-current/status`（页面心跳）、POST `/api/brake-current/start`（`stepA/intervalS/durationS`）、POST `/api/brake-current/stop`。运行时其他入口仍可关闭制动输出，但不可写入手动电流、重新连接或切入专项自动脚本。步进不操作电机，电机电流环可以独立同时运行。普通 SSJ 电源实际 CC/CV 工作状态仍按设备反馈显示。

直接在原“扭矩工作台”首页配置电源、传感器和电机，使用原有控制与曲线。按原有方式运行电机后，点击原“记录这次试验”中的 **开始记录**，结束时点击 **停止记录**，无需进入专项测试。

记录按钮只管理记录：不启动或停止电机、不改变电源输出、控制方式或保护。原电流步进、电源设定及输出开关均保留。记录期间的控制请求、操作应答、电机写入命令成功应答也进入完整时间序列。

## 保存内容

- 电机：已发送的电流目标、固件参考 Iq、实际 Iq/Id、位置、转速、NTC、模式、输出、编码器和保护状态，以及现有接口返回的其他字段。
- 双电源：各自实际 V/A/W、目标 V/A、限幅、额定量程、原始设置/状态寄存器、协议、通信配置与已返回的其他字段。
- 扭矩：逐个有效帧的序号、原始整数、换算后的扭矩/转速、计算轴功率，及传感器通信状态与元数据。
- 起始配置、各接口连接/断开状态，以及记录期间的控制请求与结果。

数据采用各接口原有接收时间，按原有真实更新速度保存；网页刷新率不决定扭矩逐帧保存速率。不同接口没有硬件同步，CSV 按来源分行，其他接口列留空，不补造同步数据。电机和电源继续沿用首页原有读取链路，记录器本身仅观察后台数据，不增加硬件输出控制。

完整文件保存在 `recordings/workbench/<id>/`：

| 文件 | 内容 |
| --- | --- |
| `timeline.csv` | 完整时间序列，常用信号为独立列，`payload_json` 包含该事件全部原字段 |
| `timeline.jsonl` | 每行一个完整事件，保留嵌套字段 |
| `report.json` | 名称、配置、起止时间、各来源计数、统计和完整性异常 |

使用 `source_recording_elapsed_s` 绘制来源信号曲线；`source_received_unix_s` 为原有 PC 接收时间，`pc_recorded_unix_s` 为记录器保存该事件的时间。`elapsed_s` 是记录器单调时钟时间，不应取代批量接收帧各自的原始时间。

## 首页及历史

首页原 **导出 CSV** 导出完整磁盘时间序列，旁边可导出 JSONL 和统计报告。“试验记录”新增完整磁盘记录列表，刷新网页后仍可查到；原有浏览器历史和事件记录保留。网页历史曲线最多保留 1500 个压缩预览点，达到此数量只压缩预览，**不会结束完整记录**。

接口掉线会保留断开状态及已有数据；传感器序号重置/缓存溢出、文件写入失败等造成不完整记录时，会明确显示中断原因。记录中断不会代替操作者控制电机或电源。重启服务不续录旧会话，旧会话标记中断，已有文件保留。

API：GET `/api/recordings/status?history=0|1`，带本机令牌 POST `/api/recordings/start`（`name`、`range`）及 `/api/recordings/stop`（`id`），GET `/api/recordings/files/<id>/timeline.csv|timeline.jsonl|report.json`。
