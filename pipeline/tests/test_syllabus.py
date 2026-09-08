"""Syllabus parsing.

The interesting cases are geometric, so the fixture is a real PDF built to the
template rather than a string: a heading hierarchy told apart by point size and
weight, and an equation set as a stacked fraction. A string fixture would test
the regexes and miss everything that actually goes wrong.
"""

from __future__ import annotations

import pymupdf
import pytest

from noteacademy_pipeline.syllabus import Syllabus, Topic, parse_syllabus, slugify, validate

BODY = 10
TOPIC_SIZE = 10
SECTION = 13
CHAPTER = 18

GUTTER_X = 62.4
BODY_X = 85.0


def write(page, x, y, text, size=BODY, bold=False):
    page.insert_text((x, y), text, fontsize=size, fontname="hebo" if bold else "helv")


@pytest.fixture
def syllabus_pdf(tmp_path):
    """A miniature syllabus laid out the way CAIE lays one out."""
    doc = pymupdf.open()
    page = doc.new_page()

    write(page, 56.7, 90, "3  Subject content", size=CHAPTER, bold=True)
    write(page, GUTTER_X, 140, "1", size=SECTION, bold=True)
    write(page, BODY_X, 140, "Motion, forces and energy", size=SECTION, bold=True)

    write(page, GUTTER_X, 170, "1.1  Motion", size=TOPIC_SIZE, bold=True)
    write(page, GUTTER_X, 200, "1")
    write(page, BODY_X, 200, "Define speed as distance travelled per unit")
    write(page, BODY_X, 212, "time")

    write(page, GUTTER_X, 240, "2")
    write(page, BODY_X, 240, "Recall and use the equation")
    # A stacked fraction: numerator, bar, denominator.
    write(page, BODY_X, 258, "speed = distance")
    page.draw_line((123.0, 262.0), (160.0, 262.0), width=0.6)
    write(page, 132.0, 274, "time")

    write(page, GUTTER_X, 300, "1.2  Forces", size=TOPIC_SIZE, bold=True)
    write(page, GUTTER_X, 330, "1.2.1  Friction")
    write(page, GUTTER_X, 360, "1")
    write(page, BODY_X, 360, "Explain the effects of friction on the motion of a body")

    path = tmp_path / "5054_syllabus.pdf"
    doc.save(path)
    doc.close()
    return path


def test_reads_the_heading_hierarchy(syllabus_pdf):
    syllabus = parse_syllabus(syllabus_pdf)

    codes = [topic.code for topic in syllabus.walk()]
    assert codes == ["1", "1.1", "1.2", "1.2.1"]
    assert syllabus.sections[0].title == "Motion, forces and energy"
    assert syllabus.sections[0].children[1].children[0].title == "Friction"


def test_objectives_hang_off_the_node_that_owns_them(syllabus_pdf):
    syllabus = parse_syllabus(syllabus_pdf)
    topics = {topic.code: topic for topic in syllabus.walk()}

    # A section is a container; a topic with sub-topics is too.
    assert topics["1"].learning_objectives == []
    assert topics["1.2"].learning_objectives == []
    assert len(topics["1.1"].learning_objectives) == 2
    assert len(topics["1.2.1"].learning_objectives) == 1


def test_a_wrapped_objective_is_one_objective(syllabus_pdf):
    syllabus = parse_syllabus(syllabus_pdf)
    first = next(t for t in syllabus.walk() if t.code == "1.1").learning_objectives[0]
    assert first == "Define speed as distance travelled per unit time"


def test_a_stacked_fraction_reads_as_a_fraction(syllabus_pdf):
    """The bug this rule exists for: 'speed = distance time' is not the equation."""
    syllabus = parse_syllabus(syllabus_pdf)
    second = next(t for t in syllabus.walk() if t.code == "1.1").learning_objectives[1]

    assert "speed = distance / time" in second
    assert "distance time" not in second
    assert syllabus.fraction_bars == 1
    assert syllabus.unresolved_bars == 0
    assert syllabus.problems == []


def test_ignores_everything_outside_the_subject_content_chapter(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    write(page, 56.7, 90, "2  Syllabus overview", size=CHAPTER, bold=True)
    write(page, GUTTER_X, 140, "1", size=SECTION, bold=True)
    write(page, BODY_X, 140, "Content overview", size=SECTION, bold=True)
    path = tmp_path / "5054_overview.pdf"
    doc.save(path)
    doc.close()

    syllabus = parse_syllabus(path)
    assert syllabus.sections == []
    assert syllabus.problems == ["no subject content found"]


def test_slugify_folds_accents_rather_than_dropping_them():
    assert slugify("Thin lenses") == "thin-lenses"
    assert slugify("Réfraction of light") == "refraction-of-light"
    assert slugify("Energy, work and power") == "energy-work-and-power"
    assert slugify("!!!") == "topic"


def leaf(code, title, objectives=("something",)):
    return Topic(code=code, title=title, slug=slugify(title),
                 learning_objectives=list(objectives))


def test_validate_rejects_a_topic_with_no_objectives():
    section = Topic(code="1", title="One", slug="one",
                    children=[leaf("1.1", "Fine"), leaf("1.2", "Empty", ())])
    problems = validate(Syllabus(sections=[section]))
    assert problems == ["1.2 'Empty' has no learning objectives"]


def test_validate_rejects_a_gap_in_the_section_numbering():
    sections = [
        Topic(code="1", title="One", slug="one", children=[leaf("1.1", "A")]),
        Topic(code="3", title="Three", slug="three", children=[leaf("3.1", "B")]),
    ]
    assert "sections are not 1..n: [1, 3]" in validate(Syllabus(sections=sections))


def test_validate_rejects_an_unresolved_fraction_bar():
    section = Topic(code="1", title="One", slug="one", children=[leaf("1.1", "A")])
    problems = validate(
        Syllabus(sections=[section], fraction_bars=4, unresolved_bars=1)
    )
    assert any("fraction bars" in problem for problem in problems)
