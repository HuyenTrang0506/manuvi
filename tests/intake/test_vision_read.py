import json

import pymupdf
import pytest

from src.intake.vision_detect import analyze_page
from src.intake.vision_read import (TextBlock, VisionConfig, VisionReader, choose_dpi, code_issues, iou,
                                    load_vision_config, parse_response, plan_tiles, to_visible, VisionParseError)
from src.llm_gateway import Gateway
from src.llm_gateway.adapters import OllamaAdapter
from src.llm_gateway.errors import DataClassViolation

from .vision_helpers import (ScriptedVLM, image_only_page, make_gateway, mixed_page, raster_label,
                             visible_truth)

CFG = VisionConfig()


def reader(tmp_path, adapter, cfg=CFG):
    return VisionReader(make_gateway(tmp_path, adapter), "job1", cfg)


# ---- rendering plan --------------------------------------------------------------------------------------
def test_dpi_follows_page_size_and_tiles_cut_big_dense_pages():
    a4, a3 = pymupdf.Rect(0, 0, 595, 842), pymupdf.Rect(0, 0, 842, 1191)
    assert choose_dpi(a4, CFG) > choose_dpi(a3, CFG)
    tiles, dpi = plan_tiles(a4, CFG)
    assert len(tiles) == 1 and tiles[0] == a4
    tiles, dpi = plan_tiles(a3, CFG, dense=True)
    assert len(tiles) >= 2 and dpi >= CFG.dense_dpi
    union = pymupdf.Rect(tiles[0])
    for t in tiles:
        union |= t
        assert max(t.width, t.height) / 72 * dpi <= CFG.max_tile_px + 1
    assert union == a3
    assert any(not (a & b).is_empty for a in tiles for b in tiles if a != b)  # overlap


# ---- mapping back to pdf coordinates -----------------------------------------------------------------------
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("cropbox", [None, (30, 40, 560, 700)])
def test_bbox_maps_back_with_rotate_and_cropbox(tmp_path, rotation, cropbox):
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_image(pymupdf.Rect(300, 400, 530, 454), stream=raster_label("CAB WORK LIGHT"))
    if cropbox:
        p.set_cropbox(pymupdf.Rect(*cropbox))
    p.set_rotation(rotation)
    truth = pymupdf.Rect(p.get_image_info()[0]["bbox"])
    vlm = ScriptedVLM(visible_truth(p, [("CAB WORK LIGHT", truth)]))
    res = reader(tmp_path, vlm).read_page(p)
    assert res.mode == "page" and len(res.blocks) == 1
    b = res.blocks[0]
    assert b.text == "CAB WORK LIGHT" and not b.needs_review
    assert iou(pymupdf.Rect(b.bbox), truth) > 0.99


def test_text_rotation_follows_page_rotate():
    from src.intake.vision_read import text_rotation
    doc = pymupdf.open()
    p = doc.new_page()
    seen = set()
    for r in (0, 90, 180, 270):
        p.set_rotation(r)
        seen.add(text_rotation(p))
    assert seen == {0, 90, 180, 270}


# ---- detection + page / region reads ----------------------------------------------------------------------
def test_image_only_page_is_read_as_a_page(tmp_path):
    doc = pymupdf.open()
    truth = image_only_page(doc)
    p = doc[0]
    d, tb = analyze_page(p, CFG)
    assert d.mode == "page" and "no_text_layer" in d.reasons and tb == []
    vlm = ScriptedVLM(visible_truth(p, truth))
    res = reader(tmp_path, vlm).read_page(p)
    assert [b.text for b in res.blocks] == ["FUSE BOX", "MAIN RELAY"]
    assert all(not b.needs_review for b in res.blocks) and res.calls == 1 and not res.failed


