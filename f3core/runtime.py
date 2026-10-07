"""Live-only orchestration. A public history record can signal, never fill, an order."""
from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal
import fcntl
import hashlib
import os
from pathlib import Path
import signal
import sys
import threading
import time

from . import config
from .config import Attention, require, safe_error
from .market import account_snapshot, market_snapshot, funding_history
from .okx import OKXClient
from .store import Store, AccountLock
from .execution import Executor
from .strategy import (RULES, entry_signal, plan_entry, evaluate_position,
                       valuation, is_decision_time, MINUTE, DAY)


@contextmanager
def account_guard(uid):
    path=config.account_lock_path(uid)
    config.plain_file(path,missing=True)
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'a+') as handle:
        try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:raise Attention('该欧易账户已有其他交易进程运行。') from None
        yield


def client(cfg,creds=None,orders=False):
    return OKXClient(mode='live',origin=cfg['origin'],proxy=cfg['proxy'],credentials=creds,allow_orders=orders)


def identity(cfg,creds,uid,spec):
    return dict(config.code_identity(cfg),uid=uid,spec=spec,
                key_fingerprint=hashlib.sha256(creds['api_key'].encode()).hexdigest())


def private_preflight(api,cfg):
    api.sync_time()
    account=account_snapshot(api,cfg)
    config.check_independent_account(account['uid'])
    market=market_snapshot(api,cfg)
    return account,market


def public_check(cfg):
    api=client(cfg)
    api.sync_time()
    m=market_snapshot(api,cfg)
    events=funding_history(api,api.now()-cfg['warmup_days']*DAY)
    history={r['ts']:r['rate'] for r in events}
    signal_result=entry_signal(history,api.now())
    return {'passed':signal_result['frame'] is not None,'mode':'live','public_only':True,
            'orders_sent':0,'spec':m['spec'],'funding_observations':len(events),
            'minimum_swap_notional_usd':str(Decimal(m['spec']['swap_min'])*Decimal(m['spec']['contract_usd'])),
            'funding_signal':signal_result,'note':'最小合约面值不是所需保证金；资金还须覆盖BTC现货、手续费和取整。'}


def account_check(cfg):
    api=client(cfg,config.credentials())
    account,market=private_preflight(api,cfg)
    plan=plan_entry(cfg,market,account)
    return {'passed':True,'mode':'live','private_read_verified':True,'orders_sent':0,
            'uid':account['uid'],'account_mode':'futures / net / BTC inverse cross 2x',
            'position_present':account['position'] is not None,
            'pending_orders':len(account['pending'])+len(account['algo_pending']),
            'entry_budget_plan':plan,
            'note':'账户读取通过不等于预算能开仓，须同时查看entry_budget_plan.allowed。'}


def arm(cfg,confirmation):
    require(confirmation=='ARM F3 REAL MONEY','未确认真钱启用；没有初始化或下单。')
    require(sys.platform.startswith('linux') and os.getuid()!=0,'请在Debian服务器以dvens普通用户启用，不要sudo。')
    creds=config.credentials(); api=client(cfg,creds)
    account,market=private_preflight(api,cfg)
    uid=account['uid']; directory=config.data_dir()
    require(not account['position'] and not account.get('positions') and not account['pending'] and not account['algo_pending'],
            '首次启用须空仓且没有普通挂单、条件或算法委托。')
    require(Decimal(account['balances']['BTC']['cash'])==0,'首次启用子账户的BTC余额须为0。')
    require(Decimal(account['balances']['USDT']['available'])>=Decimal(str(cfg['capital_usdt'])),
            '子账户可用USDT少于配置本金；没有启用或建立账户绑定。')
    plan=plan_entry(cfg,market,account)
    require(plan['allowed'],'当前资金无法满足F3两腿配平或交易所最小数量；不启用，请查看account-check计划。')
    with AccountLock(directory), account_guard(uid):
        s=Store(directory)
        try:
            require(s.identity() is None and not s.state(),'该账本已启用；使用run/status继续，不重复arm-live。')
            require(not config.binding_path(uid).exists(),'账户有原绑定而账本未启用；保留现场，不重新建立预算。')
            shared=Path.home()/'.local/state/btc-v53-live/accounts'/(config.account_key(uid)+'.json')
            require(not shared.exists(),'账户存在原独占登记；保留账本和登记文件。')
            binding=config.bind_account(uid,directory)
            ident=identity(cfg,creds,uid,market['spec'])
            ident['binding_id']=binding['binding_id']
            Executor(api,s,cfg,market['spec']).initialize(ident,account,api.now())
            s.event('armed',{'capital_usdt':cfg['capital_usdt'],'orders_sent':0},api.now())
            return {'armed':True,'orders_sent':0,'capital_usdt':cfg['capital_usdt'],
                    'max_loss_usdt':cfg['max_loss_usdt'],'note':'尚未启动交易循环；run将有真实下单权限。'}
        finally:s.close()


