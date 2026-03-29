"""
Dialog Managers
===============
Two implementations sharing the same LLM adapter interface:

  UnconstrainedDialogManager  — single system prompt, LLM has full freedom
  SkillBasedDialogManager     — step-by-step skill execution with slot filling
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

Hjälp kunden att boka en tid. Du behöver samla in:
- Typ av ärende
- Önskad verkstad
- Datum och tid
- Kundens namn och telefonnummer

När du har all information, bekräfta bokningen och ge en bokningsreferens.
Var hjälpsam och naturlig i konversationen."""


class UnconstrainedDialogManager:
    """
    Fully prompt-driven. The LLM decides how to handle the conversation.
    This is the BASELINE (system A) in the thesis comparison.
    """

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

    async def respond(
        self,
        conversation_history: List[Dict],
        user_message: str,
    ) -> Dict:
        messages = [Message(m["role"], m["content"]) for m in conversation_history]
        messages.append(Message("user", user_message))

        reply = await self.adapter.chat(
            messages=messages,
            system_prompt=self._build_system_prompt(),
            temperature=0.7,
        )

        return {
            "reply": reply,
            "system_state": None,   # unconstrained has no tracked state
            "slots_filled": {},
            "current_step": "free",
        }


# ---------------------------------------------------------------------------
# 2. Skill-Based Dialog Manager
# ---------------------------------------------------------------------------

SKILL_PATH = Path(__file__).parent.parent / "skills" / "boka_bilverkstad.yaml"


