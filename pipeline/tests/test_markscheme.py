"""Label normalisation and mark-scheme matching.

Pure logic, no network. These are the functions that decide whether a student
sees the right mark scheme next to a question, so they are the ones worth
pinning down with tests.
"""

from noteacademy_pipeline.markscheme import (
    match_mark_scheme,
    match_mcq_answers,
    normalise_label,
    parse_structured_mark_scheme,
)
from noteacademy_pipeline.schemas import MarkSchemeEntry


def _page(*lines: str) -> str:
    """One mark-scheme page's text layer: boilerplate header, then content.

    `parse_structured_mark_scheme` drops everything up to and including the
    copyright line on every page — a page with no such line is dropped
    whole, so every fixture page needs one even though the test never reads
    it back.
    """
    header = ["2", "Mark Scheme", "Syllabus", "Paper", "5054", "21", "© UCLES 2015"]
    return "\n".join(header + list(lines))


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


class TestParseStructuredMarkScheme:
    def test_reads_question_part_and_subpart_labels_with_their_marks(self):
        entries = parse_structured_mark_scheme(
            [
                _page(
                    "1",
                    "(a) distance = 5.0 m",
                    "B1",
                    "(b) (i) 12 s",
                    "M1",
                    "(ii) speed = 2.0 m / s",
                    "A1",
                )
            ]
        )
        by_label = {e.display_label: e for e in entries}
        assert by_label["1(a)"].marks == 1
        assert by_label["1(a)"].content == "distance = 5.0 m"
        assert by_label["1(b)(i)"].marks == 1
        assert by_label["1(b)(ii)"].marks == 1

    def test_accumulates_more_than_one_mark_code_for_one_point(self):
        entries = parse_structured_mark_scheme(
            [_page("2", "(a) two reasons given", "C1", "A1")]
        )
        assert entries[0].marks == 2

    def test_reads_a_question_and_its_first_part_merged_on_one_line(self):
        # CAIE sometimes opens a question straight into its first part on one
        # printed line, exactly as the question paper itself does.
        entries = parse_structured_mark_scheme(
            [_page("10 (a) molecules not arranged regularly", "B1")],
            known_questions={"10"},
        )
        assert entries[0].display_label == "10(a)"

    def test_does_not_advance_on_a_bare_digit_inside_an_enumerated_point(self):
        # A marking point can itself be a numbered list ("1 speed and
        # direction ... 2 direction changes ..."), and a bare "2" there is
        # not a new question — ordinary ascending-order text a normal reader
        # would never mistake for one either.
        entries = parse_structured_mark_scheme(
            [
                _page(
                    "9",
                    "(a) (i) 1 speed and direction",
                    "2 direction changes",
                    "B1",
                )
            ],
            known_questions={"9"},
        )
        assert [e.display_label for e in entries] == ["9(a)(i)"]
        assert "2 direction changes" in entries[0].content

    def test_a_thousands_separated_value_matching_a_later_question_number_is_not_a_new_question(
        self,
    ):
        # CAIE renders "11 250 J" for 11250 J — a space-grouped thousands
        # value that happens to start with a real, later, known question
        # number. A bare question-number token is only ever alone on its
        # line or immediately followed by the part that opens it; "11 250 J"
        # is neither, so it must stay content for whatever question is
        # already open, not trigger a false start of question 11.
        entries = parse_structured_mark_scheme(
            [
                _page(
                    "10",
                    "(c) (E =) mL or 5 x 2250",
                    "C1",
                    "11 250 J",
                    "A1",
                    "11 (a) (i) 51",
                    "B1",
                )
            ],
            known_questions={"10", "11"},
        )
        by_label = {e.display_label: e for e in entries}
        assert "11 250 J" in by_label["10(c)"].content
        assert by_label["11(a)(i)"].content == "51"


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
