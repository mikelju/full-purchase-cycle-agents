# 015 - LinkedIn series

Status: integrated (merged via PR #25)

## Contract
Goal and reason:
The owner wants to publish a short LinkedIn series about the project himself.
This change stores the series plan and the texts of the four posts in `docs/linkedin/series-plan.md`, so the owner publishes from one source.
Out of scope:
The image for post 4, still undecided.
Whether the posts use the Spanish or the English videos, which the owner decides.
Spanish copies of the animations, proposed as a separate change.
Publishing anything: the owner publishes the posts himself.
Constraints / decisions that need the owner:
The Spanish post texts and the first comment are kept exactly as the owner approved them; any change needs the owner.
Posts 2 to 4 are approved in general and the owner may still adjust their wording.
The figures in posts 2 to 4 come from the README and the change records as of 2026-10-16 and must be checked again before publishing.
Current authorization and limits:
Owner approval of the series plan, of post 1 and, in general, of posts 2 to 4: store them, push the branch and open a PR to `main`; never merge.

| ID | Observable criterion | Evidence at verification |
|---|---|---|
| C1 | `docs/linkedin/series-plan.md` exists with the plan, the rules, the status table, the four post texts and the first comment as approved, and a note on the sources of the figures | File reviewed at the final commit; post texts unchanged from the approved versions |
| C2 | `npm run check` passes | `npm run check` exit 0 at the final commit |

## Plan
- [x] Mark change 014 as integrated (merged via PR #24)
- [x] Store the series plan and the four posts (C1)
- [x] Verification, adversarial review and delivery (C2)

## Decisions
- Scope grew from post 1 to all four posts after the owner approved posts 2 to 4 in general; the image for post 4 and the choice of Spanish or English videos stay with the owner.
- Review findings on the Spanish texts are not fixed here because the texts are frozen; they go to the owner as pending decisions (see Delivery).
- Unrelated defect logged: `README.md` line 13 still says phase 10 is "built on its branch, not yet delivered", while the master plan and PR #21 show it merged.

## Adversarial review
| Round | Backend | Range | Lenses | Findings | Status |
|---|---|---|---|---|---|
| 1 | Sonnet subagent | debeb77 plus staged plan with post 1 | correctness, evidence | 3 minor: post 1 presents the coordinator (phase 12, pending) in the present tense; post 1 shows the hand-off to suppliers as automatic while the graphs still run separately; an emoji in the English framing | first two are owner decisions on frozen text; the emoji only names the owner's bullet style, kept |
| 2 | Sonnet subagent | debeb77 plus staged plan with posts 1 to 4 | evidence | 1 important: post 3 presents serving waiting orders as working while README line 13 says phase 10 is not delivered; 1 minor: post 3 says the code checks that the claim email is true, while README reports 3 critical claim drafting errors (CLM-044, CLM-066, CLM-072) | the important one discarded: PR #21 merged phase 10 and README line 954 confirms arrival order, so README line 13 is stale; the minor one is an owner decision on frozen text |

## Delivery
- Branch `cambio-015-linkedin-series`.
- C1: `docs/linkedin/series-plan.md` holds the plan, rules, status table, the four post texts, the first comment and the note on figures; the Spanish texts were not edited in this change.
- C2: `npm run check` exit 0, with ruff clean and 1288 pytest tests passed in 2215.80 s; only the known critical eval cases DOC-060, DOC-112, CLM-044, CLM-066 and CLM-072 are listed, as in change 014.
  The run started before the last Markdown edits to the plan and this record; no check stage reads these files.
- Pending owner decisions on the frozen Spanish texts: post 1 describes the coordinator (phase 12, pending) and the hand-off to suppliers as if already running; post 3 says the code checks the claim email is true while 3 critical claim drafting errors remain; the figures must be checked again before publishing.