def test_mixed_page_reads_only_the_raster_region(tmp_path):
    doc = pymupdf.open()
    rect = mixed_page(doc)
    p = doc[0]
    d, tb = analyze_page(p, CFG)
    assert d.mode == "regions" and len(d.regions) == 1 and d.regions[0].contains(rect)
    assert tb and "hydraulic" in tb[0].text
    vlm = ScriptedVLM(visible_truth(p, [("CAB WORK LIGHT", rect)]))
    res = reader(tmp_path, vlm).read_page(p, d.regions, tb)
    assert [b.text for b in res.blocks] == ["CAB WORK LIGHT"] and res.mode == "regions"
    assert len(vlm.requests) == 1
    clip = [float(v) for v in vlm.requests[0].target_id.split(":")[1].split(",")]
    assert clip[0] >= 280 and clip[1] >= 380       # the request covers the label area, not the whole page
    assert vlm.requests[0].data_class == "customer" and vlm.requests[0].model_alias == "vision"


def test_text_layer_blocks_are_not_duplicated(tmp_path):
    doc = pymupdf.open()
    rect = mixed_page(doc)
    p = doc[0]
    tb = analyze_page(p, CFG)[1]
    prose_rect = pymupdf.Rect(tb[0].bbox)
    vlm = ScriptedVLM(visible_truth(p, [("CAB WORK LIGHT", rect),
                                        ("Check the hydraulic pump pressure before starting the engine.", prose_rect)]))
    res = reader(tmp_path, vlm).read_page(p, None, tb)       # whole page read on a page that has a text layer
    assert [b.text for b in res.blocks] == ["CAB WORK LIGHT"] and res.duplicates == 1


def test_conflicting_text_on_text_layer_is_flagged(tmp_path):
    doc = pymupdf.open()
    mixed_page(doc)
    p = doc[0]
    tb = analyze_page(p, CFG)[1]
    vlm = ScriptedVLM(visible_truth(p, [("Replace the transmission belt weekly", pymupdf.Rect(tb[0].bbox))]))
    res = reader(tmp_path, vlm).read_page(p, None, tb)
    assert res.blocks[0].needs_review and "conflicts_text_layer" in res.blocks[0].reasons


# ---- recorded bad outputs -------------------------------------------------------------------------------
def read_raw(tmp_path, blocks, text_blocks=(), cfg=CFG):
    doc = pymupdf.open()
    doc.new_page().insert_text((50, 50), "x")
    vlm = ScriptedVLM(raw=json.dumps({"blocks": blocks}))
    return reader(tmp_path, vlm, cfg).read_page(doc[0], None, list(text_blocks))


def blk(text, bbox=(0.1, 0.1, 0.5, 0.15), conf=0.9, kind="label"):
    return {"text": text, "bbox": list(bbox), "kind": kind, "confidence": conf}


def test_cjk_intrusion_is_blocked_unless_source_has_japanese(tmp_path):
    res = read_raw(tmp_path, [blk("油圧ポンプ"), blk("FUSE BOX", (0.1, 0.3, 0.5, 0.35))])
    bad = next(b for b in res.blocks if "script" in b.reasons)
    assert bad.blocking and bad.needs_review
    ok = read_raw(tmp_path, [blk("油圧ポンプ")], [TextBlock("日本語の説明 text", (0, 0, 10, 10))])
    assert not ok.blocks[0].needs_review


def test_out_of_bounds_box_is_clamped_and_flagged_or_dropped(tmp_path):
    res = read_raw(tmp_path, [blk("PARTLY OUT", (0.8, 0.8, 1.3, 0.9)), blk("FAR OUT", (1.2, 1.2, 1.5, 1.6))])
    partly = next(b for b in res.blocks if b.text == "PARTLY OUT")
    assert "bbox_oob" in partly.reasons and partly.bbox[2] <= 595.01
    far = next(b for b in res.blocks if b.text == "FAR OUT")
    assert "bbox_degenerate" in far.reasons and far.blocking


def test_overlap_empty_malformed_and_low_confidence(tmp_path):
    res = read_raw(tmp_path, [blk("RELAY A"), blk("RELAY B", (0.1, 0.1, 0.5, 0.15), 0.8), blk("   "),
                              {"text": "no box"}, blk("dim label", (0.1, 0.5, 0.5, 0.55), 0.2),
                              blk("SAME", (0.1, 0.7, 0.5, 0.75)), blk("same", (0.1, 0.7, 0.5, 0.75), 0.5)])
    by = {b.text: b for b in res.blocks}
    assert "overlap" in by["RELAY A"].reasons and "overlap" in by["RELAY B"].reasons
    assert "low_confidence" in by["dim label"].reasons and by["dim label"].needs_review
    assert res.dropped == {"empty": 1, "malformed": 1}
    assert sum(b.text.lower() == "same" for b in res.blocks) == 1 and not by["SAME"].needs_review


