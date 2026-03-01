---
name: repo-analyzer
description: >
  Analyze a code repository for structure, complexity, and quality metrics.
  Use when asked to analyze, audit, review, or assess a codebase. Produces
  a structured report with file counts, language breakdown, dependency
  analysis, and potential issues.
version: 1.0.0
author: example
tags:
  - analysis
  - code-quality
  - reporting
---

# Repository Analyzer

Analyze a codebase and produce a structured quality report.

## Workflow

1. Run the analysis script to gather metrics:
   ```bash
   python /skill/scripts/analyze.py /input/ --output /output/report.json
   ```

2. Review the JSON output and synthesize findings.

3. If deeper analysis is needed, see [patterns.md](patterns.md) for
   common anti-pattern detection rules.

4. Write a human-readable summary to /output/report.md

## Output Format

The final report should include:
- **Overview**: Language breakdown, total files, lines of code
- **Structure**: Directory tree (top 2 levels)
- **Dependencies**: Package managers detected, dependency count
- **Issues Found**: Prioritized by severity (Critical/High/Medium/Low)
- **Recommendations**: Top 3 actionable improvements

## Important

- ALWAYS run the analysis script first — do not guess metrics.
- Use the actual numbers from the script output in your report.
- If the input directory is empty, report that clearly.
