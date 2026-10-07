# F3 真钱交易版 · 第一版

这是部署到 Debian 服务器的独立程序。买入 **BTC-USDT 现货**，同时做空 **BTC-USD-SWAP 币本位永续**；成交、手续费和资金费来自欧易真实记录。它不是本地模拟，也不是欧易模拟交易账户。

管理方式与 v53 一致：用 `launch.py` 操作，用 `config.json` 配置代理，用普通用户的 systemd 在后台运行。没有 `.env`，不需要让 Mac 一直开着。

**本包交付时没有密钥、没有已启用账本，也没有运行中的交易服务。** 必须由你在服务器配置、检查、启用，再启动服务。离线测试通过不代表已经在你的真实账户验证过两腿交易。

## 1. 这次的资金怎么分

| 用途 | 上限 | 账户 |
|---|---:|---|
| 原 v53 | 30 USDT | 保持原来的专用账户 |
| 新 F3 | 20 USDT | 另建、专用于 F3 的欧易子账户 |
| 合计 | 50 USDT | 不能把同一笔余额同时算给两个程序 |

本包不会修改 v53，也不转账。由你在欧易把计划给 F3 的资金放入其**交易账户**。不要把 v53 正在使用的抵押物划走。F3 预算上限 20U 在配置读取时也会检查，不能靠编辑文件提高。

20U 是否能交易，取决于实际合约最小张数、现货最小数量、手续费、价格及两腿取整。`account-check` 会计算。如果不满足，程序拒绝启用；它不会加杠杆、借款或使用 v53 的钱凑单。现货和合约不是各投入 20U：购买的 BTC 同时用作币本位空头抵押物。

F3 保留原策略的单组损失 3%、账户回撤 5% 等退出规则。启用前还必须填写一个累计亏损金额。20U 的 5% 是 1U，仅帮助你理解金额，程序不会替你选定这个值。阈值触发的是停止/退出动作，不保证实际最大损失。

## 2. 欧易账户准备

在欧易独立子账户里操作：

1. 使用真钱账户；初次启用须无持仓、无普通挂单和条件/算法委托，BTC 余额为 0。
2. 只保留本次投入的 USDT。转入交易账户后再检查。这个子账户不要同时手动交易或运行别的策略。
3. 账户设为**合约模式（原单币种保证金模式）**、**买卖/净持仓模式**。本版不支持多币种或组合保证金。
4. 将 **BTC-USD 永续 / BTC-USD-SWAP** 手动设置为**全仓 2 倍**。这里与 v53 的 BTC-USDT 永续、逐仓 15 倍不同。不要选错合约。
5. 关闭自动借币，不保留借款或其他币种资产。
6. 新建此子账户的 API，授权读取和交易，**不要提币权限**。API 使用地区应与 `openapi.okx.com` 全球站兼容。若设置 IP 白名单，应与代理实际出口相符；本包不会修改白名单。

程序会核对这些设置。没有通过时，先按提示处理账户，不能跳过检查。程序不自动切换账户模式、杠杆或划转资产。

## 3. 上传到服务器

假设压缩包已放在 Mac 桌面，文件名是 `btc-F3-live.zip`。

**在 Mac 的本地终端执行：**

```bash
scp "$HOME/Desktop/btc-F3-live.zip" dvens@100.77.57.40:/home/dvens/
ssh dvens@100.77.57.40
```

第一行上传文件，第二行登录服务器。提示符变成 `dvens@quant-server` 后，以下命令都在服务器执行。此时 `$HOME` 是 `/home/dvens`，不是 Mac 的用户目录。

```bash
if [ ! -e "$HOME/btc-F3-live" ]; then
  python3 -m zipfile -e "$HOME/btc-F3-live.zip" "$HOME"
else
  echo "btc-F3-live 已存在，请保留原文件和账本，不要覆盖。"
fi
cd "$HOME/btc-F3-live"
```

需要 Python 3.11 或更新版本，你的 Debian 13 已有 Python 3.13。运行本包不需要安装第三方 Python 库。目录必须叫 `btc-F3-live`；Linux 区分大小写。

## 4. 代理、资金和密钥

你的 Mihomo 已独立放在 `/home/dvens/mihomo`。先检查或开启原代理：

```bash
systemctl --user enable --now mihomo-proxy.service
systemctl --user is-active mihomo-proxy.service
```

显示 `active` 只说明代理进程在运行，不代表能访问欧易。

F3 的 `config.json` 已填写：

```json
"proxy": "http://127.0.0.1:17890"
```

这指向**服务器自己**的 Mihomo HTTP 入口。F3 读取这个字段后，公开和私有 API 请求都通过它发送。没有读取 Mac 的代理，也不依赖 `export` 或 `.env`。Mihomo 换节点、入口仍是这个地址时，不需要改 F3 或 v53 配置。

填写资金和亏损停止金额：

```bash
python3 "$HOME/btc-F3-live/launch.py" setup-config
```

本金直接回车使用 20U；亏损金额必须自己填写。这里只保存配置，不下单。

然后输入子账户的 API：

```bash
python3 "$HOME/btc-F3-live/launch.py" setup-credentials
```

依次粘贴 API Key、Secret Key、Passphrase。输入时屏幕不显示字符，这是正常的。密钥存入 `private/credentials.json`，权限为仅本人可读写。不要将密钥粘到聊天、GitHub 或命令行参数中。

## 5. 先检查，再允许交易

### 第一步：公开联网检查，不下单

