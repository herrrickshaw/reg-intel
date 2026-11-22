"""reg_intel.score line 24 (load_gold) and _match edges."""
import json

from reg_intel import score


def test_load_gold_sorted_and_pattern(tmp_path):
    for n in ("b", "a"):
        (tmp_path / f"{n}.json").write_text(json.dumps({"title": n}))
    (tmp_path / "c.txt").write_text("{}")
    assert [g["title"] for g in score.load_gold(str(tmp_path / "*.json"))] == ["a", "b"]
    assert score.load_gold(str(tmp_path / "*.nothing")) == []


def test_load_gold_default_pattern_is_relative_gold_dir(tmp_path, monkeypatch):
    (tmp_path / "gold").mkdir()
    (tmp_path / "gold" / "x.json").write_text(json.dumps({"title": "x"}))
    monkeypatch.chdir(tmp_path)
    assert score.load_gold() == [{"title": "x"}]


def test_match_empty_never_matches():
    assert not score._match("", "abc") and not score._match("abc", "  ")


def test_match_ratio_threshold_is_inclusive_070():
    base7 = "abcdefg"
    assert score._match(base7 + "111", base7 + "222")                       # ratio exactly 0.70
    assert score._match("abcdefghijklmno" + "1" * 5, "abcdefghijklmno" + "2" * 5)   # 0.75
    assert not score._match("abcdefghijklm" + "1" * 7, "abcdefghijklm" + "2" * 7)    # 0.65
    assert not score._match("completely different", "zzzz qqqq xxxx")


def test_match_is_case_and_whitespace_insensitive_and_symmetric():
    assert score._match("Banks  SHALL\ncomply", "banks shall comply with the rule")
    assert score._match("banks shall comply with the rule", "Banks  SHALL\ncomply")


def test_precision_human_reviewed_ignores_blank_whitespace(tmp_path, monkeypatch):
    import csv
    monkeypatch.setattr(score, "PRECISION_DIR", str(tmp_path))
    with open(tmp_path / "k.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=score.COLS)
        w.writeheader()
        w.writerow({"citation": "c1", "human": "   ", "claude_draft": ""})
        w.writerow({"citation": "c2", "human": "N", "claude_draft": "y"})
        w.writerow({"citation": "c3", "human": "", "claude_draft": "f"})
    p = score.precision("k")
    assert p == {"rows": 3, "judged": 2, "human_reviewed": 1, "strict": 0.0, "lenient": 0.5, "y": 0, "f": 1, "n": 1}
