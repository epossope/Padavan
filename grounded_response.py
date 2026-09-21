"""Provider-neutral evidence assembly and fail-closed grounded responses."""
from __future__ import annotations
import asyncio, json, time
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol
from semantic_core import EvidenceItem, EvidencePacket

PROMPT = """Return JSON only. User-specific facts require supplied evidence IDs. exact_current outranks historical, memory and conversation. exact-empty domains mean no current record may be resurrected. Never invent personal facts or mention internal IDs."""
GROUNDED_OUTPUT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["claims", "confidence", "clarification"], "properties": {"claims": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["text", "claim_type", "evidence_ids"], "properties": {"text": {"type": "string"}, "claim_type": {"enum": ["personal_fact", "general_text", "clarification"]}, "evidence_ids": {"type": "array", "items": {"type": "string"}}}}}, "confidence": {"type": "number", "minimum": 0, "maximum": 1}, "clarification": {"type": "string"}}}
MAX_ITEMS, MAX_TEXT, MAX_RESPONSE = 64, 1200, 64 * 1024
PERSONAL = {"personal_fact"}
EXACT_KEYS = {"id","person_id","event_id","task_id","reminder_id","transaction_id","note_id","file_id","project_id","source_turn_id","resolved_id","chat_id","owner_id","user_id"}

class GroundedResponseBackend(Protocol):
    async def generate_grounded(self, *, system_prompt: str, question: str, evidence: Mapping[str, Any]) -> str | Mapping[str, Any]: ...

@dataclass(slots=True)
class GroundedClaim:
    text: str; claim_type: str = "general_text"; evidence_ids: list[str] = field(default_factory=list)
@dataclass(slots=True)
class GroundedResponse:
    claims: list[GroundedClaim] = field(default_factory=list); confidence: float = 0.0; clarification: str = ""; status: str = "OK"; failure_category: str = ""
    def render(self) -> str: return " ".join(c.text for c in self.claims if c.text).strip() or self.clarification

class GroundingError(ValueError):
    def __init__(self, category): super().__init__(category); self.category = category

def _safe(value: Any, depth=0):
    if depth > 3: return None
    if value is None or isinstance(value, (bool, int, float)): return value
    if isinstance(value, str): return value[:MAX_TEXT]
    if isinstance(value, list): return [_safe(x, depth + 1) for x in value[:24]]
    if isinstance(value, dict): return {str(k)[:80]: _safe(v, depth + 1) for k, v in list(value.items())[:24] if str(k).casefold() not in EXACT_KEYS | {"token", "secret"}}
    return str(value)[:MAX_TEXT]

class EvidenceAssembler:
    def __init__(self, *, observer=None, metric_recorder=None): self.observer,self.metric=observer,metric_recorder
    def _emit(self,event,**fields):
        try:
            if self.observer:self.observer(event,**fields)
        except Exception:pass
    def build(self, validated_plan, execution_result, *, semantic_memory=None, conversation_evidence=None) -> EvidencePacket:
        started=time.perf_counter(); self._emit("evidence_build_started")
        packet=EvidencePacket()
        try:
            for step in execution_result.reads:
                domain, result = step.domain, step.result; packet.checked_exact_domains.append(domain)
                rows = result.get("events") or result.get("items") or result.get("transactions") or []
                if domain == "finance" and result.get("ok"):
                    packet.add(EvidenceItem("exact_current","finance_summary",None,_safe(result),confidence=1,domain="finance"))
                elif not rows and result.get("ok"):
                    packet.exact_empty_domains.append(domain)
                for row in rows[:MAX_ITEMS]:
                    entity_type = "person_interaction" if domain == "person" and step.operation == "interactions_list" else ("transaction" if domain == "transaction" else domain.rstrip("s"))
                    packet.add(EvidenceItem("exact_historical" if entity_type in {"transaction", "person_interaction"} else "exact_current",entity_type,row.get("id"),_safe(row),confidence=1,domain=domain))
                if domain == "person" and result.get("person_id"):
                    packet.add(EvidenceItem("exact_current","person",result["person_id"],{"resolved": True},confidence=1,domain="person"))
            if execution_result.status == "EXECUTED":
                for step in execution_result.actions:
                    ident=step.result.get("id")
                    if ident is not None: packet.add(EvidenceItem("exact_historical" if step.operation in {"delete","cancel"} else "exact_current",step.domain,ident,_safe(step.result),confidence=1,domain=step.domain))
                # Delete/update services may not return the affected id.  The
                # executor's owner-scoped result lists are the trusted receipt.
                for item in execution_result.deleted_entities:
                    packet.add(EvidenceItem("exact_historical", str(item.get("domain") or ""), item.get("id"), {"operation":"delete", "ok":True}, confidence=1, domain=str(item.get("domain") or "")))
                for item in execution_result.updated_entities:
                    packet.add(EvidenceItem("exact_current", str(item.get("domain") or ""), item.get("id"), {"operation":"update", "ok":True}, confidence=1, domain=str(item.get("domain") or "")))
            for source, items in (("semantic_memory", semantic_memory), ("conversation", conversation_evidence)):
                for item in (items or [])[:16]:
                    item = item if isinstance(item, dict) else {"text": item}
                    packet.add(EvidenceItem(source,"memory" if source=="semantic_memory" else "conversation",None,_safe(item),confidence=.5,domain=str(item.get("domain") or "")))
            packet.checked_exact_domains=list(dict.fromkeys(packet.checked_exact_domains)); packet.exact_empty_domains=list(dict.fromkeys(packet.exact_empty_domains)); packet.exact_empty=bool(packet.exact_empty_domains)
            counts={"exact_item_count":sum(x.source=="exact_current" for x in packet.items),"historical_item_count":sum(x.source=="exact_historical" for x in packet.items),"memory_item_count":sum(x.source=="semantic_memory" for x in packet.items),"conversation_item_count":sum(x.source=="conversation" for x in packet.items),"exact_empty_domain_count":len(packet.exact_empty_domains)}; self._emit("evidence_build_completed",**counts); return packet
        finally:
            if self.metric:
                try:self.metric("evidence_build_ms",(time.perf_counter()-started)*1000)
                except Exception:pass

