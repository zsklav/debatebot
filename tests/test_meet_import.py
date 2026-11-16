"""Native Meet pagination, durable bounded jobs and labelled synthetic automation."""

import json
from unittest import TestCase
from unittest.mock import Mock, patch

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
import httpx

from app.integrations.google_config import GoogleError
from app.integrations.google_oauth import GoogleOAuth
from app.integrations.meet_client import MeetClient, NotReady
from app.integrations.meet_import import MeetImports
from app.integrations.routes import build_router
from app.storage import MemoryRepository
from test_google_oauth import authorize, config, tokens

RECORD = "conferenceRecords/native-test"
TRANSCRIPT = RECORD + "/transcripts/t1"


def native_responses(request):
    path, params = request.url.path.removeprefix("/v2/"), dict(request.url.params)
    if path == RECORD:
        return {"name":RECORD,"startTime":"2026-11-15T10:00:00Z","endTime":"2026-11-15T10:10:00Z"}
    if path == RECORD + "/transcripts":
        return {"transcripts":[{"name":TRANSCRIPT,"state":"FILE_GENERATED"}]}
    if path == RECORD + "/participants":
        return {"participants":[{"name":RECORD+"/participants/p1","signedinUser":{"displayName":"Alex"}},
                                {"name":RECORD+"/participants/p2","anonymousUser":{"displayName":"Alex"}}]}
    if path == TRANSCRIPT + "/entries":
        def entry(number, speaker, text, seconds):
            return {"name":TRANSCRIPT+f"/entries/e{number}","participant":RECORD+f"/participants/p{speaker}",
                    "text":text,"startTime":f"2026-11-15T10:00:{seconds:02}Z","endTime":f"2026-11-15T10:00:{seconds+2:02}Z"}
        if params.get("pageToken") == "page-two":
            return {"transcriptEntries":[entry(3,1,"How did you handle invalidation?",30)]}
        return {"transcriptEntries":[entry(1,1,"Describe the cache.",1),entry(2,2,"I used Redis caching with a 60-second TTL.",10)],"nextPageToken":"page-two"}
    if path == "conferenceRecords":
        return {"conferenceRecords":[{"name":RECORD,"startTime":"2026-11-15T10:00:00Z","endTime":"2026-11-15T10:10:00Z"}]}
    raise AssertionError("Unexpected fake endpoint: " + path)


class MeetClientTests(TestCase):
    def gateway(self, override=None):
        self.seen=[]
        def transport(request):
            if request.method == "POST":
                return httpx.Response(200,json=tokens())
            self.seen.append(request)
            if override:
                response=override(request)
                if response is not None:
                    return response
            return httpx.Response(200,json=native_responses(request))
        oauth=authorize(MemoryRepository(),config(),transport)
        return MeetClient(oauth)

    def test_pagination_all_entries_and_duplicate_names_keep_distinct_speakers(self):
        gateway=self.gateway()
        data=gateway.fetch("alice",RECORD)
        self.assertEqual(data["provenance"]["entry_count"],3)
        self.assertEqual(len(data["provenance"]["entry_names"]),3)
        self.assertEqual(data["display_names"],{"Speaker 1":"Alex","Speaker 2":"Alex"})
        exported=json.loads(data["notebook_input"]["content"])
        self.assertEqual(len(exported["transcriptEntries"]),3)
        self.assertNotIn("nextPageToken",exported)
        self.assertEqual(gateway.requests,5)
        self.assertEqual(dict(self.seen[-1].url.params)["pageToken"],"page-two")
        self.assertTrue(all(request.headers["Authorization"] == "Bearer private-test-access" for request in self.seen))

    def test_missing_or_unfinished_transcript_waits_without_fetching_entries(self):
        for state in (None,"ENDED"):
            def override(request):
                if request.url.path.endswith("/transcripts"):
                    return httpx.Response(200,json={"transcripts":[] if state is None else [{"name":TRANSCRIPT,"state":state}]})
            gateway=self.gateway(override)
            with self.assertRaises(NotReady):
                gateway.fetch("alice",RECORD)
            self.assertEqual(gateway.requests,2)

    def test_denied_reads_and_pagination_cycles_do_not_import_partial_data(self):
        gateway=self.gateway(lambda request:httpx.Response(403,json={"error":{"message":"private upstream detail"}}))
        with self.assertRaises(GoogleError) as error:
            gateway.fetch("alice",RECORD)
        self.assertNotIn("private upstream detail",str(error.exception))
        gateway=self.gateway(lambda request:httpx.Response(200,json={"transcripts":[],"nextPageToken":"loop"}) if request.url.path.endswith("/transcripts") else None)
        with self.assertRaises(GoogleError):
            gateway.fetch("alice",RECORD)

    def test_filter_is_encoded_and_arbitrary_urls_never_become_destinations(self):
        gateway=self.gateway()
        gateway.recent("alice","https://meet.google.com/abc-defg-hij")
        self.assertEqual(dict(self.seen[0].url.params)["filter"], 'space.meeting_code = "abc-defg-hij"')
        with self.assertRaises(ValueError):
            gateway.fetch("alice","https://attacker.example/transcript")
        self.assertEqual(gateway.requests,1)


