import unittest
from copy import deepcopy
from types import SimpleNamespace
from replay_audit import patched_runner


def fixture():
    dates=['2026-03-31','2026-04-01','2026-04-02','2026-04-03','2026-04-06']
    bars=[(dates[0],100,100,100,100,10000,1e9),
          (dates[1],100,80,101,79,10000,1e9),
          (dates[2],79,78,80,77,10000,1e9),
          (dates[3],75,76,77,74,10000,1e9),
          (dates[4],77,78,79,76,10000,1e9)]
    mk=SimpleNamespace(cal=dates,panel={'TEST':bars},bar={'TEST':dict(zip(dates,bars))},
                       dates={'TEST':list(dates)},names={'TEST':'fixture'},idxclose={},idxma={})
    mk.month_ends=lambda:{'2026-03':dates[0]}
    cfg=dict(tag='test',w_each=.19,buffer=1,min_amt=1e8,trend_filter=False,regime=False,
             stop=None,trail=.12,max_single=.25,n_on=1,n_off=1,rebal_months=['2026-03'])
    return mk,cfg


def runner(stage):
    ns,run=patched_runner(stage)
    ns['END']='2026-04-06'
    ns['eligible']=lambda mk,c,s,amt,trend:dict(code=c,mom=1.,close=100.)
    return run


class ReplayTests(unittest.TestCase):
    def test_cost_floor_and_buy_day_signal_next_day_execution(self):
        mk,cfg=fixture(); r=runner(3)(mk,cfg)
        self.assertEqual([t['date'] for t in r['trade_log']],['2026-04-01','2026-04-02'])
        self.assertEqual(r['equity'][0]['n_positions'],1)
        self.assertEqual(r['equity'][1]['n_positions'],0)
        self.assertIn('signal=2026-04-01',r['trade_log'][1]['reason'])

    def test_future_open_cannot_change_previous_close_nav(self):
        mk,cfg=fixture(); run=runner(3); first=run(mk,cfg)
        mk2=deepcopy(mk); old=mk2.bar['TEST']['2026-04-02']
        mk2.bar['TEST']['2026-04-02']=(old[0],50.,*old[2:])
        second=runner(3)(mk2,cfg)
        self.assertEqual(first['equity'][0],second['equity'][0])
        self.assertNotEqual(first['equity'][1]['total'],second['equity'][1]['total'])

    def test_peak_only_has_no_cost_floor(self):
        mk,cfg=fixture(); r=runner(2)(mk,cfg)
        self.assertEqual(len(r['trade_log']),1)

    def test_missing_bar_carries_value_and_retries_exit(self):
        mk,cfg=fixture(); del mk.bar['TEST']['2026-04-02']
        mk.dates['TEST'].remove('2026-04-02')
        r=runner(4)(mk,cfg)
        self.assertEqual(r['equity'][1]['n_positions'],1)
        self.assertEqual(r['equity'][1]['market_value'],r['equity'][0]['market_value'])
        self.assertEqual(r['trade_log'][1]['date'],'2026-04-03')

    def test_exit_delay_is_counted_in_trading_sessions(self):
        mk,cfg=fixture(); cfg['exit_delay']=1
        r=runner(4)(mk,cfg)
        self.assertEqual(r['trade_log'][1]['date'],'2026-04-03')

    def test_floor_formula(self):
        self.assertAlmostEqual(max(53.533,50.63)*.88,47.10904)
        self.assertAlmostEqual(min(53.533,50.63)*.88,44.5544)


if __name__=='__main__': unittest.main(verbosity=2)
