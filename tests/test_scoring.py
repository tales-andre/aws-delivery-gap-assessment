import re
from pathlib import Path

import pytest

from evidence_kit import scoring

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "sample-report" / "scoring.csv"
SAMPLE = ROOT / "sample-report" / "gap-assessment.md"


def write(tmp_path, rows, name="s.csv"):
    p = tmp_path / name
    lines = ["task_id,task,weight,criterion_id,criterion,status,evidence,note"]
    lines += [f"{t},Task {t},{w},{c},crit,{s},E-01," for t, w, c, s in rows]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


@pytest.mark.parametrize(
    "codes, expected",
    [
        ("VV", "Complete, verified"),
        ("VS", "Complete, unverified"),
        ("SS", "Complete, unverified"),
        ("VM", "Partial"),
        ("SC", "Partial"),
        ("MC", "Not started"),
        ("UU", "Unable to assess"),
        ("MU", "Not started"),
    ],
)
def test_task_status_derivation(tmp_path, codes, expected):
    rows = [("T1", 10, f"1.{i}", c) for i, c in enumerate(codes)]
    (task,) = scoring.load(write(tmp_path, rows))
    assert task.status == expected


def test_project_range_is_weighted(tmp_path):
    rows = [("T1", 30, "1.1", "V"), ("T1", 30, "1.2", "S"), ("T2", 70, "2.1", "M")]
    low, high = scoring.project_range(scoring.load(write(tmp_path, rows)))
    assert low == pytest.approx(0.30 * 0.5)
    assert high == pytest.approx(0.30 * 1.0)


def test_unknown_status_and_inconsistent_weight_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        scoring.load(write(tmp_path, [("T1", 10, "1.1", "X")]))
    with pytest.raises(ValueError):
        scoring.load(write(tmp_path, [("T1", 10, "1.1", "V"), ("T1", 20, "1.2", "V")], "w.csv"))


def test_sample_report_numbers_match_scoring_csv():
    tasks = scoring.load(CSV)
    text = SAMPLE.read_text(encoding="utf-8")
    block = re.search(r"<!-- score:begin -->\n(.*?)\n<!-- score:end -->", text, re.S).group(1)
    assert block == scoring.markdown_table(tasks), "run: python sample-report/build.py"
    low, high = scoring.project_range(tasks)
    assert f"**{scoring.pct(low)} (verified)** and **{scoring.pct(high)} (supported)**" in text
    assert f"their {sum(t.n for t in tasks)} acceptance criteria" in text
    assert sum(t.weight for t in tasks) == 100
