# OnMixAI — 30-second demo

A self-contained walkthrough that shows the one thing that matters for a safety-critical operator
copilot: **it cites its sources, and it refuses to guess when it has none.** The corpus is fully
fictional, manufacturing-flavored (an SOP, an incident-response procedure, a maintenance schedule,
a safety-data sheet).

## Setup (once)

```bash
docker compose -f infra/docker-compose.yml up --build      # bring up the stack
cd backend && python -m scripts.seed_demo                  # seed the demo org + corpus
```

`seed_demo` is idempotent and refuses to run with `ENV=prod`. It prints the demo credentials:

```
org slug : onmix-demo
email    : demo@onmix.test
password : demo-operator-pw-123456
```

## The 30 seconds

1. **Log in** at the web app with the credentials above.

2. **Ask the answerable question** (paste verbatim):

   > What temperature should the Reactor R-200 jacket be preheated to during startup?

   → A grounded answer with an inline citation: **“…preheated to 180 °C during startup [1]”**,
   where **[1]** links to *reactor-r200-startup-sop.txt*. A precise parameter, traceable to the
   exact source — not a paraphrase.

3. **Ask the refusal question** (paste verbatim):

   > What is the maximum occupational exposure limit for hydrazine?

   → The system **refuses** rather than answering. The corpus has a safety-data sheet — but for
   *Solvent X*, not hydrazine — so there is no source for this value. Instead of guessing a safety
   parameter (the dangerous failure mode), it declines: *cite-or-refuse*. This is exactly the
   behaviour you want on a plant floor.

4. *(Optional)* Open **Admin** (the demo user is an owner): the audit log shows the two queries,
   token usage, and — if you toggle PII redaction — a consequence-confirm dialog.

## Why this demo is the point

Same code path, both queries. The answerable one proves grounding is precise; the refusal one
proves the system won't fabricate a safety value it can't source. The refusal is on content
*genuinely absent* from the corpus (asserted in `tests/demo/test_demo_path.py`), not a tuned
near-miss — so the behaviour is real, and CI keeps it from rotting.