def test_hallucinated_codes_are_flagged(tmp_path):
    known = [TextBlock("Connect YT12-AB3C and part YN20-01234-A", (0, 0, 10, 10))]
    res = read_raw(tmp_path, [blk("YT12-AB3D", (0.1, 0.1, 0.5, 0.15)), blk("YN2O-O1234-A", (0.1, 0.3, 0.5, 0.35)),
                              blk("YT12-AB3C", (0.1, 0.5, 0.5, 0.55))], known)
    by = {b.text: b for b in res.blocks}
    assert "code_vs_textlayer" in by["YT12-AB3D"].reasons
    assert "code_confusable" in by["YN2O-O1234-A"].reasons
    assert not by["YT12-AB3C"].needs_review


def test_code_issues_unit():
    assert code_issues("part YN20-01234-A", {"YN20-01234-A"}) == []
    assert code_issues("24 V", set()) == []


def test_text_too_long_for_its_box_and_repetition(tmp_path):
    res = read_raw(tmp_path, [blk("A" * 5 + " very long invented sentence about nothing at all", (0.1, 0.1, 0.12, 0.11)),
                              blk(" ".join(["PUMP"] * 12), (0.1, 0.4, 0.9, 0.45))])
    assert "text_box_mismatch" in res.blocks[0].reasons or "text_box_mismatch" in res.blocks[1].reasons
    assert any("repetition" in b.reasons for b in res.blocks)


def test_missing_confidence_counts_as_low(tmp_path):
    res = read_raw(tmp_path, [{"text": "X1", "bbox": [0.1, 0.1, 0.4, 0.15], "kind": "label"}])
    assert res.blocks[0].needs_review and "low_confidence" in res.blocks[0].reasons


def test_unparsable_answer_marks_the_page_failed(tmp_path):
    doc = pymupdf.open()
    doc.new_page().insert_text((50, 50), "x")
    vlm = ScriptedVLM(raw="Sorry, I cannot see an image.")
    res = reader(tmp_path, vlm).read_page(doc[0])
    assert res.failed and res.errors == ["VisionParseError"] and res.blocks == []
    with pytest.raises(VisionParseError):
        parse_response("nope")


def test_json_in_markdown_fence_is_accepted():
    rows, bad = parse_response('```json\n{"blocks": [{"text": "A1", "bbox": [0,0,1,1], "kind": "label", "confidence": 1}]}\n```')
    assert len(rows) == 1 and bad == 0


def test_bbox_scale_1000(tmp_path):
    doc = pymupdf.open()
    doc.new_page().insert_text((50, 50), "x")
    vlm = ScriptedVLM(raw=json.dumps({"blocks": [blk("FUSE BOX", (100, 100, 500, 150))]}))
    res = reader(tmp_path, vlm, VisionConfig(bbox_scale=1000.0)).read_page(doc[0])
    assert not res.blocks[0].needs_review and abs(res.blocks[0].bbox[0] - 59.5) < 0.01


# ---- caps, cache, blank, policy -----------------------------------------------------------------------------
def test_call_cap_skips_and_flags(tmp_path):
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_image(pymupdf.Rect(50, 50, 280, 104), stream=raster_label("ONE"))
    p.insert_image(pymupdf.Rect(50, 500, 280, 554), stream=raster_label("TWO"))
    regions = [pymupdf.Rect(40, 40, 290, 114), pymupdf.Rect(40, 490, 290, 564)]
    vlm = ScriptedVLM(visible_truth(p, [("ONE", regions[0]), ("TWO", regions[1])]))
    res = reader(tmp_path, vlm, VisionConfig(max_calls_per_job=1)).read_page(p, regions)
    assert res.calls == 1 and "budget_calls" in res.skipped and res.failed