```bash
python3 "$HOME/btc-F3-live/launch.py" check
```

检查代理、欧易时间、盘口、币本位合约规格、已结算资金费历史。输出 `passed: true` 表示这次公开检查通过。`funding_signal.allowed: false` 可以只是策略当时没有入场机会。

### 第二步：私有账户检查，不下单

```bash
python3 "$HOME/btc-F3-live/launch.py" account-check
```

必须结合看这几项：

| 输出 | 应如何理解 |
|---|---|
| `private_read_verified: true` | API 签名和账户读取成功 |
| `position_present: false` | 初次启用应没有合约持仓 |
| `pending_orders: 0` | 没有挂单 |
| `entry_budget_plan.allowed: true` | 当时的资金/规格可形成两腿计划 |
| `orders_sent: 0` | 此命令没有下单 |

`passed: true` 只表示账户读取流程完成；如果 `entry_budget_plan.allowed` 为 `false`，不能启用。该计划是下单前估算，不是承诺成交。

### 第三步：建立真钱账本，仍不下单

```bash
python3 "$HOME/btc-F3-live/launch.py" arm-live
```

看清提示后，输入要求的确认文字 `ARM F3 REAL MONEY`。程序再次检查账户并锁定本金、参数、代码、API 和账户身份，建立账本。只需成功执行一次。失败时按原因处理，不删除登记文件绕过。

### 第四步：安装后台服务，仍不下单

```bash
bash "$HOME/btc-F3-live/deploy/install-service.sh"
```

服务名是 `btc-F3-live.service`。这是普通用户服务，不用 `sudo systemctl`。

检查退出 SSH 后用户服务能否继续：

```bash
loginctl show-user dvens -p Linger
```

你之前为 v53/Mihomo 开启过，显示 `Linger=yes` 即可。如果是 `no`，只需做一次系统级授权：

```bash
sudo loginctl enable-linger dvens
```

这是首次允许用户服务长期运行的系统设置；日常管理 F3 不需要 sudo。

### 第五步：启动真钱交易循环

**以下命令启动后，F3 在条件满足时会真实买卖：**

```bash
systemctl --user enable --now btc-F3-live.service
python3 "$HOME/btc-F3-live/launch.py" status
```

第一次账户检查有多个接口，可能需要几十秒。`active` 只说明进程运行；还应看到运行记录近期更新、账户核对通过、没有未核对订单或错误。

F3 每天北京时间 **09:00** 判断资金费条件，信号通过后等待至少一分钟再检查并执行。刚启动没有马上开仓是正常情况。错过的入场信号不会在重启后追着补下单；已发送订单会按原订单号恢复核对。

## 6. 日常最常用命令

```bash
# 状态、持仓账本、代理入口、经代理访问欧易的结果
python3 "$HOME/btc-F3-live/launch.py" status

# 只看本地运行记录，不等待网络探测
python3 "$HOME/btc-F3-live/launch.py" status --local

# 暂停新增交易，继续管理已有持仓
python3 "$HOME/btc-F3-live/launch.py" pause

# 恢复条件检查；不会清除风控停止或账本故障
python3 "$HOME/btc-F3-live/launch.py" resume

# 查看最近日志
journalctl --user -u btc-F3-live.service -n 80 --no-pager
```

`pause/resume` 是交给运行中服务的请求，命令返回不代表已经处理；再查看 `status`。网络或账户检查失败时，请求可能仍在队列中。

需要真实退出两腿时：

```bash
python3 "$HOME/btc-F3-live/launch.py" flatten
```

它要求输入 `FLATTEN F3`，随后请求买平币本位空头、再卖出释放的 BTC 现货，并暂停新开仓。**不是即时平仓保证**，服务必须运行、网络可用、订单和余额可核对。检查状态与欧易实际账户，确认没有剩余合约、挂单或可卖出的 BTC。

详细操作和错误处理见 [日常管理](docs/OPERATIONS.md)，首次部署后的验证见 [运行验收](docs/ACCEPTANCE.md)。

## 7. 与原模拟版的区别

- 不根据 K 线编造成交，不补算断线时本来“应该赚到”的真钱收益。
- 成交回报与实际手续费入账；资金费来自子账户真实账单。收到下单成功回应不等于已经成交。
- 两腿是先后下单，可能只有一腿成交。会按实际成交数量对冲，必要时撤回未完成组合；未知订单结果先核对原订单号，不盲目重复。
- 网络故障、账户不匹配、订单不确定等情况下可能停止自动操作并要求人工核对。服务器关闭后，本地风控不再运行；交易所实际持仓仍在。
- 没有交易所托管的整组两腿止损，也没有自动切换代理节点或自动补资金。独立子账户用于隔离抵押物，预算本身不是损失上限保证。

完整规则与限制见 [策略与执行](STRATEGY.md)，离线验证范围见 [测试报告](TEST-REPORT.md)。

## 8. 保存到 GitHub

解压这份**原始交付包**，将 `btc-F3-live` 里面的源码和文档上传到新的仓库即可。不要上传服务器运行后的整个目录。

`.gitignore` 已排除 `private/`、`var/`、日志和数据库。GitHub 网页拖拽上传不会替你执行 `.gitignore`；手动上传时仍须检查所选文件。Mihomo 订阅、节点密码、F3 API、真实账本和账户登记都不能进入仓库。

源码部署后通过 `launch.py` 运行；GitHub 只是保存代码，不是替你运行策略。
