"""Label normalisation and mark-scheme matching.

Pure logic, no network. These are the functions that decide whether a student
sees the right mark scheme next to a question, so they are the ones worth
pinning down with tests.
"""

from noteacademy_pipeline.markscheme import (
    match_mark_scheme,
    match_mcq_answers,
    normalise_label,
)
from noteacademy_pipeline.schemas import MarkSchemeEntry


class TestNormaliseLabel:
    def test_accepts_the_spacing_variations_caie_actually_prints(self):
        for raw in ["7(a)(ii)", "7 (a) (ii)", "7(a)(II)", " 7 ( a ) ( ii ) "]:
            assert normalise_label(raw) == "7(a)(ii)"

    def test_keeps_bare_numbers_and_single_parts(self):
        assert normalise_label("12") == "12"
        assert normalise_label("3(b)") == "3(b)"

    def test_rejects_labels_with_no_question_number(self):
        assert normalise_label("(a)") is None
        assert normalise_label("BLANK PAGE") is None

    def test_does_not_confuse_roman_numerals_with_each_other(self):
        assert normalise_label("1(a)(i)") != normalise_label("1(a)(ii)")
        assert normalise_label("1(a)(iv)") == "1(a)(iv)"


class TestMatchMarkScheme:
    def test_matches_across_formatting_differences(self):
        result = match_mark_scheme(
            ["3(b)", "3(c)(i)"],
            [
                MarkSchemeEntry(display_label="3 (b)", marks=2, content="B1 for ..."),
                MarkSchemeEntry(display_label="3(c)(I)", marks=1, content="M1 for ..."),
            ],
        )
        assert result.is_clean
        assert result.matched["3(b)"].content == "B1 for ..."

    def test_reports_a_question_with_no_mark_scheme_rather_than_guessing(self):
        result = match_mark_scheme(
            ["5(a)", "5(b)"],
            [MarkSchemeEntry(display_label="5(a)", content="A1")],
        )
        assert result.matched.keys() == {"5(a)"}
        assert result.unmatched_questions == ["5(b)"]
        assert not result.is_clean

    def test_refuses_to_choose_between_duplicate_entries(self):
        # Two entries claiming one question means the paper uses a shape this
        # parser does not model. Attaching either one would be a coin flip.
        result = match_mark_scheme(
            ["4"],
            [
                MarkSchemeEntry(display_label="4", content="first"),
                MarkSchemeEntry(display_label="4", content="second"),
            ],
        )
        assert "4" not in result.matched
        assert not result.is_clean

    def test_surfaces_orphan_entries(self):
        result = match_mark_scheme(
            ["1"],
            [
                MarkSchemeEntry(display_label="1", content="ok"),
                MarkSchemeEntry(display_label="2", content="orphan"),
            ],
        )
        assert result.matched.keys() == {"1"}
        assert result.unmatched_entries == ["2"]


class TestMatchMcqAnswers:
    def test_attaches_answers_by_question_number(self):
        matched, unmatched = match_mcq_answers(
            ["1", "2", "3"], {"1": "C", "2": "A", "3": "D"}
        )
        assert matched == {"1": "C", "2": "A", "3": "D"}
        assert unmatched == []

    def test_reports_questions_the_grid_does_not_cover(self):
        matched, unmatched = match_mcq_answers(["1", "2"], {"1": "B"})
        assert matched == {"1": "B"}
        assert unmatched == ["2"]