def model_packet(packet: EvidencePacket) -> dict:
    exact_domains={x.domain for x in packet.items if x.source in {"exact_current", "exact_historical"} and x.domain}
    blocked=set(packet.exact_empty_domains) | exact_domains
    effective=[x for x in packet.items if not (x.source in {"semantic_memory","conversation"} and x.domain in blocked)]
    return {"items":[{"evidence_id":x.evidence_id,"source":x.source,"entity_type":x.entity_type,"domain":x.domain,"fields":_safe(x.fields),"confidence":x.confidence} for x in effective],"exact_empty_domains":packet.exact_empty_domains}

class GroundedResponder:
    def __init__(self,backend,*,timeout_seconds=20,observer=None,metric_recorder=None): self.backend,self.timeout,self.observer,self.metric=backend,timeout_seconds,observer,metric_recorder
    def _emit(self,event,**fields):
        try:
            if self.observer:self.observer(event,**fields)
        except Exception:pass
    async def respond(self, question: str, packet: EvidencePacket) -> GroundedResponse:
        started=time.perf_counter(); self._emit("grounded_response_started")
        try:
            payload={"question":str(question)[:MAX_TEXT],"evidence":model_packet(packet)}
            structured=getattr(self.backend,"generate_structured",None)
            if structured is not None:
                raw=await asyncio.wait_for(
                    structured(system_prompt=PROMPT,input_payload=payload,output_schema=GROUNDED_OUTPUT_SCHEMA),
                    self.timeout,
                )
            else:
                raw=await asyncio.wait_for(
                    self.backend.generate_grounded(
                        system_prompt=PROMPT,
                        question=payload["question"],
                        evidence=payload["evidence"],
                    ),
                    self.timeout,
                )
            if isinstance(raw, str):
                encoded = raw.encode("utf-8")
                if len(encoded) > MAX_RESPONSE: raise GroundingError("oversized")
                data=json.loads(raw)
            else:
                try: encoded=json.dumps(raw, ensure_ascii=False).encode("utf-8")
                except (TypeError, ValueError, OverflowError): raise GroundingError("invalid_mapping") from None
                if len(encoded) > MAX_RESPONSE: raise GroundingError("oversized")
                data=dict(raw)
            claims=[]; ids={x["evidence_id"] for x in model_packet(packet)["items"]}
            if set(data) - {"claims","confidence","clarification"} or not isinstance(data.get("claims"),list) or len(data["claims"])>16: raise GroundingError("invalid_claims")
            for item in data["claims"]:
                if not isinstance(item,dict) or set(item)-{"text","claim_type","evidence_ids"}: raise GroundingError("invalid_claim")
                text=str(item.get("text") or "").strip()
                typ=item.get("claim_type","general_text"); evidence=item.get("evidence_ids",[])
                if not text or len(text)>MAX_TEXT or typ not in {"personal_fact","general_text","clarification"} or not isinstance(evidence,list) or any(x not in ids for x in evidence): raise GroundingError("invalid_claim")
                if typ != "clarification" and not evidence: raise GroundingError("missing_evidence")
                claims.append(GroundedClaim(text,typ,evidence))
            confidence=float(data.get("confidence",0))
            if not 0 <= confidence <= 1 or (not claims and not str(data.get("clarification") or "").strip()): raise GroundingError("invalid_response")
            response=GroundedResponse(claims,confidence,str(data.get("clarification") or "")); self._emit("grounded_response_completed",claim_count=len(claims)); return response
        except Exception as exc:
            category=exc.category if isinstance(exc,GroundingError) else "provider_error"; self._emit("grounded_response_invalid" if isinstance(exc,GroundingError) else "grounded_response_failed",failure_category=category)
            empty=", ".join(packet.exact_empty_domains); return GroundedResponse([],0,"В текущих данных ничего не найдено." if empty else "Не удалось надёжно сформировать ответ.","NO_DATA",category)
        finally:
            if self.metric:
                try:self.metric("grounded_response_ms",(time.perf_counter()-started)*1000)
                except Exception:pass
