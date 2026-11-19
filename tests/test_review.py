import asyncio
import base64
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
import common
import fetcher
import run_agy
import report_server
spec = importlib.util.spec_from_file_location('local_review', ROOT/'review.py')
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)

HEAD, BASE, MERGE = 'a'*40, 'b'*40, 'c'*40
class FakeGitHub:
    def __init__(self, change=False, binary=False, mode='100644', big=False):
        self.count = 0
        self.change, self.binary, self.mode, self.big = change, binary, mode, big
    def get(self, endpoint):
        if endpoint == 'pulls/1':
            self.count += 1
            return {'number':1,'state':'open','head':{'sha': HEAD if not self.change or self.count == 1 else 'd'*40},
                    'base':{'sha':BASE,'repo':{'full_name':'owner/repo'}},'changed_files':1}
        if endpoint.startswith('compare/'):
            return {'merge_base_commit':{'sha':MERGE},'files':[{'filename':'src/main.py','status':'modified'}]}
        if endpoint.startswith('git/trees/'):
            blob = '1'*40 if HEAD in endpoint else '2'*40
            return {'truncated':False,'tree':[{'path':'src/main.py','type':'blob','mode':self.mode,'size':2,'sha':blob}]}
        if endpoint.startswith('git/blobs/'):
            raw = b'\0' if self.binary else (b'x'*90_001 if self.big else (b'x=2\n' if '1'*40 in endpoint else b'x=1\n'))
            return {'encoding':'base64','content':base64.b64encode(raw).decode()}
        raise AssertionError(endpoint)

class ValidationTests(unittest.TestCase):
    def test_allowlist_exact(self):
        common.allow_repo('owner/repo', {'repositories':['owner/repo']})
        for name in ['Owner/repo','owner/other','../repo','owner/repo;ls','https://github.com/a/b']:
            with self.assertRaises(common.ReviewError):
                common.allow_repo(name, {'repositories':['owner/repo']})
    def test_pr_validation(self):
        for value in [0,-1,True,'1;ls']:
            with self.assertRaises(common.ReviewError): common.pr_number(value)
    def test_snapshot(self):
        data=fetcher.prepare(FakeGitHub(), 'owner/repo', 1)
        self.assertEqual((data['head'],data['base'],data['merge_base']), (HEAD,BASE,MERGE))
        self.assertEqual(data['files'][0]['after'], 'x=2\n')
        self.assertIn('+x=2', data['files'][0]['diff'])
        self.assertEqual(data['id'],fetcher.prepare(FakeGitHub(),'owner/repo',1)['id'])
    def test_moved_pr(self):
        with self.assertRaisesRegex(common.ReviewError,'moved'):
            fetcher.prepare(FakeGitHub(change=True),'owner/repo',1)
    def test_closed_pr(self):
        with self.assertRaisesRegex(common.ReviewError,'open'):
            fetcher.refs({'state':'closed'},'owner/repo',1)
    def test_symlink_rejected(self):
        with self.assertRaisesRegex(common.ReviewError,'Symlink'):
            fetcher.prepare(FakeGitHub(mode='120000'),'owner/repo',1)
    def test_binary_rejected(self):
        with self.assertRaises(common.ReviewError): fetcher.prepare(FakeGitHub(binary=True),'owner/repo',1)
    def test_large_rejected(self):
        with self.assertRaises(common.ReviewError): fetcher.prepare(FakeGitHub(big=True),'owner/repo',1)
    def test_truncated_tree_rejected(self):
        api=FakeGitHub()
        with patch.object(api,'get',return_value={'truncated':True}):
            with self.assertRaises(common.ReviewError): fetcher.tree(api,HEAD)
    def test_subprocess_timeout(self):
        with self.assertRaisesRegex(common.ReviewError,'timed out'):
            common.bounded_run([sys.executable,'-c','import time;time.sleep(20)'],env=common.clean_env(),timeout=.05)
    def test_subprocess_output_limit(self):
        with self.assertRaises(common.ReviewError):
            common.bounded_run([sys.executable,'-c','print("x"*10000)'],env=common.clean_env(),limit=100)

