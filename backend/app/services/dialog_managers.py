"""
Dialog Managers
===============
Versión corregida con máquina de estados clara.

Bugs corregidos:
  1. Skip de pasos — el advance_and_respond() ahora maneja correctamente
     el paso siguiente después de extraer un slot
  2. Terminal step (slot: null) — detectado correctamente con is_terminal()
  3. TypeError en boolean validation — valores YAML convertidos a string
  4. Name/phone loop — extracción mejorada
  5. Alucinación de matrícula — system_context más restrictivo
"""

import json, re, uuid, yaml
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime

from app.services.llm_adapters import BaseLLMAdapter, Message


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

DATA_PATH = Path(__file__).parent.parent.parent / "data" / "appointments.json"

def load_appointment_data() -> Dict:
    with open(DATA_PATH) as f:
        return json.load(f)

def build_location_list(data: Dict) -> str:
    return "\n".join(
        f"- {l['name']} ({l['address']}), tel: {l['phone']}"
        for l in data["locations"]
    )

def build_service_list(data: Dict) -> str:
    return ", ".join(s["name"] for s in data["services"])

def format_slots_by_day(slots: List[str]) -> str:
    by_day: Dict[str, List[str]] = {}
    for s in slots:
        date, time = s.split(" ")
        by_day.setdefault(date, []).append(time)
    lines = []
    for date, times in sorted(by_day.items()):
        dt = datetime.strptime(date, "%Y-%m-%d")
        day_sv = ["Måndag","Tisdag","Onsdag","Torsdag","Fredag","Lördag","Söndag"][dt.weekday()]
        month_sv = ["jan","feb","mar","apr","maj","jun","jul","aug","sep","okt","nov","dec"][dt.month-1]
        lines.append(f"  {day_sv} {dt.day} {month_sv}: {', '.join(times)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 1. Unconstrained Dialog Manager
# ---------------------------------------------------------------------------

UNCONSTRAINED_SYSTEM = """Du är en bokningsassistent för en bilverkstad i Stockholmsområdet.
Du MÅSTE alltid svara på svenska, oavsett vilket språk användaren skriver på.

Tillgängliga verkstäder:
{locations}

Tillgängliga tjänster: {services}

Lediga tider per verkstad:
{slots}

Inled konversationen med ett kort, neutralt välkomstmeddelande och fråga vad
du kan hjälpa kunden med. Nämn INTE specifika tjänster i välkomstmeddelandet.

Du behöver samla in EXAKT dessa uppgifter — inget annat:
1. Typ av ärende (service, däckbyte, bromsar, AC, besiktning eller annat)
2. Önskad verkstad (välj från listan ovan)
3. Datum och tid (välj från lediga tider ovan)
4. Kundens namn
5. Kundens telefonnummer

Fråga INTE om registreringsnummer, bilmärke, årsmodell eller annan information.
Samla ENDAST in de 5 punkterna ovan.

När du har all information, bekräfta bokningen och ge en bokningsreferens.
Var hjälpsam och naturlig i konversationen.

När bokningen är HELT bekräftad, avsluta ditt svar med exakt denna token på en egen rad: [BOOKING_COMPLETE]
Fortsätt INTE konversationen efter att du skrivit [BOOKING_COMPLETE]."""


class UnconstrainedDialogManager:

    def __init__(self, adapter: BaseLLMAdapter):
        self.adapter = adapter
        self.data = load_appointment_data()

    def _build_system_prompt(self) -> str:
        all_slots = []
        for loc_id, slots in self.data["available_slots"].items():
            loc_name = next(l["name"] for l in self.data["locations"] if l["id"] == loc_id)
            all_slots.append(f"{loc_name}:\n{format_slots_by_day(slots)}")
        return UNCONSTRAINED_SYSTEM.format(
            locations=build_location_list(self.data),
            services=build_service_list(self.data),
            slots="\n".join(all_slots),
        )

    async def respond(self, conversation_history: List[Dict], user_message: str) -> Dict:
        messages = [Message(m["role"], m["content"]) for m in conversation_history]
        messages.append(Message("user", user_message))
        reply = await self.adapter.chat(
            messages=messages,
            system_prompt=self._build_system_prompt(),
            temperature=0.7,
            max_tokens=2048,
        )
        is_complete = "[BOOKING_COMPLETE]" in reply
        if is_complete:
            reply = reply.replace("[BOOKING_COMPLETE]", "").strip()
        return {
            "reply": reply,
            "system_state": None,
            "slots_filled": {},
            "current_step": "complete" if is_complete else "free",
            "is_complete": is_complete,
        }


# ---------------------------------------------------------------------------
# 2. Skill-Based Dialog Manager
# ---------------------------------------------------------------------------

SKILL_PATH = Path(__file__).parent.parent / "skills" / "boka_bilverkstad.yaml"


class SkillState:

    def __init__(self):
        self.current_step_index: int = 0
        self.slots: Dict[str, Any] = {}
        self.retry_count: int = 0
        self.booking_ref: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "current_step_index": self.current_step_index,
            "slots": self.slots,
            "retry_count": self.retry_count,
            "booking_ref": self.booking_ref,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "SkillState":
        s = cls()
        s.current_step_index = d.get("current_step_index", 0)
        s.slots = d.get("slots", {})
        s.retry_count = d.get("retry_count", 0)
        s.booking_ref = d.get("booking_ref")
        return s


class SkillBasedDialogManager:

    def __init__(self, adapter: BaseLLMAdapter):
        self.adapter = adapter
        self.data = load_appointment_data()
        with open(SKILL_PATH) as f:
            self.skill = yaml.safe_load(f)
        self.steps = self.skill["steps"]
        self.recovery = self.skill["recovery"]

    # ── Step helpers ──────────────────────────────────────────────────────────

    def _get_step(self, index: int) -> Optional[Dict]:
        return self.steps[index] if index < len(self.steps) else None

    def _is_terminal(self, step: Dict) -> bool:
        """A terminal step has slot: null (Python None) and no slots list."""
        return step.get("slot") is None and not step.get("slots")

    # ── Slot extraction ───────────────────────────────────────────────────────

    def _extract_slot(self, step: Dict, user_message: str) -> Optional[str]:
        validation = step.get("validation", {})
        vtype = validation.get("type")
        msg_lower = user_message.lower().strip()

        if vtype == "enum":
            options = [str(o) for o in validation.get("options", [])]
            fuzzy = validation.get("fuzzy_match", False)
            for opt in options:
                if opt in msg_lower:
                    return opt
            if fuzzy:
                synonyms = {
                    "service":    ["olja", "oljebyte", "service", "filter", "kontroll", "servis"],
                    "däckbyte":   ["däck", "dack", "hjul", "sommar", "vinter", "dubb"],
                    "bromsar":    ["broms", "bromsa", "bromsskiva", "belägg"],
                    "ac":         ["ac", "luft", "kyla", "klimat", "luftkonditionering", "a/c"],
                    "besiktning": ["besiktning", "besikta", "kontrollbesiktning"],
                    "annat":      ["annat", "diagnos", "fel", "ljud", "problem"],
                    "vasastan":   ["vasastan", "uppland", "vasa", "upplandsgatan"],
                    "sodermalm":  ["söder", "södermalm", "hornsgatan", "horn", "sodermalm"],
                    "nacka":      ["nacka", "värmdö", "värmdövägen"],
                    "solna":      ["solna", "frösunda"],
                    "ja":         ["ja", "yes", "ok", "okej", "stämmer", "rätt", "bekräfta", "japp", "jo", "correct"],
                    "nej":        ["nej", "no", "fel", "ändra", "avbryt", "cancel", "nope"],
                }
                for opt, syns in synonyms.items():
                    if opt in options and any(s in msg_lower for s in syns):
                        return opt
            return None

        elif vtype == "boolean":
            # Convert to string to avoid TypeError from YAML bool parsing
            true_vals  = [str(v).lower() for v in validation.get("true_values",  ["ja"])]
            false_vals = [str(v).lower() for v in validation.get("false_values", ["nej"])]
            if any(v in msg_lower for v in true_vals):
                return "ja"
            if any(v in msg_lower for v in false_vals):
                return "nej"
            return None

        elif vtype == "available_slot":
            loc_id = self.current_state.slots.get("location_id", "")
            slots_for_loc = self.data["available_slots"].get(loc_id, [])
            for slot in slots_for_loc:
                date_part, time_part = slot.split(" ")
                if time_part in msg_lower or date_part in msg_lower:
                    return slot
            time_pattern = re.search(r"\b(\d{1,2})[:\.]?(\d{0,2})\b", user_message)
            if time_pattern:
                hour   = time_pattern.group(1).zfill(2)
                minute = time_pattern.group(2) or "00"
                candidate = f"{hour}:{minute}"
                for slot in slots_for_loc:
                    if slot.endswith(candidate):
                        return slot
            return None

        elif vtype == "regex":
            pattern = validation.get("pattern", ".*")
            if re.match(pattern, user_message.strip()):
                return user_message.strip()
            return None

        return user_message.strip() if user_message.strip() else None

    def _extract_name_and_phone(self, user_message: str, state: SkillState):
        """Extract name and phone from a single message or across turns."""
        msg = user_message.strip()
        phone_pattern = re.compile(r'[\+]?[\d\s\-]{7,15}')
        phone_match = phone_pattern.search(msg)

        if phone_match:
            phone = re.sub(r'\s+', '', phone_match.group()).strip()
            name_part = (msg[:phone_match.start()] + msg[phone_match.end():])
            name_part = re.sub(r'\s+', ' ', name_part).strip()
            for filler in ["mitt namn är", "jag heter", "my name is", "namn:", "telefon:", "tel:"]:
                name_part = re.sub(filler, "", name_part, flags=re.IGNORECASE).strip()
            name_part = name_part.strip(" ,.-")

            if phone and len(phone) >= 7 and "customer_phone" not in state.slots:
                state.slots["customer_phone"] = phone
            if name_part and len(name_part) >= 2 and "customer_name" not in state.slots:
                state.slots["customer_name"] = name_part.title()
        else:
            if "customer_name" not in state.slots and len(msg) >= 2:
                state.slots["customer_name"] = msg.title()

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _run_handler(self, handler_name: str, state: SkillState) -> str:
        if handler_name == "get_available_slots":
            loc_id = state.slots.get("location_id", "")
            slots  = self.data["available_slots"].get(loc_id, [])
            loc_name = next(
                (l["name"] for l in self.data["locations"] if l["id"] == loc_id), loc_id
            )
            return (
                f"Lediga tider på {loc_name}:\n{format_slots_by_day(slots)}"
                if slots else f"Inga lediga tider hittades för {loc_name}."
            )
        elif handler_name == "create_booking":
            ref      = f"BK{uuid.uuid4().hex[:6].upper()}"
            state.booking_ref = ref
            loc_id   = state.slots.get("location_id", "")
            loc      = next((l for l in self.data["locations"] if l["id"] == loc_id), {})
            return (
                f"Bokningsreferens: {ref}\n"
                f"Verkstad: {loc.get('name', loc_id)}\n"
                f"Telefon: {loc.get('phone', '')}"
            )
        return ""

    # ── Prompt builder ────────────────────────────────────────────────────────

    def _build_step_prompt(self, step: Dict, state: SkillState, handler_context: str = "") -> str:
        global_ctx = self.skill["system_context"]
        labels = {
            "service_type":     "Ärende",
            "location_id":      "Verkstad",
            "appointment_slot": "Tid",
            "customer_name":    "Namn",
            "customer_phone":   "Telefon",
            "confirmation":     "Bekräftelse",
        }
        filled_summary = ""
        if state.slots:
            lines = [f"  {labels.get(k, k)}: {v}" for k, v in state.slots.items()]
            filled_summary = "Redan insamlad information:\n" + "\n".join(lines)

        parts = [global_ctx.strip(), ""]
        if filled_summary:
            parts += [filled_summary, ""]
        if handler_context:
            parts += [handler_context, ""]
        parts += [
            f"Nuvarande uppgift:\n{step['instruction'].strip()}",
            "",
            "VIKTIGT: Svara ENBART på det som efterfrågas i nuvarande uppgift.",
            "Fråga INTE om registreringsnummer, bilmärke, årsmodell eller annan information.",
            "Samla INTE in mer information än vad som anges i nuvarande uppgift.",
        ]
        return "\n".join(parts)

    # ── LLM call helper ───────────────────────────────────────────────────────

    async def _llm(self, step: Dict, state: SkillState,
                   conversation_history: List[Dict],
                   user_message: Optional[str],
                   handler_context: str = "") -> str:
        messages = [Message(m["role"], m["content"]) for m in conversation_history]
        if user_message:
            messages.append(Message("user", user_message))
        system_prompt = self._build_step_prompt(step, state, handler_context)
        return await self.adapter.chat(
            messages=messages,
            system_prompt=system_prompt,
            temperature=0.4,
            max_tokens=2048,
        )

    # ── Main respond ──────────────────────────────────────────────────────────

    async def respond(
        self,
        conversation_history: List[Dict],
        user_message: str,
        state_dict: Optional[Dict] = None,
    ) -> Dict:

        self.current_state = SkillState.from_dict(state_dict) if state_dict else SkillState()
        state = self.current_state
        is_first_turn = len(conversation_history) == 0

        # ── Opening greeting (turn 0) ─────────────────────────────────────────
        if is_first_turn:
            step = self._get_step(0)
            reply = await self._llm(step, state, [], None)
            return {
                "reply": reply,
                "system_state": state.to_dict(),
                "slots_filled": state.slots,
                "current_step": step["id"],
                "is_complete": False,
            }

        # ── All subsequent turns ──────────────────────────────────────────────
        step = self._get_step(state.current_step_index)
        if step is None:
            return self._complete_response(state)

        # ── Terminal step: generate closing message and mark complete ─────────
        if self._is_terminal(step):
            handler_context = self._run_handler(step.get("handler", ""), state) if step.get("handler") else ""
            reply = await self._llm(step, state, conversation_history, user_message, handler_context)
            state.current_step_index += 1
            return {
                "reply": reply,
                "system_state": state.to_dict(),
                "slots_filled": state.slots,
                "current_step": "complete",
                "is_complete": True,
            }

        # ── Single-slot step ──────────────────────────────────────────────────
        if step.get("slot"):
            extracted = self._extract_slot(step, user_message)

            if extracted:
                state.slots[step["slot"]] = extracted
                state.retry_count = 0
                state.current_step_index += 1

                next_step = self._get_step(state.current_step_index)
                if next_step is None:
                    return self._complete_response(state)

                # If next step is terminal, run it immediately
                if self._is_terminal(next_step):
                    handler_context = self._run_handler(next_step.get("handler",""), state) if next_step.get("handler") else ""
                    reply = await self._llm(next_step, state, conversation_history, user_message, handler_context)
                    state.current_step_index += 1
                    return {
                        "reply": reply,
                        "system_state": state.to_dict(),
                        "slots_filled": state.slots,
                        "current_step": "complete",
                        "is_complete": True,
                    }

                # Run handler for next step if needed
                handler_context = self._run_handler(next_step["handler"], state) if next_step.get("handler") else ""
                reply = await self._llm(next_step, state, conversation_history, user_message, handler_context)
                return {
                    "reply": reply,
                    "system_state": state.to_dict(),
                    "slots_filled": state.slots,
                    "current_step": next_step["id"],
                    "is_complete": False,
                }
            else:
                # Extraction failed — retry
                state.retry_count += 1
                if state.retry_count >= self.recovery.get("max_retries_per_slot", 3):
                    state.retry_count = 0
                    step = {**step, "instruction": self.recovery["fallback_instruction"]}

                reply = await self._llm(step, state, conversation_history, user_message)
                return {
                    "reply": reply,
                    "system_state": state.to_dict(),
                    "slots_filled": state.slots,
                    "current_step": step["id"],
                    "is_complete": False,
                }

        # ── Multi-slot step (name + phone) ────────────────────────────────────
        if step.get("slots"):
            self._extract_name_and_phone(user_message, state)

            if all(k in state.slots for k in step["slots"]):
                state.current_step_index += 1
                next_step = self._get_step(state.current_step_index)
                if next_step is None:
                    return self._complete_response(state)

                handler_context = self._run_handler(next_step["handler"], state) if next_step.get("handler") else ""
                reply = await self._llm(next_step, state, conversation_history, user_message, handler_context)
                return {
                    "reply": reply,
                    "system_state": state.to_dict(),
                    "slots_filled": state.slots,
                    "current_step": next_step["id"],
                    "is_complete": False,
                }
            else:
                # Not all slots collected yet — ask again
                reply = await self._llm(step, state, conversation_history, user_message)
                return {
                    "reply": reply,
                    "system_state": state.to_dict(),
                    "slots_filled": state.slots,
                    "current_step": step["id"],
                    "is_complete": False,
                }

        # Fallback
        reply = await self._llm(step, state, conversation_history, user_message)
        return {
            "reply": reply,
            "system_state": state.to_dict(),
            "slots_filled": state.slots,
            "current_step": step["id"],
            "is_complete": False,
        }

    def _complete_response(self, state: SkillState) -> Dict:
        return {
            "reply": "Tack för din bokning!",
            "system_state": state.to_dict(),
            "slots_filled": state.slots,
            "current_step": "complete",
            "is_complete": True,
        }