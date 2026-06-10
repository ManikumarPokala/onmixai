"""The OnMixAI demo corpus + the two demo queries — the single source of truth shared by the seed
script (scripts/seed_demo.py) and the end-to-end test (tests/demo/test_demo_path.py), so the demo
you show and the demo CI verifies can never drift.

Fully fictional, manufacturing/operations-flavored (an SOP, an incident-response procedure, a
maintenance schedule, a safety-data sheet) so it reads like an operator copilot — no real content
of any company. The two queries are the payload:

  * ANSWERABLE pulls a SPECIFIC, citable parameter (a named step + a number) from one document, so
    the cited answer shows a precise source + location, not a vague paraphrase.
  * REFUSAL is plausibly-operational and safety-flavored — an exposure limit for a material that is
    genuinely NOT in the corpus — so the system visibly refuses to guess a safety parameter it has
    no source for, rather than fabricating one. That is the sentence that matters on a plant floor.
"""

from dataclasses import dataclass

DEMO_ORG_SLUG = "onmix-demo"
DEMO_ORG_NAME = "OnMixAI Demo Co."
DEMO_USER_EMAIL = "demo@onmix.test"
DEMO_USER_PASSWORD = "demo-operator-pw-123456"  # demo-only; the script refuses to run in prod
DEMO_USER_NAME = "Demo Operator"
DEMO_COLLECTION = "Operations knowledge base"


@dataclass(frozen=True)
class DemoDoc:
    filename: str
    content: str


# Each document is short and self-contained; the SOP carries the answerable parameter.
DOCS: tuple[DemoDoc, ...] = (
    DemoDoc(
        "reactor-r200-startup-sop.txt",
        "Reactor R-200 Startup Procedure (SOP-R200-01).\n"
        "Step 1 — Purge: inert the vessel with nitrogen at 2.5 bar for 10 minutes.\n"
        "Step 2 — Preheat: bring the reactor jacket to 180 degrees C before introducing "
        "feedstock.\n"
        "Step 3 — Charge: add feedstock at no more than 50 kg per minute with the agitator "
        "running.\n"
        "Step 4 — Hold: maintain 180 degrees C and monitor jacket pressure until the batch "
        "is stable.",
    ),
    DemoDoc(
        "coolant-leak-incident-response.txt",
        "Coolant Leak Incident Response (IRP-04).\n"
        "On detection of a cooling-water leak: isolate the affected loop, switch to the backup "
        "chiller, and notify the shift supervisor. Do not restart the reactor until the leak is "
        "confirmed sealed and jacket temperature is back within the documented startup range.",
    ),
    DemoDoc(
        "pump-p101-maintenance-schedule.txt",
        "Pump P-101 Maintenance Schedule (MS-P101).\n"
        "Quarterly: inspect the mechanical seal and replace if weeping. Annually: replace bearings "
        "and re-align the coupling to within 0.05 mm. Log all work in the maintenance system.",
    ),
    DemoDoc(
        "solvent-x-safety-data.txt",
        "Solvent X Safety Data Summary (SDS-X).\n"
        "Solvent X is a flammable liquid. Store below 25 degrees C, away from ignition sources. "
        "The documented occupational exposure limit for Solvent X is 50 ppm (8-hour TWA). Use "
        "local exhaust ventilation when handling.",
    ),
)

# --- the two demo queries (verbatim) ---

# Answerable: a specific numeric parameter from a named step in the SOP.
ANSWERABLE_QUERY = (
    "What temperature should the Reactor R-200 jacket be preheated to during startup?"
)
ANSWERABLE_DOC = "reactor-r200-startup-sop.txt"  # the citation must resolve here
ANSWERABLE_FACT = "180"  # degrees C — the precise value the cited answer should carry

# Refusal: a safety/exposure parameter for a material that is NOT anywhere in the corpus. The
# corpus DOES contain a safety sheet (Solvent X) — but not for hydrazine — so the system must
# refuse to guess rather than mis-attribute Solvent X's limit. ``REFUSAL_ABSENT_TERM`` is asserted
# to appear in zero chunks, proving the refusal is genuinely out-of-corpus (not a near-miss).
REFUSAL_QUERY = "What is the maximum occupational exposure limit for hydrazine?"
REFUSAL_ABSENT_TERM = "hydrazine"
