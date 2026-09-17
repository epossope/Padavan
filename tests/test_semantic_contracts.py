import unittest

from semantic_core import (ActionRequest, EntityReference, EvidenceItem,
                           EvidencePacket, ReadRequest, SemanticPlan)


class SemanticContractsTests(unittest.TestCase):
    def test_typed_plan_contains_no_trusted_owner(self):
        person = EntityReference(type="person", mention="Иван")
        plan = SemanticPlan(
            intent="schedule_meeting", disposition="commit", entities=[person],
            reads=[ReadRequest(domain="person", operation="resolve", entity_refs=[person])],
            actions=[ActionRequest(domain="event", operation="event_create", fields={"title": "Встреча"})],
        )
        self.assertIsInstance(plan, SemanticPlan)
        self.assertEqual("commit", plan.disposition)
        self.assertEqual("resolve", plan.reads[0].operation)
        self.assertEqual("event_create", plan.actions[0].operation)
        self.assertNotIn("chat_id", plan.to_dict())

    def test_evidence_packet_orders_exact_state_before_memory(self):
        packet = EvidencePacket()
        packet.add(EvidenceItem("semantic_memory", "event", "old", {"title": "old"}))
        packet.add(EvidenceItem("exact_current", "event", 7, {"title": "current"}))
        self.assertEqual(["exact_current", "semantic_memory"], [item.source for item in packet.items])
