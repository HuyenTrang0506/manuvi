import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from src.cli import app
from src.formats import FormatError, out_name
from src.formats.job import build_file_output, prepare_file, translate_file
from src.formats.qa import format_issues
from src.jobs import DirQueue, JobRecord, JobState, LocalStore
from src.llm_gateway.adapters import FakeAdapter, estimate_tokens
from src.pipeline.runner import make_gateway, run_job
from src.pipeline.schema import Segment
from src.worker import Worker

from .fixtures import make_docx, make_pptx
from .test_markdown import CORPUS


def sources(tmp_path):
    md = tmp_path / "in.md"
    md.write_text("# Relay box\n\nDisconnect the **battery** and keep `YT12-AB3C` and [the guide](http://x.example/a).\n\n"
                  "| Part | Note |\n|---|---|\n| YN20-01234-A | Check 12 Nm |\n", encoding="utf-8")
    return {"md": md, "docx": make_docx(tmp_path / "in.docx"), "pptx": make_pptx(tmp_path / "in.pptx")}


@pytest.mark.parametrize("fmt", ["md", "docx", "pptx"])
def test_end_to_end_fake_model_same_format_and_review_data(tmp_path, fmt):
    src = sources(tmp_path)[fmt]
    res = translate_file(src, tmp_path / "out", model="fake")
    assert res["state"] == "done" and res["format"] == fmt
    out = Path(res["output"])
    assert out.name == f"in.vi.{fmt}" and out.stat().st_size > 0
    segs = json.loads(Path(res["segments"]).read_text("utf-8"))
    # same segments.json shape as the PDF pipeline, so the review UI works unchanged
    for s in segs:
        Segment.model_validate({k: v for k, v in s.items() if k not in ("decision", "_pv")})
        assert {"id", "page", "section", "source", "target", "status", "issues", "risk", "kind", "decision"} <= set(s)
        assert s["target"].startswith("VI: ") or s["target"] == ""
    if fmt == "md":
        text = out.read_text("utf-8")
        assert "`YT12-AB3C`" in text and "(http://x.example/a)" in text and "YN20-01234-A" in text and "12 Nm" in text
        assert "|---|---|" in text and "VI: " in text
    else:
        assert out.read_bytes()[:2] == b"PK"


def test_prepare_file_writes_chunks_with_protected_spans(tmp_path):
    md = sources(tmp_path)["md"]
    info = prepare_file(tmp_path / "job", md)
    chunks = json.loads((tmp_path / "job" / "chunks.json").read_text("utf-8"))
    assert info["chunks"] == len(chunks) and all(c["fmt"] == "md" for c in chunks)
    para = next(c for c in chunks if "battery" in c["source"])
    assert "`YT12-AB3C`" in para["protect"] and para["ctx"] == "Relay box"
    assert re.search(r"<b\d+>battery</b\d+>", para["source"])


def test_rejected_files_never_reach_the_pipeline(tmp_path):
    p = make_docx(tmp_path / "vba.docx", extra_entries={"word/vbaProject.bin": b"x"})
    with pytest.raises(FormatError) as e:
        prepare_file(tmp_path / "job", p)
    assert e.value.code == "MACRO_REJECTED" and not (tmp_path / "job" / "chunks.json").exists()


class Tamper(FakeAdapter):
    """Model that misbehaves: drops inline tags and protected placeholders on the first attempt."""
    name = "fake"

    def __init__(self, **kw):
        super().__init__(**kw)
        self.attempts = 0

    def complete(self, req, model_id):
        self.calls += 1
        user = next(m for m in req.messages if m["role"] == "user")["content"]
        fix = any(m["role"] == "user" and m["content"].startswith("FIX") for m in req.messages)
        out = "Đã dịch: " + user
        if not fix:
            out = re.sub(r"</?[a-z]\d+/?>", "", out)            # drop tags
            out = re.sub(r"⟦P\d+⟧", "", out)                    # drop placeholders
        return {"text": out, "tokens_in": estimate_tokens(req.messages), "tokens_out": estimate_tokens(out)}


def test_guard_catches_dropped_tags_and_placeholders_then_fix_loop_repairs(tmp_path):
    md = sources(tmp_path)["md"]
    job = tmp_path / "job"
    prepare_file(job, md)
    gw = make_gateway(job, "dev", adapters={"fake": Tamper()})
    res = run_job(job, gateway=gw, approve_batches=True, approve_safety=True, batch_size=100)
    assert res.state == "done"
    segs = {s["id"]: s for s in json.loads((job / "segments.json").read_text("utf-8"))}
    para = next(s for s in segs.values() if "battery" in s["source"])
    assert para["status"] in ("ok", "warn")                      # repaired by the fix loop
    assert "`YT12-AB3C`" in para["target"] and re.search(r"<b\d+>", para["target"])
    dest, rep = build_file_output(job, md)
    assert "**" in dest.read_text("utf-8") and "`YT12-AB3C`" in dest.read_text("utf-8")


