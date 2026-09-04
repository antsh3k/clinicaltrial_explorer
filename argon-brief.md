# Argon Take-Home: CT Agent

## Goal

Build a system that allows an AI agent to answer complex questions about the clinical trial landscape using ClinicalTrials.gov data.

You should ingest and index a ClinicalTrials.gov dataset, then build an agent that can use that index to answer questions such as:

- What drugs are currently being developed for a given indication?
- What are the most advanced clinical programs in an indication?
- Which companies are most active in a particular disease area?
- What mechanisms of action or therapeutic targets are being investigated?
- What trials are studying a particular mechanism of action?
- What biomarkers or patient subgroups are commonly being targeted?
- What combination therapies are being studied with a particular asset or mechanism?

The system should return **structured, useful, and traceable answers**, not simply perform semantic search over trial descriptions.

## Assignment

Use a ClinicalTrials.gov dataset as your source of truth (go to clinicaltrials.gov, click "search" while leaving all fields empty, and click "download". Json or csv is fine).

Design and implement:

1. **An indexing pipeline** that transforms the raw ClinicalTrials.gov data into a representation suitable for answering landscape-level questions (e.g. ontology).
2. **An agent or query system** that can reason over the indexed data and answer open-ended questions about assets, trials, sponsors, indications, mechanisms, targets, biomarkers, patient populations, and other relevant concepts. This can be a barebones CLI agent that plugs into your index in a loop.
3. **A useful output experience** that lets us understand the answer and inspect the supporting trials or evidence.

We intentionally do not prescribe:

- The ontology or schema
- The indexing strategy
- Whether you use SQL, search, embeddings, a graph, or some combination
- How much information should be derived deterministically versus with LLMs

These are part of the exercise.

## Things to Consider

ClinicalTrials.gov data is messy. For example:

- The same drug may appear under multiple names.
- Interventions may contain combinations, background therapies, or placebo.
- Conditions may be expressed at different levels of specificity.
- Trial phase does not necessarily equal the development stage of an asset.
- Mechanisms of action and targets may not be explicitly structured in the source data.
- Sponsors, collaborators, and asset owners are different concepts.
- A single trial may contain multiple arms, cohorts, biomarkers, or patient populations.

We are interested in how you structure and reason about this problem space.

## Evaluation

Create a lightweight way to evaluate whether the system is producing correct answers. For a small set of representative landscape questions, show:

- The answer produced by your system
- The underlying trials supporting it
- How you determined whether the answer was correct
- Important errors or limitations you discovered

The key question is:

**How do you know the agent is accurately representing the clinical trial landscape with completeness?**

## Deliverables

Please provide:

- Source repository and run instructions
- Simple Agent / query interface (via CLI is fine)
- Several example landscape questions and answers
- A brief explanation of your indexing / ontology architecture, where it performs well, where it performs poorly
- Precision/Recall tradeoffs
- Evaluation results and key failure modes
- A short description of how you used coding agents / LLMs during development. Optional: Please share the prompts/actions you took when using an AI coding agent.

**You are encouraged to use AI and coding agents extensively.**

We are evaluating your ability to take a messy life-sciences dataset and build a system that can reliably turn it into a clean and usable index.

Please do not reach for just keyword search, BM25, or semantic search.
