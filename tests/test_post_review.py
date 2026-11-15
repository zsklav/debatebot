"""Post-interview contracts: real graph/Qdrant, scripted models, no provider calls."""

from copy import deepcopy
import json
import os
import unittest
from unittest.mock import Mock, patch

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.testclient import TestClient

from app.review import demo
from app.review.council import evaluate
from app.review.evidence import prepare, retrieve
from app.review.models import ReviewInput
from app.review.providers import ModelAdapter, ModelRoute, ReviewConfigurationError, ReviewProviderError, configuration
from app.review.routes import build_router
from app.storage import MemoryRepository


class Encoder:
    dimension = 3
    def query(self, text):
        return [1.0 + text.lower().count('cache'), 1.0 + text.lower().count('transaction'), 1.0]
    def passages(self, texts):
        return [self.query(text) for text in texts]


class ReviewTests(unittest.TestCase):
    def test_source_roles_and_lines_survive_parsing(self):
        sources = prepare(demo.payload())
        candidate = [s for s in sources if s.speaker == 'Candidate']
        self.assertEqual(candidate[0].start_line, 2)
        self.assertEqual(candidate[1].start_line, 4)
        self.assertIn('60-second TTL', candidate[0].text)
        self.assertEqual(sources[-1].kind, 'resume')

    def test_unlabelled_empty_and_excluded_criteria_fail_before_review(self):
        for transcript in ['Some unlabelled interview text with no speakers.', 'Interviewer: Any answer?\nCandidate:   ']:
            data = {**demo.SAMPLE, 'transcript':transcript}
            with self.assertRaises(ValueError):
                prepare(ReviewInput.model_validate(data))
        data = deepcopy(demo.SAMPLE)
        data['criteria'][0]['label'] = 'Age'
        with self.assertRaises(ValueError):
            ReviewInput.model_validate(data)

    def test_qdrant_context_is_ephemeral_and_isolated_between_interviews(self):
        payload = demo.payload()
        first = retrieve(payload, prepare(payload), Encoder())
        other = ReviewInput.model_validate({**demo.SAMPLE, 'resume':'A different applicant: queue processing.', 'transcript':'Interviewer: What did you build?\nCandidate: A completely separate Python queue worker.'})
        second = retrieve(other, prepare(other), Encoder())
        self.assertTrue(any('Redis' in s.text for s in first))
        self.assertFalse(any('Redis' in s.text for s in second))

    def test_graph_has_evidence_and_anonymous_adjudication_with_zero_demo_calls(self):
        adapters = [demo.ScriptedAdapter(role) for role in ('a', 'b', 'judge')]
        result = evaluate(demo.payload(), adapters, encoder=Encoder(), seed=1, demo=True)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['audit']['model_requests'], 0)
        self.assertEqual(result['audit']['node_history'], ['ingest','retrieve','reviewers','validate_reviews','adjudicate','validate_final'])
        self.assertEqual(result['audit']['reviewer_order'], ['Reviewer 2','Reviewer 1'])
        self.assertEqual(result['final']['findings'][0]['resolution'], 'proposed_resolution')

    def test_invalid_reviewer_quote_stops_adjudication_but_preserves_other_review(self):
        adapters = [demo.ScriptedAdapter(role) for role in ('a','b','judge')]
        adapters[0].invoke = Mock(return_value=json.dumps({'findings':[
            {'criterion_id':'caching', 'status':'demonstrated', 'explanation':'This invented quote is not source evidence.', 'evidence':[{'source_id':'transcript-002','quote':'Invented'}]},
            {'criterion_id':'transactions','status':'not_assessed','explanation':'No evidence available.','evidence':[]}]}))
        adapters[2].invoke = Mock()
        result = evaluate(demo.payload(), adapters, encoder=Encoder())
        self.assertEqual(result['status'], 'partial')
        self.assertIsNone(result['final'])
        self.assertEqual(len(result['reviews']), 1)
        adapters[2].invoke.assert_not_called()

    def test_judge_is_not_given_provider_ids_and_cannot_invent_agreement(self):
        adapters = [demo.ScriptedAdapter(role) for role in ('a','b','judge')]
        original = adapters[2].invoke
        captured = []
        def judge(messages):
            captured.append(messages)
            data = json.loads(original(messages))
            data['findings'][0]['resolution'] = 'agreed'
            return json.dumps(data)
        adapters[2].invoke = judge
        result = evaluate(demo.payload(), adapters, encoder=Encoder())
        self.assertEqual(result['status'], 'partial')
        context = json.loads(captured[0][-1]['content'])
        self.assertNotIn('models', context)
        self.assertNotIn('provider', json.dumps(context))


