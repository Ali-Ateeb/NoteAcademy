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
    pageNumber: 8,
    questionText:
      "A wave has a frequency of 50 Hz and a wavelength of 4.0 m. What is its speed?",
    markScheme: "C — v = fλ = 50 Hz × 4.0 m = 200 m/s.",
    correctOption: "C",
    cropStorageKey: "papers/5054/2019-mj/p12/crops/13.png",
    cropUrl: null,
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
    pageNumber: 14,
    questionText:
      "Which circuit component has a resistance that decreases as its temperature rises?",
    markScheme: null,
    correctOption: null,
    cropStorageKey: "papers/5054/2019-mj/p12/crops/27.png",
    cropUrl: null,
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
    pageNumber: 16,
    questionText:
      "A student heats 0.50 kg of water from 20 °C to 60 °C. The specific heat capacity of water is 4200 J/(kg·°C). How much thermal energy is transferred?",
    markScheme: "B — E = mcΔθ = 0.50 × 4200 × 40 = 84 000 J.",
    correctOption: "B",
    cropStorageKey: "papers/5054/2019-mj/p12/crops/31.png",
    cropUrl: null,
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
    pageNumber: 17,
    questionText:
      "The diagram shows a ray of light passing from glass into air. Which angle is the angle of refraction?",
    markScheme: "A — the angle between the refracted ray and the normal in the second medium.",
    correctOption: "A",
    cropStorageKey: "papers/5054/2019-mj/p12/crops/34.png",
    cropUrl: null,
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
    pageNumber: 19,
    questionText:
      "Which statement about the nucleus of an atom is correct?",
    markScheme: null,
    correctOption: null,
    cropStorageKey: "papers/5054/2019-mj/p12/crops/38.png",
    cropUrl: null,
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
