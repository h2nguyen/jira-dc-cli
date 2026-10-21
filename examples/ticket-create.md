---
# Omit project when `jira use PROJECT_KEY` has already been run.
project: DEMO
issuetype: Story
summary: User can export the monthly report
labels: reporting,backlog
priority: Medium
components: Reporting
fix_versions: Release 1.0
assignee:
fields_json: {}
---
## Goal

Allow users to export the monthly report in a reusable file format.

## Acceptance criteria

- The export action is visible to authorized users.
- The exported file contains the same data shown in the report.
- Empty and large reports are handled without an error.

## Notes

Add technical details, links, and dependencies here.
