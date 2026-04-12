"""
Dialog Managers — fixed version
Fixes:
  1. TypeError: 'in <string>' requires string as left operand, not bool
     — caused by YAML parsing true_values/false_values as Python booleans
  2. Name/phone collection loop — now accepts name and phone in any order,
     in the same message or separate messages, with clearer prompting
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
    lines = []
    for loc in data["locations"]:
        lines.append(f"- {loc['name']} ({loc['address']}), tel: {loc['phone']}")
    return "\n".join(lines)


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
Du hjälper kunder att boka tider för bilservice och reparationer.
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

    def _get_step(self, index: int) -> Optional[Dict]:
        if index < len(self.steps):
            return self.steps[index]
        return None

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
                    "service": ["olja", "oljebyte", "service", "filter", "kontroll", "servis"],
                    "däckbyte": ["däck", "dack", "hjul", "sommar", "vinter", "dubb"],
                    "bromsar": ["broms", "bromsa", "bromsskiva", "belägg"],
                    "ac": ["ac", "luft", "kyla", "klimat", "luftkonditionering", "a/c"],
                    "besiktning": ["besiktning", "besikta", "kontrollbesiktning"],
                    "annat": ["annat", "diagnos", "fel", "ljud", "problem"],
                    "vasastan": ["vasastan", "uppland", "vasa", "upplandsgatan"],
                    "sodermalm": ["söder", "södermalm", "hornsgatan", "horn", "sodermalm"],
                    "nacka": ["nacka", "värmdö", "värmdövägen"],
                    "solna": ["solna", "frösunda", "norra"],
                    "ja": ["ja", "yes", "ok", "okej", "stämmer", "rätt", "bekräfta", "japp", "jo", "correct"],
                    "nej": ["nej", "no", "fel", "ändra", "avbryt", "cancel", "nope"],
                }
                for opt, syns in synonyms.items():
                    if opt in options and any(s in msg_lower for s in syns):
                        return opt
            return None

        elif vtype == "boolean":
            # FIX: convert all values to strings to avoid TypeError when YAML parses as bool
            true_vals = [str(v).lower() for v in validation.get("true_values", ["ja"])]
            false_vals = [str(v).lower() for v in validation.get("false_values", ["nej"])]
            if any(v in msg_lower for v in true_vals):
                return "ja"
            if any(v in msg_lower for v in false_vals):
                return "nej"
            return None

        elif vtype == "available_slot":
            slots_for_loc = self.data["available_slots"].get(
                self.current_state.slots.get("location_id", ""), []
            )
            for slot in slots_for_loc:
                date_part, time_part = slot.split(" ")
                if time_part in msg_lower or date_part in msg_lower:
                    return slot
            time_pattern = re.search(r"\b(\d{1,2})[:\.]?(\d{0,2})\b", user_message)
            if time_pattern:
                hour = time_pattern.group(1).zfill(2)
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

    def _run_handler(self, handler_name: str, state: SkillState) -> str:
        if handler_name == "get_available_slots":
            loc_id = state.slots.get("location_id", "")
            slots = self.data["available_slots"].get(loc_id, [])
            loc_name = next(
                (l["name"] for l in self.data["locations"] if l["id"] == loc_id), loc_id,
            )
            if slots:
                return f"Lediga tider på {loc_name}:\n{format_slots_by_day(slots)}"
            return f"Inga lediga tider hittades för {loc_name}."

        elif handler_name == "create_booking":
            ref = f"BK{uuid.uuid4().hex[:6].upper()}"
            state.booking_ref = ref
            slots = state.slots
            loc_id = slots.get("location_id", "")
            loc = next((l for l in self.data["locations"] if l["id"] == loc_id), {})
            return (
                f"Bokningsreferens: {ref}\n"
                f"Verkstad: {loc.get('name', loc_id)}\n"
                f"Telefon: {loc.get('phone', '')}"
            )
        return ""

    def _build_step_prompt(self, step: Dict, state: SkillState, handler_context: str = "") -> str:
        global_ctx = self.skill["system_context"]
        step_instruction = step["instruction"]
        filled_summary = ""
        if state.slots:
            lines = []
            labels = {
                "service_type": "Ärende",
                "location_id": "Verkstad",
                "appointment_slot": "Tid",
                "customer_name": "Namn",
                "customer_phone": "Telefon",
                "confirmation": "Bekräftelse",
            }
            for k, v in state.slots.items():
                lines.append(f"  {labels.get(k, k)}: {v}")
            filled_summary = "Redan insamlad information:\n" + "\n".join(lines)

        parts = [global_ctx.strip(), ""]
        if filled_summary:
            parts += [filled_summary, ""]
        if handler_context:
            parts += [handler_context, ""]
        parts += [
            f"Nuvarande uppgift:\n{step_instruction.strip()}",
            "",
            "Svara ENBART på det som efterfrågas i nuvarande uppgift. "
            "Samla inte in annan information.",
        ]
        return "\n".join(parts)

    def _extract_name_and_phone(self, user_message: str, state: SkillState):
        """
        FIX: Improved name/phone extraction.
        Handles same-message or separate-message input.
        Phone is detected by digits/+/- pattern.
        Name is everything that is NOT the phone number.
        """
        msg = user_message.strip()

        # Detect phone: 7+ digits, may include +, -, spaces
        phone_pattern = re.compile(r'[\+]?[\d\s\-]{7,15}')
        phone_match = phone_pattern.search(msg)

        if phone_match:
            phone = re.sub(r'\s+', '', phone_match.group()).strip()
            # Remove phone from message to get name
            name_part = msg[:phone_match.start()] + msg[phone_match.end():]
            name_part = re.sub(r'\s+', ' ', name_part).strip()
            # Remove common filler words
            for filler in ["mitt namn är", "jag heter", "my name is", "namn:", "telefon:", "tel:"]:
                name_part = name_part.lower().replace(filler, "").strip()
            name_part = name_part.strip(" ,.-")

            if phone and len(phone) >= 7:
                if "customer_phone" not in state.slots:
                    state.slots["customer_phone"] = phone
            if name_part and len(name_part) >= 2:
                if "customer_name" not in state.slots:
                    state.slots["customer_name"] = name_part.title()
        else:
            # No phone detected — treat as name if name not yet collected
            if "customer_name" not in state.slots and len(msg) >= 2:
                state.slots["customer_name"] = msg.title()

    async def respond(
        self,
        conversation_history: List[Dict],
        user_message: str,
        state_dict: Optional[Dict] = None,
    ) -> Dict:
        self.current_state = SkillState.from_dict(state_dict) if state_dict else SkillState()
        state = self.current_state

        step = self._get_step(state.current_step_index)

        if step is None:
            return {
                "reply": "Tack för din bokning! Är det något annat jag kan hjälpa dig med?",
                "system_state": state.to_dict(),
                "slots_filled": state.slots,
                "current_step": "complete",
                "is_complete": True,
            }

        is_first_turn = len(conversation_history) == 0
        handler_context = ""

        # ── Terminal step (no slot) ───────────────────────────────────────────
        if not is_first_turn and step.get("slot") is None and not step.get("slots"):
            if step.get("handler"):
                handler_context = self._run_handler(step["handler"], state)

            state.current_step_index += 1
            messages = [Message(m["role"], m["content"]) for m in conversation_history]
            messages.append(Message("user", user_message))
            system_prompt = self._build_step_prompt(step, state, handler_context)

            reply = await self.adapter.chat(
                messages=messages, system_prompt=system_prompt,
                temperature=0.4, max_tokens=2048,
            )
            return {
                "reply": reply,
                "system_state": state.to_dict(),
                "slots_filled": state.slots,
                "current_step": "complete",
                "is_complete": True,
            }

        # ── Single-slot step ─────────────────────────────────────────────────
        elif not is_first_turn and step.get("slot"):
            extracted = self._extract_slot(step, user_message)

            if extracted:
                slot_key = step["slot"]
                state.slots[slot_key] = extracted
                state.retry_count = 0
                state.current_step_index += 1

                step = self._get_step(state.current_step_index)
                if step is None:
                    return {
                        "reply": "Bokningen är klar! Tack för att du kontaktade oss.",
                        "system_state": state.to_dict(),
                        "slots_filled": state.slots,
                        "current_step": "complete",
                        "is_complete": True,
                    }
                if step.get("handler"):
                    handler_context = self._run_handler(step["handler"], state)
            else:
                state.retry_count += 1
                max_retries = self.recovery.get("max_retries_per_slot", 3)
                if state.retry_count >= max_retries:
                    state.retry_count = 0
                    step = {**step, "instruction": self.recovery["fallback_instruction"]}

        # ── Multi-slot step (name + phone) ───────────────────────────────────
        elif not is_first_turn and step.get("slots"):
            # FIX: use improved extraction that handles both in one message
            self._extract_name_and_phone(user_message, state)

            if all(k in state.slots for k in step["slots"]):
                state.current_step_index += 1
                step = self._get_step(state.current_step_index)
                if step is None:
                    return {
                        "reply": "Klart!",
                        "system_state": state.to_dict(),
                        "slots_filled": state.slots,
                        "current_step": "complete",
                        "is_complete": True,
                    }

        if not handler_context and step.get("handler"):
            handler_context = self._run_handler(step["handler"], state)

        messages = [Message(m["role"], m["content"]) for m in conversation_history]
        if not is_first_turn:
            messages.append(Message("user", user_message))

        system_prompt = self._build_step_prompt(step, state, handler_context)

        reply = await self.adapter.chat(
            messages=messages, system_prompt=system_prompt,
            temperature=0.4, max_tokens=2048,
        )

        return {
            "reply": reply,
            "system_state": state.to_dict(),
            "slots_filled": state.slots,
            "current_step": step["id"],
            "is_complete": False,
        }