"""C10 (phase 04): the exceptions demo pauses both samples and the answer command, a separate process, stores them."""

import os
import re
import sqlite3
import subprocess
import sys
import textwrap

from purchase_cycle import config

# Each step runs as its own process with outgoing connections blocked, on the stored recordings.
STEP = textwrap.dedent(
    """
    import socket, sys
    from purchase_cycle import cli

    def blocked(*args, **kwargs):
        raise OSError("network blocked by test")

    socket.socket.connect = blocked
    socket.create_connection = blocked
    sys.exit(cli.main(sys.argv[1:]))
    """
)


def _run(tmp_path, *args):
    env = {**os.environ, "ANTHROPIC_API_KEY": "", "LANGSMITH_API_KEY": "", "PYTHONIOENCODING": "utf-8"}
    checkpoints = [] if args[-1] == "list" else ["--checkpoints", str(tmp_path / "checkpoints.db")]
    step = subprocess.run(
        [sys.executable, "-c", STEP, "--db", str(tmp_path / "business.db"), *args, *checkpoints],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=tmp_path,
    )
    assert step.returncode == 0, step.stdout + step.stderr
    return step.stdout


def test_demo_pauses_both_samples_and_the_answers_store_them_in_new_processes(tmp_path):
    demo = _run(tmp_path, "exceptions-demo")
    assert "mode=replay" in demo
    assert '  line 1: ambiguous - "sanitiser gel litre" x 7  candidates: GEL-1000, GEL-5000' in demo
    assert '  line 3: quantity - "ethyl alcohol 70% 100 ml" x 1200' in demo
    assert '  line 1: ambiguous - "38 packs of caps" x 38  candidates: CAP-BOUF, WIPE-WASH-CAP' in demo
    assert '  line 2: unknown - "30 hospital beds" x 30' in demo
    assert demo.count("question (round 1):") == 2
    assert "Hydroalcoholic hand sanitiser gel 5 litre jerrycan" in demo
    assert "our catalog does not carry hospital beds" in demo
    # The recorded questions hold en dashes; the printed and stored questions are plain ASCII.
    printed = re.findall(r"question \(round 1\):\n(.*?)\npaused", demo, re.DOTALL)
    assert len(printed) == 2 and all(q.isascii() for q in printed)
    with sqlite3.connect(tmp_path / "business.db") as conn:
        stored = [row[0] for row in conn.execute("SELECT question FROM clarifications")]
    assert sorted(stored) == sorted(printed)
    threads = re.findall(r"paused, nothing stored; thread_id=(\S+)", demo)
    assert [t.split("-", 1)[1] for t in threads] == ["web_form_submission", "email_order"]

    answers = config.ROOT / "examples" / "exceptions"
    web = _run(tmp_path, "clarify", "answer", threads[0], "--file", str(answers / "web_form_answer.txt"))
    assert f"thread_id={threads[0]}  channel=web_form  customer=CLI-003" in web
    assert "interpretation:\n  line 1: set GEL-1000 x 7\n  line 3: set ALC70-100 x 120\n" in web
    assert "stored order: 1\n" in web
    assert "Your web form order CLD-0070 is registered as order 1:" in web
    assert "- Ethyl alcohol 70% 100 ml: 120 x bottle at 1.15 EUR = 138.00 EUR" in web

    email = _run(tmp_path, "clarify", "answer", threads[1], "--file", str(answers / "email_answer.txt"))
    assert f"thread_id={threads[1]}  channel=email  customer=RES-010" in email
    assert "interpretation:\n  line 1: set CAP-BOUF x 38\n  line 2: remove\n" in email
    assert "stored order: 2\n" in email
    assert "Subject: Re: Monte Alto order" in email
    assert 'As you asked, these lines are removed from the order:\n- "30 hospital beds" (30)' in email

    assert _run(tmp_path, "clarify", "list") == "no pending clarifications\n"