def test_time_cap(tmp_path):
    doc = pymupdf.open()
    mixed_page(doc)
    t = iter(range(0, 10000, 1000))
    rd = VisionReader(make_gateway(tmp_path, ScriptedVLM()), "job1", VisionConfig(max_seconds_per_job=5),
                      clock=lambda: next(t))
    rd.seconds = 10
    res = rd.read_page(doc[0], [pymupdf.Rect(290, 390, 540, 464)])
    assert "budget_time" in res.skipped and res.failed


def test_same_image_is_served_from_the_cache(tmp_path):
    doc = pymupdf.open()
    rect = mixed_page(doc)
    p = doc[0]
    vlm = ScriptedVLM(visible_truth(p, [("CAB WORK LIGHT", rect)]))
    gw = make_gateway(tmp_path, vlm)
    first = VisionReader(gw, "job1").read_page(p, [rect])
    second = VisionReader(gw, "job1").read_page(p, [rect])
    assert vlm.calls == 1 and second.cache_hits == 1 and second.calls == 0
    assert [b.text for b in second.blocks] == [b.text for b in first.blocks]


def test_blank_regions_cost_no_call(tmp_path):
    doc = pymupdf.open()
    p = doc.new_page()
    vlm = ScriptedVLM()
    res = reader(tmp_path, vlm).read_page(p)
    assert vlm.calls == 0 and res.skipped == ["blank"] and res.blocks == []


def test_customer_pages_never_reach_a_non_local_model(tmp_path):
    doc = pymupdf.open()
    mixed_page(doc)
    remote = OllamaAdapter(base_url="https://models.example.com/v1")
    gw = Gateway(cache_path=tmp_path / "c.sqlite", env={"MANUVI_PROFILE": "qa"}, adapters={"ollama": remote})
    with pytest.raises(DataClassViolation):
        VisionReader(gw, "job1").read_page(doc[0], [pymupdf.Rect(290, 390, 540, 464)])


def test_config_defaults_and_env(tmp_path):
    assert load_vision_config(env={}).max_calls_per_job == 40
    assert load_vision_config(env={"MANUVI_VISION_MAX_CALLS_PER_JOB": "3"}).max_calls_per_job == 3
    assert load_vision_config(env={}, max_seconds_per_job=5).max_seconds_per_job == 5


def test_lookalike_letters_in_a_code_are_blocked_as_foreign_script(tmp_path):
    res = read_raw(tmp_path, [blk("УN20-01234-A")])      # Cyrillic У instead of Y
    assert res.blocks[0].blocking and "script" in res.blocks[0].reasons


def test_ollama_request_shape_and_end_to_end_through_the_real_adapter(tmp_path):
    """qa profile: alias vision -> ollama adapter on localhost; image goes as a data URL with a JSON schema."""
    import httpx
    seen = {}
    doc = pymupdf.open()
    rect = mixed_page(doc)
    p = doc[0]
    truth = visible_truth(p, [("CAB WORK LIGHT", rect)])

    def handler(request):
        body = json.loads(request.content)
        seen["body"], seen["url"] = body, str(request.url)
        from .vision_helpers import scripted_answer
        # the tile is not in the body, so answer for the region we asked (single region read below)
        text = scripted_answer("p1:" + ",".join(f"{v:.2f}" for v in seen["clip"]), truth)
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}],
                                         "usage": {"prompt_tokens": 5, "completion_tokens": 5}})

    ad = OllamaAdapter(client=httpx.Client(transport=httpx.MockTransport(handler)))
    gw = Gateway(cache_path=tmp_path / "c.sqlite", env={"MANUVI_PROFILE": "qa"}, adapters={"ollama": ad})
    region = pymupdf.Rect(290, 390, 540, 464)
    seen["clip"] = tuple(to_visible(p, region))
    res = VisionReader(gw, "job1").read_page(p, [region])
    assert [b.text for b in res.blocks] == ["CAB WORK LIGHT"] and not res.blocks[0].needs_review
    body = seen["body"]
    assert seen["url"].endswith("/v1/chat/completions") and body["model"] == "qwen3-vl:8b"
    parts = body["messages"][1]["content"]
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert body["response_format"]["type"] == "json_schema" and body["temperature"] == 0.0
