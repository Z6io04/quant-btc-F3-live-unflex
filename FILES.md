# 文件说明

| 文件 | 工作 |
|---|---|
| `launch.py` | 解析你输入的操作：设置、检查、启用、状态、暂停、恢复、退出 |
| `config.json` | 公开运行参数。代理、20U预算上限、启用前必填的亏损限额 |
| `f3core/config.py` | 参数检查、私有密钥读取、账户独占与代码身份 |
| `f3core/okx.py` | 欧易 API 签名、强制代理、请求范围与未知下单结果区分 |
| `f3core/market.py` | 获取实际合约规格、账户余额/模式、手续费、盘口和已结算资金费 |
| `f3core/strategy.py` | 原 F3 信号、两腿数量计划和退出条件；不直接发订单 |
| `f3core/execution.py` | 发真实订单、部分成交后配平、原订单恢复、真实成交和账单核对 |
| `f3core/store.py` | SQLite 持久账本、订单请求、成交记录、控制请求、进程锁 |
| `f3core/runtime.py` | 主循环：核对、查询、决策、执行；重启后继续原账本 |
| `f3core/status.py` | 展示最近记录，独立检查代理与欧易公开接口；不读取 API 密钥 |
| `deploy/install-service.sh` | 安装普通用户的服务定义，不启动交易 |
| `deploy/btc-F3-live.service` | 告诉 systemd 从哪个目录启动哪个 Python 程序 |
| `tests/` | 假账户、假网络的离线验证，不是真钱测试订单 |
| `reference/` | 原 F3 配置副本及来源摘要，仅供审核，不参与实盘配置 |
| `tools/check_release.py` | 检查原始交付文件的哈希与是否混入私有数据 |
| `release-manifest.json` | 本次交付源文件校验信息 |

运行后才会生成 `private/credentials.json` 和 `var/live/`。真实订单与持仓数据在后者；不要用旧纸面账本替换这个文件夹。

整个过程：systemd 启动 `python3 launch.py run` → 主循环调用策略 → 执行模块通过本机 Mihomo 访问欧易 → 欧易真实订单/账单回写 SQLite。你执行 `launch.py status` 时，是另开一个只读展示进程，不是再开第二个交易循环。
