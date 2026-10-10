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
