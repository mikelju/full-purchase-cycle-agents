"""Dataset and evaluation commands of the `purchase-cycle` CLI."""

from pathlib import Path

from purchase_cycle.config import MODES
from purchase_cycle.evaluation import dataset as ds
from purchase_cycle.evaluation.planning import SEED, build_plan, read_jsonl, write_jsonl


def cmd_dataset_plan(args) -> int:
    plan = build_plan(args.seed)
    write_jsonl(ds.PLAN_PATH, plan)
    print(f"Planned {len(plan)} cases with seed {args.seed} -> {ds.PLAN_PATH}")
    return 0


def cmd_dataset_check(args) -> int:
    plan = {c["id"]: c for c in read_jsonl(ds.PLAN_PATH)}
    rows = read_jsonl(Path(args.batch))
    failed = 0
    for row in rows:
        errors = ds.validate_case(dict(plan[row["id"]], sentence=row["sentence"]))
        if errors:
            failed += 1
            print(f"{row['id']}: {'; '.join(errors)}")
    print(f"{len(rows)} sentences checked, {failed} rejected")
    return 1 if failed else 0


def cmd_dataset_build(args) -> int:
    plan = read_jsonl(ds.PLAN_PATH)
    cases, rejected = ds.build_dataset(plan, ds.load_sentences())
    for case_id, errors in rejected.items():
        print(f"{case_id}: {'; '.join(errors)}")
    if rejected:
        print(f"{len(rejected)} of {len(plan)} cases rejected; rewrite their sentences and build again")
        return 1
    ds.save_dataset(cases)
    print(f"Built {len(cases)} cases -> {ds.DATASET_PATH}")
    return 0


def add_eval_commands(sub) -> None:
    dataset = sub.add_parser("dataset", help="plan, check and build the golden dataset")
    dsub = dataset.add_subparsers(dest="dataset_command", required=True)
    plan = dsub.add_parser("plan", help="write the seeded case plan")
    plan.add_argument("--seed", type=int, default=SEED)
    plan.set_defaults(handler=cmd_dataset_plan)
    check = dsub.add_parser("check", help="validate a batch of written sentences against the plan")
    check.add_argument("batch")
    check.set_defaults(handler=cmd_dataset_check)
    build = dsub.add_parser("build", help="join plan and sentences, validate and write the dataset")
    build.set_defaults(handler=cmd_dataset_build)

    from purchase_cycle.evaluation import audit

    audit_parser = sub.add_parser("audit", help="owner audit of dataset labels")
    asub = audit_parser.add_subparsers(dest="audit_command", required=True)
    asub.add_parser("create", help="write the review file").set_defaults(handler=lambda args: audit.create_audit())
    asub.add_parser("report", help="compute the label error rate").set_defaults(
        handler=lambda args: audit.report_audit()
    )

    from purchase_cycle.evaluation import web_form_dataset

    web_form_dataset.add_commands(sub)

    from purchase_cycle.evaluation.harness import add_run_commands

    add_run_commands(sub, MODES)
