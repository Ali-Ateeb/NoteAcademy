"""Both grade-threshold table layouts, from text extracted off real Cambridge
PDFs (physics 5054 and chemistry 5070, June 2024, plus the June 2026 layout
change) -- trimmed to the lines the parser actually reads; the marketing
footer and contact details every PDF also carries add nothing to the test."""

from noteacademy_pipeline.grade_thresholds import parse_grade_threshold_text

PHYSICS_JUNE_2024 = """
Grade thresholds taken for Syllabus 5054 (Physics) in the June 2024 examination.
Minimum raw mark required for grade:
Maximum raw
mark
available
A
B
C
D
E
Component 11
40
29
24
19
17
15
Component 21
80
45
34
21
16
12
Grade A* does not exist at the level of an individual component.
The overall thresholds for the different grades were set as follows.
Option
Maximum
mark after
weighting
Combination of
components
A*
A
B
C
D
E
AX
200
11, 21, 31
154
127
100
73
61
50
AY
200
12, 22, 32
158
129
100
72
61
50
"""

# The June 2026 layout: no more two-letter option code, and a new
# single-component route (component 5, no variant).
PHYSICS_JUNE_2026 = """
Component grade thresholds for syllabus 5054 (Physics) in the June 2026 exam series.
Minimum raw mark required for grade:
Component
Maximum raw
mark
A
B
C
D
E
Component 11
40
29
24
18
16
15
Component 50
90
69
60
51
42
33
Grade A* does not exist at the level of an individual component.
Combination of
components
Maximum
weighted
mark
A*
A
B
C
D
E
11, 21, 41
200
163
132
101
70
58
47
50
90
78
69
60
51
42
33
"""


def test_component_thresholds_cascade_down_from_the_max_available_mark():
    components, _ = parse_grade_threshold_text(PHYSICS_JUNE_2024)
    by_component = {(c.component, c.variant): c for c in components}

    rows = {r.grade: r for r in by_component[(1, 1)].rows}
    assert rows["A"].min_mark == 29
    assert rows["A"].max_mark == 40  # the paper's own maximum
    assert rows["B"].min_mark == 24
    assert rows["B"].max_mark == 28  # one below A's minimum


def test_legacy_combination_rows_keep_their_option_code():
    _, combinations = parse_grade_threshold_text(PHYSICS_JUNE_2024)
    ax = next(c for c in combinations if c.combination == "AX")

    assert ax.components == "11, 21, 31"
    rows = {r.grade: r for r in ax.rows}
    assert rows["A*"].min_mark == 154
    assert rows["A*"].max_mark == 200
    assert rows["A"].min_mark == 127
    assert rows["A"].max_mark == 153  # one below A*'s minimum
    assert rows["E"].min_mark == 50


def test_2026_layout_has_no_option_code_and_a_solo_component_route():
    components, combinations = parse_grade_threshold_text(PHYSICS_JUNE_2026)

    # 'Component 50' is component 5, variant 0 -- same two-digit decoding
    # `naming.py` uses for a CAIE filename's own paper number.
    solo = next(c for c in components if c.component == 5)
    assert solo.variant == 0

    triple = next(c for c in combinations if c.components == "11, 21, 41")
    assert triple.combination == "11,21,41"  # no option code printed, so the
    # component list doubles as the identifier

    solo_combination = next(c for c in combinations if c.components == "50")
    assert solo_combination.combination == "50"
    assert {r.grade: r.min_mark for r in solo_combination.rows}["A*"] == 78
