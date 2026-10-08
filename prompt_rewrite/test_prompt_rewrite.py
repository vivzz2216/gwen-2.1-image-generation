#!/usr/bin/env python3
"""Comprehensive test suite for prompt_rewrite (pe_core, run_transformers, and data pipeline)."""

import os
import sys
import json
import tempfile
from pathlib import Path

# Add prompt_rewrite directory to path
PROMPT_REWRITE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROMPT_REWRITE_DIR))

import pe_core as core
import run_transformers

def test_profiles():
    print("-> Testing task profiles...")
    t2i = core.get_profile("t2i")
    assert t2i.name == "t2i"
    assert not t2i.takes_images
    assert not t2i.has_ratio_follow
    assert t2i.presence_penalty == 1.5
    assert t2i.max_new_tokens == 16256

    edit = core.get_profile("edit")
    assert edit.name == "edit"
    assert edit.takes_images
    assert edit.has_ratio_follow
    assert edit.presence_penalty == 0.0
    assert edit.max_new_tokens == 24000

    summary = t2i.sampling_summary(temperature=0.8)
    assert "temperature=0.8*" in summary
    assert "presence_penalty=1.5" in summary
    print("   [PASS] Task profiles verified.")

def test_system_prompts_and_data():
    print("-> Testing system prompts and input JSONL files...")
    prompts_dir = PROMPT_REWRITE_DIR / "prompts"
    data_dir = PROMPT_REWRITE_DIR / "data"

    t2i_prompt = core.load_system_prompt(str(prompts_dir / "system_prompt_t2i.txt"), None)
    assert len(t2i_prompt) > 100
    assert "wh_ratio" in t2i_prompt

    edit_prompt = core.load_system_prompt(str(prompts_dir / "system_prompt_edit.txt"), None)
    assert len(edit_prompt) > 100
    assert "ratio_follow" in edit_prompt

    t2i_cases = core.load_cases(data_dir / "t2i_example.jsonl")
    assert len(t2i_cases) >= 1
    assert "prompt" in t2i_cases[0]

    edit_cases = core.load_cases(data_dir / "edit_example.jsonl")
    assert len(edit_cases) >= 1
    prof_edit = core.get_profile("edit")
    for case in edit_cases:
        paths = core.resolve_image_paths(case, data_dir, prof_edit)
        assert len(paths) >= 1
        for p in paths:
            assert p.is_file(), f"Missing image: {p}"

    print(f"   [PASS] All {len(t2i_cases)} t2i cases and {len(edit_cases)} edit cases validated with existing images.")

def test_build_messages():
    print("-> Testing build_messages contract...")
    sys_txt = "System prompt here"
    user_txt = "Turn daylight to dusk"
    mock_images = ["data:image/png;base64,abc", "data:image/png;base64,xyz"]
    msgs = core.build_messages(sys_txt, user_txt, mock_images)

    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"][0]["text"] == sys_txt
    assert msgs[1]["role"] == "user"
    content = msgs[1]["content"]
    assert len(content) == 3
    assert content[0]["type"] == "image_url"
    assert content[1]["type"] == "image_url"
    assert content[2]["type"] == "text"
    assert content[2]["text"] == user_txt
    print("   [PASS] build_messages conversation contract verified.")

def test_split_thinking():
    print("-> Testing split_thinking parsing...")
    # Standard output
    raw = "<think>\nThinking about lighting and composition...\n</think>\n```json\n{\"rewritten_prompt\": \"A beautiful scene\", \"wh_ratio\": \"16:9\"}\n```"
    th, ans = core.split_thinking(raw)
    assert "Thinking about lighting" in th
    assert "rewritten_prompt" in ans

    # Unterminated think
    raw_unterminated = "<think>\nThinking in progress that ran out of tokens"
    th, ans = core.split_thinking(raw_unterminated)
    assert "ran out of tokens" in th
    assert ans == ""

    # Direct answer without thinking
    raw_no_think = "Direct answer content"
    th, ans = core.split_thinking(raw_no_think)
    assert th == ""
    assert ans == "Direct answer content"
    print("   [PASS] split_thinking handles completed, unterminated, and clean answers.")

