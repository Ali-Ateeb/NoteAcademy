/**
 * Development fixtures.
 *
 * IMPORTANT: the questions below were written for this scaffold. They are not
 * Cambridge past-paper questions and must never be presented as such. Their only
 * job is to let the practice arena, the viewer and the analytics run before the
 * ingestion pipeline has loaded anything real. Every one carries
 * `source: "sample"` for the same reason.
 *
 * Real content arrives through pipeline/ and is read from Postgres — see
 * catalog.ts for the seam.
 */

import type { Level, McqQuestion, Paper, Subject, Topic } from "./types";

export const SEED_NOTICE =
  "Sample content written for development. Not Cambridge past-paper material.";

export const levels: Level[] = [
  { code: "o_level", name: "O Level", slug: "o-level" },
  { code: "igcse", name: "IGCSE", slug: "igcse" },
  { code: "as_level", name: "AS Level", slug: "as-level" },
  { code: "a_level", name: "A Level", slug: "a-level" },
];

export const subjects: Subject[] = [
  {
    slug: "physics-5054",
    levelCode: "o_level",
    syllabusCode: "5054",
    title: "Physics",
    description:
      "Every paper from 2010 onward, segmented by question and tagged to the current syllabus.",
    isPublished: true,
  },
  {
    slug: "mathematics-4024",
    levelCode: "o_level",
    syllabusCode: "4024",
    title: "Mathematics (Syllabus D)",
    description: "Ingestion in progress.",
    isPublished: false,
  },
  {
    slug: "chemistry-5070",
    levelCode: "o_level",
    syllabusCode: "5070",
    title: "Chemistry",
    description: "Ingestion in progress.",
    isPublished: false,
  },
  {
    slug: "biology-5090",
    levelCode: "o_level",
    syllabusCode: "5090",
    title: "Biology",
    description: "Ingestion in progress.",
    isPublished: false,
  },
];

/** The 2023–2025 Physics 5054 topic tree, trimmed to the units the sample
 *  questions cover. Codes follow CAIE's own unit numbering. */
export const topics: Topic[] = [
  {
    code: "1.1",
    slug: "physical-quantities",
    title: "Physical quantities and measurement",
    learningObjectives: [
      "describe how to measure length, volume and time",
      "distinguish between scalar and vector quantities",
    ],
    questionCount: 2,
    parentCode: null,
    isRevisable: true,
  },
  {
    code: "1.2",
    slug: "kinematics",
    title: "Kinematics",
    learningObjectives: [
      "define speed, velocity and acceleration",
      "plot and interpret distance–time and speed–time graphs",
      "state that acceleration of free fall near the Earth is constant",
    ],
    questionCount: 3,
    parentCode: null,
    isRevisable: true,
  },
  {
    code: "1.3",
    slug: "dynamics",
    title: "Dynamics",
    learningObjectives: [
      "state and apply Newton's laws of motion",
      "describe the effect of friction on motion",
      "recall and use the relationship between force, mass and acceleration",
    ],
    questionCount: 3,
    parentCode: null,
    isRevisable: true,
  },
  {
    code: "1.5",
    slug: "energy-work-power",
    title: "Energy, work and power",
    learningObjectives: [
      "recall and use the expressions for kinetic and gravitational potential energy",
      "relate work done to the magnitude of a force and the distance moved",
      "define power as work done per unit time",
    ],
    questionCount: 2,
    parentCode: null,
    isRevisable: true,
  },
  {
    code: "2.1",
    slug: "thermal-physics",
    title: "Thermal physics",
    learningObjectives: [
      "describe melting, boiling and evaporation in terms of particle behaviour",
      "define specific heat capacity and use the associated equation",
    ],
    questionCount: 1,
    parentCode: null,
    isRevisable: true,
  },
  {
    code: "4.2",
    slug: "current-electricity",
    title: "Current electricity",
    learningObjectives: [
      "recall and use the relationship between potential difference, current and resistance",
      "calculate combined resistance of resistors in series and in parallel",
    ],
    questionCount: 1,
    parentCode: null,
    isRevisable: true,
  },
];

function paper(
  year: number,
  season: Paper["season"],
  component: number,
  variant: number,
  questionType: Paper["questionType"],
  questionCount: number,
): Paper {
  return {
    slug: `physics-5054-${year}-${season.replace("_", "-")}-p${component}${variant}`,
    subjectSlug: "physics-5054",
    year,
    season,
    component,
    variant,
    questionType,
    questionCount,
    documents: [
      { docType: "qp", pageCount: questionType === "mcq" ? 16 : 20 },
      { docType: "ms", pageCount: questionType === "mcq" ? 2 : 12 },
      { docType: "er", pageCount: 9 },
    ],
  };
}

