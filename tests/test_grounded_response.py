import asyncio
import unittest
from grounded_response import EvidenceAssembler, GroundedResponder
from plan_runtime import ExecutionResult, ExecutionStep

class FakeBackend:
    def __init__(self, reply): self.reply=reply; self.calls=[]
    async def generate_grounded(self, **kwargs): self.calls.append(kwargs); return self.reply

class GroundingTests(unittest.TestCase):
    def test_exact_empty_suppresses_memory_and_action_receipts_need_success(self):
        result=ExecutionResult("EXECUTED","r",reads=[ExecutionStep("find","read","EXECUTED",{"ok":True,"events":[]},"event","search")])
        packet=EvidenceAssembler().build(None,result,semantic_memory=[{"domain":"event","meeting":"tomorrow 15"}])
        self.assertEqual(["event"],packet.exact_empty_domains)
        self.assertEqual("semantic_memory",packet.items[0].source)
        self.assertFalse([x for x in asyncio.run(_packet_items(packet)) if x["source"] == "semantic_memory"])
        failed=EvidenceAssembler().build(None,ExecutionResult("FAILED","r",actions=[ExecutionStep("x","action","EXECUTED",{"id":3},"event","create")]))
        self.assertFalse(failed.items)
    def test_exact_finance_and_grounded_claim_ids(self):
        packet=EvidenceAssembler().build(None,ExecutionResult("EXECUTED","r",reads=[ExecutionStep("f","read","EXECUTED",{"ok":True,"total":1250,"currency":"RUB"},"finance","summary")]))
        self.assertEqual("exact_current",packet.items[0].source)
        response=asyncio.run(GroundedResponder(FakeBackend({"claims":[{"text":"1250 RUB","claim_type":"personal_fact","evidence_ids":["e1"]}],"confidence":1})).respond("Сколько?",packet))
        self.assertEqual("1250 RUB",response.render())
        invalid=asyncio.run(GroundedResponder(FakeBackend({"claims":[{"text":"x","claim_type":"personal_fact","evidence_ids":["bad"]}]})).respond("x",packet))
        self.assertEqual("NO_DATA",invalid.status)
    def test_model_packet_hides_owner_and_entity_ids(self):
        backend=FakeBackend({"clarification":"x"}); packet=EvidenceAssembler().build(None,ExecutionResult("EXECUTED","r",reads=[ExecutionStep("f","read","EXECUTED",{"ok":True,"events":[{"id":8,"chat_id":4,"participants":[{"person_id":7}],"title":"x"}]},"event","search")]))
        asyncio.run(GroundedResponder(backend).respond("x",packet))
        self.assertNotIn("chat_id",str(backend.calls[0]["evidence"]))
        self.assertNotIn("entity_id",str(backend.calls[0]["evidence"]))

    def test_historical_suppresses_competing_memory_and_delete_receipt_is_safe(self):
        result=ExecutionResult("EXECUTED","r",reads=[ExecutionStep("i","read","EXECUTED",{"ok":True,"items":[{"id":4,"interaction":"Noema"}]},"person","interactions_list")],deleted_entities=[{"domain":"event","id":52}])
        packet=EvidenceAssembler().build(None,result,semantic_memory=[{"domain":"person","interaction":"другой проект"}],conversation_evidence=[{"domain":"person","interaction":"друг"}])
        visible=asyncio.run(_packet_items(packet))
        self.assertFalse([x for x in visible if x["source"] in {"semantic_memory","conversation"} and x["domain"]=="person"])
        receipt=next(x for x in visible if x["domain"]=="event")
        self.assertEqual("exact_historical",receipt["source"])
        self.assertNotIn("52",str(receipt))
        failed=EvidenceAssembler().build(None,ExecutionResult("FAILED","r",deleted_entities=[{"domain":"event","id":52}]))
        self.assertFalse(failed.items)

    def test_oversized_mapping_fails_closed(self):
        packet=EvidenceAssembler().build(None,ExecutionResult("EXECUTED","r",reads=[ExecutionStep("f","read","EXECUTED",{"ok":True,"total":0},"finance","summary")]))
        response=asyncio.run(GroundedResponder(FakeBackend({"claims":[],"clarification":"x"*70000})).respond("x",packet))
        self.assertEqual("NO_DATA",response.status)

async def _packet_items(packet):
    from grounded_response import model_packet
    return model_packet(packet)["items"]
