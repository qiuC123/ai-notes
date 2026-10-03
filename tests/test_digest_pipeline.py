from __future__ import annotations

import copy
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from ai_notes import digest, digest_pipeline as pipeline, digest_runtime as runtime


class PipelineTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        project = Path(__file__).resolve().parents[1]
        for file in ('config/digest_selection.json', 'docs/prompts/digest-selection.md'):
            dest = self.root / file
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes((project/file).read_bytes())
        self.now = digest._now()
        self.period = (self.now.date()-timedelta(days=1)).isoformat()
        self.calls=[]
        self.defer=False
        self.text='Organizes local files. Install with Python. MIT License.'
        self.client=httpx.Client(transport=httpx.MockTransport(self.respond))
        self.addCleanup(self.client.close)
        self.real_model=runtime.ModelClient
        model_patch=patch.object(runtime,'ModelClient',side_effect=lambda root: self.real_model(root,base_url='https://provider.example/v1',model='fixture',api_key='fixture-only',client=self.client))
        model_patch.start(); self.addCleanup(model_patch.stop)
        source_patch=patch.object(pipeline.sources,'fetch',side_effect=self.fetch)
        source_patch.start(); self.addCleanup(source_patch.stop)
        supplement=patch.object(pipeline.sources,'collect',return_value={'status':'empty','source_results':[]})
        supplement.start(); self.addCleanup(supplement.stop)
        self.seed()

    def seed(self):
        discovered=(self.now-timedelta(days=40)).isoformat()
        records=[]
        for i in range(6):
            url=f'https://projects.example/tool-{i}'
            records.append(dict(url=url,title=f'Tool {i}',category='开源项目',summary='Local files',reason='Reusable workflow',
                source_urls=[url],published_at=None,kind='project',evidence_status='discovered',evidence_urls=[],
                verification_level='documented',verified_at=None,discovered_at=discovered,change_note=''))
        digest.ingest(self.root,dict(schema_version='digest-batch.v2',run_id='fixture',collected_at=discovered,
            sources=[dict(name='fixture',url=records[0]['url'],status='ok',detail='Synthetic source')],candidates=records))

    def fetch(self,root,url):
        raw=self.text.encode(); sha=hashlib.sha256(raw).hexdigest()
        path=self.root/'source.txt'; path.write_bytes(raw)
        return dict(url=url,body_path='source.txt',sha256=sha,content_type='text/plain',fetched_at=self.now.isoformat(),checked_at=self.now.isoformat())

    def respond(self,request):
        payload=json.loads(request.content)
        system=payload['messages'][0]['content']
        material=json.loads(payload['messages'][1]['content'])
        self.calls.append(system)
        if 'You shortlist' in system:
            ids=[c['candidate_id'] for c in material['cards']]
            output={'selected_ids':ids[:material['deep_limit']], 'decisions':[{'candidate_id':cid,'reason':'Useful local workflow'} for cid in ids]}
        elif 'supported practical facts' in system:
            if self.defer:
                output={'qualified':False,'reason':'Required usage evidence missing','facts':None}
            else:
                url=material['originals'][0]['url']
                facts=dict(title=material['candidate']['title'],category='开源项目',summary='Organizes local files.',reason='减少重复整理工作。',
                    audience='处理本地文件的读者',usage_conditions='Install with Python.',detail='可用于重复整理目录；先确认当前依赖。',
                    retention_reason='本地文件管理是持续需求。',evidence_urls=[url],open_source_status='confirmed',
                    claims=[{'field':field,'text':text,'evidence_url':url,'quote':text} for field,text in
                        [('summary','Organizes local files.'),('usage_conditions','Install with Python.'),('license','MIT License.'),('category','Organizes local files.')]])
                if material['candidate']['kind']=='update':
                    facts['claims'].append(dict(field='change_note',text='Adds local file organization.',evidence_url=url,quote='Organizes local files.'))
                output={'qualified':True,'reason':'Original documents support it','facts':facts}
        else:
            url=material['card']['evidence_context'][0]['url']
            output=dict(precheck=dict(status='PASS',reasons=['Concrete workflow'],evidence_refs=[url]),
                scores={key:dict(score=8,reason='Readable source supports this dimension',evidence_refs=[url]) for key in ('value','novelty','evidence','usability','interest')},
                flags=[],reason='适合本地文件管理读者。')
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(output,ensure_ascii=False)}}],'usage':{'prompt_tokens':10,'completion_tokens':10}})

    def run_job(self,kind='daily',period=None):
        job=runtime.enqueue(self.root,{'action':'generate','ranking_type':kind,'period':period or self.period})
        return job,runtime.work_once(self.root)

    def test_original_review_score_archive_and_retry_are_idempotent(self):
        job,result=self.run_job()
        self.assertEqual('completed',result['status'],result)
        self.assertEqual('archived',result['result']['status'])
        self.assertEqual(6,result['result']['item_count'])
        self.assertEqual(13,len(self.calls))
        self.assertEqual(1,len(runtime.notices(self.root)))
        self.assertEqual('idle',runtime.work_once(self.root)['status'])
        self.assertEqual(13,len(self.calls))
        with runtime._db(self.root,write=False) as con:
            prepared=json.loads(con.execute('SELECT checkpoints FROM jobs WHERE job_id=?',(job['job_id'],)).fetchone()[0])
        self.assertEqual(6,len(prepared['issue']['items']))
        self.assertTrue(all(x['verification_level']=='documented' for x in prepared['issue']['items']))

    def test_monthly_shortfall_remains_draft(self):
        previous_month=(self.now.date().replace(day=1)-timedelta(days=1)).strftime('%Y-%m')
        _,result=self.run_job('monthly',previous_month)
        self.assertEqual('completed',result['status'],result)
        self.assertEqual('draft',result['result']['status'])
        self.assertEqual(6,result['result']['item_count'])
        self.assertIsNone(pipeline._existing(self.root,'monthly',previous_month))
        self.assertEqual(14,runtime.notices(self.root)[0]['payload']['shortfall'])

    def test_archive_commit_before_notice_is_recovered_without_model_calls(self):
        real_archive=digest.archive
        def crash(root,issue):
            real_archive(root,issue)
            raise digest.DigestError('archive committed; injected export/notification interruption')
        with patch.object(digest,'archive',side_effect=crash):
            job,result=self.run_job()
        self.assertEqual('failed',result['status'])
        before=len(self.calls)
        runtime.retry(self.root,job['job_id'])
        result=runtime.work_once(self.root)
        self.assertEqual('completed',result['status'],result)
        self.assertEqual(before,len(self.calls))
        self.assertEqual(1,len([n for n in runtime.notices(self.root) if n['payload']['outcome']=='archived']))

    def test_deferred_checkpoint_can_resume_without_verified_at(self):
        self.defer=True
        job=runtime.enqueue(self.root,{'action':'generate','ranking_type':'daily','period':self.period})
        original=runtime.checkpoint
        interrupted=False
        def crash(root,job_id,owner,stage,value):
            nonlocal interrupted
            original(root,job_id,owner,stage,value)
            if stage.startswith('original:') and not interrupted:
                interrupted=True
                raise OSError('interrupted after deferred checkpoint')
        with patch.object(runtime,'checkpoint',side_effect=crash):
            result=runtime.work_once(self.root)
        self.assertEqual('failed',result['status'])
        runtime.retry(self.root,job['job_id'])
        result=runtime.work_once(self.root)
        self.assertEqual('completed',result['status'],result)
        self.assertEqual('draft',result['result']['status'])
        self.assertEqual(0,result['result']['item_count'])
        self.assertEqual(7,len(self.calls))

    def test_unread_quote_is_rejected_and_not_verified(self):
        contexts=[dict(url='https://project.example',text='real text')]
        output={'qualified':True,'reason':'claimed','facts':dict(title='t',category='开源项目',summary='s',reason='r',audience='a',usage_conditions='u',detail='d',retention_reason='r',evidence_urls=['https://project.example'],open_source_status='confirmed',claims=[dict(field='summary',text='x',evidence_url='https://project.example',quote='invented')])}
        with self.assertRaisesRegex(runtime.RuntimeError,'quote'):
            pipeline._facts(output,contexts,{'kind':'project','category':'开源项目'})

    def test_existing_unrelated_archive_is_not_announced_again(self):
        prior={'items':[]}
        job={'payload':{'ranking_type':'daily','period':self.period},'checkpoints':{}}
        with patch.object(pipeline,'_existing',return_value=prior),patch.object(digest,'recover'):
            result=pipeline.generate(self.root,job,'unused')
        self.assertTrue(result['reused_archive'])
        self.assertEqual([],runtime.notices(self.root))
        self.assertEqual([],self.calls)

    def test_monthly_triage_keeps_all_ids_with_bounded_prose(self):
        cards=[dict(url=f'https://projects.example/tool-{i}',canonical_url=f'https://projects.example/tool-{i}',kind='project',category='开源项目',title='long'*50,summary='文字'*1000,reason='理由'*1000,change_note='') for i in range(150)]
        compact=pipeline._bounded_triage(cards)
        self.assertEqual(150,len(compact))
        self.assertEqual(150,len({x['candidate_id'] for x in compact}))
        self.assertLessEqual(len(runtime._json({'cards':compact})),50000)
        self.assertEqual('文字'*1000,cards[0]['summary'])

    def test_verified_update_uses_original_increment_not_discovery_claim(self):
        runtime.enqueue(self.root,{'action':'generate','ranking_type':'daily','period':self.period})
        job=runtime.claim(self.root,'owner')
        record=digest.candidates(self.root,ranking_type='daily',period=self.period)['candidates'][0]
        event_url=record['url']+'/release/v2'
        record.update(kind='update',change_note='Unproven magical rewrite',event=dict(id='v2',url=event_url,occurred_at=(self.now-timedelta(days=1)).isoformat(),type='update'))
        model=self.real_model(self.root,base_url='https://provider.example/v1',model='fixture',api_key='fixture-only',client=self.client)
        value=pipeline._review_original(self.root,job,'owner',model,record,'update-test',37)
        self.assertEqual('Adds local file organization.',value['observation']['change_note'])


if __name__=='__main__':
    unittest.main()
