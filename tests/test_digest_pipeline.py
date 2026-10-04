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
        # A fully due period even when these tests run before the 09:00 slot.
        self.period = (self.now.date()-timedelta(days=2)).isoformat()
        self.calls=[]
        self.fetch_calls=[]
        self.score_inputs=[]
        self.malformed_scores=False
        self.malformed_scope=False
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
        self.fetch_calls.append(url)
        raw=self.text.encode(); sha=hashlib.sha256(raw).hexdigest()
        path=self.root/'source.txt'; path.write_bytes(raw)
        return dict(url=url,body_path='source.txt',sha256=sha,content_type='text/html' if getattr(self,'html',False) else 'text/plain',fetched_at=self.now.isoformat(),checked_at=self.now.isoformat())

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
                if material['candidate']['kind']=='news':
                    facts.update(category='模型与运行工具',open_source_status='closed',event_date=self.news_date)
                    facts['claims']=[c for c in facts['claims'] if c['field']!='license']
                    facts['claims'].append(dict(field='event_date',text=self.news_date,evidence_url=url,quote=self.news_date))
                if 'claims.scope.v1' in system:
                    for claim in facts['claims']:
                        if claim['field']=='usage_conditions':
                            claim['scope']=dict(version=None,platform=None,host_architecture=None,
                                build_architecture=None,installation_path='Python',requirement='required',
                                conditions=[],evidence_kind='documentation')
                            if self.malformed_scope:
                                claim['scope']['platform']='invented platform'
                output={'qualified':True,'reason':'Original documents support it','facts':facts}
        else:
            self.score_inputs.append(material)
            url=material['card']['evidence_context'][0]['url']
            output=dict(precheck=dict(status='PASS',reasons=['Concrete workflow'],evidence_refs=[url]),
                scores={key:dict(score=8,reason='Readable source supports this dimension',evidence_refs=[url]) for key in ('value','novelty','evidence','usability','interest')},
                flags=[],reason='适合本地文件管理读者。')
            if self.malformed_scores:
                output['decision']='select'  # Model cannot decide or bind a review.
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
            stages={row[0] for row in con.execute('SELECT stage FROM requests')}
        self.assertEqual({'screen-v4-evidence-scope', 'verify-facts-v5-scoped-claims',
                          'value-score-v5-scoped-source'}, stages)
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

    def test_old_screen_keeps_original_source_contract_without_backfilling_scope(self):
        runtime.enqueue(self.root,{'action':'generate','ranking_type':'daily','period':self.period})
        job=runtime.claim(self.root,'owner')
        # Older persisted screens have no source_review_contract marker.
        job['checkpoints']['screen']={'records':{},'selected_ids':[],'decisions':[],'offset':0}
        record=digest.candidates(self.root,ranking_type='daily',period=self.period)['candidates'][0]
        model=self.real_model(self.root,base_url='https://provider.example/v1',model='fixture',api_key='fixture-only',client=self.client)
        reviewed=pipeline._review_original(self.root,job,'owner',model,record,'legacy-source',37)
        self.assertEqual(pipeline.VERIFY_PROMPT_V4+'\n'+pipeline.READER_FOCUS,self.calls[-1])
        self.assertTrue(all('scope' not in claim for claim in reviewed['facts']['claims']))
        with runtime._db(self.root,write=False) as con:
            self.assertEqual('verify-facts-v4-source-scope',con.execute(
                'SELECT stage FROM requests WHERE request_id=?',(reviewed['request_id'],)).fetchone()['stage'])
        pipeline._review_original(self.root,job,'owner',model,record,'legacy-source',37)
        self.assertEqual(1,len(self.calls))

    def test_invalid_new_scope_retains_raw_receipt_and_defers_without_retries(self):
        self.malformed_scope=True
        job,result=self.run_job()
        self.assertEqual('completed',result['status'],result)
        self.assertEqual('draft',result['result']['status'])
        self.assertEqual(0,result['result']['item_count'])
        self.assertEqual(7,len(self.calls))  # screen plus six original reviews
        self.assertEqual(6,len(self.fetch_calls))
        self.assertEqual([],self.score_inputs)
        self.assertTrue(all('原文结构核验失败' in failure for failure in result['result']['failures']))
        with runtime._db(self.root,write=False) as con:
            checkpoints=json.loads(con.execute('SELECT checkpoints FROM jobs WHERE job_id=?',(job['job_id'],)).fetchone()[0])
        receipts=[value for key,value in checkpoints.items() if key.startswith('original-response:')]
        self.assertEqual(6,len(receipts))
        self.assertTrue(all(value['checks'] and value['contexts'] for value in receipts))
        self.assertTrue(all(next(claim for claim in value['receipt']['output']['facts']['claims']
                                 if claim['field']=='usage_conditions')['scope']['platform']=='invented platform' for value in receipts))
        self.assertEqual('idle',runtime.work_once(self.root)['status'])
        self.assertEqual(7,len(self.calls))

    def test_original_response_checkpoint_crash_reuses_bad_scope_without_fetching(self):
        self.malformed_scope=True
        original=runtime.checkpoint
        interrupted=False
        retained=None
        def crash(root,job_id,owner,stage,value):
            nonlocal interrupted,retained
            original(root,job_id,owner,stage,value)
            if stage.startswith('original-response:') and not interrupted:
                interrupted=True
                retained=copy.deepcopy(value)
                raise OSError('interrupted after raw original response checkpoint')
        with patch.object(runtime,'checkpoint',side_effect=crash):
            job,result=self.run_job()
        self.assertEqual('failed',result['status'])
        self.assertEqual(2,len(self.calls))
        self.assertEqual(1,len(self.fetch_calls))
        original_url=self.fetch_calls[0]
        runtime.retry(self.root,job['job_id'])
        result=runtime.work_once(self.root)
        self.assertEqual('completed',result['status'],result)
        self.assertEqual(7,len(self.calls))  # only five remaining review calls
        self.assertEqual(6,len(self.fetch_calls))
        self.assertEqual(1,self.fetch_calls.count(original_url))
        with runtime._db(self.root,write=False) as con:
            checkpoints=json.loads(con.execute('SELECT checkpoints FROM jobs WHERE job_id=?',(job['job_id'],)).fetchone()[0])
        self.assertIn(retained,[value for key,value in checkpoints.items() if key.startswith('original-response:')])
        originals=[value for key,value in checkpoints.items() if key.startswith('original:')]
        self.assertEqual(6,len(originals))
        self.assertTrue(all(value['deferred'] for value in originals))

    def scoped_review_fixture(self):
        runtime.enqueue(self.root,{'action':'generate','ranking_type':'daily','period':self.period})
        job=runtime.claim(self.root,'owner')
        pipeline._save(self.root,job,'owner','screen',{'source_review_contract':'claims.scope.v1'})
        record=digest.candidates(self.root,ranking_type='daily',period=self.period)['candidates'][0]
        model=self.real_model(self.root,base_url='https://provider.example/v1',model='fixture',api_key='fixture-only',client=self.client)
        return job,record,model

    def test_expired_original_resumes_new_response_instead_of_repeating_refresh(self):
        job,record,model=self.scoped_review_fixture()
        first=pipeline._review_original(self.root,job,'owner',model,record,'expiry',37)
        self.assertEqual(1,len(self.calls))
        original=runtime.checkpoint
        retained=None
        def crash(root,job_id,owner,stage,value):
            nonlocal retained
            original(root,job_id,owner,stage,value)
            if stage=='original-response:expiry':
                retained=copy.deepcopy(value)
                raise OSError('interrupted after refresh response checkpoint')
        future=digest._timestamp(first['verified_at'],'verified_at')+timedelta(hours=24)
        self.now=future
        with patch.object(digest,'_now',return_value=future):
            with patch.object(runtime,'checkpoint',side_effect=crash),self.assertRaises(OSError):
                pipeline._review_original(self.root,job,'owner',model,record,'expiry',37)
            self.assertEqual(2,len(self.calls))
            self.assertEqual(2,len(self.fetch_calls))
            self.assertNotEqual(first['request_id'],retained['receipt']['request_id'])
            self.assertEqual(first['request_id'],job['checkpoints']['original:expiry']['request_id'])
            # Reload the persisted checkpoints, as a restarted worker would.
            with runtime._db(self.root,write=False) as con:
                job['checkpoints']=json.loads(con.execute('SELECT checkpoints FROM jobs WHERE job_id=?',(job['job_id'],)).fetchone()[0])
            result=pipeline._review_original(self.root,job,'owner',model,record,'expiry',37)
        self.assertEqual(retained['receipt']['request_id'],result['request_id'])
        self.assertEqual(2,len(self.calls))
        self.assertEqual(2,len(self.fetch_calls))
        self.assertEqual(retained,job['checkpoints']['original-response:expiry'])

    def test_frozen_original_input_reuses_http_receipt_before_response_checkpoint(self):
        job,record,model=self.scoped_review_fixture()
        original=runtime.checkpoint
        def crash(root,job_id,owner,stage,value):
            if stage=='original-response:before-raw':
                raise OSError('interrupted before response checkpoint write')
            original(root,job_id,owner,stage,value)
        with patch.object(runtime,'checkpoint',side_effect=crash),self.assertRaises(OSError):
            pipeline._review_original(self.root,job,'owner',model,record,'before-raw',37)
        with runtime._db(self.root,write=False) as con:
            job['checkpoints']=json.loads(con.execute('SELECT checkpoints FROM jobs WHERE job_id=?',(job['job_id'],)).fetchone()[0])
            receipt=con.execute('SELECT request_id,status FROM requests').fetchone()
        self.assertEqual('succeeded',receipt['status'])
        self.assertNotIn('original-response:before-raw',job['checkpoints'])
        frozen=copy.deepcopy(job['checkpoints']['original-input:before-raw'])
        self.now+=timedelta(minutes=5)
        self.text+=' Updated page after the interruption.'
        with patch.object(pipeline,'VERIFY_PROMPT',pipeline.VERIFY_PROMPT+' Changed current prompt.'):
            result=pipeline._review_original(self.root,job,'owner',model,record,'before-raw',37)
        self.assertEqual(receipt['request_id'],result['request_id'])
        self.assertEqual(frozen,job['checkpoints']['original-input:before-raw'])
        self.assertEqual(1,len(self.calls))
        self.assertEqual(1,len(self.fetch_calls))

    def test_uncertain_original_uses_same_frozen_input_without_second_http_call(self):
        job,record,_=self.scoped_review_fixture()
        attempts=[]
        def unavailable(request):
            attempts.append(request)
            raise httpx.ReadTimeout('outcome unknown',request=request)
        with httpx.Client(transport=httpx.MockTransport(unavailable)) as client:
            model=self.real_model(self.root,base_url='https://provider.example/v1',model='fixture',api_key='fixture-only',client=client)
            with self.assertRaises(runtime.RequestUncertain):
                pipeline._review_original(self.root,job,'owner',model,record,'uncertain',37)
            frozen=copy.deepcopy(job['checkpoints']['original-input:uncertain'])
            self.now+=timedelta(minutes=5)
            with self.assertRaises(runtime.RequestUncertain):
                pipeline._review_original(self.root,job,'owner',model,record,'uncertain',37)
        self.assertEqual(1,len(attempts))
        self.assertEqual(1,len(self.fetch_calls))
        self.assertEqual(frozen,job['checkpoints']['original-input:uncertain'])

    def add_news(self, days_ago=2):
        self.news_date=(self.now-timedelta(days=days_ago)).isoformat()
        self.text+=' Announced '+self.news_date
        url='https://news.example/model-announcement'
        digest.ingest(self.root,dict(schema_version='digest-batch.v2',run_id='news-fixture',collected_at=self.now.isoformat(),
            sources=[dict(name='official',url=url,status='ok',detail='Synthetic news discovery')],candidates=[
                dict(url=url,title='Model announcement',category='模型与运行工具',summary='News discovery',reason='Possible reader impact',
                     source_urls=[url],published_at=None,kind='news',evidence_status='discovered',evidence_urls=[],
                     verification_level='documented',verified_at=None,discovered_at=self.now.isoformat(),change_note='')]))

    def test_news_gets_original_event_and_can_archive_without_open_source_license(self):
        self.add_news()
        self.html=True
        self.text=f'<html><p>Organizes local files. Install with Python. MIT License.</p><time datetime="{self.news_date}">Announcement day</time></html>'
        _,result=self.run_job()
        self.assertEqual('completed',result['status'],result)
        self.assertEqual(7,result['result']['item_count'])
        issue=pipeline._existing(self.root,'daily',self.period)
        news=next(item for item in issue['items'] if item['kind']=='news')
        self.assertEqual(self.news_date,news['event']['occurred_at'])
        self.assertEqual(news['url'],news['event']['url'])
        self.assertIn('AI 动态',issue['title'])
        self.assertEqual('documented',news['verification_level'])

    def test_old_news_date_is_saved_but_not_repackaged_for_today(self):
        self.add_news(days_ago=3)
        _,result=self.run_job()
        self.assertEqual('completed',result['status'],result)
        self.assertEqual(6,result['result']['item_count'])
        self.assertFalse(any(item['kind']=='news' for item in pipeline._existing(self.root,'daily',self.period)['items']))

    def test_news_and_project_update_share_one_issue_slot_for_same_event(self):
        self.add_news()
        discovered=(self.now-timedelta(days=40)).isoformat()
        event_url='https://news.example/model-announcement'
        digest.ingest(self.root,dict(schema_version='digest-batch.v2',run_id='update-fixture',collected_at=self.now.isoformat(),
            sources=[dict(name='original',url=event_url,status='ok',detail='Synthetic update of same event')],candidates=[
                dict(url='https://projects.example/same-model',title='Same model update',category='开源项目',summary='Update',reason='Changed workflow',
                     source_urls=[event_url],published_at=None,kind='update',evidence_status='discovered',evidence_urls=[],
                     event=dict(id='v2',url=event_url,occurred_at=self.news_date,type='update'),
                     verification_level='documented',verified_at=None,discovered_at=discovered,change_note='Updated files')]))
        _,result=self.run_job()
        self.assertEqual('completed',result['status'],result)
        self.assertEqual(7,result['result']['item_count'])
        issue=pipeline._existing(self.root,'daily',self.period)
        self.assertEqual(1,sum(item.get('event',{}).get('url')==event_url for item in issue['items']))

    def test_score_input_excludes_ledger_eligibility_and_model_response_is_audited(self):
        job,result=self.run_job()
        self.assertEqual('completed',result['status'],result)
        for value in self.score_inputs:
            self.assertNotIn('prepare_id',value)
            self.assertNotIn('reviewer',value)
            self.assertNotIn('eligibility',value['card'])
            self.assertNotIn('observation',value['card'])
            self.assertNotIn('verification_level',value['card']['material'])
            self.assertNotIn('evidence_status',value['card']['material'])
            claim=next(claim for claim in value['card']['source_claims'] if claim['field']=='usage_conditions')
            self.assertEqual('Python',claim['scope']['installation_path'])
            self.assertEqual('Install with Python.',claim['quote'])
        with runtime._db(self.root,write=False) as con:
            checkpoints=json.loads(con.execute('SELECT checkpoints FROM jobs WHERE job_id=?',(job['job_id'],)).fetchone()[0])
        responses=[v for k,v in checkpoints.items() if k.startswith('score-response:')]
        self.assertEqual(6,len(responses))
        self.assertTrue(all(v['status']=='accepted' and v['request_id'] for v in responses))

    def test_malformed_score_retains_raw_response_and_finishes_without_retry(self):
        self.malformed_scores=True
        job,result=self.run_job()
        self.assertEqual('completed',result['status'],result)
        self.assertEqual('draft',result['result']['status'])
        self.assertEqual(0,result['result']['item_count'])
        self.assertEqual(6,len(result['result']['failures']))
        self.assertEqual(13,len(self.calls))
        with runtime._db(self.root,write=False) as con:
            checkpoints=json.loads(con.execute('SELECT checkpoints FROM jobs WHERE job_id=?',(job['job_id'],)).fetchone()[0])
        responses=[v for k,v in checkpoints.items() if k.startswith('score-response:')]
        self.assertTrue(all(v['status']=='rejected' and v['raw_output']['decision']=='select' for v in responses))
        self.assertEqual(6,len([k for k in checkpoints if k.startswith('original:')]))

    def test_score_checkpoint_crash_reuses_receipt_instead_of_resending(self):
        original=runtime.checkpoint
        interrupted=False
        def crash(root,job_id,owner,stage,value):
            nonlocal interrupted
            original(root,job_id,owner,stage,value)
            if stage.startswith('score-response:') and not interrupted:
                interrupted=True
                raise OSError('interrupted after score-response checkpoint')
        with patch.object(runtime,'checkpoint',side_effect=crash):
            job,result=self.run_job()
        self.assertEqual('failed',result['status'])
        self.assertEqual(8,len(self.calls))  # screen + 6 originals + first score
        runtime.retry(self.root,job['job_id'])
        result=runtime.work_once(self.root)
        self.assertEqual('completed',result['status'],result)
        self.assertEqual(13,len(self.calls))  # only the remaining 5 scores
        with runtime._db(self.root,write=False) as con:
            checkpoints=json.loads(con.execute('SELECT checkpoints FROM jobs WHERE job_id=?',(job['job_id'],)).fetchone()[0])
        responses=[v for k,v in checkpoints.items() if k.startswith('score-response:')]
        self.assertEqual(6,len(responses))
        self.assertTrue(all(v['raw_output']==v['assessment'] for v in responses))

    def test_upgrade_resumes_frozen_v3_score_cache_including_bad_receipt(self):
        policy_path=self.root/'config/digest_selection.json'
        prompt_path=self.root/'docs/prompts/digest-selection.md'
        current_policy=policy_path.read_text(encoding='utf-8')
        current_prompt=prompt_path.read_text(encoding='utf-8')
        legacy=json.loads(current_policy)
        legacy['version']='v3-assessment-contract-uncalibrated'
        legacy.pop('usage_evidence_gaps')
        legacy.pop('assessment_contract',None)
        legacy['flag_basis']={k:legacy['flag_basis'][k] for k in ('routine_update','unsupported_promotion')}
        policy_path.write_text(json.dumps(legacy),encoding='utf-8')
        legacy_prompt='Frozen v3 scoring prompt. Return the four assessment fields.'
        prompt_path.write_text(legacy_prompt,encoding='utf-8')
        original=runtime.checkpoint
        interrupted=False
        saved_bad=None
        def crash(root,job_id,owner,stage,value):
            nonlocal interrupted, saved_bad
            original(root,job_id,owner,stage,value)
            if stage.startswith('score-response:') and not interrupted:
                interrupted=True
                saved_bad=copy.deepcopy(value)
                raise OSError('interrupted after frozen v3 score receipt')
        self.malformed_scores=True
        with patch.object(runtime,'checkpoint',side_effect=crash):
            job,result=self.run_job()
        self.assertEqual('failed',result['status'])
        self.assertEqual(8,len(self.calls))  # screen + 6 originals + first score
        self.assertEqual('rejected',saved_bad['status'])
        self.assertEqual('select',saved_bad['raw_output']['decision'])
        with runtime._db(self.root,write=False) as con:
            prior=con.execute('SELECT stage,output FROM requests WHERE request_id=?',
                              (saved_bad['request_id'],)).fetchone()
            self.assertEqual('value-score-v3-news-contract',prior['stage'])
            raw_receipt=prior['output']

        # Upgrade the current policy and prompt. The unfinished job must keep
        # its frozen prompt/policy and reuse its actual request cache entry.
        policy_path.write_text(current_policy,encoding='utf-8')
        prompt_path.write_text(current_prompt,encoding='utf-8')
        self.malformed_scores=False
        runtime.retry(self.root,job['job_id'])
        result=runtime.work_once(self.root)
        self.assertEqual('completed',result['status'],result)
        self.assertEqual(5,result['result']['item_count'])
        self.assertEqual(13,len(self.calls))  # only five unattempted scores call the model
        with runtime._db(self.root,write=False) as con:
            checkpoints=json.loads(con.execute('SELECT checkpoints FROM jobs WHERE job_id=?',
                                               (job['job_id'],)).fetchone()[0])
            scores=con.execute("SELECT stage,output FROM requests WHERE stage LIKE 'value-score-%'").fetchall()
            unchanged=con.execute('SELECT output FROM requests WHERE request_id=?',
                                  (saved_bad['request_id'],)).fetchone()[0]
        self.assertEqual(raw_receipt,unchanged)
        self.assertEqual(6,len(scores))
        self.assertEqual({'value-score-v3-news-contract'},{row['stage'] for row in scores})
        responses=[v for k,v in checkpoints.items() if k.startswith('score-response:')]
        self.assertIn(saved_bad,responses)
        prepared=pipeline.selection.load_preparation(self.root,checkpoints['preparation']['prepare_id'])
        self.assertEqual(legacy,prepared['policy'])
        self.assertEqual(legacy_prompt,pipeline.selection.get_prompt(self.root,prepared))

    def test_date_only_news_is_retained_but_does_not_invent_daily_time(self):
        self.add_news()
        self.news_date=self.period
        self.text='Organizes local files. Install with Python. MIT License. Published '+self.news_date
        _,result=self.run_job()
        self.assertEqual('completed',result['status'],result)
        self.assertEqual(6,result['result']['item_count'])
        # Verify the original date survived ingestion, with no invented midnight.
        con=digest._connect(self.root)
        try:
            rows=con.execute('SELECT payload FROM observations').fetchall()
        finally:
            con.close()
        news=[json.loads(r[0]) for r in rows if json.loads(r[0]).get('kind')=='news' and json.loads(r[0]).get('event')]
        self.assertTrue(news)
        self.assertEqual(self.period,news[-1]['event']['occurred_on'])
        self.assertNotIn('occurred_at',news[-1]['event'])

    def test_date_only_month_interior_can_be_used_without_fake_timestamp(self):
        previous_month=(self.now.date().replace(day=1)-timedelta(days=1)).strftime('%Y-%m')
        self.add_news()
        self.news_date=previous_month+'-15'
        self.text='Organizes local files. Install with Python. MIT License. Published '+self.news_date
        job,result=self.run_job('monthly',previous_month)
        self.assertEqual('completed',result['status'],result)
        self.assertEqual(7,result['result']['item_count'])
        with runtime._db(self.root,write=False) as con:
            checkpoints=json.loads(con.execute('SELECT checkpoints FROM jobs WHERE job_id=?',(job['job_id'],)).fetchone()[0])
        item=next(x for x in checkpoints['issue']['items'] if x['kind']=='news')
        self.assertEqual(self.news_date,item['event']['occurred_on'])
        self.assertEqual('unknown',item['event']['timezone'])

    def test_article_metadata_extraction_skips_modified_date_and_unrelated_jsonld(self):
        parser=pipeline._Text()
        parser.feed('''<meta name="published_time" content="2026-09-30T16:00:00Z">
        <meta property="article:modified_time" content="2026-10-02T00:00:00Z">
        <time itemprop="dateModified" datetime="2026-10-02T00:00:00Z">October 2, 2026</time>
        <script type="application/ld+json">{"@graph":[{"@type":"Organization","datePublished":"2020-01-01"},
        {"@type":"NewsArticle","datePublished":"2026-09-29","dateModified":"2026-10-03"}]}</script>
        <script>secretCode()</script><p>Visible article</p>''')
        text=' '.join(parser.parts)
        self.assertIn('2026-09-30T16:00:00Z',text)
        self.assertIn('2026-09-29',text)
        self.assertIn('Visible article',text)
        for absent in ('2026-10-02','October 2, 2026','2026-10-03','2020-01-01','secretCode'):
            self.assertNotIn(absent,text)

    def test_quoted_date_cannot_be_promoted_to_fictional_midnight(self):
        self.assertTrue(pipeline._date_quote_support('2026-09-29','September 29, 2026'))
        self.assertTrue(pipeline._date_quote_support('2026-09-29','2026年9月29日'))
        self.assertFalse(pipeline._date_quote_support('2026-09-29T00:00:00Z','September 29, 2026'))
        self.assertFalse(pipeline._date_quote_support('2026-09-29','no original date'))
        self.assertFalse(pipeline._date_quote_support('2026-09-29','2026-09-29T14:00:00Z'))
        self.assertTrue(pipeline._date_quote_support('2026-09-29T22:00:00+08:00','2026-09-29T14:00:00Z'))
        self.assertTrue(pipeline._date_quote_support('2026-09-29T14:00Z','2026-09-29T14:00Z'))
        self.assertTrue(pipeline._date_quote_support('2026-09-29T14:00:00+0000','2026-09-29T14:00:00+0000'))
        self.assertFalse(pipeline._date_quote_support('2026-09-29','2026-09-29',
                                                     'publication metadata: 2026-09-29T14:00:00Z'))

    def test_modified_date_alone_cannot_verify_news_event(self):
        self.add_news()
        self.news_date=self.period
        self.html=True
        self.text=f'<p>Organizes local files. Install with Python. MIT License.</p><time itemprop="dateModified" datetime="{self.news_date}">{self.news_date}</time>'
        _,result=self.run_job()
        self.assertEqual('completed',result['status'],result)
        self.assertEqual(6,result['result']['item_count'])
        self.assertEqual(1,len(result['result']['failures']))
        self.assertIn('claim quote not found',result['result']['failures'][0])
        con=digest._connect(self.root)
        try:
            records=[json.loads(row[0]) for row in con.execute('SELECT payload FROM observations')]
        finally:
            con.close()
        self.assertFalse(any(r['kind']=='news' and r['evidence_status']=='verified' for r in records))


if __name__=='__main__':
    unittest.main()
