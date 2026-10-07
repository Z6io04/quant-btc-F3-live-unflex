"""Offline lifecycle/time/identity regression tests for the live orchestrator."""
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from f3core import runtime, config
from f3core.execution import Executor
from f3core.store import Store
from f3core.strategy import DAY, MINUTE, HOUR

NINE = int(datetime(2026, 10, 7, 1, 0, tzinfo=timezone.utc).timestamp() * 1000)
CREDS={'api_key':'offline-key','secret_key':'offline-secret','passphrase':'offline-pass'}
SPEC={'spot_lot':'0.00001','spot_min':'0.0001','spot_tick':'0.1','swap_lot':'1','swap_min':'1',
      'swap_tick':'0.1','contract_usd':'10','tier_max_contracts':'10000','mmr':'0.004'}


def account(cash='100'):
    return {'uid':'123','balances':{'USDT':{'cash':cash,'eq':cash,'available':cash},
            'BTC':{'cash':'0','eq':'0','available':'0'}},
            'position':None,'positions':[],'pending':[],'algo_pending':[],
            'fees':{'spot':'0.001','swap':'0.0005'}}


class ClockAPI:
    def __init__(self, now): self.clock=now; self.posts=[]; self.syncs=0
    def now(self): return self.clock
    def sync_time(self): self.syncs+=1; return self.clock
    def bills(self, **kwargs): return []
    def write(self, path, body, exp_time=None):
        self.posts.append((path,deepcopy(body)))
        return [{'sCode':'0','ordId':'42','clOrdId':body['clOrdId']}]
    def get_order(self, *args): return None


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name)/'ledger'
        self.store=Store(self.directory); self.addCleanup(self.store.close)
        self.cfg=dict(config.DEFAULT,capital_usdt=100,max_loss_usdt=20)
        self.api=ClockAPI(NINE-HOUR)
        self.account=account()
        self.saved={'uid':'123','spec':deepcopy(SPEC),'binding_id':'binding'}
        Executor(self.api,self.store,self.cfg).initialize(self.saved,self.account)
        self.runner=runtime.Runner(self.api,self.store,self.cfg)
        self.patches=[]
        self.install('f3core.runtime.account_snapshot',side_effect=lambda *a:deepcopy(self.account))
        self.market_mock=self.install('f3core.runtime.market_snapshot',side_effect=lambda *a:self.market())
        self.history_mock=self.install('f3core.runtime.funding_history',side_effect=lambda *a:self.events())
        self.install('socket.socket',side_effect=AssertionError('runtime tests cannot use network'))
        self.identity_mock=self.install('f3core.runtime.Runner.verify_identity',return_value=deepcopy(self.saved))

    def install(self,target,**kwargs):
        p=patch(target,**kwargs); value=p.start(); self.addCleanup(p.stop); self.patches.append(p); return value

    def market(self):
        return {'now':self.api.now(),'mark':'10000','spec':deepcopy(SPEC),
                'spot':{'bid':'10000','ask':'10000','ts':self.api.now()},
                'swap':{'bid':'10000','ask':'10000','ts':self.api.now()}}

    def events(self):
        anchor=self.api.now()//DAY*DAY
        return [{'ts':anchor-i*8*HOUR,'rate':'0.0001'} for i in range(53)]

    def state(self,**changes):
        value=self.store.state(); value.update(changes); self.store.set_state(value)

    def test_daily_0900_waits_one_minute_before_entry_plan(self):
        self.api.clock=NINE
        state=self.runner.tick()
        self.assertEqual(state['entry_signal']['due'],NINE+MINUTE)
        self.assertIsNone(state['op'])
        self.api.clock=NINE+MINUTE-1
        self.assertIsNone(self.runner.tick()['op'])
        self.api.clock=NINE+MINUTE
        state=self.runner.tick()
        self.assertNotIn('entry_signal',state)
        self.assertEqual(state['op']['type'],'entry')
        self.assertEqual(state['op']['stage'],'spot')
        self.assertEqual(self.api.posts,[])

    def test_initialize_mid_0900_minute_skips_incomplete_signal(self):
        self.state(initialized_at=NINE+1000)
        self.api.clock=NINE+2000
        state=self.runner.tick()
        self.assertNotIn('entry_signal',state)
        self.assertIsNone(state['op'])
        self.api.clock=NINE+DAY
        self.assertEqual(self.runner.tick()['entry_signal']['due'],NINE+DAY+MINUTE)

    def test_no_historical_entry_away_from_fresh_0900(self):
        self.api.clock=NINE+2*MINUTE
        state=self.runner.tick()
        self.assertNotIn('entry_signal',state)
        self.assertIsNone(state['op'])
        self.assertEqual(self.api.posts,[])

    def test_expired_signal_is_removed_without_order(self):
        self.state(entry_signal={'known_at':NINE,'due':NINE+MINUTE})
        self.api.clock=NINE+2*MINUTE+1
        state=self.runner.tick()
        self.assertNotIn('entry_signal',state)
        self.assertIsNone(state['op'])
        self.assertEqual(self.api.posts,[])

    def test_pause_control_removes_pending_entry(self):
        self.state(entry_signal={'known_at':NINE,'due':NINE+MINUTE})
        self.store.control('pause')
        self.api.clock=NINE+MINUTE
        state=self.runner.tick()
        self.assertTrue(state['paused'])
        self.assertNotIn('entry_signal',state)
        self.assertIsNone(state['op'])
        self.assertEqual(self.store.controls(),[])

    def test_resume_control_allows_fresh_signal(self):
        self.state(paused=True)
        self.store.control('resume')
        self.api.clock=NINE
        state=self.runner.tick()
        self.assertFalse(state['paused'])
        self.assertIn('entry_signal',state)

    def test_uid_mismatch_blocks_every_order(self):
        self.account['uid']='456'
        self.api.clock=NINE
        with self.assertRaises(config.Attention): self.runner.tick()
        self.assertEqual(self.api.posts,[])

    def test_spec_mismatch_blocks_every_order(self):
        changed=self.market(); changed['spec']['contract_usd']='100'
        self.market_mock.side_effect=None; self.market_mock.return_value=changed
        with self.assertRaises(config.Attention): self.runner.tick()
        self.assertEqual(self.api.posts,[])

    def test_spec_mismatch_in_post_history_refresh_also_blocks(self):
        first=self.market(); changed=deepcopy(first); changed['spec']['contract_usd']='100'
        self.market_mock.side_effect=[first,changed]
        with self.assertRaises(config.Attention): self.runner.tick()
        self.assertEqual(self.api.posts,[])

    def test_excess_unallocated_usdt_does_not_inflate_budget_or_mutate_account(self):
        self.state(baseline_usdt='150',cash_usdt='150')
        self.account=account('150')
        risk=runtime.risk_account(self.account,self.store.state(),self.cfg)
        self.assertEqual(Decimal(risk['balances']['USDT']['cash']),100)
        self.assertEqual(Decimal(risk['balances']['USDT']['eq']),100)
        self.assertEqual(self.account['balances']['USDT']['cash'],'150')
        self.api.clock=NINE-10*MINUTE
        state=self.runner.tick()
        self.assertEqual(Decimal(state['last_equity']),100)
        self.assertEqual(Decimal(state['peak_equity']),100)

    def test_history_failure_still_plans_immediate_margin_exit(self):
        position={'opened_at':NINE-DAY,'entered_at':NINE-DAY,'entry_equity':'100','capital_at_entry':'100',
                  'contracts':'9','coins_btc':'0.009','funding_btc':'0','treasury_sold_btc':'0',
                  'bad_days':0,'last_daily_check':None}
        self.state(phase='active',cash_usdt='10',btc_balance='0.009',contracts='9',position=position)
        self.account['balances']['USDT']={k:'10' for k in ('cash','eq','available')}
        self.account['balances']['BTC']={'cash':'0.009','eq':'0.001','available':'0.0001'}
        self.account['position']={'contracts':'-9','avgPx':'10000','upl':'0','margin':'0'}
        self.history_mock.side_effect=RuntimeError('offline public history timeout')
        self.api.clock=NINE-10*MINUTE
        state=self.runner.tick()
        self.assertIsNotNone(state['op'])
        self.assertEqual(state['op']['type'],'exit')
        self.assertEqual(state['op']['reason'],'margin_guard')

    def test_failed_history_refresh_clears_cached_positive_entry(self):
        self.api.clock=NINE
        self.assertIn('entry_signal',self.runner.tick())
        self.assertTrue(self.runner._history)
        self.history_mock.side_effect=RuntimeError('public data unavailable')
        self.api.clock=NINE+MINUTE
        state=self.runner.tick()
        self.assertEqual(self.runner._history,{})
        self.assertNotIn('entry_signal',state)
        self.assertIsNone(state['op'])
        self.assertEqual(self.api.posts,[])

    def test_run_restart_discards_pending_signal_without_replay(self):
        self.state(entry_signal={'known_at':NINE,'due':NINE+MINUTE},treasury_due=NINE+MINUTE)
        self.api.clock=NINE+MINUTE
        self.store.close()
        self.store=Store(self.directory)
        # Existing addCleanup refers to the earlier handle; register current one.
        self.addCleanup(self.store.close)
        with patch('f3core.runtime.sys.platform','linux'), patch('f3core.runtime.os.getuid',return_value=1000), \
             patch('f3core.runtime.signal.signal'), patch('f3core.runtime.config.data_dir',return_value=self.directory), \
             patch('f3core.runtime.config.credentials',return_value=CREDS), \
             patch('f3core.runtime.account_guard',return_value=nullcontext()), \
             patch('f3core.runtime.client',return_value=self.api):
            runtime.run(self.cfg,once=True)
        state=self.store.state()
        self.assertNotIn('entry_signal',state)
        self.assertNotIn('treasury_due',state)
        self.assertIsNone(state['op'])
        self.assertEqual(self.api.posts,[])


class IdentityAndPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'ledger'); self.addCleanup(self.store.close)
        self.cfg=dict(config.DEFAULT,capital_usdt=100,max_loss_usdt=20)
        self.api=ClockAPI(NINE)
        self.network=patch('socket.socket',side_effect=AssertionError('network forbidden'))
        self.network.start(); self.addCleanup(self.network.stop)

    def test_real_identity_verifier_rejects_code_or_key_change(self):
        with patch('f3core.runtime.config.code_identity',return_value={'files':{'launch.py':'old'},'config':self.cfg}):
            saved=runtime.identity(self.cfg,CREDS,'123',SPEC)
        saved['binding_id']='same-binding'
        Executor(self.api,self.store,self.cfg).initialize(saved,account())
        runner=runtime.Runner(self.api,self.store,self.cfg)
        with patch('f3core.runtime.config.validate_binding') as checked, \
             patch('f3core.runtime.config.credentials',return_value=CREDS), \
             patch('f3core.runtime.config.code_identity',return_value={'files':{'launch.py':'old'},'config':self.cfg}):
            self.assertEqual(runner.verify_identity(),saved)
            checked.assert_called_once()
        with patch('f3core.runtime.config.validate_binding'), \
             patch('f3core.runtime.config.credentials',return_value=CREDS), \
             patch('f3core.runtime.config.code_identity',return_value={'files':{'launch.py':'new'},'config':self.cfg}):
            with self.assertRaises(config.Attention): runner.verify_identity()
        with patch('f3core.runtime.config.validate_binding'), \
             patch('f3core.runtime.config.credentials',return_value=dict(CREDS,api_key='new-key')), \
             patch('f3core.runtime.config.code_identity',return_value={'files':{'launch.py':'old'},'config':self.cfg}):
            with self.assertRaises(config.Attention): runner.verify_identity()

    def test_private_preflight_orders_reads_before_fresh_market(self):
        steps=[]
        self.api.sync_time=lambda:steps.append('clock')
        with patch('f3core.runtime.account_snapshot',side_effect=lambda *a:steps.append('account') or account()), \
             patch('f3core.runtime.config.check_independent_account',side_effect=lambda *a:steps.append('independent')), \
             patch('f3core.runtime.market_snapshot',side_effect=lambda *a:steps.append('market') or {'spec':SPEC}):
            runtime.private_preflight(self.api,self.cfg)
        self.assertEqual(steps,['clock','account','independent','market'])
        self.assertEqual(self.api.posts,[])

    def test_arm_confirmation_and_nonlinux_gates_precede_credentials(self):
        with patch('f3core.runtime.config.credentials',side_effect=AssertionError('must not read credentials')):
            with self.assertRaises(config.Attention): runtime.arm(self.cfg,'no')
            with patch('f3core.runtime.sys.platform','darwin'):
                with self.assertRaises(config.Attention): runtime.arm(self.cfg,'ARM F3 REAL MONEY')

    def test_public_client_never_enables_mutations(self):
        with patch('f3core.runtime.OKXClient') as maker:
            runtime.client(self.cfg)
            kwargs=maker.call_args.kwargs
            self.assertIs(kwargs['allow_orders'],False)
            self.assertIsNone(kwargs['credentials'])
            self.assertEqual(kwargs['mode'],'live')
            self.assertEqual(kwargs['proxy'],self.cfg['proxy'])


class ConfigurationSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name).resolve()
        self.directory=self.home/'f3-ledger'
        self.p=patch('f3core.config.Path.home',return_value=self.home)
        self.p.start(); self.addCleanup(self.p.stop)
        self.d=patch('f3core.config.data_dir',return_value=self.directory)
        self.d.start(); self.addCleanup(self.d.stop)

    def test_binding_reserves_v53_namespace_and_cannot_change_ledger(self):
        value=config.bind_account('123',self.directory)
        config.validate_binding('123',self.directory,value['binding_id'])
        shared=self.home/'.local/state/btc-v53-live/accounts'/(config.account_key('123')+'.json')
        saved=json.loads(shared.read_text())
        self.assertEqual(saved['account'],config.account_key('123'))
        self.assertEqual(saved['mode'],'live')
        self.assertEqual(saved['phase'],'active')
        with self.assertRaises(config.Attention): config.bind_account('123',self.home/'other')
        with self.assertRaises(config.Attention): config.validate_binding('123',self.directory,'another-binding')

    def test_existing_v53_binding_blocks_f3(self):
        shared=self.home/'.local/state/btc-v53-live/accounts'/(config.account_key('123')+'.json')
        config.atomic_json(shared,{'schema':1,'account':config.account_key('123'),'mode':'live',
                                  'directory':str(self.home/'v53'),'phase':'active','binding_id':'v53-id'})
        with self.assertRaises(config.Attention): config.bind_account('123',self.directory)
        self.assertFalse(config.binding_path('123').exists())

    def test_missing_shared_reservation_never_passes_validation(self):
        value=config.bind_account('123',self.directory)
        shared=self.home/'.local/state/btc-v53-live/accounts'/(config.account_key('123')+'.json')
        shared.unlink()
        with self.assertRaises((config.Attention,FileNotFoundError)):
            config.validate_binding('123',self.directory,value['binding_id'])

    def test_credentials_require_private_regular_file(self):
        p=self.home/'credentials.json'
        config.atomic_json(p,CREDS)
        self.assertEqual(config.credentials(p),CREDS)
        p.chmod(0o644)
        with self.assertRaises(config.Attention):config.credentials(p)
        p.chmod(0o600)
        link=self.home/'link.json'; link.symlink_to(p)
        with self.assertRaises(config.Attention):config.credentials(link)

    def test_config_rejects_demo_remote_proxy_and_disabled_limits(self):
        p=self.home/'config.json'
        baseline=dict(config.DEFAULT,capital_usdt=20,max_loss_usdt=1)
        config.atomic_json(p,baseline)
        self.assertEqual(config.load_config(p)['capital_usdt'],20)
        for change in ({'mode':'demo'},{'proxy':'http://other:17890'},{'proxy':'http://127.0.0.1:0'},
                       {'capital_usdt':True},{'max_loss_usdt':0},{'max_loss_usdt':30}):
            config.atomic_json(p,dict(baseline,**change),overwrite=True)
            with self.assertRaises(config.Attention):config.load_config(p)


if __name__=='__main__':unittest.main()