class ImportJobTests(TestCase):
    def setUp(self):
        self.repository=MemoryRepository()
        self.clock=Mock(return_value=1000)
        self.oauth=GoogleOAuth(self.repository,config=config)
        self.imports=MeetImports(self.repository,self.oauth,clock=self.clock)

    def ready(self):
        job=self.imports.create("alice",demo=True)
        self.imports.tick()
        self.assertEqual(self.repository.get_meet_import(job["id"],"alice")["status"],"waiting")
        self.clock.return_value=1003
        self.imports.tick()
        return self.repository.get_meet_import(job["id"],"alice")

    def test_demo_automates_capture_but_never_connects_google_and_completes_once(self):
        job=self.ready()
        self.assertEqual(job["status"],"needs_speakers")
        self.assertEqual(job["google_requests"],0)
        self.assertIsNone(self.repository.get_google_connection("alice"))
        roles={"Speaker 1":"Interviewer","Speaker 2":"Candidate"}
        first,reused=self.imports.complete(job["id"],"alice","Synthetic interview",roles)
        again,reused_again=self.imports.complete(job["id"],"alice","Different title",roles)
        self.assertFalse(reused)
        self.assertTrue(reused_again)
        self.assertEqual(first["id"],again["id"])
        self.assertEqual(again["document"]["title"],"Synthetic interview")
        self.assertEqual(first["document"]["import_provenance"]["provider"],"demo")
        self.assertEqual(len(self.repository.notebooks),1)
        self.assertEqual(self.imports.create("alice",demo=True)["id"],job["id"])

    def test_worker_lease_expires_and_stale_ack_cannot_overwrite_cancel(self):
        job=self.imports.create("alice",demo=True)
        first=self.repository.claim_meet_import(1000)
        self.assertIsNone(self.repository.claim_meet_import(1001))
        second=self.repository.claim_meet_import(1241)
        self.assertNotEqual(first["lease_token"],second["lease_token"])
        self.assertFalse(self.repository.finish_meet_import(job["id"],first["lease_token"],"needs_speakers",now=1242,message="stale"))
        self.repository.action_meet_import(job["id"],"alice","cancel",1243)
        self.assertFalse(self.repository.finish_meet_import(job["id"],second["lease_token"],"needs_speakers",now=1244,message="late"))
        self.assertEqual(self.repository.get_meet_import(job["id"],"alice")["status"],"cancelled")

    def test_expired_jobs_stop_without_provider_requests_and_can_retry(self):
        job=self.imports.create("alice",demo=True)
        self.clock.return_value=2300
        self.imports.tick()
        expired=self.repository.get_meet_import(job["id"],"alice")
        self.assertEqual(expired["status"],"expired")
        self.assertEqual(expired["google_requests"],0)
        retried=self.repository.action_meet_import(job["id"],"alice","retry",2301)
        self.assertEqual(retried["status"],"waiting")
        self.assertEqual(retried["attempts"],0)

    def test_jobs_require_owner_and_confirmed_roles(self):
        job=self.ready()
        self.assertIsNone(self.imports.owned(job["id"],"bob"))
        self.assertEqual(self.repository.list_meet_imports("bob"),[])
        with self.assertRaises(ValueError):
            self.imports.complete(job["id"],"alice","Synthetic interview",{})
        self.assertEqual(self.repository.notebooks,{})


class ImportRouteTests(TestCase):
    def test_public_status_never_claims_demo_is_google_and_other_owners_cannot_prepare(self):
        repository=MemoryRepository()
        oauth=GoogleOAuth(repository)
        imports=MeetImports(repository,oauth,clock=lambda:1000)
        def owner(request:Request):
            if not request.headers.get("x-test-owner"):
                raise HTTPException(401,"Sign in")
            return request.headers["x-test-owner"]
        app=FastAPI()
        app.include_router(build_router(repository,owner,oauth,imports))
        with patch.dict("os.environ",{},clear=True), TestClient(app) as client:
            prefix="/api/integrations/google"
            self.assertEqual(client.get(prefix+"/status").status_code,401)
            headers={"x-test-owner":"alice"}
            status=client.get(prefix+"/status",headers=headers).json()
            self.assertFalse(status["configured"])
            self.assertFalse(status["connected"])
            job=client.post(prefix+"/demo",json={},headers=headers).json()
            self.assertEqual(job["provider"],"demo")
            for suffix in ("","/prepare"):
                self.assertEqual(client.get(prefix+"/jobs/"+job["id"]+suffix,headers={"x-test-owner":"bob"}).status_code,404)