class RunnerTests(unittest.TestCase):
    def test_settings(self):
        run_agy.validate_settings(ROOT/'docker/agy-settings.json')
    def test_stream_framing(self):
        good={'event':'result','result':{'status':'SUCCESS','response':'ok','num_turns':1}}
        self.assertEqual(run_agy.parse_result(json.dumps(good).encode())['response'],'ok')
        for raw in [b'',b'{}',b'not-json',json.dumps(good).encode()+b'\n'+json.dumps(good).encode(),
                    json.dumps(good).encode()+b'\n{"event":"step_update"}']:
            with self.assertRaises((common.ReviewError,ValueError)):run_agy.parse_result(raw)
    def test_source_and_tokens_not_in_argv_or_env(self):
        import io
        source={'files':[{'after':'SENSITIVE_SOURCE_MARKER'}]}
        called={}
        def fake(args, **kwargs):
            called.update(args=args,**kwargs)
            return b'{"event":"result","result":{"status":"SUCCESS","response":"ok","num_turns":1}}'
        with patch.object(run_agy,'validate_settings'), patch.object(run_agy,'bounded_run',side_effect=fake), \
             patch.object(sys,'stdin',io.TextIOWrapper(io.BytesIO(json.dumps(source).encode()))), \
             patch.dict(os.environ,{'GH_TOKEN':'DO_NOT_PASS','REPORT_MCP_TOKEN':'ALSO_NOT_PASS','HOME':'/home/reviewer'}), \
             patch('builtins.print'):
            run_agy.main()
        self.assertNotIn('SENSITIVE_SOURCE_MARKER',str(called['args']))
        self.assertNotIn('DO_NOT_PASS',str(called['env']))
        self.assertNotIn('ALSO_NOT_PASS',str(called['env']))
        event=json.loads(called['input'])
        self.assertEqual(event['event'],'user')
        self.assertIn('SENSITIVE_SOURCE_MARKER',event['message']['content'])
        self.assertNotIn('--dangerously-skip-permissions',called['args'])
    def test_fetch_token_only(self):
        with patch.dict(os.environ,{'GH_TOKEN':'FETCH_ONLY','PUBLISHER_TOKEN':'NO'}), patch.object(review,'bounded_run',return_value=b'{}') as call:
            review.docker(['exec','runner'])
            self.assertNotIn('GH_TOKEN',call.call_args.kwargs['env'])
            review.docker(['run','fetcher'],fetch=True)
            self.assertEqual(call.call_args.kwargs['env']['GH_TOKEN'],'FETCH_ONLY')
            self.assertNotIn('PUBLISHER_TOKEN',call.call_args.kwargs['env'])

class ReportTests(unittest.TestCase):
    def test_auth(self):
        async def check(auth):
            events=[]
            async def app(scope,receive,send): events.append({'called':True})
            async def send(event):events.append(event)
            middleware=report_server.BearerAuth(app,'x'*32)
            await middleware({'type':'http','headers':auth},None,send)
            return events
        self.assertEqual(asyncio.run(check([]))[0]['status'],401)
        self.assertEqual(asyncio.run(check([(b'authorization',b'Bearer '+b'x'*32)])),[{'called':True}])
        self.assertEqual(asyncio.run(check([(b'authorization',b'Bearer '+b'x'*32)]*2))[0]['status'],401)
    def test_path_traversal_and_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)
            with self.assertRaises(ValueError):report_server.load_report(directory,'../secret')
            (directory/('a'*64+'.json')).symlink_to('/etc/passwd')
            with self.assertRaises(ValueError):report_server.load_report(directory,'a'*64)
    def test_mcp_readonly_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            server=report_server.make_server(Path(tmp),['localhost:*'])
            tools=asyncio.run(server.list_tools())
            self.assertEqual({t.name for t in tools},{'list_reports','get_report'})
            self.assertTrue(all(t.annotations.readOnlyHint for t in tools))
    def test_operator_dedup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'config.json').write_text('{"repositories":["owner/repo"]}')
            bundle=fetcher.prepare(FakeGitHub(),'owner/repo',1)
            (root/'state/reports').mkdir(parents=True)
            (root/'state/reports'/ (bundle['id']+'.json')).write_text('{}')
            with patch.object(review,'ROOT',root), patch.object(review,'fetch',return_value=bundle),patch.object(review,'docker') as model,patch('builtins.print'):
                review.review('owner/repo',1)
                model.assert_not_called()
    def test_operator_marks_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'config.json').write_text('{"repositories":["owner/repo"]}')
            bundle=fetcher.prepare(FakeGitHub(),'owner/repo',1)
            responses=[bundle,{'head':HEAD,'base':BASE},{'head':'d'*40,'base':BASE}]
            with patch.object(review,'ROOT',root), patch.object(review,'fetch',side_effect=responses),patch.object(review,'docker',return_value=b'{"response":"report"}'),patch('builtins.print'):
                review.review('owner/repo',1)
            data=json.loads(next((root/'state/reports').glob('*.json')).read_text())
            self.assertEqual(data['freshness'],'stale-at-completion')
            self.assertFalse(data['executed_code'])
            self.assertFalse(data['published_to_github'])

if __name__=='__main__':unittest.main()
