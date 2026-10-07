# F3 日常管理与排错

下列命令都在服务器的 `dvens` 用户执行；F3 专属服务名是 `btc-F3-live.service`。不会修改 v53。

## 一、查看状态

```bash
python3 "$HOME/btc-F3-live/launch.py" status
python3 "$HOME/btc-F3-live/launch.py" status --local
systemctl --user is-active mihomo-proxy.service btc-F3-live.service
journalctl --user -u btc-F3-live.service -n 80 --no-pager
```

第一条额外探测本地代理和欧易公开时间接口；第二条不联网，适合网络卡顿时查看。状态中的仓位和余额是**最近成功核对时的记录**，不是执行 status 时重新查询私有账户得到的持仓。

| 状态文字 | 含义和下一步 |
|---|---|
| 尚未启用 | 先完成配置、账户检查、`arm-live` |
| `running` | 循环在运行，可能等待 09:00 信号或持有组合 |
| `paused` | 新开仓已暂停；持仓管理继续 |
| `enter_spot/enter_swap/entry_trim` | 两腿仍在建立或配平，不能当作完整对冲 |
| `exit_swap/exit_spot` | 正在按顺序退出；直到实际核对完成才算结束 |
| `needs_attention` | 查看未核对订单、风控和余额核对原因 |
| `error` 或记录过期 | 本轮未完成，不能据旧数字认定运行正常 |
| `active` | 进程活着，仅此一项不能证明策略正常 |

真实资金费以 BTC 记；成交费用负数表示收费。净值包含 BTC 价值、合约浮盈以及估计退出成本，不等于可直接提走的已实现利润。USD 合约面值与 USDT 暂按 1:1 估算。

## 二、暂停、恢复、退出和停止的区别

| 目的 | 命令 | 实际效果 |
|---|---|---|
| 暂停新开仓 | `python3 "$HOME/btc-F3-live/launch.py" pause` | 请求暂停；已持有组合继续风险检查，未完成入场可能撤回 |
| 恢复新开仓 | `python3 "$HOME/btc-F3-live/launch.py" resume` | 无故障时恢复；不清除风控停止 |
| 退出两腿 | `python3 "$HOME/btc-F3-live/launch.py" flatten` | 确认后排队真实买平空头、卖出现货，并暂停新开仓 |
| 启动服务 | `systemctl --user start btc-F3-live.service` | 已启用账本进入真钱循环 |
| 停止服务 | `systemctl --user stop btc-F3-live.service` | 停止程序；**不平仓、不卖 BTC** |
| 重启服务 | `systemctl --user restart btc-F3-live.service` | 保留原账本恢复核对，不重新计算本金 |
| 取消开机启动 | `systemctl --user disable btc-F3-live.service` | 下次开机不启动；本次仍可运行 |
| 停止并取消自启 | `systemctl --user disable --now btc-F3-live.service` | 停止程序且取消自启，仍不平仓 |

`pause/resume/flatten` 要由运行中服务处理。先看“等待处理的操作”，再看状态变化。网络出错期间处理可能延迟。

服务收到停止信号后会在发送前检查并阻止新的下单；此前已经受理的订单仍可能成交。停止不会取消交易所真实持仓，也不能替代订单核对。

计划长期停用：先 `pause`，按需 `flatten`，在欧易核对实际合约已平、没有挂单、现货已处理，再 `disable --now`。保留整个 `var/live` 和账户登记文件。不要通过删除数据库重新开始。

## 三、代理检查

```bash
systemctl --user is-active mihomo-proxy.service
ss -ltn 'sport = :17890'
python3 "$HOME/btc-F3-live/launch.py" check
```

依次检查：代理进程、本机端口、通过代理访问欧易所需公开接口。公开通过后再做：

```bash
python3 "$HOME/btc-F3-live/launch.py" account-check
```

它只查询 API，不下单。已有持仓时 `entry_budget_plan.allowed: false`、理由 `position_exists` 可以是正常现象。

当前代理入口写在 `config.json` 的 `proxy` 中，默认 `http://127.0.0.1:17890`。公开与私有请求都强制用它；代理不可用时不自动改为直连。`17890` 是代理端口，不是欧易 API 地址，也不是可以浏览的网站。

Mihomo 换节点但入口不变，F3 不需要重配。v53 和 F3 共用代理，所以不要在任一策略持仓期间故意停止代理测试。

