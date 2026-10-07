# 首次部署后的运行验收

这些检查用于确认你的实际服务器环境；交付包的离线测试不能代替它们。不要为了测试去停掉正在被 v53 持仓使用的 Mihomo，也不要通过真实断电测试恢复。

## 1. 首次交易前

按 README 完成公开检查、私有账户检查、预算计划和启用。第一次启动服务后，可先请求暂停新增交易：

```bash
python3 "$HOME/btc-F3-live/launch.py" pause
python3 "$HOME/btc-F3-live/launch.py" status
```

等待状态显示暂停、无待处理操作；同时确认欧易子账户空仓、无挂单。启动和暂停之间不是一个原子操作，不能只凭执行过 `pause` 就假定没有订单。

成功迹象：

- F3 和 Mihomo 服务均为 `active`。
- 运行记录最近更新，私有余额核对时间持续前进。
- 欧易公开接口连通；未核对订单为 0；没有风控停止或余额差异。
- 子账户 UID 是 F3 子账户，不是 v53 所用账户。

## 2. 退出 SSH 后检查

```bash
loginctl show-user dvens -p Linger
exit
```

在 Mac 重新登录，然后查看：

```bash
ssh dvens@100.77.57.40
python3 "$HOME/btc-F3-live/launch.py" status
```

`Linger=yes`，服务仍运行，原本金和账本没有变。F3 会保留暂停状态；不要为测试重新 `arm-live`。

## 3. 只重启 F3 进程

仅在已确认空仓、无未决订单时测试：

```bash
systemctl --user restart btc-F3-live.service
python3 "$HOME/btc-F3-live/launch.py" status
journalctl --user -u btc-F3-live.service -n 40 --no-pager
```

检查原本金/历史记录保留、暂停状态保留、重新核对成功。未到新的 09:00 信号不会补开断线期间的历史单。整个服务器重启会同时影响 v53，因此这里先只重启 F3。

服务器本身重启的验证应安排在 **v53 和 F3 都确认没有持仓及未决订单**、允许停机的时间。正常执行系统重启，随后重新 SSH 检查代理和两个服务。不要用智能插座强制断电。

## 4. 检查故障处理而不破坏正在使用的代理

仓库的离线测试覆盖代理失败、下单超时、部分成交、重复订单保护和恢复，完全使用假数据与假传输：

```bash
cd "$HOME/btc-F3-live"
python3 -B -m unittest discover -s tests -v
```

这些测试不会加载真实密钥、不会发真实订单。不要为了验证测试去编辑已经启用的程序或配置，程序会发现身份变化而停止自动交易。

实际节点不稳定时，观察 `status` 的公开连接结果和运行错误、账本核对时间。`active` 而记录过期也属于故障。恢复连通后，检查服务是否恢复核对、未决订单是否消失；旧数字仍在屏幕上不代表已恢复。

## 5. 开始按信号运行

确认验收完成后：

```bash
python3 "$HOME/btc-F3-live/launch.py" resume
python3 "$HOME/btc-F3-live/launch.py" status
```

显示允许新开仓、没有风控停止后，等待下一次北京时间 09:00 日检。不要用“强制下一单”代替策略信号。本版没有真钱 `test-buy` 命令。

第一次实际组合建立后，在欧易核对现货 BTC、BTC-USD 永续空头张数、手续费及子账户；状态应显示两腿核对完成，而不是仍在 `enter_swap`。第一次退出时，同样核对两腿与剩余零头。只有这一步完成，才获得你环境里的真实执行证据；本包没有替你完成这一阶段。