def risk_account(account,state,cfg):
    value=deepcopy(account)
    excess=Decimal(state['baseline_usdt'])-Decimal(str(cfg['capital_usdt']))
    for key in ('cash','eq'):
        value['balances']['USDT'][key]=str(Decimal(value['balances']['USDT'][key])-excess)
    return value


def health(s,phase,message,now,market=None):
    state=s.state()
    state['health']={'phase':phase,'message':message,'checked_at':now,
                     'market_time':market['now'] if market else None}
    s.set_state(state)


class Runner:
    def __init__(self,api,store,cfg):
        self.api,self.store,self.cfg=api,store,cfg
        self.execution=Executor(api,store,cfg)
        self._history=None; self._history_at=0

    def verify_identity(self):
        saved=self.store.identity()
        require(saved is not None,'尚未启用；请先完成arm-live。')
        config.validate_binding(saved['uid'],config.data_dir(),saved['binding_id'])
        current=identity(self.cfg,config.credentials(),saved['uid'],saved['spec'])
        current['binding_id']=saved['binding_id']
        require(current==saved,'代码、参数、API或账户身份与原账本不同；停止自动交易，不重建账本。')
        return saved

    def tick(self):
        ident=self.verify_identity()
        a,s,c,e=self.api,self.store,self.cfg,self.execution
        a.sync_time()
        # Reconcile submitted IDs/bills first, then obtain current balances and
        # fresh quotes. A stale snapshot cannot be reused after applying fills.
        e.recover(); e.sync_bills()
        account=account_snapshot(a,c)
        require(account['uid']==ident['uid'],'API返回了不同账户；停止交易。')
        market=market_snapshot(a,c)
        require(market['spec']==ident['spec'],'交易规格发生变化；保留订单与账本，人工核对后升级。')
        # Public history is used for decisions only. Never credit predicted
        # rates or replay an old signal into a real order after a downtime.
        now=a.now()
        if self._history is None or now-self._history_at>=60_000:
            try:
                rows=funding_history(a,now-c['warmup_days']*DAY,now)
                self._history={row['ts']:row['rate'] for row in rows}
            except Exception:
                # Missing signals block new entry / funding-deterioration decisions,
                # never the recovery and risk exit of an existing real position.
                self._history={}
                s.event('funding_signal_unavailable',{'new_entry_blocked':True},a.now())
            self._history_at=a.now()
            # History read may take seconds: refresh final quotes before sending.
            market=market_snapshot(a,c)
            require(market['spec']==ident['spec'],'刷新后的交易规格已变化；本轮不提交订单。')
        state=e.tick(market,account,prepared=True)
        now=a.now()
        if s.unresolved() or state.get('reconciliation_mismatch_since'):
            health(s,'needs_attention',state['last_decision'],now,market)
            return s.state()
        # Mark estimate in the executor is supplemented by conservative costs of
        # liquidating both legs. Remove idle excess USDT outside this budget.
        risk=risk_account(account,state,c)
        evaluation=evaluate_position(c,market,risk,state.get('position'),self._history,now,
                                     peak_equity_usdt=state['peak_equity'])
        state=s.state()
        equity=Decimal(evaluation['equity_usdt']); peak=max(Decimal(state['peak_equity']),equity)
        dd=max(Decimal(0),(peak-equity)/peak)
        state.update(last_equity=str(equity),peak_equity=str(peak),max_drawdown=str(max(Decimal(state['max_drawdown']),dd)))
        if evaluation['halt']:
            state.update(halted=state.get('halted') or 'F3累计亏损或回撤达到限制。',paused=True)
            if state.get('position') or state.get('op'):state['flatten_requested']=True
        if state.get('position'):
            state['position'].update(bad_days=evaluation['bad_days'],last_daily_check=evaluation['last_daily_check'])
        s.set_state(state)
        # Ongoing execution (including rollback of an incomplete pair) takes
        # precedence. Never start a second group while an operation is pending.
        if not state.get('op'):
            if state.get('position'):
                if evaluation['exit_reason']:
                    e.begin_exit(evaluation['exit_reason'])
                    s.event('exit_signal',evaluation,now)
                else:
                    self._treasury(evaluation,now)
            else:
                self._entry(market,account,now)
        state=s.state()
        phase=('needs_attention' if state.get('halted') else
               state['phase'] if state.get('op') else 'paused' if state['paused'] else 'running')
        health(s,phase,state.get('last_decision','等待下一次检查。'),a.now(),market)
        return s.state()

    def _entry(self,market,account,now):
        s,c,e=self.store,self.cfg,self.execution
        state=s.state(); pending=state.get('entry_signal')
        if state['paused'] or state.get('halted') or now<state['cooldown_until']:
            if pending:
                state.pop('entry_signal',None); s.set_state(state)
            return
        if pending:
            due=pending['due']
            if now>due+c['signal_ttl_seconds']*1000:
                state.pop('entry_signal',None); state['last_decision']='入场信号过期，跳过，等待下次日检。'; s.set_state(state)
            elif now>=due:
                state.pop('entry_signal',None); s.set_state(state)
                # Recheck settled data and costs at the moment of planning.
                verdict=entry_signal(self._history,now,account['fees']['spot'],account['fees']['swap'])
                cfg=dict(c,capital_usdt=min(float(c['capital_usdt']),float(state['last_equity'])))
                plan=plan_entry(cfg,market,account)
                if verdict['allowed'] and plan['allowed']:
                    e.begin_entry(plan,market,account)
                else:
                    state=s.state(); state['last_decision']='信号或最小交易量不满足，本次不下单。'; s.set_state(state)
                    s.event('entry_skipped',{'signal':verdict,'plan':plan},now)
            return
        minute=now//MINUTE*MINUTE
        start=(state['initialized_at']+MINUTE-1)//MINUTE*MINUTE
        if minute>=start and is_decision_time(now,state.get('last_daily_entry')):
            verdict=entry_signal(self._history,minute,account['fees']['spot'],account['fees']['swap'])
            state['last_daily_entry']=minute
            state['last_decision']='日检已完成；'+('信号通过，等待一分钟后核对下单条件。' if verdict['allowed'] else '本次资金费条件不足。')
            if verdict['allowed']:state['entry_signal']={'known_at':minute,'due':minute+MINUTE}
            s.set_state(state); s.event('entry_signal',verdict,now)
        else:
            state['last_decision']='等待北京时间09:00资金费日检；断线期间的旧入场信号不会补下单。'
            s.set_state(state)

    def _treasury(self,evaluation,now):
        state=self.store.state(); pending=state.get('treasury_due')
        if pending and now>=pending:
            state.pop('treasury_due',None); self.store.set_state(state)
            if Decimal(evaluation['treasury_btc'])>0 and not state.get('halted'):
                self.execution.begin_treasury(evaluation['treasury_btc'])
        elif not pending and Decimal(evaluation['treasury_btc'])>0:
            state['treasury_due']=now+MINUTE; self.store.set_state(state)


def run(cfg,once=False):
    require(sys.platform.startswith('linux') and os.getuid()!=0,'请在Linux以普通用户运行真钱服务。')
    directory=config.data_dir()
    require((directory/'state.sqlite3').is_file(),'尚未启用账本；先运行arm-live。')
    stop=threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:stop.set())
    with AccountLock(directory):
        s=Store(directory)
        try:
            saved=s.identity(); require(saved is not None,'缺少原启用记录，不能自动初始化。')
            with account_guard(saved['uid']):
                api=client(cfg,config.credentials(),orders=True)
                runner=Runner(api,s,cfg); runner.verify_identity()
                runner.execution.stopping=stop.is_set
                # Pending public signals are not resumed across a process restart.
                state=s.state(); state.pop('entry_signal',None); state.pop('treasury_due',None); s.set_state(state)
                while not stop.is_set():
                    try:runner.tick()
                    except Exception as exc:
                        message=safe_error(exc)
                        health(s,'error',message,api.now())
                        print(message,flush=True)
                    if once:break
                    stop.wait(cfg['poll_seconds'])
                if stop.is_set():
                    health(s,'stopped','服务已收到停止请求；已受理订单可能继续成交，原账本已保留。',api.now())
        finally:s.close()