class ProviderTests(unittest.TestCase):
    def test_adapter_budget_and_transport_contract(self):
        seen = []
        def respond(request):
            seen.append(json.loads(request.content))
            self.assertEqual(request.headers['Authorization'], 'Bearer private-test-key')
            return httpx.Response(200, json={'choices':[{'finish_reason':'stop','message':{'content':'{}'}}], 'usage':{'total_tokens':9}})
        route = ModelRoute('a','openrouter','explicit/model','family','private-test-key')
        adapter = ModelAdapter(route, httpx.MockTransport(respond))
        self.assertNotIn('private-test-key', repr(route))
        self.assertEqual(adapter.invoke([{'role':'user','content':'JSON'}]), '{}')
        self.assertFalse(seen[0]['provider']['allow_fallbacks'])
        self.assertEqual(adapter.tokens, 9)
        with self.assertRaises(ReviewProviderError):
            adapter.invoke([])
        self.assertEqual(len(seen), 1)

    def test_missing_or_duplicate_families_never_silently_fall_back(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ReviewConfigurationError):
            configuration()
        env = {'GROQ_API_KEY':'test'}
        for role in ('A','B','JUDGE'):
            env.update({f'POST_REVIEW_{role}_PROVIDER':'groq', f'POST_REVIEW_{role}_MODEL':f'model-{role}', f'POST_REVIEW_{role}_FAMILY':'same-family'})
        with patch.dict(os.environ, env, clear=True), self.assertRaises(ReviewConfigurationError):
            configuration()

    def test_truncated_provider_output_is_not_an_assessment(self):
        adapter = ModelAdapter(ModelRoute('a','groq','m','family','key'), httpx.MockTransport(
            lambda request:httpx.Response(200,json={'choices':[{'finish_reason':'length','message':{'content':'{}'}}]})))
        with self.assertRaises(ReviewProviderError):
            adapter.invoke([])


class ReviewRouteTests(unittest.TestCase):
    def setUp(self):
        self.repository = MemoryRepository()
        def owner(request: Request):
            if not request.headers.get('x-test-owner'):
                raise HTTPException(401, 'Sign in')
            return request.headers['x-test-owner']
        app = FastAPI()
        app.include_router(build_router(self.repository, owner))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.headers = {'x-test-owner':'alice'}

    def test_preview_is_local_and_requires_auth(self):
        self.assertEqual(self.client.post('/api/reviews/prepare',json=demo.SAMPLE).status_code,401)
        with patch('app.review.routes.configuration') as models:
            response = self.client.post('/api/reviews/prepare',json=demo.SAMPLE,headers=self.headers)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['model_requests'],0)
        models.assert_not_called()

    def test_external_review_needs_explicit_confirmation_before_config_or_calls(self):
        with patch('app.review.routes.configuration') as models:
            response=self.client.post('/api/reviews/run',json=demo.SAMPLE,headers=self.headers)
        self.assertEqual(response.status_code,400)
        models.assert_not_called()

    def test_sample_report_is_saved_reused_and_cannot_be_reopened_by_another_owner(self):
        real = evaluate
        with patch('app.review.routes.evaluate', side_effect=lambda *a,**k:real(*a,encoder=Encoder(),**k)) as running:
            first=self.client.post('/api/reviews/demo',json={},headers=self.headers)
            second=self.client.post('/api/reviews/demo',json={},headers=self.headers)
        self.assertEqual(first.status_code,200)
        self.assertEqual(first.json()['run_id'],second.json()['run_id'])
        self.assertTrue(first.json()['demo'])
        self.assertEqual(running.call_count,1)
        path='/api/reviews/'+first.json()['run_id']
        self.assertEqual(self.client.get(path,headers=self.headers).status_code,200)
        self.assertEqual(self.client.get(path,headers={'x-test-owner':'bob'}).status_code,404)
