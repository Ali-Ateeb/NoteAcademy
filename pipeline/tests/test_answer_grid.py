"""Parsing an MCQ answer key out of a mark scheme's text layer.

The fixtures below reproduce the *structure* of a CAIE multiple-choice mark
scheme — the Question/Answer/Marks table and the page furniture around it —
without reproducing any real paper's content.

This parser is the one place where a plausible-looking error does the most
damage: a student trusts an answer key completely. So the tests are mostly about
what it refuses to do.
"""

from noteacademy_pipeline.markscheme import parse_mcq_answer_grid, validate_answer_grid


def grid_page(rows: list[tuple[int, str]], page: int = 2, of: int = 3) -> str:
    """A mark scheme page with the same furniture as the real thing."""
    body = "\n".join(f"{number}\n{answer}\n1" for number, answer in rows)
    return (
        "1234/11\nCambridge O Level – Mark Scheme\nPUBLISHED\nMay/June 2026\n"
        f"© Cambridge University Press & Assessment 2026\nPage {page} of {of}\n"
        f"Question\nAnswer\nMarks\n{body}\n"
        "1234/11 Mark Scheme June 2026"
    )


COVER = (
    "Cambridge O Level\nPHYSICS\n1234/11\nPaper 1 Multiple Choice\n"
    "May/June 2026\nMARK SCHEME\nMaximum Mark: 40\nPublished\n"
)


class TestParseAnswerGrid:
    def test_reads_a_grid_spanning_several_pages(self):
        answers = parse_mcq_answer_grid([
            COVER,
            grid_page([(1, "D"), (2, "A"), (3, "B")]),
            grid_page([(4, "C"), (5, "D")], page=3),
        ])
        assert answers == {"1": "D", "2": "A", "3": "B", "4": "C", "5": "D"}

    def test_ignores_the_cover_page(self):
        # The cover carries "1234/11" and "Maximum Mark: 40" — bare numbers that
        # a looser parser would happily read as questions.
        assert parse_mcq_answer_grid([COVER]) == {}

    def test_page_furniture_does_not_become_an_answer(self):
        # "Page 2 of 3" and the footer year are numbers adjacent to letters.
        answers = parse_mcq_answer_grid([grid_page([(1, "A")])])
        assert answers == {"1": "A"}

    def test_returns_nothing_when_there_is_no_grid(self):
        # A structured paper's mark scheme, or a scan with no text layer. The
        # caller uses the empty result to fall back to vision.
        assert parse_mcq_answer_grid(["Marking guidance\n\n1(a) B1 for stating..."]) == {}
        assert parse_mcq_answer_grid([""]) == {}

    def test_drops_a_question_two_rows_disagree_about(self):
        # Never pick one of two conflicting answers: a wrong key is worse than a
        # missing one, because the student has no way to tell.
        answers = parse_mcq_answer_grid([
            grid_page([(1, "A"), (2, "B")]),
            grid_page([(1, "C")], page=3),
        ])
        assert "1" not in answers
        assert answers["2"] == "B"

    def test_accepts_a_repeated_row_that_agrees(self):
        answers = parse_mcq_answer_grid([
            grid_page([(1, "A")]),
            grid_page([(1, "A")], page=3),
        ])
        assert answers == {"1": "A"}

    def test_rejects_an_option_letter_outside_the_valid_set(self):
        assert parse_mcq_answer_grid([grid_page([(1, "F")])]) == {}

    def test_normalises_lowercase_options(self):
        assert parse_mcq_answer_grid([grid_page([(1, "d")])]) == {"1": "D"}


class TestValidateAnswerGrid:
    def test_a_complete_grid_has_no_problems(self):
        answers = {str(n): "A" for n in range(1, 41)}
        assert validate_answer_grid(answers, 40) == []

    def test_reports_a_hole_in_the_middle(self):
        # 39 of 40 is a parser failure, not a shorter paper. Saying so is what
        # stops the bank quietly acquiring gaps.
        answers = {str(n): "A" for n in range(1, 41) if n != 27}
        problems = validate_answer_grid(answers, 40)
        assert any("27" in p for p in problems)

    def test_reports_a_short_grid(self):
        answers = {str(n): "A" for n in range(1, 39)}
        problems = validate_answer_grid(answers, 40)
        assert any("38" in p for p in problems)

    def test_reports_a_grid_that_does_not_start_at_one(self):
        answers = {str(n): "A" for n in range(2, 41)}
        assert validate_answer_grid(answers, 39)