## 四、错误怎么处理

| 提示/情况 | 处理方式 |
|---|---|
| 超时、TLS EOF、公开接口失败 | 查看 Mihomo 和端口；验证节点能访问欧易。恢复后观察原服务自动重试 |
| 公开成功、私有检查失败 | 核对 API 所属子账户、权限、Passphrase、白名单、服务器时间和账户地区；不要把密钥打印给别人 |
| 最小数量或两腿配平失败 | 20U 可能不足或当时价格使取整不合适；保留资金，不改杠杆/合约强行启动 |
| 可用 USDT 少于本金 | 检查资金是否在该子账户的交易账户、有无冻结；未启用时可重新 `setup-config` 降低预算，再检查 |
| 未核对订单 | 程序只查询原订单，不重复下单。去欧易查看订单是否存在/已成交；不要删除订单记录或重建账本 |
| 已下单却查不到订单 | 保留服务日志和原订单记录，人工核对；本版没有“直接忽略此订单并继续”的按钮 |
| 非本策略账单、余额不匹配 | 排查手动买卖、划转、其他程序、借款或漏同步记录。不要用 `resume` 试图绕过 |
| 风控停止 | `resume` 不会清零亏损或回撤；需核对真实资产和风险原因，再决定后续维护 |
| 代码、参数、API身份改变 | 恢复原来对应版本并核对；已启用后不能直接换密钥、改本金、改代理入口或替换代码 |
| 账户有绑定但账本未启用 | 可能首次启用期间断电/磁盘写入失败，保存 `var/live` 与 `.local/state`，按原账户核对恢复；不自动重新建账 |
| 断线很久后不能自动恢复 | 本版对账追溯窗口有限，离线超过约 6 天会拒绝自动恢复，避免漏掉真实账单；需人工核对完整交易记录 |
| 合约已平，但 BTC 未卖完 | 等待抵押 BTC 解冻；低于现货最小数量的零头会保留并显示 |
| 多次限价退出仍未成交 | 查看实际账户，必要时人工处理；本版不会无限提高滑点追价 |

处于“订单未知”或“持仓核对不通过”时，即使发出 `flatten`，程序也可能不能自动退出，因为它无法确定应该处理多少真实资产。紧急情况应在欧易核对并处理实际持仓；手动操作后，原账本会要求人工恢复核对，不能当作未发生过。

## 五、文件放在哪里

| 路径 | 用途 | 能不能随便删 |
|---|---|---|
| `~/btc-F3-live/launch.py` | 统一命令入口 | 运行需要 |
| `~/btc-F3-live/config.json` | 资金、代理、固定执行限制 | 启用后不能随意改 |
| `~/btc-F3-live/private/credentials.json` | 私有 API 凭据 | 不能公开；运行需要 |
| `~/btc-F3-live/var/live/` | SQLite 账本、订单意图、成交、资金费、状态和锁 | **不能删**；包含数据库伴随文件 |
| `~/.config/systemd/user/btc-F3-live.service` | 普通用户服务定义 | 由安装脚本创建 |
| `~/.local/state/btc-F3-live/accounts/` | 账户与账本绑定 | 不能删来重新启用 |
| `~/.local/state/btc-v53-live/accounts/` | 与既有 v53 共用的账户独占登记命名空间 | 不能整目录删除，会影响 v53 的账户保护 |
| `~/mihomo/` | 共用代理程序和节点配置 | F3 与 v53 都可能使用 |

共享登记目录只用于防止同一欧易账户被两个策略重复启用；F3 的交易账本、资金和子账户仍独立。

需要备份时，优先在两腿已处理、F3 已停止后，保存整个 F3 目录以及账户绑定文件。只复制 `state.sqlite3` 而漏掉正在使用的 WAL 文件可能丢记录。备份含密钥，应私密保存，不上传 GitHub。不要把备份副本在另一台服务器上同时启动。

## 六、几个操作边界

- 本版没有自动扩本金、提款、划转、清除风控、变更 API 或无账本接管手动持仓的命令。
- `systemctl` 是管理后台进程的工具，不负责交易决策。重启进程不会自动平掉交易所仓位。
- `exit` 只退出 SSH；开启 linger 的后台服务继续运行。
- 运行期间不要直接切断智能插座电源。若确需关机，先处理真实持仓并确认无未决订单，再停止服务、正常关机。