def test_unfixable_tags_are_flagged_needs_review():
    assert [i.rule_id for i in format_issues("a <b1>x</b1>", "a x")] == ["FMT001", "LEAK001"] or \
        "FMT001" in [i.rule_id for i in format_issues("a <b1>x</b1>", "a x")]
    assert "FMT001" in [i.rule_id for i in format_issues("<x1/> a", "a <x1/> <x1/>")]
    assert not [i for i in format_issues("a <b1>x</b1>", "Đã <b1>y</b1>") if i.rule_id == "FMT001"]
    # leakage rule reuses the eval metric: English words left over are reported
    assert "LEAK001" in [i.rule_id for i in format_issues("Disconnect the battery now", "Disconnect the battery ngay")]


def test_human_edit_in_segments_json_reaches_the_output(tmp_path):
    md = sources(tmp_path)["md"]
    res = translate_file(md, tmp_path / "out", model="fake", job_dir=tmp_path / "job")
    segs = json.loads((tmp_path / "job" / "segments.json").read_text("utf-8"))
    head = next(s for s in segs if s["source"] == "Relay box")
    head["target"] = "Hộp rơ le"
    rej = next(s for s in segs if "Disconnect" in s["source"])
    rej["decision"] = "rejected"
    (tmp_path / "job" / "segments.json").write_text(json.dumps(segs, ensure_ascii=False))
    dest, _ = build_file_output(tmp_path / "job", md, tmp_path / "out2")
    text = dest.read_text("utf-8")
    assert text.startswith("# Hộp rơ le\n") and "Disconnect the **battery**" in text    # rejected -> source kept


def test_out_name():
    assert out_name("My Manual (v2).docx", "docx") == "My_Manual_v2.vi.docx"
    assert out_name("/x/notes.md", "md") == "notes.vi.md"


def test_cli_translate_file(tmp_path):
    md = sources(tmp_path)["md"]
    r = CliRunner().invoke(app, ["translate-file", str(md), "--model", "fake", "--out", str(tmp_path / "o")])
    assert r.exit_code == 0, r.output
    assert (tmp_path / "o" / "in.vi.md").exists()
    bad = tmp_path / "x.docm"
    bad.write_bytes(b"PK\x03\x04")
    r = CliRunner().invoke(app, ["translate-file", str(bad), "--out", str(tmp_path / "o")])
    assert r.exit_code == 2 and "MACRO_REJECTED" in r.output


@pytest.mark.parametrize("fmt", ["docx", "pptx", "md"])
def test_worker_runs_non_pdf_job(tmp_path, fmt):
    from src.pipeline.demo import DemoTranslator
    src = sources(tmp_path)[fmt]
    store = LocalStore(tmp_path / "store")
    q = DirQueue(tmp_path / "q", sleep=lambda s: None)
    jid = "job" + fmt + "abc"
    store.put_job(JobRecord(id=jid, name="My Doc", owner="founder", sections="1:1-1", format=fmt))
    store.put_bytes(jid, f"input.{fmt}", src.read_bytes())
    q.enqueue(jid)
    w = Worker(q, store, tmp_path / "work", profile="dev", adapters=lambda: {"fake": DemoTranslator()}, heartbeat=0.05)
    r = w.run_once()
    for _ in range(10):     # "Note" in the text is a safety word: a human approves each such segment in the app
        if r == "done":
            break
        assert r == "waiting"
        segs = json.loads(store.get_bytes(jid, "segments.json"))
        for sg in segs:
            if sg["kind"] == "signal":
                sg["decision"] = "approved"
        store.put_bytes(jid, "segments.json", json.dumps(segs).encode())
        q.enqueue(jid)
        r = w.run_once()
    assert r == "done"
    st = store.get_status(jid)
    assert st.state == JobState.done and st.error_code is None and st.pending == 0
    assert f"out/My_Doc.vi.{fmt}" in st.outputs and "out/segments.xlsx" in st.outputs
    assert store.exists(jid, f"out/My_Doc.vi.{fmt}")
    segs = json.loads(store.get_bytes(jid, "segments.json"))
    assert segs and all(s["target"] for s in segs)


def test_worker_fails_job_on_hostile_file(tmp_path):
    store = LocalStore(tmp_path / "store")
    q = DirQueue(tmp_path / "q", sleep=lambda s: None)
    jid = "jobhostile1"
    p = make_docx(tmp_path / "v.docx", extra_entries={"word/vbaProject.bin": b"x"})
    store.put_job(JobRecord(id=jid, name="x", owner="founder", sections="1:1-1", format="docx"))
    store.put_bytes(jid, "input.docx", p.read_bytes())
    q.enqueue(jid)
    w = Worker(q, store, tmp_path / "work", profile="dev", heartbeat=0.05)
    assert w.run_once() == "failed"
    st = store.get_status(jid)
    assert st.state == JobState.failed and st.error_code == "MACRO_REJECTED"