export const papers: Paper[] = [
  paper(2019, "may_june", 1, 2, "mcq", 12),
  paper(2019, "may_june", 2, 2, "structured", 11),
  paper(2019, "oct_nov", 1, 2, "mcq", 40),
  paper(2019, "oct_nov", 2, 2, "structured", 11),
  paper(2020, "may_june", 1, 2, "mcq", 40),
  paper(2020, "may_june", 2, 2, "structured", 11),
  paper(2021, "oct_nov", 1, 1, "mcq", 40),
  paper(2022, "may_june", 1, 2, "mcq", 40),
];

const P = "physics-5054-2019-may-june-p12";

/** Sample MCQs, authored for this scaffold. See the file header. */
export const mcqQuestions: McqQuestion[] = [
  {
    id: "q1",
    paperSlug: P,
    displayLabel: "1",
    questionText:
      "A student measures the thickness of one page of a book by measuring a stack of 200 pages as 12.0 mm. What is the thickness of one page?",
    options: {
      A: "0.006 mm",
      B: "0.06 mm",
      C: "0.6 mm",
      D: "6.0 mm",
    },
    correctOption: "B",
    markScheme: "B — 12.0 mm ÷ 200 = 0.060 mm.",
    examinerNote:
      "The most common wrong answer was C, from dividing by 20 rather than 200. Candidates who wrote the division out rather than doing it mentally were far more likely to be correct.",
    topicCodes: ["1.1"],
    cropUrl: null,
  },
  {
    id: "q2",
    paperSlug: P,
    displayLabel: "2",
    questionText:
      "Which pair contains one scalar quantity and one vector quantity?",
    options: {
      A: "distance and speed",
      B: "mass and energy",
      C: "speed and velocity",
      D: "velocity and acceleration",
    },
    correctOption: "C",
    markScheme:
      "C — speed is a scalar (magnitude only); velocity is a vector (magnitude and direction).",
    examinerNote:
      "A frequently missed question. Many candidates treated 'distance and speed' as a scalar/vector pair; both are scalars.",
    topicCodes: ["1.1"],
    cropUrl: null,
  },
  {
    id: "q3",
    paperSlug: P,
    displayLabel: "3",
    questionText:
      "A car travels 150 m in 10 s at constant speed, then stops for 5 s. What is its average speed over the whole 15 s?",
    options: { A: "5.0 m/s", B: "10 m/s", C: "15 m/s", D: "20 m/s" },
    correctOption: "B",
    markScheme:
      "B — average speed = total distance ÷ total time = 150 m ÷ 15 s = 10 m/s.",
    examinerNote:
      "C was the most common error: candidates found the speed during the moving phase (15 m/s) and ignored the stationary period. Average speed always uses total time.",
    topicCodes: ["1.2"],
    cropUrl: null,
  },
  {
    id: "q4",
    paperSlug: P,
    displayLabel: "4",
    questionText:
      "The speed–time graph for an object is a horizontal line above the time axis. What does this show?",
    options: {
      A: "the object is stationary",
      B: "the object is moving at constant speed",
      C: "the object is accelerating uniformly",
      D: "the object is decelerating uniformly",
    },
    correctOption: "B",
    markScheme:
      "B — constant (non-zero) speed. The gradient of a speed–time graph is acceleration; a horizontal line means zero acceleration.",
    examinerNote:
      "Candidates who confused this with a distance–time graph chose A. Reading the axis labels before interpreting the shape avoids the whole error.",
    topicCodes: ["1.2"],
    cropUrl: null,
  },
  {
    id: "q5",
    paperSlug: P,
    displayLabel: "5",
    questionText:
      "An object falls freely near the Earth's surface. Air resistance is negligible. What happens to its acceleration as it falls?",
    options: {
      A: "it increases",
      B: "it decreases",
      C: "it stays constant",
      D: "it becomes zero",
    },
    correctOption: "C",
    markScheme:
      "C — with negligible air resistance the only force is weight, so acceleration is constant at approximately 9.8 m/s².",
    examinerNote:
      "A was chosen by candidates who confused increasing *speed* with increasing acceleration. The speed increases; the acceleration does not.",
    topicCodes: ["1.2"],
    cropUrl: null,
  },
  {
    id: "q6",
    paperSlug: P,
    displayLabel: "6",
    questionText:
      "A resultant force of 12 N acts on a mass of 3.0 kg. What is the acceleration produced?",
    options: { A: "0.25 m/s²", B: "4.0 m/s²", C: "9.0 m/s²", D: "36 m/s²" },
    correctOption: "B",
    markScheme: "B — a = F / m = 12 N ÷ 3.0 kg = 4.0 m/s².",
    examinerNote:
      "Well answered. A came from inverting the relationship; candidates who wrote down F = ma before substituting rarely made that error.",
    topicCodes: ["1.3"],
    cropUrl: null,
  },
  {
    id: "q7",
    paperSlug: P,
    displayLabel: "7",
    questionText:
      "A book rests on a table. Which statement describes the pair of forces named by Newton's third law?",
    options: {
      A: "the weight of the book and the normal contact force from the table",
      B: "the weight of the book and the weight of the table",
      C: "the force of the book on the table and the force of the table on the book",
      D: "the normal contact force and the friction on the book",
    },
    correctOption: "C",
    markScheme:
      "C — a third-law pair acts on two different bodies, is of the same type, and is equal in magnitude and opposite in direction.",
    examinerNote:
      "A was by far the most popular wrong answer. Those two forces act on the *same* body and balance because the book is in equilibrium — that is the first law, not the third.",
    topicCodes: ["1.3"],
    cropUrl: null,
  },
  {
    id: "q8",
    paperSlug: P,
    displayLabel: "8",
    questionText:
      "A crate is pushed along a rough horizontal floor at constant velocity. What is true of the forces on the crate?",
    options: {
      A: "the push is greater than the friction",
      B: "the push is equal to the friction",
      C: "the push is less than the friction",
      D: "there is no friction",
    },
    correctOption: "B",
    markScheme:
      "B — constant velocity means zero acceleration, so the resultant force is zero and the push balances the friction.",
    examinerNote:
      "A recurring misconception: candidates assume that because the crate is moving, the push must exceed the friction. Motion needs no resultant force; only a *change* in motion does.",
    topicCodes: ["1.3"],
    cropUrl: null,
  },
  {
    id: "q9",
    paperSlug: P,
    displayLabel: "9",
    questionText:
      "A force of 25 N moves an object 4.0 m in the direction of the force. How much work is done?",
    options: { A: "6.3 J", B: "21 J", C: "29 J", D: "100 J" },
    correctOption: "D",
    markScheme: "D — W = F × d = 25 N × 4.0 m = 100 J.",
    examinerNote: "Well answered across the cohort.",
    topicCodes: ["1.5"],
    cropUrl: null,
  },
  {
    id: "q10",
    paperSlug: P,
    displayLabel: "10",
    questionText:
      "A motor does 3000 J of work in 20 s. What is its useful power output?",
    options: { A: "15 W", B: "60 W", C: "150 W", D: "60 000 W" },
    correctOption: "C",
    markScheme: "C — P = W / t = 3000 J ÷ 20 s = 150 W.",
    examinerNote:
      "D came from multiplying rather than dividing. A quick check of the magnitude — a small motor producing 60 kW is implausible — catches this.",
    topicCodes: ["1.5"],
    cropUrl: null,
  },
  {
    id: "q11",
    paperSlug: P,
    displayLabel: "11",
    questionText:
      "Which process involves a liquid changing to a gas at temperatures below its boiling point?",
    options: { A: "condensation", B: "evaporation", C: "melting", D: "sublimation" },
    correctOption: "B",
    markScheme:
      "B — evaporation occurs at the liquid surface at any temperature; boiling occurs throughout the liquid at a fixed temperature.",
    examinerNote:
      "Candidates who lost this mark generally could not distinguish evaporation from boiling. The distinguishing points are *where* it happens and *at what temperature*.",
    topicCodes: ["2.1"],
    cropUrl: null,
  },
  {
    id: "q12",
    paperSlug: P,
    displayLabel: "12",
    questionText:
      "Two 6.0 Ω resistors are connected in parallel. What is the combined resistance?",
    options: { A: "1.5 Ω", B: "3.0 Ω", C: "6.0 Ω", D: "12 Ω" },
    correctOption: "B",
    markScheme:
      "B — 1/R = 1/6.0 + 1/6.0 = 2/6.0, so R = 3.0 Ω. Two equal resistors in parallel give half the value.",
    examinerNote:
      "D was chosen by candidates applying the series rule. Combined parallel resistance is always *less* than the smallest individual resistance — a useful sanity check.",
    topicCodes: ["4.2"],
    cropUrl: null,
  },
];
