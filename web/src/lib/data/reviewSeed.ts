/**
 * Review-queue fixtures.
 *
 * These model what the pipeline actually produces when it is unsure: a question
 * it extracted but refused to publish, carrying the specific check that fired
 * and the topic assignment it was not confident about. As with seed.ts, the
 * question content was written for this scaffold and is not Cambridge material.
 *
 * The point of the fixtures is the *shape of the failures*, not the physics —
 * each one exercises a different flag so the queue UI can be built and tested
 * against every case before real papers exist.
 */

import type { ReviewItem } from "./types";

const PAPER = "physics-5054-2019-may-june-p12";
const PAPER_TITLE = "Physics 5054 · May / June 2019 · Paper 12";

export const reviewItems: ReviewItem[] = [
  {
    id: "r1",
    paperSlug: PAPER,
    paperTitle: PAPER_TITLE,
    displayLabel: "13",
    questionType: "mcq",
    questionText:
      "A wave has a frequency of 50 Hz and a wavelength of 4.0 m. What is its speed?",
    markScheme: "C — v = fλ = 50 Hz × 4.0 m = 200 m/s.",
    correctOption: "C",
    crops: [
      { storageKey: "papers/5054/2019-mj/p12/crops/13.png", pageNumber: 8, url: null },
    ],
    parts: null,
    extractionConfidence: 0.62,
    flags: ["low_tag_confidence"],
    proposedTopics: [
      {
        code: "3.1",
        title: "General wave properties",
        confidence: 0.62,
        reasoning: "Applies the wave equation relating speed, frequency and wavelength.",
      },
      {
        code: "1.2",
        title: "Kinematics",
        confidence: 0.31,
        reasoning: "Mentions speed, but not in the sense of motion of a body.",
      },
    ],
  },
  {
    id: "r2",
    paperSlug: PAPER,
    paperTitle: PAPER_TITLE,
    displayLabel: "27",
    questionType: "mcq",
    questionText:
      "Which circuit component has a resistance that decreases as its temperature rises?",
    markScheme: null,
    correctOption: null,
    crops: [
      { storageKey: "papers/5054/2019-mj/p12/crops/27.png", pageNumber: 14, url: null },
    ],
    parts: null,
    extractionConfidence: 0.88,
    flags: ["unmatched_mark_scheme"],
    proposedTopics: [
      {
        code: "4.2",
        title: "Current electricity",
        confidence: 0.94,
        reasoning: "Tests recall of the behaviour of a thermistor in a circuit.",
      },
    ],
  },
  {
    id: "r3",
    paperSlug: PAPER,
    paperTitle: PAPER_TITLE,
    displayLabel: "31",
    questionType: "mcq",
    questionText:
      "A student heats 0.50 kg of water from 20 °C to 60 °C. The specific heat capacity of water is 4200 J/(kg·°C). How much thermal energy is transferred?",
    markScheme: "B — E = mcΔθ = 0.50 × 4200 × 40 = 84 000 J.",
    correctOption: "B",
    crops: [
      { storageKey: "papers/5054/2019-mj/p12/crops/31.png", pageNumber: 16, url: null },
    ],
    parts: null,
    extractionConfidence: 0.41,
    flags: ["text_layer_mismatch", "low_tag_confidence"],
    proposedTopics: [
      {
        code: "2.1",
        title: "Thermal physics",
        confidence: 0.55,
        reasoning: "Uses the specific heat capacity relationship.",
      },
      {
        code: "1.5",
        title: "Energy, work and power",
        confidence: 0.48,
        reasoning: "Concerns a quantity of energy transferred.",
      },
    ],
  },
  {
    id: "r4",
    paperSlug: PAPER,
    paperTitle: PAPER_TITLE,
    displayLabel: "34",
    questionType: "mcq",
    questionText:
      "The diagram shows a ray of light passing from glass into air. Which angle is the angle of refraction?",
    markScheme: "A — the angle between the refracted ray and the normal in the second medium.",
    correctOption: "A",
    crops: [
      { storageKey: "papers/5054/2019-mj/p12/crops/34.png", pageNumber: 17, url: null },
    ],
    parts: null,
    extractionConfidence: 0.71,
    flags: ["bbox_outside_page"],
    proposedTopics: [
      {
        code: "3.2",
        title: "Light",
        confidence: 0.91,
        reasoning: "Requires identifying refraction angles relative to the normal.",
      },
    ],
  },
  {
    id: "r5",
    paperSlug: PAPER,
    paperTitle: PAPER_TITLE,
    displayLabel: "38",
    questionType: "mcq",
    questionText:
      "Which statement about the nucleus of an atom is correct?",
    markScheme: null,
    correctOption: null,
    crops: [
      { storageKey: "papers/5054/2019-mj/p12/crops/38.png", pageNumber: 19, url: null },
    ],
    parts: null,
    extractionConfidence: 0.79,
    flags: ["ambiguous_mark_scheme", "no_text_layer"],
    proposedTopics: [
      {
        code: "5.1",
        title: "Atomic and nuclear physics",
        confidence: 0.89,
        reasoning: "Tests knowledge of nuclear composition.",
      },
    ],
  },
  {
    id: "r6",
    paperSlug: "physics-5054-2015-may-june-p21",
    paperTitle: "Physics 5054 · May / June 2015 · Paper 21",
    displayLabel: "9",
    questionType: "structured",
    // Not read: structured papers are segmented geometrically, the same as
    // multiple-choice, so there is no extracted text to show here either —
    // see the mcq items above for the identical case.
    questionText: "",
    markScheme: null,
    correctOption: null,
    crops: [
      { storageKey: "papers/5054/2015-mj/p21/crops/9.png", pageNumber: 12, url: null },
      { storageKey: "papers/5054/2015-mj/p21/crops/9.1.png", pageNumber: 13, url: null },
      { storageKey: "papers/5054/2015-mj/p21/crops/9.2.png", pageNumber: 14, url: null },
    ],
    parts: [
      { displayLabel: "9(a)(i)", maxMarks: 2, markSchemeText: "speed and mass", reviewFlags: [] },
      {
        displayLabel: "9(a)(ii)",
        maxMarks: 1,
        markSchemeText: "1 speed and direction or distance/time and direction",
        reviewFlags: [],
      },
      {
        displayLabel: "9(a)(iii)",
        maxMarks: 2,
        markSchemeText: "force of gravity from / towards Earth",
        reviewFlags: [],
      },
      { displayLabel: "9(b)(i)", maxMarks: 1, markSchemeText: "450 000 N", reviewFlags: [] },
      {
        displayLabel: "9(b)(ii)",
        maxMarks: 2,
        markSchemeText: "(a =) F / m or 50 000 / 40 000",
        reviewFlags: [],
      },
      {
        // A sub-part the mark scheme never mentions — a genuine QP/MS
        // granularity mismatch, not a segmentation failure, so it is loaded
        // flagged rather than silently paired with the wrong entry.
        displayLabel: "9(c)(i)",
        maxMarks: null,
        markSchemeText: null,
        reviewFlags: ["unmatched_mark_scheme"],
      },
      {
        displayLabel: "9(c)(ii)",
        maxMarks: 3,
        markSchemeText: "start at origin and straight line for first section",
        reviewFlags: [],
      },
      { displayLabel: "9(c)(iii)", maxMarks: 1, markSchemeText: "area under graph", reviewFlags: [] },
    ],
    extractionConfidence: 1,
    flags: ["unmatched_mark_scheme"],
    proposedTopics: [],
  },
];

/** Topics the reviewer can reassign to. In the real app this is the subject's
 *  current syllabus version, loaded from the database. */
export const reviewTopicOptions = [
  { code: "1.1", title: "Physical quantities and measurement" },
  { code: "1.2", title: "Kinematics" },
  { code: "1.3", title: "Dynamics" },
  { code: "1.5", title: "Energy, work and power" },
  { code: "2.1", title: "Thermal physics" },
  { code: "3.1", title: "General wave properties" },
  { code: "3.2", title: "Light" },
  { code: "4.2", title: "Current electricity" },
  { code: "5.1", title: "Atomic and nuclear physics" },
];