class SkillState:
    """Tracks progress through the skill steps and collected slot values."""

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
    """
    Executes the YAML skill step by step.
    This is the CONSTRAINED system (system B) in the thesis comparison.

    Flow per turn:
      1. Load state from session
      2. Try to extract the expected slot from user's message
      3. If valid → advance to next step, build next prompt
      4. If invalid → retry with on_invalid instruction
      5. Call any Python handler (e.g. fetch available slots)
      6. Build a tightly scoped system prompt for the current step only
    """

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

    # -- Slot extraction ----------------------------------------------------

    def _extract_slot(self, step: Dict, user_message: str) -> Optional[str]:
        """Try to extract and validate the expected slot from user input."""
        validation = step.get("validation", {})
        vtype = validation.get("type")
        msg_lower = user_message.lower().strip()

        if vtype == "enum":
            options = validation["options"]
            fuzzy = validation.get("fuzzy_match", False)
            # Direct match
            for opt in options:
                if opt in msg_lower:
                    return opt
            # Fuzzy synonyms
            if fuzzy:
                synonyms = {
                    "service": ["olja", "oljebyte", "service", "filter", "kontroll"],
                    "däckbyte": ["däck", "hjul", "sommar", "vinter", "dubb"],
                    "bromsar": ["broms", "bromsa", "bromsskiva", "belägg"],
                    "ac": ["ac", "luft", "kyla", "klimat", "luftkonditionering"],
                    "besiktning": ["besiktning", "besikta", "kontrollbesiktning"],
                    "annat": ["annat", "diagnos", "fel", "ljud", "problem"],
                    "vasastan": ["vasastan", "uppland", "vasa", "norr"],
                    "sodermalm": ["söder", "södermalm", "hornsgatan", "horn"],
                    "nacka": ["nacka", "värmdö", "värmdövägen"],
                    "solna": ["solna", "frösunda", "norra"],
                    "ja": ["ja", "yes", "ok", "okej", "stämmer", "rätt", "bekräfta", "correct"],
                    "nej": ["nej", "no", "fel", "ändra", "avbryt", "cancel"],
                }
                for opt, syns in synonyms.items():
                    if opt in options and any(s in msg_lower for s in syns):
                        return opt
            return None

        elif vtype == "boolean":
            true_vals = validation.get("true_values", ["ja"])
            false_vals = validation.get("false_values", ["nej"])
            if any(v in msg_lower for v in true_vals):
                return "ja"
            if any(v in msg_lower for v in false_vals):
                return "nej"
            return None

        elif vtype == "available_slot":
            loc_id = self.data["available_slots"]
            # Try to find a time mention in the message
            slots_for_loc = self.data["available_slots"].get(
                self.current_state.slots.get("location_id", ""), []
            )
            for slot in slots_for_loc:
                date_part, time_part = slot.split(" ")
                if time_part in msg_lower or date_part in msg_lower:
                    return slot
            # Try partial time match like "08:00" or "8"
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
            if re.match(pattern, msg_lower):
                return user_message.strip()
            return None

        # No validation = always accept
        return user_message.strip() if user_message.strip() else None

    # -- Python handlers ----------------------------------------------------

    def _run_handler(self, handler_name: str, state: SkillState) -> str:
        """Run a Python handler and return context to inject into the prompt."""
        if handler_name == "get_available_slots":
            loc_id = state.slots.get("location_id", "")
            slots = self.data["available_slots"].get(loc_id, [])
            loc_name = next(
                (l["name"] for l in self.data["locations"] if l["id"] == loc_id),
                loc_id,
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

    # -- Prompt building ----------------------------------------------------

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

    # -- Main respond method ------------------------------------------------

    async def respond(
        self,
        conversation_history: List[Dict],
        user_message: str,
        state_dict: Optional[Dict] = None,
    ) -> Dict:
        # Restore or create state
        self.current_state = SkillState.from_dict(state_dict) if state_dict else SkillState()
        state = self.current_state

        step = self._get_step(state.current_step_index)

        if step is None:
            # Skill complete — should not happen in normal flow
            return {
                "reply": "Tack för din bokning! Är det något annat jag kan hjälpa dig med?",
                "system_state": state.to_dict(),
                "slots_filled": state.slots,
                "current_step": "complete",
            }

        # First turn: just greet, no extraction needed
        is_first_turn = len(conversation_history) == 0

        slot_filled = False
        handler_context = ""

        if not is_first_turn and step.get("slot"):
            extracted = self._extract_slot(step, user_message)

            if extracted:
                slot_key = step["slot"]
                state.slots[slot_key] = extracted
                state.retry_count = 0
                state.current_step_index += 1
                slot_filled = True

                # Advance to next step
                step = self._get_step(state.current_step_index)
                if step is None:
                    return {
                        "reply": "Bokningen är klar! Tack för att du kontaktade oss.",
                        "system_state": state.to_dict(),
                        "slots_filled": state.slots,
                        "current_step": "complete",
                    }

                # Run handler for new step if defined
                if step.get("handler"):
                    handler_context = self._run_handler(step["handler"], state)
            else:
                # Slot extraction failed
                state.retry_count += 1
                max_retries = self.recovery.get("max_retries_per_slot", 3)
                if state.retry_count >= max_retries:
                    state.retry_count = 0
                    # Use fallback instruction
                    step = {**step, "instruction": self.recovery["fallback_instruction"]}

        elif not is_first_turn and step.get("slots"):
            # Multi-slot step (name + phone)
            for slot_key in step["slots"]:
                extracted = self._extract_slot(
                    {**step, "slot": slot_key, "validation": step.get("validation", {}).get(slot_key, {})},
                    user_message,
                )
                if extracted:
                    state.slots[slot_key] = extracted

            # Check if all required slots are filled
            if all(k in state.slots for k in step["slots"]):
                state.current_step_index += 1
                step = self._get_step(state.current_step_index)
                if step is None:
                    return {"reply": "Klart!", "system_state": state.to_dict(), "slots_filled": state.slots, "current_step": "complete"}

        # Run handler for current step if defined and not yet run
        if not handler_context and step.get("handler"):
            handler_context = self._run_handler(step["handler"], state)

        # Build prompt and get reply
        messages = [Message(m["role"], m["content"]) for m in conversation_history]
        if not is_first_turn:
            messages.append(Message("user", user_message))

        system_prompt = self._build_step_prompt(step, state, handler_context)

        reply = await self.adapter.chat(
            messages=messages,
            system_prompt=system_prompt,
            temperature=0.4,   # lower temp = more predictable for constrained system
        )

        return {
            "reply": reply,
            "system_state": state.to_dict(),
            "slots_filled": state.slots,
            "current_step": step["id"],
        }