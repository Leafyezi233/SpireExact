import json,threading,unittest,urllib.request,urllib.error,tempfile
from pathlib import Path
from unittest.mock import patch
from dashboard import jobs,server

class DashboardJobTests(unittest.TestCase):
    def test_manual_seeds_are_reserved_outside_holdout(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);(root/'experiments').mkdir()
            path=root/'experiments/seed-ledger.json';path.write_text('{"reserved":[],"runs":[]}')
            jobs.register_manual_seed(root,'manual-test','123','v1')
            jobs.register_manual_seed(root,'manual-test','123','v1')
            rows=json.loads(path.read_text())['runs']
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['kind'],'user_interactive')
            self.assertEqual(rows[0]['seeds'],['123'])
            self.assertTrue(rows[0]['not_a_holdout_result'])
    def test_seed_and_budget_do_not_accept_paths_or_commands(self):
        self.assertEqual(jobs.validate({'seed':' 123ABC ','minutes':15}),{'seed':'123ABC','minutes':15,'dp':True})
        self.assertEqual(jobs.validate({'seed':'123ABC','minutes':15,'dp':False}),{'seed':'123ABC','minutes':15,'dp':False})
        for seed in ('','../42','42;calc','--help','42\n43','a'*65):
            with self.assertRaises(ValueError):jobs.validate({'seed':seed})
        for budget in (True,0,181,'180'):
            with self.assertRaises(ValueError):jobs.validate({'seed':'42','minutes':budget})
        for dp in ('yes',1,None,0):
            with self.assertRaises(ValueError):jobs.validate({'seed':'42','dp':dp})
        with self.assertRaises(ValueError):jobs.validate({'seed':'42','prefix':'a-winning-route'})
        with self.assertRaises(ValueError):jobs.validate({'seed':'42','route-mode':'force-then-search'})

    def test_dp_switch_chooses_the_route_recipe(self):
        # The panel's DP entry is the route plugin, not the public i100 recipe:
        # the public switch list cannot be extended with --route-mode because
        # those flags are explicit and resolve_route_mode refuses to override them.
        request=jobs.validate({'seed':'42','minutes':45})
        profile={'runtime_profile':'server-bounded-large-gen0','worker_memory_mib':1792,
                 'solver_settings':['--feature-profile','i100','--clinic','--scheduler','focus']}
        on=jobs.solver_settings(request,profile)
        off=jobs.solver_settings({**request,'dp':False},profile)
        self.assertIn('--clinic',off)
        self.assertIn('--route-mode',on)
        self.assertEqual(on[on.index('--route-mode')+1],'force-then-search')
        self.assertIn('--strict-then-search',on)
        self.assertIn('--route-strict',on)
        self.assertEqual(on[on.index('--feature-profile')+1],'i100')
        for flag in ('--clinic','--scheduler','--root-policies','--macro-plateau','--strategy-prior'):
            self.assertNotIn(flag,on)

    def test_topology_presets_are_a_closed_set(self):
        self.assertEqual(jobs.DEFAULT_TOPOLOGY,'3x2')
        self.assertEqual(set(jobs.TOPOLOGIES),{'3x2','6x1','7x1','2x3'})
        for name,row in jobs.TOPOLOGIES.items():
            # A multi-threaded worker requires the legacy CLR runtime profile.
            if row['dop']>1:self.assertEqual(row['runtime_profile'],'legacy',name)
            self.assertIn(row['worker_memory_mib'],jobs.WORKER_MEMORY_CHOICES)
        for bad in ('4x4','','3X2','3x2 ','8x1'):
            with self.assertRaises(ValueError):jobs.validate({'seed':'42','topology':bad})
        for bad in (0,512,2048+1,True,'1792'):
            with self.assertRaises(ValueError):jobs.validate({'seed':'42','worker_memory_mib':bad})
        for bad in (0,-1,1000001,True,'100'):
            with self.assertRaises(ValueError):jobs.validate({'seed':'42','evaluations':bad})

    def test_absent_settings_keep_the_recorded_shape(self):
        # A caller that sends no topology must still get the historical argv,
        # so old manifests and the recorded public recipe stay comparable.
        request=jobs.validate({'seed':'42','minutes':45,'dp':False})
        profile={'runtime_profile':'server-large-gen0','worker_memory_mib':1792,
                 'solver_settings':['--scheduler','focus']}
        args=jobs.solver_settings(request,profile)
        self.assertEqual(args[args.index('--workers')+1],'7')
        self.assertEqual(args[args.index('--dop')+1],'1')
        self.assertEqual(args[args.index('--reserve-mib')+1],'1024')
        self.assertEqual(args[args.index('--runtime-profile')+1],'server-large-gen0')
        self.assertEqual(args[args.index('--worker-memory-mib')+1],'1792')
        self.assertEqual(args[args.index('--evaluations')+1],'1000000')

    def test_topology_and_memory_reach_the_launcher(self):
        request=jobs.validate({'seed':'42','minutes':45,'dp':True,'topology':'2x3',
                               'worker_memory_mib':1024,'evaluations':250000})
        profile={'runtime_profile':'server-bounded-large-gen0','worker_memory_mib':1792,
                 'solver_settings':[]}
        args=jobs.solver_settings(request,profile)
        self.assertEqual(args[args.index('--workers')+1],'2')
        self.assertEqual(args[args.index('--dop')+1],'3')
        self.assertEqual(args[args.index('--worker-memory-mib')+1],'1024')
        self.assertEqual(args[args.index('--reserve-mib')+1],'1024')
        self.assertEqual(args[args.index('--runtime-profile')+1],'legacy')
        self.assertEqual(args[args.index('--evaluations')+1],'250000')
        # The panel default is the multi-threaded 3x2 preset.
        default=jobs.validate({'seed':'42','minutes':45,'topology':'3x2'})
        args=jobs.solver_settings(default,profile)
        self.assertEqual((args[args.index('--workers')+1],args[args.index('--dop')+1]),('3','2'))

    def test_a_topology_preset_owns_the_runtime_profile_and_memory(self):
        # Regression: solver-profile.json repeats --runtime-profile and
        # --worker-memory-mib. argparse keeps the LAST occurrence, so appending
        # the profile recipe after the layout turned a 3x2 launch into
        # dop=2 + server-bounded-large-gen0 and tripped pool.py:88
        # ('Experimental runtime profiles require solver DOP 1').
        profile=json.loads((Path(__file__).resolve().parent.parent/'dashboard/solver-profile.json').read_text())
        request=jobs.validate({'seed':'42','minutes':30,'dp':False,'topology':'3x2'})
        args=jobs.solver_settings(request,profile)
        self.assertEqual(args.count('--runtime-profile'),1)
        self.assertEqual(args.count('--worker-memory-mib'),1)
        self.assertEqual(args[args.index('--runtime-profile')+1],'legacy')
        self.assertEqual(args[args.index('--dop')+1],'2')
        # The recipe keeps its own copies of those two flags when no topology
        # was requested, so recorded historical arguments stay byte-identical.
        legacy=jobs.validate({'seed':'42','minutes':30,'dp':False})
        self.assertEqual(jobs.solver_settings(legacy,profile).count('--runtime-profile'),2)

    def test_http_launch_requires_local_origin_and_session_token(self):
        http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        url=f'http://127.0.0.1:{http.server_port}'
        def submit(token=None,origin=None):
            headers={'Content-Type':'application/json'}
            if token:headers['X-SpireBoard-Token']=token
            if origin:headers['Origin']=origin
            req=urllib.request.Request(url+'/api/jobs',data=b'{"seed":"0","minutes":1}',headers=headers,method='POST')
            try:
                with urllib.request.urlopen(req,timeout=5)as r:return r.status,json.load(r)
            except urllib.error.HTTPError as e:
                with e:return e.code,json.load(e)
        try:
            with patch.object(jobs,'launch',return_value={'run_id':'manual-test','queued':False})as launch:
                self.assertEqual(submit()[0],403)
                self.assertEqual(submit(jobs.TOKEN,'https://example.invalid')[0],403)
                launch.assert_not_called()
                self.assertEqual(submit(jobs.TOKEN,url)[0],202)
                launch.assert_called_once()
            with urllib.request.urlopen(url+'/api/health',timeout=5)as r:
                self.assertEqual(json.load(r)['service'],'spireboard')
        finally:http.shutdown();http.server_close();thread.join(timeout=2)