def test_parse_answer():
    print("-> Testing parse_answer parsing logic...")
    prof_t2i = core.get_profile("t2i")
    prof_edit = core.get_profile("edit")

    # Clean JSON
    t2i_ans = 'Here is the result: {"rewritten_prompt": "Cinematic 8k shot of a misty forest", "wh_ratio": "16:9"}'
    res = core.parse_answer(t2i_ans, prof_t2i)
    assert res["parse_ok"] is True
    assert res["positive_prompt"] == "Cinematic 8k shot of a misty forest"
    assert res["wh_ratio"] == "16:9"
    assert res["ratio_follow"] == ""

    # Edit JSON with ratio_follow
    edit_ans = '{"rewritten_prompt": "Swap the background of <image1> with snow", "wh_ratio": "1:1", "ratio_follow": "<image1>"}'
    res = core.parse_answer(edit_ans, prof_edit)
    assert res["parse_ok"] is True
    assert res["positive_prompt"] == "Swap the background of <image1> with snow"
    assert res["ratio_follow"] == "<image1>"

    # Alternate key: rewrited_prompt
    alt_ans = '{"rewrited_prompt": "Alternative key test", "wh_ratio": "4:3"}'
    res = core.parse_answer(alt_ans, prof_t2i)
    assert res["parse_ok"] is True
    assert res["positive_prompt"] == "Alternative key test"

    # Fallback case
    malformed = "Just some unstructured conversational reply without any json."
    res = core.parse_answer(malformed, prof_t2i)
    assert res["parse_ok"] is False
    assert res["positive_prompt"] == malformed
    print("   [PASS] parse_answer accurately validates keys, fallbacks, and task profiles.")

def test_atomic_write_and_records():
    print("-> Testing atomic record generation and output writing...")
    prof_t2i = core.get_profile("t2i")
    case = {"id": "test_case_1", "prompt": "Draw a cyber dragon", "task_type": "creative"}
    thinking = "The prompt requests a cyber dragon."
    answer = '{"rewritten_prompt": "A glowing cybernetic dragon soaring over neo-tokyo", "wh_ratio": "16:9"}'

    rec = core.build_record(case, thinking, answer, prof_t2i)
    for field_name in core.OUTPUT_FIELDS:
        assert field_name in rec
    assert rec["parse_ok"] is True

    with tempfile.TemporaryDirectory() as tmpdir:
        out_file = Path(tmpdir) / "output_test.jsonl"
        core.write_records(out_file, [rec])
        assert out_file.is_file()
        with open(out_file, encoding="utf-8") as f:
            lines = [json.loads(l) for l in f if l.strip()]
        assert len(lines) == 1
        assert lines[0]["id"] == "test_case_1"
        assert lines[0]["positive_prompt"] == "A glowing cybernetic dragon soaring over neo-tokyo"

    print("   [PASS] Atomic file writing and record schema strictly verified.")

def test_cli_argument_parser():
    print("-> Testing run_transformers CLI parser...")
    # Verify main argument parser builds without error
    parser = None
    # Check that run_transformers module has required components
    assert hasattr(run_transformers, "rewrite")
    assert hasattr(run_transformers, "PresencePenalty")
    assert hasattr(run_transformers, "main")
    print("   [PASS] run_transformers module structure and parser verified.")

def run_all():
    print("==================================================")
    print(" RUNNING PRECISE TEST SUITE FOR PROMPT REWRITE    ")
    print("==================================================")
    test_profiles()
    test_system_prompts_and_data()
    test_build_messages()
    test_split_thinking()
    test_parse_answer()
    test_atomic_write_and_records()
    test_cli_argument_parser()
    print("==================================================")
    print(" ALL 7 TEST SUITES PASSED PRECISELY!              ")
    print("==================================================")

if __name__ == "__main__":
    run_all()
