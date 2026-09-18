import asyncio
import unittest
from datetime import datetime
from semantic_core import SemanticPlan, ReadRequest, ActionRequest
from semantic_orchestrator import SemanticShadowOrchestrator, ShadowReadExecutor

class Planner:
    def __init__(self, plan): self.result=plan; self.calls=0
    async def plan(self,*args,**kwargs): self.calls+=1; return self.result
class Validator:
    def validate(self,owner,plan,**kwargs): return type("V",(),{"request_id":kwargs["request_id"],"reads":plan.reads,"actions":plan.actions})()
class Services:
    def __init__(self):self.reads=0
    def read(self,owner,item):self.reads+=1; return {"ok":True,"total":1250,"currency":"RUB"}
class Assembler:
    def build(self,*args,**kwargs):return object()
class Responder:
    async def respond(self,*args,**kwargs):return type("R",(),{"status":"OK"})()

class ShadowTests(unittest.TestCase):
    def args(self): return dict(trusted_owner=1,request_id="r",utterance="private",now=datetime.now(),timezone="Europe/Moscow",conversation_context={})
    def test_off_never_calls_planner(self):
        planner=Planner(SemanticPlan("x","answer")); orchestrator=SemanticShadowOrchestrator(planner,Validator(),Services(),Assembler(),Responder(),enabled=False)
        self.assertIsNone(orchestrator.schedule(**self.args())); self.assertEqual(0,planner.calls)
    def test_read_executes_only_reads_and_commit_never_writes(self):
        services=Services(); read=SemanticShadowOrchestrator(Planner(SemanticPlan("finance","read",reads=[ReadRequest("finance","summary",read_id="f")])),Validator(),services,Assembler(),Responder(),enabled=True)
        result=asyncio.run(read.run(**self.args())); self.assertEqual("READ_COMPLETED",result.status); self.assertEqual(1,services.reads)
        self.assertEqual(("OK","OK","OK","OK",1,0,[]),(result.planner_status,result.validation_status,result.read_status,result.grounding_status,result.read_count,result.action_count,result.proposed_operations)); self.assertGreater(result.latency_ms,0)
        commit=SemanticShadowOrchestrator(Planner(SemanticPlan("meeting","commit",actions=[ActionRequest("event","create",action_id="e")])),Validator(),services,Assembler(),Responder(),enabled=True)
        result=asyncio.run(commit.run(**self.args())); self.assertEqual("VALIDATED_COMMIT",result.status); self.assertEqual(1,services.reads)
    def test_same_turn_schedules_once(self):
        planner=Planner(SemanticPlan("x","answer")); o=SemanticShadowOrchestrator(planner,Validator(),Services(),Assembler(),Responder(),enabled=True)
        async def run():
            a=o.schedule(**self.args()); b=o.schedule(**self.args()); await a; return b
        self.assertIsNone(asyncio.run(run())); self.assertEqual(1,planner.calls)

    def test_capacity_is_bounded_without_backlog(self):
        class Slow(Planner):
            async def plan(self,*args,**kwargs): await asyncio.sleep(.02); return self.result
        o=SemanticShadowOrchestrator(Slow(SemanticPlan("x","answer")),Validator(),Services(),Assembler(),Responder(),enabled=True,max_concurrency=2)
        async def run():
            tasks=[o.schedule(**{**self.args(),"request_id":str(i)}) for i in range(20)]
            accepted=[x for x in tasks if x]; await asyncio.gather(*accepted); return len(accepted)
        self.assertEqual(2,asyncio.run(run()))
